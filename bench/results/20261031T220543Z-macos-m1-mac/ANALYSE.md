# macOS — M1 : le démon Python et le client

Question : le démon Python et le client, portés sur macOS, servent-ils les outils à l'identique et avec quel gain, sur Intel et sur Apple Silicon ?

## Gain, chemin chaud

`bench/macos/warm.py` : vrai client, démon chaud (trois appels d'apprentissage d'abord), 20 runs alternés natif / servi, chemin rapide obligatoire (`SPEEDRUNNER_REQUIRE_FAST=1`), médianes de temps mur. Le lancement d'un processus depuis Python coûte à lui seul ~5 ms sur le portable : il est compté des deux côtés.

| Commande | Intel natif | Intel servi | Gain | M6 natif | M6 servi | Gain |
|---|---:|---:|---:|---:|---:|---:|
| `black --version` | 317 ms | 27 ms | x11,7 | 54 ms | 6,1 ms | x8,9 |
| `black --check sample.py` | 327 ms | 35 ms | x9,3 | 55 ms | 6,6 ms | x8,3 |
| `pip --version` | 524 ms | 32 ms | x16,3 | 67 ms | 6,8 ms | x9,8 |
| `aws --version` | 720 ms | 149 ms | x4,8 | 121 ms | 28 ms | x4,4 |
| `aws configure list` | 743 ms | 170 ms | x4,4 | 126 ms | 32 ms | x3,9 |
| `http --version` | 437 ms | 48 ms | x9,1 | 81 ms | 10,5 ms | x7,7 |
| `poetry --version` | 805 ms | 54 ms | x14,9 | 132 ms | 10,6 ms | x12,5 |
| `ansible --version` | 746 ms | 48 ms | x15,4 | 90 ms | 7,2 ms | x12,5 |

**16 sorties sur 16 identiques** (code, stdout, stderr). Détail : `warm-intel.jsonl`, `warm-arm.jsonl`, machines dans `machine*.txt`.

## Où passe le reste (Intel, black)

`bench/phase_client.py`, démon chaud : 8,9 ms de la connexion à la réponse R, 10,4 ms d'exécution. Dans l'enfant, `why_not_fast` prend 6,2 ms, dont l'empreinte : sans inotify, l'enfant fait un `stat` de chaque fichier de module (286 pour black), soit 1,6 ms de `stat` et 1,8 ms de pages recopiées (les compteurs de références des chemins, au premier passage après le fork). Sous Linux, inotify ramène cette vérification à un `select()`.

Piste, non faite : kqueue `EVFILT_VNODE` dans le parent seulement (un fd par fichier, `RLIMIT_NOFILE` à relever puis à rendre aux enfants), et l'enfant qui demande au parent, sur le socket de contrôle, s'il a vu un événement, avec un repli sur `stat` si le parent ne répond pas à temps (apprentissage en cours). Gain attendu : 3 à 6 ms par appel sur l'Intel, ~1 ms sur le M6.

## La règle CoreFoundation

`bench/macos/cfcanary.py` : pour chaque usage dans un parent neuf, ses threads natifs, puis 5 enfants par appel candidat, tués ou non (`cfcanary-intel.txt`, `cfcanary-arm.txt`).

| Dans le parent | Threads | Enfant tué par `_get_proxy_settings` (Intel / M6) |
|---|---:|---|
| rien, `import urllib.request` (charge `_scproxy`) | 1 | 0/5 / 0/5 |
| `getproxies()`, `_get_proxy_settings()` | **3** | 0/5 / **5/5** |
| `platform.mac_ver()`, `getaddrinfo`, `ssl`, `CFStringCreate` | 1 | 0/5 / 0/5 |
| `CFRunLoopGetCurrent()` (ctypes) | **1** | **5/5 / 5/5** |

Trois conclusions :

- **Charger `_scproxy` est sans danger ; l'utiliser ne l'est pas.** `import urllib.request` le charge (pip, aws, httpie, requests…). Refuser de forker dès qu'il est chargé, comme le proposait M0, aurait désactivé presque tous les outils : le démon refuse quand CoreFoundation a *servi*.
- **La règle des threads ne suffit pas** : une boucle d'exécution CoreFoundation (`CFRunLoopGetCurrent`, que peut appeler une extension C ou un module ctypes) ne laisse aucun thread, et tue pourtant chaque enfant, sur Intel comme sur Apple Silicon.
- **Un seul appel le détecte** parmi ceux essayés : `_scproxy._get_proxy_settings()`, fait dans un enfant jetable. D'où la sonde « canari » du démon (`cf_unsafe`) : un fork et un appel à configd, ~2 ms sur le M6, 10 à 17 ms sur l'Intel, faits seulement quand l'état préchargé change (chaque import de niveau 1, chaque lot d'imports appris, et dans la sonde). Avec des threads dans le processus, le canari n'est plus fiable (il passe sur l'Intel après `getproxies`) : les threads sont donc vérifiés d'abord, et suffisent alors à refuser.

Tests : classe `CoreFoundation` de `tests/test_matrix.py` (un outil qui l'utilise à l'exécution, un module d'entrée qui l'utilise au chargement, des modules appris qui l'utilisent avec ou sans thread). Sans le canari, deux des trois échouent : enfant tué par SIGSEGV sur l'Intel.

## Fidélité

| Suite | Portable (Intel, 3.12) | Mac mini (M6, 3.9) |
|---|---|---|
| `test_matrix.py` | 77 tests, 9 sautés, vert | 77 tests, 9 sautés, vert |
| `test_shell.py` | 15, vert (bash 5 de Homebrew) | 15, 8 sautés (bash 3.2 seulement), vert |
| `test_scripts.py` | 19, vert | 19, vert |

Sautés, faute de sens sur macOS : les 6 tests de lanceurs (ptrace, Linux seulement ; le test « sans ptrace, en natif et une seule fois » tourne), les 3 tests de sécurité qui demandent root (comme sous Linux en utilisateur), et le pair d'un autre uid (pas de `setfsuid`). Sous Linux, dans Docker : debian, alpine, no-ptrace, py3.9, py3.14, node24 et stress, tous verts.
