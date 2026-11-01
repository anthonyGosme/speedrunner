# macOS — M3 : le démon Node

Question : un processus Node, forké sous macOS, survit-il assez bien pour que le démon Node serve à l'identique, et avec quel gain ?

## Go/no-go : le fork de Node

`bench/macos/nodefork.js` (Mac mini, Node 24.22.2) : un Node préchargé forke des enfants qui exécutent du JSON, un hachage, zlib, le GC, puis un timer et des E/S asynchrones par le pool de threads de libuv, après le fork.

| Lancement | Enfants sortis en 0 |
|---|---|
| sans option | 100 / 100 |
| options du démon (`--single-threaded --v8-pool-size=1 --no-lazy`) | 200 / 200 |
| idem, plus `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` | 200 / 200 |

**GO.** Sous Linux, V8 marque son tas `MADV_DONTFORK` et l'addon doit le défaire ; macOS n'a pas cette option, le tas est hérité tel quel. `uv_loop_fork` refait la file kqueue de libuv dans l'enfant. Avec les options du démon, Node garde 4 threads (`MainThread`, `DelayedTaskSchedulerWorker`, `V8Worker`, `SignalInspector`, plus les `libuv-worker` du pool), aux mêmes noms que sous Linux : le décompte des threads du démon s'applique tel quel, par la liste des threads de Mach.

## Gain, chemin chaud

`bench/macos/warm.py` (vrai client, démon chaud, apprentissage terminé, 20 runs alternés). eslint 9.39.5, prettier 3.9.10, TypeScript 7.0.2.

| Commande | i7 natif | i7 servi | Gain | M6 natif | M6 servi | Gain |
|---|---:|---:|---:|---:|---:|---:|
| `npm --version` | 203 ms | 72 ms | x2,8 | 41 ms | 16,6 ms | x2,5 |
| `eslint --version` | 286 ms | 50 ms | x5,7 | 53 ms | 6,0 ms | x8,8 |
| `eslint sample.js` | 381 ms | 116 ms | x3,3 | 64 ms | 11,6 ms | x5,6 |
| `prettier --version` | 158 ms | 32,5 ms | x4,9 | 31 ms | 6,4 ms | x4,9 |
| `prettier sample.js` | 221 ms | 71 ms | x3,1 | 40 ms | 12,3 ms | x3,3 |
| `tsc --version` | 137 ms | 59 ms | x2,3 | 24 ms | 10,0 ms | x2,4 |

Node 23.7 de Homebrew sur l'i7, Node 24.22.2 officiel sur le M6. 12 sorties sur 12 identiques. Détail : `warm-node-*.jsonl`.

## Fidélité

| Suite | i7 (Node 23.7) | M6 (Node 24 et 22) | `macos-26` (Node 24) | `macos-15-intel` (Node 22.23.2) |
|---|---|---|---|---|
| `test_node.py` (33 tests) | verte | verte | verte | 3 cas d'erreur non rattrapée diffèrent (plus bas) |
| `test_stress.py`, Node compris | — | ~16 000 appels en 45 s, vert | vert | — |

Linux (Docker) : node22, node24, node24-alpine et stress-node, verts.

## Ce que M3 a trouvé

- **Les numéros de signaux.** Le démon Node codait en dur ceux de Linux (SIGCHLD 17, SIGUSR1 10) ; sous macOS, 17 est SIGSTOP et 10 SIGBUS : son self-pipe ne s'installait pas, et le démon ne voyait la fin d'aucun enfant. Tant que l'enfant envoie lui-même son X, rien ne se voit ; mais TypeScript 7 fait `process.execve()` vers son compilateur natif, et son client attendait sans fin un W. Signaux par nom désormais, et un self-pipe impossible à poser désactive le démon. Test : `nodetest execve`.
- **Une erreur à propriétés** termine sa pile par « {` » (util.inspect liste ensuite ses propriétés) ; le relais de l'addon, qui remplace les frames du démon par celle d'un lancement natif, perdait cette accolade. Vrai sous Linux aussi, jamais testé : cas `exit throw-props`.
- **CoreFoundation.** `process.title = …` passe par CoreFoundation (libuv) : après lui, chaque enfant qui change son titre est tué (SIGSEGV, vu sur le Mac mini). Cela lance aussi 2 threads, que la sonde voit : le module est refusé. Le canari reste au démarrage et après chaque lot appris (un fork ; par module, il faisait durer 6,5 s l'apprentissage d'eslint sur l'i7). Test : classe `CoreFoundation` de `test_node.py`.
- **Objective-C** : Node a des threads, comme Ruby ; le client lance son démon avec `OBJC_DISABLE_INITIALIZE_FORK_SAFETY`.
- **`pipe2`** existe dans le SDK de macOS 27, pas dans celui de macOS 26 : l'addon a sa version, sous un autre nom.

## Un Node sans cache de code

Sur le runner `macos-15-intel`, le Node de l'image est le `node@22` de Homebrew, construit sans le cache de code ni le snapshot de ses modules internes (`node_use_node_code_cache: false`). Il les compile alors depuis les sources, et `--no-lazy` (option du démon) change le nom qu'infère V8 : `node` seul affiche `at Module._extensions..js`, `node --no-lazy` affiche `at Object..js`, dans la pile d'une erreur non rattrapée. Trois cas de `test_exit_variants` différaient. Relancé sans `--no-lazy` (par `process.execve`), le démon nommait juste, mais les appels au terminal restaient bloqués sur ce runner, ce qui ne se reproduit pas ici (le Node de Homebrew du portable a son cache). Un tel Node n'est donc pas servi (exécution native, même sortie) ; le Node 24 officiel, le Node 22.23.4 officiel et le Node 23.7 de Homebrew le sont. La CI teste le Node 24 de nodejs.org sur les deux runners.
