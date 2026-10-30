# Panel Node (étape N1) — le démon Node, client compris

Machine : MacBook (Intel i7-9750H), Docker Desktop, conteneur `linux/amd64` Debian 12, Node 24.22.2 (`machine.txt`). Image : [bench/node/Dockerfile](../../node/Dockerfile), où les CLI sont installées globalement. Commandes : [bench/node_panel.py](../../node_panel.py), avec le protocole de `panel.py` : 20 runs alternés, démon chaud, chemin rapide exigé, sortie comparée au natif. Le client est le binaire statique (musl) de `docker/test-all.sh`, avec les deux addons embarqués.

## Résultat

13 commandes. 12 sont servies, **12 sorties identiques au natif**. Gain : moyenne géométrique x3,7, médiane x3,4.

| Commande | Natif | speedrunner | Gain (IC 95 %) |
|---|---:|---:|---:|
| `eslint --version` | 244 ms | 16 ms | **x15,4** (15,3–15,8) |
| `next --version` | 179 ms | 21 ms | **x8,7** |
| `firebase --version` | 916 ms | 137 ms | **x6,7** (779 ms gagnées) |
| `eslint sample.js` | 325 ms | 50 ms | **x6,5** |
| `npm --version` | 143 ms | 38 ms | x3,8 |
| `jest --version` | 129 ms | 36 ms | x3,6 |
| `webpack --version` | 120 ms | 37 ms | x3,2 |
| `prettier --check sample.js` | 209 ms | 70 ms | x3,0 |
| `ng version` | 549 ms | 189 ms | x2,9 |
| `tsc --version` | 67 ms | 34 ms | x2,0 |
| `vercel --version` | 197 ms | 150 ms | x1,3 |
| `nest --version` | 959 ms | 811 ms | x1,2 |

Lecture :

- **Le plafond n'est pas atteint partout.** Le prototype du go/no-go préchargeait tout ce qu'un run avait chargé. Le démon, lui, refuse ce que la sonde juge risqué.
  - nest perd l'essentiel : la plupart de ses modules lancent des threads ou lisent les arguments au chargement.
  - vercel et prettier passent l'essentiel de leur temps restant dans leur propre travail, qui est plus lent avec `--single-threaded`.
- **tsc** est TypeScript 7, écrit en Go : le script Node ne fait que lancer le binaire. Le gain (x2) vient du seul démarrage de Node.
- **vite n'a pas été servi dans ce run.** Son bundler (rolldown, en Rust) lance des threads au chargement, et le démon refuse ces modules. Un module accepté par la sonde (`node:http2`) a ensuite lancé un thread dans le démon lui-même : le démon est sorti, comme prévu, et le suivant l'a mis en liste noire. La mesure est tombée pendant ce redémarrage. Même servi, vite ne gagnerait rien : ses modules refusés se rechargent à chaque appel, sans compilation en parallèle (x0,5 mesuré pendant la mise au point).

## Ce que la mise au point a révélé

Tout cela est documenté dans docs/ARCHITECTURE.md §2 bis (Node) et §11.

- **`MADV_DONTFORK` sur le tas de V8.** L'enfant plantait au premier accès à un objet JavaScript.
- **Le masque de signaux, propre à chaque thread.** Un SIGCHLD lu par `signalfd` partait aux threads de Node, et un client attendait indéfiniment. Le démon utilise désormais un self-pipe.
- **Ce que Node garde en cache dans un processus forké.**
  - `process.pid` : Ctrl-C partait au démon.
  - `process.cwd()` : prettier cherchait ses fichiers dans le dossier du démon.
  - L'état de `Math.random` : chaque enfant tirait les mêmes nombres.
- **La sortie de Node dans un enfant.** Elle bloquait sur une variable de condition, ou avortait après une exception non rattrapée. L'enfant sort donc par `_exit`, et l'addon relaie le rapport d'erreur de Node tel qu'un run natif l'afficherait.
- **Le paquet `debug`** parcourt les clés de l'environnement au chargement. Ce parcours est noté : un client dont les clés diffèrent part en natif.
