# macOS — M1 : le démon Python et le client

Question : le démon Python et le client, portés sur macOS, servent-ils les outils à l'identique et avec quel gain, sur Intel et sur Apple Silicon ?

## Gain, chemin chaud

`bench/macos/warm.py` : vrai client, démon chaud (trois appels d'apprentissage d'abord), 20 runs alternés natif / servi, chemin rapide obligatoire (`SPEEDRUNNER_REQUIRE_FAST=1`), médianes de temps mur. Le lancement d'un processus depuis Python coûte à lui seul ~5 ms sur le portable : il est compté des deux côtés.

| Commande | Intel natif | Intel servi | Gain | M6 natif | M6 servi | Gain |
|---|---:|---:|---:|---:|---:|---:|
| `black --version` | 311 ms | 22,7 ms | x13,7 | 54 ms | 4,4 ms | x12,3 |
| `black --check sample.py` | 335 ms | 30,5 ms | x11,0 | 55 ms | 5,1 ms | x10,7 |
| `pip --version` | 508 ms | 24,7 ms | x20,6 | 67 ms | 5,1 ms | x13,1 |
| `aws --version` | 698 ms | 137 ms | x5,1 | 121 ms | 25 ms | x4,8 |
| `aws configure list` | 723 ms | 163 ms | x4,4 | 126 ms | 30 ms | x4,2 |
| `http --version` | 427 ms | 41,8 ms | x10,2 | 81 ms | 8,8 ms | x9,2 |
| `poetry --version` | 770 ms | 43,1 ms | x17,9 | 132 ms | 8,0 ms | x16,5 |
| `ansible --version` | 725 ms | 40,4 ms | x17,9 | 90 ms | 5,4 ms | x16,6 |

**16 sorties sur 16 identiques** (code, stdout, stderr). Détail : `warm-intel.jsonl`, `warm-arm.jsonl`, machines dans `machine*.txt`. Avant kqueue (empreinte par `stat` dans l'enfant) : `warm-*-stat.jsonl`, par exemple black 27 ms (Intel) et 6,1 ms (M6), poetry 54 et 10,6 ms.

## L'empreinte : de `stat` à kqueue

`bench/phase_client.py`, black chaud sur l'Intel. Avec un `stat` de chaque fichier de module dans l'enfant (pas d'inotify), la phase connexion → prêt prenait 8,9 ms, dont 6,2 ms dans `why_not_fast` : 1,6 ms de `stat` (286 fichiers) et 1,8 ms de pages recopiées (les compteurs de références des chemins, au premier passage après le fork).

Avec kqueue dans le parent (une file n'est pas héritée au fork), qui répond `F` ou `S` à chaque enfant sur le socket de contrôle : **3,4 ms**. Sur le bench, black passe de 27 à 22,7 ms (Intel) et de 6,1 à 4,4 ms (M6), poetry de 54 à 43 ms et de 10,6 à 8,0 ms. Deux pièges :

- `NOTE_ATTRIB` arrive sur une extension `.so` chaque fois qu'un autre processus la charge, sans rien changer (ctime intact) : le démon se croyait périmé dès le premier appel. Il n'est pas demandé ; écriture, extension, suppression et renommage suffisent.
- La limite de fichiers ouverts d'un Terminal est 256, moins que les fichiers de modules d'aws : le parent la relève, chaque spare la rend avant d'accepter. Matrice passée sous `ulimit -n 256` sur les deux Mac, test `test_open_file_limit`.

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
| `test_matrix.py` | 78 tests, 9 sautés, vert (`ulimit -n 256`) | 78 tests, 9 sautés, vert (`ulimit -n 256`) |
| `test_shell.py` | 15, vert (bash 5 de Homebrew) | 15, 8 sautés (bash 3.2 seulement), vert |
| `test_scripts.py` | 26, 4 sautés (hook pre-commit : lanceur), vert | 26, 4 sautés, vert |

Sautés, faute de sens sur macOS : les 6 tests de lanceurs (ptrace, Linux seulement ; le test « sans ptrace, en natif et une seule fois » tourne), les 3 tests de sécurité qui demandent root (comme sous Linux en utilisateur), et le pair d'un autre uid (pas de `setfsuid`). Sous Linux, dans Docker : debian, alpine, no-ptrace, py3.9, py3.14, node24 et stress, tous verts.

Stress (`tests/test_stress.py`, avec les commandes CoreFoundation sur macOS) : ~10 000 appels en 90 s sur l'Intel, ~29 000 en 60 s sur le M6, sous `kill -9`, arrêts et péremptions, sans échec.

Le canari ne laisse pas de rapport de crash : l'enfant qui le fait transforme les signaux de plantage en `_exit`. Sans cela, chaque détection ouvrait une fenêtre « Python a quitté de manière imprévue » sur l'écran (vu sur le portable pendant ce travail).
