# pre-commit de bout en bout

Un petit projet Python (`bench/precommit/project`) dont le commit lance les hooks habituels : black, isort, flake8, mypy. On mesure ce que fait le hook (`pre-commit run --files shop/cart.py`) et un vrai `git commit`, le fichier modifié avant chaque appel (sa docstring, hors chronométrage), pour qu'aucun cache de résultat ne réponde. 20 tours alternés, sept manières ; machine et versions dans `machine.txt`.

## Résultat

| Manière | Temps | Gain |
|---|---:|---:|
| `pre-commit run`, hooks par défaut (`language: python`) | 1 156 ms | — |
| `srr pre-commit run` | 981 ms | x1,18 |
| `speedrunner exec pre-commit run` | 983 ms | x1,18 |
| `pre-commit run`, hooks `language: system` | 1 107 ms | x1,04 |
| `speedrunner exec pre-commit run`, hooks `language: system` | 276 ms | **x4,2** |
| `git commit` (hooks `language: system`) | 1 124 ms | x1,03 |
| `speedrunner exec git commit` | 493 ms | **x2,3** |

Les quatre hooks passent dans les sept cas, avec le même verdict qu'en natif.

## Lecture

- **Hooks par défaut : seul pre-commit gagne.** Avec `language: python` (le cas de presque toutes les configurations), pre-commit installe chaque outil dans son propre environnement et l'appelle par son chemin absolu. speedrunner ne voit que ce qui passe par lui (`srr`, les wrappers, les liens de `speedrunner exec` en tête du `PATH`) : il sert pre-commit lui-même (~175 ms gagnées), pas ses hooks.
- **Hooks `language: system` : x4,2.** Les mêmes outils pris dans le `PATH` passent par les liens de `speedrunner exec`, et chacun est servi par son démon.
- **Un vrai commit : x2,3.** git lance le hook installé par pre-commit, qui démarre pre-commit par le chemin absolu de Python : pre-commit reste natif (~220 ms), ses hooks sont servis.

## Le bug trouvé

Au premier passage, `speedrunner exec pre-commit run` ne gagnait rien. pre-commit lit `PATH` au chargement ; son démon, démarré par `srr`, avait donc enregistré un `PATH` sans le dossier de liens de `speedrunner exec`, et chaque appel sous `exec` partait en natif (« import-time env differs: PATH »). Utiliser `srr` dans le terminal et `exec` dans les scripts est l'usage normal. Corrigé : en comparant le `PATH` lu à l'import, les démons Python, Ruby et Node ignorent leurs propres dossiers de liens, qui mènent aux mêmes outils (test `test_tool_reading_path_at_import_served_under_exec`). Les chiffres ci-dessus sont ceux du client corrigé.

## Pistes

- Servir pre-commit quand git lance le hook : le script installé se termine par un `if … exec python -m pre_commit … elif …`, que le suivi des lanceurs (une commande `exec` finale) ne reconnaît pas.
- Servir les hooks `language: python` : il faudrait intercepter l'exécution par chemin absolu (hors de portée d'un dossier de liens).
