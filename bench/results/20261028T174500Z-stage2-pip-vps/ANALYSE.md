# Étape 2 — généricité : pip (VPS, 2026-10-28)

Mesure manuelle, sans script dédié. pip 26.2.2 du venv `venv-aws`, `HEADSTART_PRELOAD=1,2,3` (valeur par défaut), aucune configuration propre à pip. Apprentissage : `pip --version`, `pip list` et `pip show pip`, une fois chacun via headstart. Mesure : `hyperfine -N --warmup 3 --runs 20`.

| Commande | Natif | headstart | Gain |
|---|---|---|---|
| `pip --version` | 121 ± 12 ms | 14 ± 5 ms | x8,7 |
| `pip list` | 139 ± 12 ms | 18 ± 2 ms | x7,6 |

- La sortie est identique au natif pour les deux commandes (vérifié avec `diff`).
- Le démon a appris 232 modules en deux générations, sans aucun refus. Le niveau 3 ne s'active pas : pip n'utilise pas botocore.
- À l'étape 1, avec le niveau 1 seul, pip ne gagnait que x1,2 : il importe presque tout paresseusement. Le niveau 2 est le niveau générique.
