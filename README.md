# aioonkyo_alt

Distribution alternative de [`aioonkyo`](https://github.com/arturpragacz/aioonkyo)
par Artur Pragacz.

**Ce n'est pas une amélioration.** C'est la même bibliothèque, avec quelques
commandes ISCP supplémentaires et une détection des coupures silencieuses, qui
n'ont pas encore été proposées ou fusionnées en amont. Si vous n'avez pas besoin de ces commandes, utilisez `aioonkyo` :
elle est maintenue, celle-ci ne l'est que par son auteur.

Le paquet d'import s'appelle `aioonkyo_alt` afin que les deux distributions
puissent cohabiter dans le même `site-packages` — Home Assistant installe déjà
`aioonkyo` pour son intégration Onkyo officielle.

```python
from aioonkyo_alt import command, query
command.DialogEnhancement(5).raw   # b'VOC05'
```

## Commandes ajoutées

| Kind | ISCP | Plage | Testé sur |
|---|---|---|---|
| `DIALOG_ENHANCEMENT` | `VOC` | 0–5 | Pioneer VSX-LX305 |

## Détection des coupures silencieuses

Un ampli dont la partie réseau se fige ne ferme pas la connexion TCP. Avec
`aioonkyo` 0.4.0, une connexion inactive paraît alors vivante indéfiniment, et
une commande envoyée n'échoue qu'au bout d'environ 15 minutes de
retransmissions.

`connect()` et `Receiver.open_connection()` activent désormais le keepalive TCP
(`KeepAlive(idle=30, interval=10, count=3)` par défaut) : un ampli muet est
détecté en une minute environ, au repos comme après une commande (sous Linux,
`TCP_USER_TIMEOUT` borne aussi l'attente des acquittements). La lecture renvoie
alors `None`, comme pour toute autre déconnexion.

```python
from aioonkyo_alt import KeepAlive, connect

async with connect(info, keepalive=KeepAlive(idle=15, interval=5, count=3)) as receiver:
    ...
async with connect(info, keepalive=None) as receiver:  # comportement amont
    ...
```

Vérifié en simulation (trafic du récepteur bloqué par pare-feu : coupure détectée
en `idle + interval × count` secondes) ; pas encore éprouvé lors d'une vraie panne.

## Versionnage

`<version amont>.postN`. `0.4.0.post1` = amont 0.4.0, première itération.
Quand l'amont sort 0.5.0, ce fork repart en `0.5.0.post1`.

## Resynchronisation amont

```bash
git fetch upstream --tags
git merge 0.5.0     # merge, pas rebase : préserve la détection de renommage
```

## Remerciements

Tout le mérite de la conception et de la réalisation revient à Artur Pragacz.

## Licence

GPL-3.0-only, inchangée par rapport à l'amont.
