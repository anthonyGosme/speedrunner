# speedrunner contre les démons dédiés des outils

Plusieurs outils ont fabriqué leur propre démon, faute d'un démarrage assez rapide : eslint_d, prettierd, `rubocop --server`, dmypy, blackd. Chacun le fait pour lui seul ; speedrunner le fait pour tout outil, sans configuration. Ce panel les compare sur le cas pour lequel ces démons existent : l'enregistrement d'un fichier dans un éditeur.

## Protocole

- Image `bench/daemons/Dockerfile` (node:24-bookworm), Docker Desktop sur le portable (i7-9750H) ; versions dans `machine.txt`. speedrunner est la release v0.3.1, installée par `install.sh` comme chez un utilisateur.
- Un petit fichier par outil. **Avant chaque appel, il reçoit une ligne de commentaire nouvelle** (hors chronométrage) : aucun cache de résultat ne répond (celui de rubocop, celui de mypy), comme après un vrai enregistrement.
- 20 tours ; dans chaque tour : natif, speedrunner (démon chaud, chemin rapide exigé), démon dédié (chaud, par son client habituel). Médiane, min et max ; gain = rapport des médianes, intervalle à 95 % par bootstrap.
- Sorties : code de sortie, stdout et stderr de speedrunner comparés au natif ; code et stdout du client dédié comparés au natif.
- Mêmes versions des deux côtés : eslint_d 14 embarque eslint 9.39.5, la version installée (eslint_d 15 embarque eslint 10 : écarté). Un `.rubocop.yml` (`NewCops: enable`), comme dans tout projet : sans lui, rubocop affiche à chaque appel la liste des règles en attente, et son client serveur est aussi lent que le natif.

## Résultat

| Outil | Natif | speedrunner | Démon dédié | Gain speedrunner | Gain démon dédié |
|---|---:|---:|---:|---:|---:|
| eslint, un fichier | 328 ms | 43 ms | 107 ms (eslint_d) | **x7,6** | x3,1 |
| prettier, un fichier | 211 ms | 62 ms | 105 ms (prettierd) | **x3,4** | x2,0 |
| rubocop, un fichier | 297 ms | 108 ms | 303 ms (`--server`) | **x2,8** | x1,0 |
| mypy, un fichier | 213 ms | 50 ms | 147 ms (dmypy) | **x4,2** | x1,4 |
| black, stdin | 241 ms | 26 ms | 19 ms (blackd + curl) | x9,4 | **x12,7** |

Sorties de speedrunner identiques au natif sur les cinq. Clients dédiés : même stdout que le natif, sauf `rubocop --server`, qui colore sa sortie même hors terminal.

## Lecture

- **speedrunner gagne quatre duels sur cinq.** Le client d'un démon dédié est lui-même un processus Node, Python ou Ruby qui démarre à chaque appel : 100 à 150 ms pour eslint_d, prettierd et dmypy, ~270 ms pour le client de `rubocop --server` (Ruby avec rubygems). Celui de speedrunner est un binaire Rust qui démarre en ~1 ms ; l'outil tourne dans un fork déjà chargé.
- **blackd gagne**, parce que son client est `curl`, un binaire C, et que black n'a presque rien à faire par appel. Il faut en revanche le lancer soi-même et parler HTTP : c'est ce que font les extensions d'éditeur.
- **`rubocop --server` ne gagne rien ici.** Son serveur inspecte le fichier en ~90 ms (`--debug`), mais son client Ruby coûte autant que le démarrage qu'il évite. Sans locale (`LANG` absent), rubocop natif prenait ~1,9 s et le serveur ~0,3 s : le serveur garde sa raison d'être dans ce cas, et sur de gros projets.

## Limites

- Un petit fichier. Sur un gros projet, un démon dédié garde en mémoire ce que speedrunner refait à chaque appel : l'état incrémental de dmypy, la configuration et les règles de rubocop, le programme TypeScript de typescript-eslint. Il peut alors l'emporter ; ce panel ne le mesure pas.
- Docker sur un portable : les valeurs absolues sont bruitées, les rapports sont stables d'un passage à l'autre (trois passages, protocole affiné entre eux : configuration rubocop, puis fichier modifié à chaque appel).
