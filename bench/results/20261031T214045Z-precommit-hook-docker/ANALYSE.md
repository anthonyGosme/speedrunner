# pre-commit servi quand git lance son hook

Suite de [20261031T185251Z-precommit-docker](../20261031T185251Z-precommit-docker/ANALYSE.md). Ce premier passage montrait que, sous `speedrunner exec git commit`, les hooks déclarés `language: system` étaient servis, mais pas pre-commit lui-même (~220 ms en natif à chaque commit). Même projet, mêmes sept manières, même méthode : 20 tours alternés, fichier modifié avant chaque appel, hors chronométrage. Le client est construit depuis l'arbre et monté sur celui de l'image ; `compare.py`, monté lui aussi, compare désormais la sortie complète et le code de sortie de chaque manière à ceux de la même commande en natif. Machine et versions dans `machine.txt`.

## Comment git atteint speedrunner

git exécute `.git/hooks/pre-commit` par son chemin, et ce script lance Python par son chemin absolu (`exec "$INSTALL_PYTHON" -mpre_commit hook-impl …`) : aucun des deux ne passe par les liens que `speedrunner exec` place en tête du `PATH`. Il reste une seule recherche dans le `PATH` sur ce trajet : le shebang du hook, `#!/usr/bin/env bash`, où `env` cherche `bash`.

- **Un lien `bash`**, à côté du lien `pre-commit`, dans le dossier de liens d'`exec` et d'`activate`. Il n'existe que si pre-commit y a le sien.
- **Appelé sous le nom `bash`**, le client lit seulement `argv[1]`. Si c'est un hook écrit par pre-commit (sa marque en deuxième ligne, et une fin `if … exec … elif … exec … else … exit 1 fi`), il le suit comme un lanceur (§2 ter de l'architecture) : le vrai bash l'exécute sous ptrace, le client l'arrête à l'exec de Python, puis sert `python -mpre_commit hook-impl …` par un démon. Sinon, il fait `exec` du vrai bash avec les mêmes arguments.
- **Rien ne tourne deux fois** : le préambule du hook tourne une fois, en natif, et pre-commit une fois, servi ; en repli (pas de ptrace, démon absent ou qui refuse), le client exécute la commande lue, telle quelle.

Options écartées : `core.hooksPath` (pre-commit refuse alors de s'installer, et cela remplace les hooks d'autres outils) ; une commande qui réécrirait le hook (`pre-commit install` l'écrase, ou le renomme en `pre-commit.legacy` et l'exécute en plus du sien) ; un lien `python3` (chemin absolu) ; un lien `git` qui passerait `core.hooksPath` par l'env (hérité par les git que lancent les hooks). Le lien `bash` ne demande aucune configuration et ne touche à aucun fichier du dépôt.

## Résultat

| Manière | Temps | Gain | Avant |
|---|---:|---:|---:|
| `pre-commit run`, hooks par défaut (`language: python`) | 1 153 ms | — | 1 156 ms |
| `srr pre-commit run` | 991 ms | x1,16 | 981 ms |
| `speedrunner exec pre-commit run` | 998 ms | x1,16 | 983 ms |
| `pre-commit run`, hooks `language: system` | 1 106 ms | x1,04 | 1 107 ms |
| `speedrunner exec pre-commit run`, hooks `language: system` | 276 ms | x4,2 | 276 ms |
| `git commit` (hooks `language: system`) | 1 109 ms | — | 1 124 ms |
| `speedrunner exec git commit` | **306 ms** | **x3,6** | 493 ms (x2,3) |

Gain d'un commit mesuré contre le même commit en natif. Sur les sept manières, mêmes verdicts et même sortie, au code de sortie près, que la commande native correspondante. Les six démons (pre-commit deux fois, black, isort, flake8, mypy) sont prêts et ont fini d'apprendre avant la mesure.

## Lecture

- **Le commit gagne 190 ms de plus** : 493 → 306 ms. C'est le démarrage de Python et les imports de pre-commit, qui ne sont plus refaits.
- **Il reste 30 ms par rapport à `pre-commit run` servi** (306 contre 276 ms) : git lui-même, le préambule du hook (un bash et un `dirname`), le suivi sous ptrace et le passage par le client sous le nom `bash`.
- **Le prix pour les autres scripts bash.** Sous `exec` ou `activate`, quand pre-commit a son lien, tout `bash` appelé par son nom passe par le client, qui le relance aussitôt : un script bash vide passe de 4,1 à 8,2 ms (hyperfine, 300 runs, même image ; `/usr/bin/true` seul y prend 1,9 ms, et le client `--version` 3,3 ms). C'est le coût d'un démarrage du client, plus lent dans la VM de Docker Desktop que sur une machine Linux.
- **Deux démons pre-commit.** Celui du hook exécute `python -m pre_commit` ; celui de `srr pre-commit` exécute le script `pre-commit`. `sys.argv[0]` et l'entrée diffèrent, donc ils ne se partagent pas : un démon de plus en mémoire (de l'ordre de 30 Mo d'après la FAQ, non mesuré ici).

## Le bug trouvé

Au premier passage du test avec le vrai pre-commit, chaque commit partait en natif : « import-time env differs: GIT_AUTHOR_DATE ». `pre_commit/xargs.py` calcule à l'import la longueur maximale d'une ligne de commande, `ARG_MAX` moins la taille de tout l'environnement, plafonnée à 128 Kio, et la fige comme valeur par défaut d'un argument. Il lit donc toutes les variables, et git passe au hook `GIT_AUTHOR_DATE`, qui change à chaque commit. Le démon avait raison de refuser. Sans correction, aucun commit n'aurait été servi, ni dans le test ni dans ce bench : la date change à chaque seconde.

Correction, sous forme d'adaptateur comme celui de rspec : les lectures faites par `_environ_size` de `pre_commit.xargs` ne sont pas notées, et l'enfant recalcule la valeur avec l'env du client. Test : `PreCommitHook.test_real_pre_commit` dans `tests/test_scripts.py`.

## Tests

Classe `PreCommitHook` de `tests/test_scripts.py` (7 tests). Suite `scripts` verte sous Debian, Alpine, Fedora et sans ptrace (les tests qui suivent le hook y sont sautés), matrice verte sous Debian (74 tests) :

- un hook au gabarit exact de pre-commit 4, sur `python -mhstest` : sortie, code de sortie et trace identiques au natif, que git ou le test l'exécute ;
- un vrai `git commit` : sortie identique, préambule et outil exécutés une fois par commit ;
- un hook qui échoue fait échouer le commit, avec la même sortie, et `HEAD` ne bouge pas ;
- sans démon (ou sans ptrace), le commit part en natif sans rien afficher, préambule et outil exécutés une fois ;
- les autres scripts bash, et `bash -c`, passent tels quels ; le lien `bash` n'existe qu'à côté de celui de pre-commit ;
- le vrai pre-commit (`repo: local`, `language: system`), un hook qui passe et un qui échoue : même sortie, même code, un seul passage du hook.

## Reproduire

```bash
docker build -t sr-precommit -f bench/precommit/Dockerfile bench
docker run --rm -v "$PWD/bench/results:/out" \
  -v "$PWD/client/target/docker-linux-amd64/speedrunner:/usr/local/bin/speedrunner:ro" \
  -v "$PWD/bench/precommit/compare.py:/opt/bench/compare.py:ro" \
  sr-precommit python3 /opt/bench/compare.py /out/<dossier>
```
