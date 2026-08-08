# aioonkyo_alt

Distribution alternative de [`aioonkyo`](https://github.com/arturpragacz/aioonkyo)
par Artur Pragacz.

**Ce n'est pas une amélioration.** C'est la même bibliothèque, avec quelques
commandes ISCP supplémentaires qui n'ont pas encore été proposées ou fusionnées
en amont. Si vous n'avez pas besoin de ces commandes, utilisez `aioonkyo` :
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
