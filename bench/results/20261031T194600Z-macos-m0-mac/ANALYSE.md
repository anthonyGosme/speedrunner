# macOS — go/no-go (M0)

Question : le principe de speedrunner (un parent Python préchargé qui forke un enfant par appel) tient-il sur macOS, où le fork sans exec est réputé fragile, et que rapporterait-il ? Mesuré sur le portable (macOS 26.7, Intel i7-9750H, Python 3.12) avec `bench/macos/ceiling.py`, un prototype sans client ni socket : c'est un plafond, comme l'étape 0 sous Linux.

## Résultat : GO, pour Python d'abord

| Commande | Natif | Fork préchargé | Plafond | Sortie |
|---|---:|---:|---:|---|
| `black --version` | 348 ms | 10 ms | x34 | identique |
| `black --check sample.py` | 351 ms | 22 ms | x16 | identique |
| `pip --version` | 572 ms | 13 ms | x43 | identique |
| `aws --version` | 797 ms | 137 ms | x5,8 | identique |
| `aws configure list` | 821 ms | 174 ms | x4,7 | identique |
| `http --version` | 483 ms | 36 ms | x14 | identique |
| `ansible --version` | 820 ms | 16 ms | x51 | voir plus bas |
| `poetry --version` | 876 ms | 35 ms | x25 | identique |

20 tours alternés ; mêmes chiffres, à quelques millisecondes près, quand le parent a d'abord touché CoreFoundation (`--touch-cf`). Détail dans `ceiling.jsonl`.

- **Le gain est plus grand que sous Linux.** Python seul démarre en 50 ms sur ce Mac ; le reste du temps natif vient du chargement des modules, où chaque ouverture de fichier et chaque bibliothèque `.so` (black et mypy sont compilés par mypyc) coûtent cher sur macOS. C'est exactement ce que le préchargement supprime.
- **ansible** : seule la ligne `python version` diffère, parce que le prototype tournait sous `venv/bin/python` et non sous l'interpréteur du shebang (`python3.12`). Le démon, lui, est lancé par l'interpréteur du shebang.

## Sûreté du fork

- **2 000 forks** d'un parent qui a fait de gros imports (httpie, awscli) et utilisé CoreFoundation (détection de proxy d'`urllib`), SSL et `platform`, chaque enfant refaisant ces appels : **aucun échec** (`forkstress.json`, `bench/macos/forkstress.py`).
- **Mais la détection de proxy lance deux threads natifs** (`bench/macos/threads.py`) : SystemConfiguration démarre ses propres threads. Les imports, SSL et `platform` n'en lancent aucun. Le démon doit donc, comme sous Linux, compter ses threads natifs avant chaque fork et refuser de forker s'il y en a : un outil qui touche CoreFoundation **à l'import** partira en natif (sûr), un outil qui le fait **à l'exécution** (une requête HTTP, le cas habituel) le fera dans l'enfant, après le fork, sans risque. Sur macOS, le compte passe par `proc_pidinfo(PROC_PIDTASKINFO)` au lieu de `/proc/self/task`.

## Ce qu'il faut porter

Tout le reste du produit suppose Linux. Par pièce :

| Pièce | Linux | macOS |
|---|---|---|
| Uid du pair | `SO_PEERCRED` | `getpeereid` / `LOCAL_PEERCRED` |
| Threads natifs | `/proc/self/task` | `proc_pidinfo` (libproc) |
| Fichiers surveillés | inotify | kqueue `EVFILT_VNODE` (un fd par fichier : relever `RLIMIT_NOFILE`), ou `stat` |
| Env reçu (PEP 538) | `/proc/self/environ` | `sysctl KERN_PROCARGS2` |
| Script appelant (`activate`) | `/proc/<ppid>/cmdline`, fd 255 | `sysctl KERN_PROCARGS2` |
| Suivi des lanceurs | ptrace | pas d'équivalent simple : repli natif |
| Appels système | `accept4`, `pipe2`, `O_PATH` | `accept` + `fcntl`, `pipe` + `fcntl`, `O_RDONLY` |
| Binaire | statique (musl) | dynamique (libSystem), universel x86_64 + arm64 |
| Démon Node | `MADV_DOFORK`, `pipe2`, inotify | à réétudier (V8 et libuv sur macOS) |

## Proposition

1. **M1 — Python et le client** : le démon Python et le client Rust sur macOS (Intel et Apple Silicon), avec la matrice Python, sur les runners macOS de GitHub. Les lanceurs partent en natif. Estimation : quelques jours.
2. **M2 — Ruby et PHP** : mêmes remplacements, dans leurs démons.
3. **M3 — Node** : à part ; l'addon et le fork de V8 sont à réétudier sur macOS.
4. **Distribution** : un binaire universel dans les releases, et la formule Homebrew étendue à macOS (`on_macos`).

Ce n'est pas pour le lancement de mardi : le README le dit (« macOS, Windows? Not yet »).
