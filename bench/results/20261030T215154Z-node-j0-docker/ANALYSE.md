# Go/no-go Node (J0) — fork d'un Node préchargé

Machine : MacBook (Intel i7-9750H), Docker Desktop, conteneur `linux/amd64` Debian 12, Node 24.22.2 (`machine.txt`). Image et prototype : [bench/node/](../../node/). Pour refaire la mesure :

```bash
docker build -t speedrunner-node-j0 bench/node
docker run --rm --network none -e J0_FLAGS="--single-threaded --v8-pool-size=1 --no-lazy" \
  -v $PWD/bench/node:/j0:ro -v $PWD/out:/out speedrunner-node-j0 python3 /j0/ceiling.py /out
```

Le prototype n'a pas de client : un démon (`daemon.mjs`) précharge les modules qu'un premier run a chargés (`learn.cjs`), puis forke un enfant par requête, lue sur un pipe. Ce sont donc des plafonds, à 1 ms près (le coût du client Rust). 20 runs alternés par commande, sortie comparée au natif.

## Verdict : GO, sous conditions

Le fork d'un Node préchargé fonctionne et il est sûr. On a enchaîné 1 000 appels d'eslint et 1 000 appels d'un script qui sollicite le GC, chacun forké depuis le même démon : 1 000 sorties identiques sur 1 000 à chaque fois, sans aucun blocage. La part du démarrage et du chargement dans le temps natif dépasse largement 50 % : 74 % pour npm, 87 à 91 % pour eslint, 94 % pour nest.

| Commande | Natif | Forké | Gain plafond | Sortie |
|---|---:|---:|---:|---|
| `nest --version` | 968 ms | 53 ms | **x18,1** | identique |
| `eslint --version` | 246 ms | 22 ms | **x11,2** | identique |
| `next --version` | 181 ms | 20 ms | **x9,0** | identique |
| `eslint sample.js` | 330 ms | 48 ms | **x7,0** | identique |
| `firebase --version` | 920 ms | 234 ms | x3,9 | identique |
| `npm --version` | 142 ms | 37 ms | x3,8 | identique |
| `prettier --check sample.js` | 213 ms | 78 ms | x2,8 | identique |
| `vercel --version` | 204 ms | 156 ms | x1,3 | identique |
| script vide (plancher) | 47 ms | 6,6 ms | x7,1 | identique |
| script qui sollicite le GC | 366 ms | 564 ms | **x0,65** | identique |

## Trois obstacles levés

1. **Node n'a pas de `fork()`.** Un addon N-API en C ([fork.c](../../node/fork.c)) fournit `fork`, `waitpid`, `dup2` et `_exit`. Dans l'enfant, il appelle `uv_loop_fork` pour donner à la boucle libuv son propre epoll.
2. **V8 marque son tas `MADV_DONTFORK`** (indicateur `dc` dans smaps). L'enfant perdait ces pages et plantait au premier accès au tas (SIGSEGV dans `napi_get_uv_event_loop`). L'addon remet `MADV_DOFORK` sur ces zones juste avant chaque fork. Cette marque est un détail interne de V8, à surveiller à chaque version de Node.
3. **Node garde toujours au moins 4 threads** : planificateur de tâches différées, worker V8, thread de l'inspecteur. Ils disparaissent dans l'enfant.
   - Avec `--single-threaded`, V8 ne leur confie rien, et le GC et le JIT tournent dans l'enfant. Sans cette option, le premier GC de l'enfant bloque.
   - À la sortie, Node attend ses threads de travail, que l'enfant n'a pas : l'enfant sort donc par `_exit`, une fois tous les écouteurs `exit` passés.

## Les coûts

- **`--single-threaded`** rend le GC et la compilation séquentiels. Ce qui calcule beaucoup ralentit : le script qui sollicite le GC prend 55 % de temps en plus. Il faudrait que le démon apprenne, outil par outil, quand le natif est plus rapide, et parte alors en natif.
- **`--no-lazy`** compile toutes les fonctions dans le parent. Sans cette option, l'enfant recompile à chaque appel ce qu'il exécute : `eslint sample.js` prenait 89 ms au lieu de 41. En contrepartie, le démon démarre environ 40 % plus lentement et prend plus de mémoire.
- **La distribution** : l'addon est du code natif. Il faudrait le compiler en CI pour chaque cible (x86_64 et aarch64, glibc et musl) et l'embarquer dans le client. L'ABI N-API est stable d'une version de Node à l'autre.

## Ce qui ne passe pas encore

- **Des threads lancés au préchargement.** vite (18 threads `rolldown-worker`, du Rust), ng, webpack et tsc démarrent des threads pendant le préchargement, et le démon refuse alors de forker. Pour ng et tsc, ce sont les 4 threads du pool de libuv (`libuv-worker`). Or libuv réinitialise ce pool dans l'enfant, par `pthread_atfork`. On pourrait donc les accepter, après un test dédié. Pour les autres threads, il faut sauter le module qui les lance, comme le fait le niveau 1 en Python.
- **Une sortie différente avec jest.** `jest --version` cherche un fichier de configuration au lieu d'afficher sa version : l'analyse des arguments a lieu au préchargement. Remplacer `process.argv` en place ne suffit pas. C'est la même famille de problème que `sys.argv` figé en Python.
- **Hors périmètre.**
  - pnpm 12 est un binaire natif (SEA) : rien à gagner.
  - yarn 1 est un lanceur shell : il faudrait étendre le suivi des lanceurs.
  - tsc est désormais TypeScript 7, en Go : son script Node ne fait que lancer le binaire.
