"""Receiver."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
import logging
import socket
from typing import TYPE_CHECKING, Final, Generic, Protocol, Self, TypeVar

from .common import BasicReceiverInfo, ReceiverInfo
from .instruction import Instruction
from .protocol import (
    DiscoveryInfo,
    EISCPDiscovery,
    OnkyoConnectionError,
    read_message,
    write_message,
)
from .status import Status

_LOGGER = logging.getLogger(__name__)


BROADCAST_ADDRESS: Final = "255.255.255.255"


@dataclass(frozen=True, kw_only=True)
class KeepAlive:
    """TCP keepalive settings for the receiver connection.

    A receiver whose network stack freezes does not close the connection: without
    probes, an idle connection looks alive forever, and writes are retried by the
    kernel for about 15 minutes before failing. With these settings, a silent
    receiver is detected after about ``idle + interval * count`` seconds, both
    while idle (keepalive probes) and after a write (``TCP_USER_TIMEOUT``).
    """

    idle: int = 30
    """Seconds without traffic before the first probe."""
    interval: int = 10
    """Seconds between probes."""
    count: int = 3
    """Unanswered probes before the connection is considered dead."""

    @property
    def timeout(self) -> int:
        """Seconds after which an unresponsive receiver is considered gone."""
        return self.idle + self.interval * self.count


DEFAULT_KEEPALIVE: Final = KeepAlive()


class _SocketWithOptions(Protocol):
    """Socket accepting options (``socket.socket`` or asyncio's transport socket)."""

    def setsockopt(self, level: int, optname: int, value: int, /) -> None:
        """Set a socket option."""


def _apply_keepalive(sock: _SocketWithOptions, keepalive: KeepAlive) -> None:
    """Enable TCP keepalive on the socket, with the options the platform supports."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    # TCP_KEEPIDLE is named TCP_KEEPALIVE on macOS.
    idle_option = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    options: list[tuple[int | None, int]] = [
        (idle_option, keepalive.idle),
        (getattr(socket, "TCP_KEEPINTVL", None), keepalive.interval),
        (getattr(socket, "TCP_KEEPCNT", None), keepalive.count),
        # Linux only: also bounds how long written data may stay unacknowledged.
        (getattr(socket, "TCP_USER_TIMEOUT", None), keepalive.timeout * 1000),
    ]
    for option, value in options:
        if option is not None:
            sock.setsockopt(socket.IPPROTO_TCP, option, value)


async def interview(
    host: str,
    *,
    port: int = 60128,
) -> ReceiverInfo:
    """Interview Onkyo Receiver."""

    target_str = f"{host}:{port}"
    _LOGGER.debug("Interviewing receiver: %s", target_str)

    receiver_info_future: asyncio.Future[ReceiverInfo] = asyncio.Future()

    def callback(discovery: DiscoveryInfo) -> None:
        """Receiver interviewed, connection not yet active."""
        if receiver_info_future.done():
            return
        receiver_info = ReceiverInfo(
            host=host,
            ip=discovery.ip,
            port=discovery.iscp_port,
            model_name=discovery.model_name,
            identifier=discovery.identifier,
        )
        receiver_info_future.set_result(receiver_info)

    protocol = EISCPDiscovery(target_str, callback)

    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(lambda: protocol, remote_addr=(host, port))

    try:
        receiver_info = await receiver_info_future
    finally:
        protocol.close()

    _LOGGER.debug("Interviewed receiver %s: %s", target_str, receiver_info)

    return receiver_info


async def discover(
    address: str = BROADCAST_ADDRESS,
    *,
    port: int = 60128,
) -> AsyncGenerator[ReceiverInfo]:
    """Discover Onkyo Receivers."""

    target_str = f"{address}:{port}"
    _LOGGER.debug("Discovering receivers on %s", target_str)

    receivers_discovered: set[str] = set()
    receiver_info_queue: asyncio.Queue[ReceiverInfo] = asyncio.Queue()

    def callback(discovery: DiscoveryInfo) -> None:
        """Receiver discovered, connection not yet active."""
        info = ReceiverInfo(
            host=discovery.ip,
            ip=discovery.ip,
            port=discovery.iscp_port,
            model_name=discovery.model_name,
            identifier=discovery.identifier,
        )
        if info.identifier not in receivers_discovered:
            receivers_discovered.add(info.identifier)
            receiver_info_queue.put_nowait(info)

    protocol = EISCPDiscovery(target_str, callback)

    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(
        lambda: protocol,
        remote_addr=(address, port),
        allow_broadcast=True,
    )

    try:
        while True:
            receiver_info = await receiver_info_queue.get()
            _LOGGER.debug("Discovered receiver on %s: %s", target_str, receiver_info)
            yield receiver_info
    finally:
        protocol.close()


InfoT = TypeVar("InfoT", bound=BasicReceiverInfo, default=ReceiverInfo)


async def _connect_receiver_retry(info: InfoT, keepalive: KeepAlive | None) -> Receiver[InfoT]:
    """Connect to the receiver, retrying on failure."""
    sleep_time = 10
    sleep_time_max = 180
    while True:
        try:
            return await Receiver.open_connection(info, keepalive=keepalive)
        except OSError:
            await asyncio.sleep(sleep_time)
            sleep_time = min(sleep_time * 2, sleep_time_max)


@asynccontextmanager
async def connect(
    info: InfoT,
    *,
    retry: bool = False,
    run_queue: bool = False,
    keepalive: KeepAlive | None = DEFAULT_KEEPALIVE,
) -> AsyncGenerator[Receiver[InfoT]]:
    """Connect to the receiver.

    ``keepalive=None`` disables TCP keepalive (behaviour of aioonkyo 0.4.0).
    """
    _LOGGER.debug(
        "Async context manager connect (retry: %s, run_queue: %s): %s", retry, run_queue, info
    )

    if retry:
        receiver = await _connect_receiver_retry(info, keepalive)
    else:
        receiver = await Receiver.open_connection(info, keepalive=keepalive)

    try:
        if run_queue:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(receiver.run_queue())
                yield receiver
        else:
            yield receiver
    finally:
        receiver.close()


class _ReceiverState(Enum):
    """Receiver state."""

    CONNECTED = "CONNECTED"
    RUNNING_QUEUE = "RUNNING_QUEUE"
    CLOSED = "CLOSED"


@dataclass
class Receiver(Generic[InfoT]):
    """Receiver (connected)."""

    info: InfoT
    _reader: asyncio.StreamReader
    _writer: asyncio.StreamWriter
    _read_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _read_queue: asyncio.Queue[Status] | None = None
    _write_queue: asyncio.Queue[Instruction] | None = None
    _state: _ReceiverState = _ReceiverState.CONNECTED

    @classmethod
    async def open_connection(
        cls, info: InfoT, *, keepalive: KeepAlive | None = DEFAULT_KEEPALIVE
    ) -> Self:
        """Open connection to the receiver."""
        _LOGGER.debug("Connecting: %s", info)
        reader, writer = await asyncio.open_connection(info.host, info.port)
        if keepalive is not None:
            sock: _SocketWithOptions | None = writer.get_extra_info("socket")
            if sock is not None:
                try:
                    _apply_keepalive(sock, keepalive)
                except OSError as exc:
                    _LOGGER.warning("Could not enable TCP keepalive (%s): %s", exc, info)
        _LOGGER.debug("Connected: %s", info)
        return cls(info, reader, writer)

    async def run_queue(self) -> None:
        """Run queue reader/writer."""
        if self._state is not _ReceiverState.CONNECTED:
            raise RuntimeError(
                "Run queue called on receiver not in CONNECTED state, "
                f"current state: {self._state}, info: {self.info}"
            )

        self._read_queue = asyncio.Queue()
        self._write_queue = asyncio.Queue()
        _LOGGER.debug("Run queue starting: %s", self.info)
        self._state = _ReceiverState.RUNNING_QUEUE

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._read_messages())
                tg.create_task(self._write_messages())
        except* OnkyoConnectionError as exc:
            _LOGGER.warning("Disconnect detected (%s): %s", exc.exceptions, self.info)
        finally:
            _LOGGER.debug("Run queue ending: %s", self.info)
            self._do_close()

    async def _read_messages(self) -> None:
        """Read messages."""
        queue = self._read_queue
        if TYPE_CHECKING:
            assert queue is not None
        async with self._read_lock:
            while True:
                message = await read_message(self._reader)
                _LOGGER.debug("[%s]   << %s", self.info.host, message)
                await queue.put(message)

    async def _write_messages(self) -> None:
        """Write messages."""
        queue = self._write_queue
        if TYPE_CHECKING:
            assert queue is not None
        async with self._write_lock:
            while True:
                message = await queue.get()
                await write_message(self._writer, message)
                _LOGGER.debug("[%s] >>>> %s", self.info.host, message)

    async def read(self) -> Status | None:
        """Read from the receiver."""
        if self._state is _ReceiverState.CLOSED:
            return None

        if self._read_queue is None:
            async with self._read_lock:
                try:
                    message = await read_message(self._reader)
                except OnkyoConnectionError as exc:
                    self._disconnect_from_read_write(exc)
                    return None
                except Exception as exc:
                    self._disconnect_from_read_write(exc)
                    raise
        else:
            try:
                message = await self._read_queue.get()
            except asyncio.QueueShutDown:
                return None

        _LOGGER.debug("[%s] <<<< %s", self.info.host, message)
        return message

    async def write(self, message: Instruction) -> None:
        """Write to the receiver."""
        if self._state is _ReceiverState.CLOSED:
            raise RuntimeError(f"Write called on receiver in CLOSED state, info: {self.info}")

        if self._write_queue is None:
            async with self._write_lock:
                try:
                    await write_message(self._writer, message)
                except Exception as exc:
                    self._disconnect_from_read_write(exc)
                    raise
                _LOGGER.debug("[%s] >>>> %s", self.info.host, message)
        else:
            _LOGGER.debug("[%s] >>   %s", self.info.host, message)
            self._write_queue.put_nowait(message)

    def _disconnect_from_read_write(self, exc: Exception) -> None:
        """Disconnect the receiver (called from read/write)."""
        if self._state is _ReceiverState.CLOSED:
            _LOGGER.debug("Disconnect - already closed: %s", self.info)
            return

        if isinstance(exc, OnkyoConnectionError):
            _LOGGER.warning("Disconnect detected (%s): %s", exc, self.info)
        self._do_close()

    def _do_close(self) -> None:
        """Close connection."""
        if self._state is _ReceiverState.CLOSED:
            raise RuntimeError(f"Do close called on receiver in CLOSED state, info: {self.info}")

        _LOGGER.debug("Closing: %s", self.info)
        self._state = _ReceiverState.CLOSED
        self._writer.close()  # writer closes the whole stream, including the reader
        if self._read_queue is not None:
            self._read_queue.shutdown(immediate=True)

    def close(self) -> None:
        """Close connection."""
        if self._state is _ReceiverState.CLOSED:
            return
        if self._state is _ReceiverState.RUNNING_QUEUE:
            raise RuntimeError(
                f"Close called on receiver in RUNNING_QUEUE state, info: {self.info}"
            )
        self._do_close()


class BasicReceiver(Receiver[BasicReceiverInfo]):
    """Basic receiver (connected)."""


__all__ = [
    "KeepAlive",
    "DEFAULT_KEEPALIVE",
    "interview",
    "discover",
    "connect",
    "Receiver",
]
