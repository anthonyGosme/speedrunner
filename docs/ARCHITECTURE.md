# speedrunner — architecture et reprise du projet

Ce document décrit ce qui est construit, comment les pièces s'emboîtent, où en est le projet et ce qu'il faut savoir pour continuer sans retomber dans les pièges déjà rencontrés. État au 2026-10-31, version 0.3.1 : Python, Ruby, PHP et Node (expérimental), Linux x86_64 et aarch64 ; Python aussi sur macOS, Intel et Apple Silicon (M1, §2 quinquies).

## 1. En une phrase

Un démon garde une CLI Python déjà initialisée en mémoire et sert chaque appel par `fork()`. Un client Rust minuscule lui passe l'appel, et le shell réveille le démon pendant que l'utilisateur tape. Résultat mesuré : `aws --version` passe de ~230 ms à ~37 ms (x6), `pip --version` de 121 à 14 ms (x8,7), avec une sortie identique au natif.

## 2. Où est quoi

```
docs/
  ARCHITECTURE.md         ce document
  prior-art.md            étude de JumpTheGun, quicken, python-preloaded
  multi-runtime.md        Ruby / Node / JVM / CRIU : proposition et mesures
server/speedrunner_daemon.py  le démon Python, bibliothèque standard uniquement
server/speedrunner_daemon.rb  le démon Ruby, même protocole, bibliothèque standard uniquement
server/speedrunner_daemon.php le démon PHP, même protocole (pcntl, posix, FFI)
server/speedrunner_daemon.mjs le démon Node, même protocole
server/speedrunner_node.c   l'addon du démon Node : fork, passage de fd, signaux, pty
client/src/main.rs        le client, Rust, seule dépendance : libc (=0.2.175)
client/build.rs           embarque l'addon Node, compilé pour glibc et pour musl
client/src/launcher.rs    suivi des lanceurs (az, gcloud) sous ptrace
client/src/platform.rs    ce qui diffère entre Linux et macOS côté client
shell/speedrunner.zsh       wrappers et anticipation pour zsh ≥ 5.3
shell/speedrunner.bash      la même chose pour bash ≥ 4.2
tests/
  test_matrix.py          matrice de non-régression, natif contre speedrunner
  test_shell.py           intégration zsh et bash, pilotée dans un vrai pty
  test_ruby.py            matrice Ruby (rbtest), sur le harnais de test_matrix.py
  test_php.py             matrice PHP (phptest, composer, artisan)
  test_node.py            matrice Node (nodetest, nodetty)
  test_stress.py          courses : appels parallèles pendant que les démons s'arrêtent, meurent, se périment
  test_scripts.py         activate, exec, mode automatique
  fixture/hstest/         outil de test Python : une sous-commande par comportement
  fixture/rbtest/         outil de test Ruby : une gem avec un vrai binstub
  fixture/nodetest/       outils de test Node, sans installation
bench/
  baseline.sh, phases.py, summarize.py   étape 0
  preload_ceiling.py      prototype des niveaux de préchargement, sans client
  warm.sh                 natif contre démon chaud (hyperfine)
  phase_client.py         découpe un appel : poignée de main / exécution / destruction
  anticipation.py         démon arrêté, puis prewarm, puis D ms de frappe, puis exécution
  keystroke.zsh           coût du hook zsh par frappe
  script.sh               boucles scriptées avec `srr`
  launchers.sh            CLI à lanceur (az, gcloud) et `./manage.py`
  node/                   go/no-go Node : image, prototype de fork, plafonds
  node_panel.py           panel des CLI Node, dans l'image de node/
  macos/                  macOS : plafonds et cas CoreFoundation (M0), canari et bench chaud (M1)
  results/<date>-<étape>-vps/   chaque mesure, avec un ANALYSE.md
```

Le code vit dans le dépôt https://github.com/anthonyGosme/speedrunner.

## 2 bis. Plusieurs runtimes

Le client lit le shebang de l'outil et choisit le démon de son runtime : `server/speedrunner_daemon.py` pour un interpréteur `python*`, `server/speedrunner_daemon.rb` pour `ruby*`. Le démon est lancé par l'interpréteur même de l'outil. Le protocole, le modèle de processus (parent, spare, enfant), le verrou, la sécurité, l'empreinte par inotify et la règle n°1 sont identiques : le démon Ruby est un portage du démon Python, avec les niveaux 1 et 2. Différences propres à Ruby :

- **Niveau 2.** L'enfant apprend les fichiers chargés par `TracePoint(:script_compiled)`, dans l'ordre de *début* de chargement, ainsi que les gems activées. Après X, il les envoie au parent. Celui-ci les fait vérifier par une sonde (mêmes règles qu'en Python), active les gems, requiert les fichiers acceptés par leur chemin absolu, puis met le tout en cache. En plus, un enfant jetable rejoue d'abord tous les fichiers dans l'ordre natif, refusés compris, et note les espaces de noms que chaque fichier ouvre pendant son chargement (`TracePoint(:class)`). La sonde refuse un fichier qui en ouvre d'autres sans les fichiers refusés : il dépend de ce qu'ils définissent (voir §11, fastlane).
- **Niveau 1.** Il précharge les `require` de haut niveau du script. Pour un binstub RubyGems, il active aussi la gem (`Gem.activate_bin_path`), puis précharge les `require` de haut niveau de l'exécutable de la gem. Un argument `_x.y.z_` (choix de version par le binstub) part en natif.
- **Exécution.** `load script`, et les exceptions comme `exit` remontent jusqu'à Ruby, comme en natif. Le dernier `at_exit` (enregistré en premier) nettoie la trace, affiche lui-même une exception non rattrapée (Ruby le ferait après les `at_exit`), vide les fichiers ouverts pendant l'appel (suivis par un `prepend` sur `IO#initialize`), puis envoie X. Interrupt et SignalException n'envoient pas de X : Ruby meurt du signal, et W fait foi.
- **Contexte.** `IO#reopen` pour les fd 0/1/2, en restaurant les encodages. Ruby 3.1 n'a pas `fchdir` : on fait `chdir` par `/proc/self/fd/N`. `ENV.replace` en place. Dispositions de signaux remises au défaut Ruby, plus les ignorés hérités. Pont `/dev/tty` sur `IO.console` et `File.open("/dev/tty")`.
- **Variables d'environnement.** Au démarrage : `RUBY*`, `GEM_*`, `LANG`, `LC_ALL`, `LC_CTYPE`. Pendant le chargement, les lectures d'`ENV` sont enregistrées par un `prepend` sur `ENV`.
- **inotify** passe par Fiddle, l'équivalent de ctypes.

**PHP** (`server/speedrunner_daemon.php`), le même protocole, avec une autre stratégie de préchargement :
- **Rien de l'outil ne s'exécute dans le parent.** Pré-compiler (`opcache_compile_file`) ou pré-déclarer des classes y lierait tôt des classes que Composer redéclare ensuite par un `require` ordinaire, ce qui est fatal. Le gain vient du fork (le démarrage de PHP et de ses extensions) et d'**OPcache partagé**. Le client lance le démon avec `-d opcache.enable_cli=1 -d opcache.validate_timestamps=1 -d opcache.revalidate_freq=0` ; la mémoire partagée est héritée par les enfants, et ce qu'un enfant compile sert aux suivants, comme pour PHP-FPM. Un fichier modifié est recompilé, et l'empreinte couvre l'interpréteur et ses fichiers ini.
- **Descripteurs.** Les sockets, le passage des fd, `SO_PEERCRED`, `dup2` et `fchdir` passent par FFI vers la libc : PHP n'expose ni les numéros de fd ni `dup2`.
- **Exécution.** `Speedrunner\Daemon::main()` rend la main dans l'enfant, qui inclut l'outil au niveau global du fichier : les variables de haut niveau de l'outil sont donc des globales. `$argv`, `$argc`, `$_SERVER` (environnement, puis `PHP_SELF` … `argc`, dans l'ordre natif) et `$_ENV` (selon `variables_order`) sont reconstruits. Un gestionnaire d'exceptions reproduit le message natif (journal et affichage, selon `log_errors` et `display_errors`), sans la frame du démon, puis sort en 255.
- **Fin.** PHP ne donne aucun code de sortie avant son arrêt : pas de X, le client attend le statut d'attente W.
- **Forme `php script args`.** Le client la reconnaît quand l'outil est l'interpréteur PHP et que son premier argument est un fichier. Il sert alors le script, avec son propre démon. `$argv[0]` vaut le chemin tel qu'il a été tapé, et le démon le résout par rapport au cwd du client. `speedrunner status|stop php <script>` vise ce démon. En mode automatique, `activate` crée aussi un raccourci `php`.

**Node** (`server/speedrunner_daemon.mjs`), le même protocole, sur un `fork()` que Node n'a pas :
- **Addon.** `speedrunner_node.c`, un addon Node-API d'environ 500 lignes, fournit `fork` (avec `uv_loop_fork` dans l'enfant), le passage de fd, `dup2`, `poll`, les signaux, inotify et un pty. Il déclare lui-même les fonctions Node-API qu'il appelle : il se compile avec un compilateur C seul, et une compilation sert toutes les versions de Node. `client/build.rs` l'embarque en deux variantes, glibc (compilée sur Ubuntu 22.04) et musl (Alpine) ; le client choisit selon l'interpréteur ELF du binaire `node`. Sans la variante, l'outil part en natif.
- **Threads.** Node garde au moins quatre threads : un planificateur de tâches différées, un worker V8 et le thread de l'inspecteur, qui n'existent pas dans l'enfant. Le client lance le démon avec `--single-threaded --v8-pool-size=1` : V8 ne leur confie plus de travail, et le GC comme le JIT tournent dans le thread principal. `--no-lazy` compile dans le parent toutes les fonctions qu'il charge ; sans lui, chaque enfant recompile ce qu'il exécute (eslint : 89 ms au lieu de 41).
- **Préchargement appris seulement** (niveau 2). Un `module.registerHooks` (Node ≥ 22.15) note les modules que l'enfant charge, dans l'ordre où leur chargement commence, CommonJS comme ES. Après X, l'enfant les envoie au parent, qui les fait juger par une sonde. Elle refuse un module qui affiche quelque chose, modifie l'environnement, lance un thread, laisse du travail en attente (`process.getActiveResourcesInfo()`), lit les arguments, ou échoue. Les threads du pool de libuv sont tolérés : libuv le réinitialise après un fork (`pthread_atfork`). Le parent charge ensuite les modules acceptés, avec les mêmes contrôles.
- **Ce que les imports lisent.** Pendant les chargements, `process.env` et `process.argv` sont remplacés par des proxys. On note les clés d'environnement lues, et le fait d'avoir parcouru les clés (le paquet `debug` cherche les `DEBUG_*`). Pour `process.argv`, on note les drapeaux cherchés par `includes` ou `indexOf`, comme le fait supports-color ; toute autre lecture des arguments fait refuser le module. On note aussi les réponses de `tty.isatty` et la création de `process.stdout`, `stderr` et `stdin`. Un client qui obtiendrait une autre réponse part en natif. Les imports se font avec un pty sur les fd qui étaient des terminaux pour le client qui a lancé le démon, pendant toute la vie du démon.
- **Exécution.** `Module.runMain(script)`, depuis un `setImmediate` : CommonJS ou ES module, comme `node script`. `process.argv` et `process.env` sont modifiés en place, `process.execArgv` est vidé.
- **Fin.** Une fois tous les écouteurs `exit` passés, l'enfant envoie X, puis ce qu'il a appris, et sort par `_exit` : la destruction normale de Node attendrait des threads qui n'existent pas dans l'enfant. Après une exception non rattrapée, Node émet `exit` *avant* d'afficher l'erreur. L'addon relaie alors stderr par un pipe jusqu'à la ligne `Node.js vX`, puis envoie X et sort avec le code. Au passage, il remplace les deux frames du démon par celle d'un lancement natif (`node:internal/main/run_main_module`), apprise une fois par version de Node. Le client n'a donc pas besoin du W du démon, qui peut avoir disparu (`kill -9`).

Pour un nouveau runtime : un démon `server/speedrunner_daemon.<ext>` qui parle ce protocole, une entrée dans `Runtime` (client), un outil de test et une matrice `tests/test_<runtime>.py` qui réutilise le harnais de `test_matrix.py`.

## 2 ter. Lanceurs

Un lanceur prépare un environnement, puis exécute l'interpréteur sur le vrai outil. gcloud est un script shell qui finit par `exec "$CLOUDSDK_PYTHON" … lib/gcloud.py "$@"` ; az, installé par pip, un script Python qui finit par `os.execl(sys.executable, …, "-m", "azure.cli", …)`. Précharger le lanceur ne sert à rien : il faut servir ce qu'il exécute ([client/src/launcher.rs](../client/src/launcher.rs)).

- **Reconnaissance.** Un script shell (`sh`, `bash`, `dash`, `ksh`, `zsh`, directement ou par `env`) dont la dernière commande commence par `exec `, ou qui finit par un bloc `if … fi` dont chaque branche se termine par `exec …` ou `exit` (une au moins par `exec`, sans bloc imbriqué) ; ou un script Python dont la dernière ligne de haut niveau commence par `os.exec`. Tout ce qui précède ne fait que préparer cet exec.
- **Suivi.** Le client lance le lanceur avec ses arguments, sous ptrace (`PTRACE_TRACEME`, puis `PTRACE_O_TRACEEXEC | PTRACE_O_EXITKILL`). Il ne s'arrête qu'aux exec, et transmet au lanceur les signaux qu'il reçoit. Il passe le premier exec (le lanceur lui-même) et ceux d'un shell ou d'`env`. À l'exec d'un `python*`, le noyau arrête le processus avant la première instruction de l'interpréteur. Le client lit alors `/proc/<pid>/cmdline`, `environ`, `status` (umask, `SigIgn`) et vérifie que le cwd et les fd 0 à 2 sont les siens, puis tue le processus. Rien de l'outil n'a tourné, et le préambule a tourné une fois, comme en natif : 6,6 ms pour gcloud, un démarrage de Python (7,5 ms) pour az.
- **Interpréteur.** C'est `argv[0]` tel que le lanceur l'a donné, cherché dans *son* `PATH` s'il est nu, et qui doit se résoudre en `/proc/<pid>/exe`. Le fichier résolu ne convient pas : un python de venv se reconnaît au chemin par lequel on l'appelle.
- **Cible.** Des options sans effet propre à chaque appel (`-B -E -I -O -P -R -S -b -q -s -u`, `-X …`, `-W …`), puis un script ou `-m module`. Sinon (`-c`, `-`, une autre option), le client exécute la commande lue telle quelle. La cible a son propre démon, nommé d'après le lanceur et haché sur interpréteur, options et script ou module. Ce démon est lancé avec l'env construit par le lanceur. La requête porte l'env, l'umask et les signaux ignorés que le lanceur a laissés, pas ceux du client.
- **Repli.** Le client exécute (`execve`) exactement la commande lue : le préambule ne tourne pas une seconde fois.
- **Lanceur qui n'exécute pas Python** (gcloud avec `CLOUDSDK_USE_GOCLOUD`, ou tout autre exec). Le client le détache et l'attend en parent : il ignore SIGINT et SIGQUIT, que le terminal envoie au lanceur dans le même groupe, relaie SIGTERM et SIGHUP, puis sort comme lui.
- **`status`, `stop`, `prewarm`.** Chaque suivi réussi écrit le nom du socket de la cible dans `<socket du lanceur>.target`, dans le dossier runtime. `status` et `stop` visent ce démon. `prewarm` l'interroge. S'il est absent, `prewarm` relance le préambule sans argument, avec les fd 0 à 2 sur `/dev/null`, le tue à l'exec, puis démarre le démon, seulement si la cible est la même. `prewarm` ne fait rien pour un lanceur dont aucun appel n'a encore été suivi.
- **Mode automatique d'`activate`.** Il ne crée de lien que pour les scripts Python ou Ruby : az y est, gcloud non. Beaucoup de scripts shell du système finissent par un `exec` qui n'a rien à voir avec Python, et chacun tournerait alors sous le client. Pour gcloud, il faut le nommer (`activate gcloud`, `exec -t gcloud`), l'inscrire, ou passer par `srr`.
- **Hook git de pre-commit.** `pre-commit install` écrit `.git/hooks/pre-commit`, un script bash qui finit par `if [ -x "$INSTALL_PYTHON" ]; then exec "$INSTALL_PYTHON" -mpre_commit …; elif command -v pre-commit …; then exec pre-commit …; else … exit 1; fi`. git l'exécute par son chemin, et le hook lance Python par son chemin absolu : aucun des deux ne passe par les liens de `speedrunner exec`. Le seul passage par le `PATH` est le shebang `#!/usr/bin/env bash` : `env` cherche `bash` dans le `PATH`.
  - **Un lien `bash`**, à côté de celui de `pre-commit`, dans le dossier de liens d'`exec` et d'`activate` (mode automatique : pre-commit trouvé sur le `PATH` ; outils nommés : `-t pre-commit`). Sans pre-commit, bash n'est pas touché.
  - **Appelé sous le nom `bash`**, le client regarde seulement `argv[1]`. Si c'est un fichier qui porte la marque de pre-commit (`# File generated by pre-commit: https://pre-commit.com`, deuxième ligne) et qui finit comme un lanceur, il le suit comme un lanceur : le vrai bash l'exécute sous ptrace, avec la même ligne de commande, et le client sert le `python -mpre_commit hook-impl …` qu'il exécute par un démon nommé `pre-commit` (la forme collée `-mMODULE` est reconnue). Dans tous les autres cas (`bash -c`, un autre script, une option), il fait `exec` du vrai bash, avec les mêmes arguments : un démarrage du client de plus, soit 4,1 → 8,2 ms pour un script bash vide dans Docker Desktop, où `/usr/bin/true` seul prend 1,9 ms.
  - **Repli** comme pour tout lanceur : sans ptrace, ou si le démon refuse, le client exécute le vrai bash, ou la commande Python lue, une seule fois ; ni le préambule ni pre-commit ne tournent deux fois. Le démarrage du démon suit la règle du mode automatique (au deuxième appel de pre-commit, compté avec ses appels directs), ou dès le premier appel si pre-commit est nommé ou inscrit.
  - **Écarté** : `core.hooksPath` (pre-commit refuse alors de s'installer, et cela remplace les hooks de husky ou d'autres) ; une commande qui réécrit le hook (`pre-commit install` l'écrase, ou le renomme en `pre-commit.legacy` et l'exécute en plus du sien : risque de double exécution) ; un lien `python3` (le hook utilise un chemin absolu) ; un lien `git` qui passerait `core.hooksPath` par l'env (hérité par les git lancés par les hooks, et le hook ne serait plus dans son dossier). Le lien `bash` ne demande rien à l'utilisateur, ne modifie aucun fichier du dépôt et ne change rien hors de `speedrunner exec` et d'`activate`.
- **`-m module` dans le démon** (`--module`). Le fichier exécuté est trouvé comme le fait runpy : en important les paquets parents, et le paquet lui-même s'il a un `__main__`. Cet import vient en tête du niveau 1, sous les mêmes garde-fous. Natif, `sys.path[0]` est le cwd : le démon le retire, et chaque enfant met le cwd du client. Un fichier du cwd qui masquerait un module préchargé (`json.py`, ou un paquet avec `__init__`) fait partir l'appel en natif. L'enfant exécute par `runpy._run_module_as_main` dans un `__main__` neuf : une trace montre les mêmes frames qu'en natif.
- **Entrées de `sys.path` ajoutées par l'outil** (gcloud ajoute `lib/third_party`). L'enfant les envoie avec ses imports appris, et le cache les garde (`"paths"`). La sonde et le parent importent avec ces entrées en tête de `sys.path`, puis le restaurent. Un module trouvé dans un autre fichier que chez l'enfant est refusé.

## 2 quinquies. macOS (M1 : Python et le client ; M2 : Ruby et PHP)

Même produit, même protocole ; seuls les mécanismes du système changent. Côté client, ils sont regroupés dans [client/src/platform.rs](../client/src/platform.rs) (`cfg(target_os)`) ; côté démons, dans une section « platform » (`MACOS`) des démons Python et Ruby, et dans `libc()` du démon PHP. Linux garde exactement son comportement.

| Pièce | Linux | macOS |
|---|---|---|
| Uid (et gid) du pair | `SO_PEERCRED` | client : `getpeereid` ; démon : `LOCAL_PEERCRED` (`struct xucred`) |
| Threads natifs du démon | `/proc/self/task` | `proc_pidinfo(PROC_PIDTASKINFO)`, `pti_threadnum` ; un échec compte comme des threads |
| Fichiers de modules surveillés | inotify, un `select()` dans l'enfant | kqueue dans le parent seul ; l'enfant reçoit son verdict (plus bas) |
| Env reçu au lancement | `/proc/self/environ` | `sysctl KERN_PROCARGS2`, et les empreintes de l'env du client (plus bas) |
| cwd passé à l'enfant | `O_PATH` | `O_SEARCH` (pas besoin du droit de lecture non plus) ; chemin par `F_GETPATH` |
| Sortie des imports capturée | `memfd_create` | fichier créé puis aussitôt supprimé dans le dossier runtime |
| Fds reçus | `MSG_CMSG_CLOEXEC` | `fcntl(F_SETFD)` après réception |
| Terminal passé à l'enfant | `/dev/tty` | le périphérique lui-même (`/dev/ttys003`), trouvé par `proc_pidinfo(PROC_PIDTBSDINFO)` et `devname` |
| SIGTERM relayé | par pidfd | par pid |
| Script appelant (`activate`) | `/proc/<ppid>/cmdline`, `cwd`, `fd/255` | `KERN_PROCARGS2`, `PROC_PIDVNODEPATHINFO`, `proc_pidfdinfo` sur le fd 255 |
| Lanceurs (az, gcloud) | suivis sous ptrace | exécutés en natif |
| Ruby | `O_TMPFILE`, inotify, `/proc/self/fd` | fichier supprimé dans `$TMPDIR`, `stat`, `fchdir` par Fiddle |
| PHP (FFI vers la libc) | `msghdr`, `cmsghdr` 64 bits, `accept4`, `SOCK_CLOEXEC` | ceux de libSystem : `cmsghdr` à longueur 32 bits alignée sur 4, `accept` + `fcntl` (le socket accepté hériterait d'`O_NONBLOCK`), `fcntl` déclaré variadique |
| Node | servi | exécuté en natif (M3) |

**La règle CoreFoundation.** Une fois que CoreFoundation a servi dans un processus, un enfant forké qui s'en sert à son tour est tué (« The process has forked and you cannot use this CoreFoundation functionality safely »). Mesures dans `bench/results/…-macos-m1-mac/ANALYSE.md` :

- charger `_scproxy` (ce que fait `import urllib.request`) est sans danger ; c'est l'appeler (la détection de proxy d'urllib) qui est fatal, sur Apple Silicon. Le démon ne refuse donc pas un `_scproxy` chargé, ce qui aurait désactivé pip, aws, httpie… ;
- la détection de proxy laisse 3 threads natifs : la règle des threads la bloque déjà. Mais une boucle d'exécution CoreFoundation (`CFRunLoopGetCurrent`, par une extension C ou par ctypes) n'en laisse aucun, et tue chaque enfant, sur Intel aussi ;
- **le canari** (`cf_unsafe`) : un enfant jetable appelle `_scproxy._get_proxy_settings()`, le seul appel trouvé qui déclenche la vérification de CoreFoundation ; s'il meurt, ce processus ne doit plus forker. Coût : un fork et un aller-retour à configd, ~2 ms sur un M6, 10 à 17 ms sur un i7. Il ne tourne que quand l'état préchargé change : après chaque import de niveau 1 (qui est alors sauté, « uses CoreFoundation at import », comme un import qui lance un thread), après le module de `-m`, après chaque lot d'imports appris appliqué dans le parent (en cas d'échec, le lot est mis en liste noire et le démon redémarre), et dans la sonde ;
- dans la sonde, un canari par module coûterait trop : un seul, quand le lot est importé. S'il meurt, ou si un module refusé a laissé des threads (le canari n'est alors plus fiable : il passe sur l'Intel après `getproxies`), tout le lot est rejugé avec un canari après chaque module, jusqu'à la fin de cet apprentissage ;
- un outil qui utilise CoreFoundation pendant son exécution (le cas courant : une requête HTTP) le fait dans l'enfant, depuis un parent qui ne l'a jamais utilisé : sans danger.

**L'empreinte par kqueue.** Une file kqueue n'est pas héritée au fork, et `EVFILT_VNODE` demande un fd par fichier (`O_EVTONLY`). Le parent seul surveille donc les fichiers de modules. Il relève sa limite de fichiers ouverts (jusqu'à 10 240 ; celle d'un Terminal est 256), et chaque spare ferme ces fds et rend la limite avant d'accepter : l'outil voit la même qu'en natif (test `test_open_file_limit`). Au message `A` d'un enfant, le parent lit la file et répond `F` (rien) ou `S` (périmé) sur le socket de contrôle ; le noyau range l'événement pendant l'écriture même, comme pour inotify. Sans réponse en 20 ms (parent occupé à apprendre, ou disparu), l'enfant fait un `stat` de chaque fichier. Comme sous Linux, le parent s'arrête dès qu'un événement arrive. Pas de `NOTE_ATTRIB` : une extension `.so` en reçoit un chaque fois qu'un autre processus la charge, sans changement ; écriture, extension, suppression, renommage (pip qui remplace un fichier) restent vus. Gain : la phase connexion → prêt de black passe de 8,9 à 3,4 ms sur un i7.

**Le canari ne fait pas de rapport de crash.** Un enfant qui plante fait écrire un rapport par macOS et ouvrir la fenêtre « Python a quitté de manière imprévue » sur l'écran de l'utilisateur. L'enfant canari transforme donc SIGSEGV, SIGBUS, SIGILL, SIGTRAP et SIGABRT en `_exit(signal)` : statut non nul, pas de rapport (vérifié sur le Mac mini).

**Ruby et Objective-C.** Un processus Ruby a toujours un second thread, sa minuterie. Après un fork dans un processus qui a des threads, le runtime Objective-C refuse à l'enfant la première utilisation de toute classe (« +initialize may have been in progress in another thread when fork() was called », SIGABRT), même dans un parent qui n'a jamais touché CoreFoundation : un outil Ruby servi qui toucherait Foundation planterait, là où il marche en natif. Le thread de Ruby ne fait jamais d'Objective-C : le client lance le démon Ruby avec `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` (et `SPEEDRUNNER_OBJC_ADDED=1`), que le démon retire aussitôt de son env ; rien si l'utilisateur l'avait lui-même. Le canari Ruby appelle `SCDynamicStoreCopyProxies` par Fiddle, comme `_scproxy`, aux mêmes moments qu'en Python. Python et PHP n'ont qu'un thread : pas concernés.

**PHP.** Rien de l'outil ne s'exécute dans le parent : un seul canari, au démarrage (une extension qui aurait utilisé CoreFoundation). Toute la couche C passe par FFI, avec des structures et constantes propres à chaque système. Sur l'arm64 d'Apple, les arguments variadiques passent par la pile : `fcntl` déclaré avec un prototype fixe recevait n'importe quoi, et chaque spare mourait.

**L'env que voit l'outil.** Sous macOS, `python3.x` est souvent un lanceur qui fait lui-même un `exec` : celui de python.org ajoute `__CF_USER_TEXT_ENCODING` (absent d'une session ssh), `/usr/bin/python3` ajoute `SDKROOT`, `CPATH`, `LIBRARY_PATH` et `MANPATH`. `KERN_PROCARGS2` donne l'env de l'interpréteur après ce lanceur, sans dire ce qui venait du client. Le client passe donc au démon, dans `SPEEDRUNNER_LAUNCH_ENV`, une empreinte FNV-1a 64 de chaque entrée de son env, suivie de son nom. Le démon en déduit ce que le lanceur et CPython ont ajouté ou changé, et l'ajoute à l'env de chaque requête, comme en natif. Une clé de démarrage (`PYTHON*`, `LC_*`, `LANG`) changée par le lanceur fait partir l'appel en natif.

## 3. Vue d'ensemble des processus

```
 shell zsh ── fonction aws() ──► speedrunner run aws -- args      (ou : srr aws args)
                                      │ client Rust
                                      │ socket Unix $XDG_RUNTIME_DIR/speedrunner/aws-<hash>.sock
                                      ▼
 ┌──────────── démon (un par outil) ─────────────────────────────────────────┐
 │ parent : précharge, ne fait jamais accept() lui-même, ramasse ses enfants │
 │   ├── spare : déjà forké, attend sur select() + accept()                  │
 │   ├── enfant actif : un appel en cours (ex-spare), exécute l'outil        │
 │   └── sonde : enfant jetable qui vérifie des imports appris (niveau 2)    │
 │ socketpair de contrôle parent ↔ chaque enfant                             │
 └───────────────────────────────────────────────────────────────────────────┘
```

- **Le parent** reste mono-thread, et sans asyncio (pas fork-safe). Il boucle sur `select()` : self-pipe des signaux, sockets de contrôle des enfants, file inotify. Il ne lit jamais cette file : il s'arrête au premier événement.
- **Le spare** est forké d'avance. Quand il accepte une connexion, il passe au parent une copie du fd client (message `A` avec `SCM_RIGHTS`), puis le parent forke aussitôt le spare suivant. Le parent garde ainsi de quoi envoyer le statut d'attente `W`.
- **L'enfant** vérifie, reçoit la requête, attend le « go », applique le contexte du client, exécute le script par `runpy`, rend le code de sortie, puis se détruit sans bloquer personne.

## 4. Protocole

Petit-boutiste, sur le socket Unix. Toute trame : `kind (1 octet) + u32 longueur + charge`.

| Sens | Message | Rôle |
|---|---|---|
| client → enfant | `HS1` + `R`/`P`/`S` + u32 + corps, fds en `SCM_RIGHTS` | R = exécuter, P = ping/statut, S = arrêter le démon |
| enfant → client | `N(raison)` | exécute en natif (avant tout démarrage) |
| enfant → client | `R(pid i32)` | prêt, attend le go |
| enfant → client | `P(texte)` | réponse au ping (`ready …`, `stale: …`, `disabled: …`) |
| client → enfant | `G` | go : **à partir d'ici, le client ne bascule plus jamais en natif** |
| enfant → client | `X(code i32)` | code de sortie, envoyé depuis le dernier `atexit` ; le client sort dessus |
| parent → client | `W(kind u8, valeur i32)` | statut d'attente (0 = sorti, 1 = tué par signal), quand X n'est pas venu |

Corps d'un `R` : liste d'argv (u32 compte, puis blobs u32+octets ; argv[0] = chemin du script), liste d'env (`CLÉ=valeur`), u32 umask, u64 masque des signaux ignorés hérités, groupes (u32 compte + u32 chacun). Fds : 0, 1, 2, un `O_PATH` du cwd, et `/dev/tty` si le client a un terminal de contrôle.

Socket de contrôle parent ↔ enfant : `A` (+ fd client : « j'ai accepté »), `L(json)` (« voici ce que j'ai importé et chargé », envoyé après X), `Q` (parent → spare : « retire-toi »), et en PHP `K` (parent → enfant : « j'ai ta copie »). Un enfant PHP ne rend sa fin que par le W du démon : il attend `K` avant de servir. S'il voit la fin de fichier à la place (démon tué par `kill -9`, que le noyau libère pendant quelques millisecondes pendant lesquelles le spare prend encore des appels), il ferme la connexion avant la réponse `R`, et le client part en natif. Trouvé par le stress sur arm64.

**Règle n°1 : jamais de double exécution.** Avant `G`, toute anomalie (pas de socket, refus N, poignée de main ratée, empreinte périmée) mène à `exec` de la vraie CLI. Après `G`, on ne relance jamais : sans X ni W, le client affiche « lost track … not re-running it » et sort en 125. `SPEEDRUNNER_REQUIRE_FAST=1` remplace le repli natif par une sortie en 124, pour les tests et les benches.

## 5. Cycle de vie du démon

1. **Lancement par le client** (`run` qui ne trouve pas de démon, ou `prewarm`). Le client lit d'abord l'état du verrou `<socket>.lock` :
   - verrou libre : il lance ;
   - `running <pid>` : rien à faire ;
   - `exiting <pid>` : il lance un candidat qui n'attendra que la sortie de *ce* pid.

   Double fork. Le petit-fils lâche les fd du client (sinon un pipeline attendrait sa fin), prend le verrou (au plus 3 s d'attente), y écrit `running <pid>`, puis `exec` l'interpréteur du shebang avec `speedrunner_daemon.py --tool --script --socket --lock-fd 3 --origin`. Résultat : un seul démon, quel que soit le nombre de clients concurrents.
2. **Ouverture du socket avant le préchargement.** Un appel qui arrive pendant le démarrage attend le premier spare, au plus 3 s, au lieu de partir en natif. Le dossier runtime est vérifié par `fstat` sur un fd (propriétaire = nous, mode exactement 0700), et le démon travaille ensuite relativement à ce fd. Socket en 0600, umask d'origine rétabli juste après.
3. **Préchargement** (§6), vérification des threads Python et natifs (`/proc/self/task`, `proc_pidinfo` sur macOS, plus le canari CoreFoundation, §2 quinquies), calcul de l'empreinte et des watches inotify (§7).
4. **Service.** Boucle du parent : spare, `on_ctl` (A/L), `reap` (W), application de l'apprentissage quand aucun enfant n'est actif.
5. **Arrêt** : `S` (stop), empreinte périmée (SIGUSR1 envoyé par un enfant), fichier surveillé modifié, 4 h d'inactivité (`SPEEDRUNNER_IDLE_TIMEOUT`), ou import appris qui se comporte mal (le démon suivant le met en liste noire). Il se retire d'abord : il écrit `exiting <pid>` dans le verrou, supprime son socket (seulement si c'est encore le sien, d'après l'inode), ferme son socket d'écoute et renvoie son spare. Il relâche aussi son verrou, qui ne dit que qui sert le socket : le démon suivant peut démarrer aussitôt, même si un appel long est en cours. Puis il attend ses enfants en cours et leur envoie leur W : en PHP, et pour un enfant mort d'un signal, c'est la seule façon pour le client d'apprendre leur fin. (Garder le verrou pendant cette attente faisait abandonner le démarreur du suivant au bout de 3 s : l'outil restait sans démon jusqu'à l'appel d'après. Trouvé par `platforms`, Python 3.14 ; test `test_stopped_mid_run_lets_a_new_daemon_start`.)
6. **Crash** (`kill -9`) : les enfants actifs finissent et envoient leur X, le spare sort. Le prochain client trouve un socket sans écoute, le relance en arrière-plan et part en natif.

## 6. Préchargement

`SPEEDRUNNER_PRELOAD` (par défaut `1,2,3`) choisit les niveaux, chacun testé seul et combiné.

| Niveau | Mécanisme | Points clés |
|---|---|---|
| 1 | imports de haut niveau du script (AST) | `sys.path[0]` = dossier du script (sauf `-I`/`-P`). Sortie produite à l'import capturée au niveau des fd (memfd) et rejouée à chaque appel. Diff d'env de l'import et clés d'env lues à l'import enregistrés. |
| 2 | imports paresseux appris | L'enfant note l'ordre de **début** des imports (événement d'audit `import`), et non l'ordre de `sys.modules`, qui est celui des fins d'import. Une sonde jetable importe les modules un par un et refuse ceux qui affichent, modifient l'env, lancent un thread ou échouent, ainsi que ceux qui importent un module déjà refusé (sans le réexécuter). Elle repart à neuf après chaque refus. Les modules acceptés sont importés dans le parent, puis le spare est remplacé. Le tout se fait par étapes d'environ 0,25 s : entre deux, la boucle du parent remplace le spare consommé, et `status` affiche `learning=N`. |
| 3 | fichiers botocore parsés d'avance | Enveloppe `botocore.loaders.JSONFileLoader._load_file`. Un enfant prend chaque entrée au plus une fois, et seulement si le `stat` correspond. Même message de debug qu'en natif. Budget LRU de 16 Mo de JSON source (`SPEEDRUNNER_MEMO_MB`). Actif seulement si botocore est déjà importé. |
| 4 | instantané du driver (étape 4 bis) | pas commencé |

Persistance : `$XDG_CACHE_HOME/speedrunner/<outil>-<sha256 de l'empreinte>.json` = `{"modules", "paths", "blacklist", "files"}`, rechargé au démarrage. Une installation modifiée change l'empreinte, donc le fichier de cache.

Avant chaque fork, `gc.freeze()` : le GC de l'enfant ne parcourt pas le tas hérité, et n'en recopie donc pas les pages.

## 7. Ce que l'enfant vérifie et applique

Avant de répondre `R` (`why_not_fast`), sinon `N` et exécution native :

- l'UID du pair (`SO_PEERCRED`, `LOCAL_PEERCRED` sur macOS), le gid et les groupes du client identiques à ceux du démon ;
- le chemin du script, tel que le client l'a lancé : absolu, ou relatif à son cwd (`./manage.py`) s'il désigne le même fichier ; pour un module, `-m` suivi de son nom, et aucun fichier du cwd qui masque un module préchargé ;
- l'empreinte : `stat` du script, de l'interpréteur, des dossiers de `sys.path` et des `RECORD`, plus un `select()` sur la file inotify des fichiers de modules (sur macOS, le verdict du parent, §2 quinquies) ;
- l'env de démarrage : `PYTHON*`, `LC_ALL`, `LC_CTYPE`, `LANG` identiques à ceux du démon ;
- l'env figé à l'import : les clés modifiées *ou lues* par le code de haut niveau doivent avoir la même valeur qu'au démarrage du démon. Exemples réels : botocore fige `AWS_NEW_RETRIES_2026` et un chemin dérivé de `HOME`, et gettext fige la langue.

Après `G` (`apply_request`, puis `run_tool`) :

- `dup2` des fd 0/1/2, `fchdir` sur le cwd du client, umask du client ;
- `os.environ` mis à jour **en place** (`clear()` + `update()`), puis réapplication du diff de l'import ;
- dispositions de signaux natives : SIGINT → KeyboardInterrupt, SIG_IGN hérités, masque vide ;
- buffering de stdout/stdin selon `isatty()`, sans recréer les objets ;
- pont `/dev/tty` : `os.open`, `open` et `io.open` de `/dev/tty` rendent une copie du fd du client ;
- `sys.orig_argv` : l'interpréteur et les options du démon (ceux de l'outil), puis le script ou `-m module`, puis les arguments ;
- sortie de l'import rejouée, puis `runpy.run_path(script, run_name="__main__")`, ou pour un module `runpy._run_module_as_main` ;
- sortie à la manière de CPython (`SystemExit` None/int/autre, exception affichée avec la trace limitée aux frames du script, KeyboardInterrupt qui se termine par SIGINT) ;
- dernier `atexit` (enregistré en premier) : vidage des écrivains bufferisés et de stdout/stderr, puis `X`, puis fd 0/1/2 vers `/dev/null`, puis `L`. Si vider stdout échoue, pas de X : la destruction sort en 120, comme en natif.

## 8. Shell et scripts

- `shell/speedrunner.zsh` lit `${XDG_CONFIG_HOME:-~/.config}/speedrunner/tools` et définit `aws() { command speedrunner run aws -- "$@" }` pour chaque outil inscrit, sauf alias ou fonction de l'utilisateur. Une fonction `speedrunner` recharge les wrappers juste après `speedrunner enroll`.
- Anticipation : widget sur `zle-line-pre-redraw`. Une fois par ligne, quand le premier mot (après d'éventuels `VAR=valeur`) est complet et inscrit, il lance `speedrunner prewarm <outil> &!`. Le coût est d'environ 2 µs par frappe dans les cas courants, et l'unique fork (~0,5 ms) part en arrière-plan.
- **bash** (`shell/speedrunner.bash`, `eval "$(speedrunner init bash)"`) : mêmes wrappers, même rechargement après `enroll`. bash n'a pas de crochet par frappe ; l'anticipation passe par `bind -x` sur la touche espace (keymaps emacs et vi-insert), puisqu'un nom de commande est complet quand un espace le suit. La fonction insère elle-même l'espace à `READLINE_POINT` (en caractères : testé avec du texte multi-octets), puis applique la même règle que zsh. Elle est réarmée à chaque ligne par `PROMPT_COMMAND`, placée en tête et qui rend le `$?` reçu, pour les prompts qui l'affichent. Si la touche espace fait déjà autre chose chez l'utilisateur (`magic-space`…), elle n'est pas touchée : seule l'anticipation est perdue.
- **Scripts, sans préfixe** : `eval "$(speedrunner activate [outils…])"` en tête du script, ou `speedrunner exec [-t outil]… commande` (y compris en shebang : `#!/usr/bin/env -S speedrunner exec bash`).
  - **Principe.** Un dossier `$XDG_RUNTIME_DIR/speedrunner/bin-<hash>/` contient des liens nommés d'après les outils, qui pointent tous vers le client ; il est placé en tête du `PATH`. Appelé sous le nom d'un outil, le client le sert : `aws args` équivaut à `speedrunner run aws -- args`. Pour trouver le vrai outil, `resolve_in_path` saute tout candidat qui se résout en notre propre binaire, ce qui exclut toute boucle. Le `PATH` étant hérité, `xargs`, `make` et les sous-scripts sont couverts.
  - **Mode automatique**, sans outil nommé : dossier `bin-auto-<hash du PATH>`, un lien par script Python ou Ruby trouvé sur le `PATH` (plus `bash` si pre-commit en est, pour son hook git : §2 ter), avec la règle de recherche de `PATH` (la première entrée d'un nom l'emporte). Le dossier n'est reparcouru que si un dossier du `PATH` change : son tampon est dans `.auto`. Les outils inscrits sont préchauffés tout de suite, et ceux que le script appellera plus d'une fois aussi. Pour le savoir, `script::command_counts` ([client/src/script.rs](../client/src/script.rs)) lit le script : celui du shell parent (`/proc/<ppid>/cmdline`, `-c TEXTE` ou le fichier ; à défaut, le fd 255 de bash) pour `activate`, ou celui de la commande pour `exec`.
    - **Lexique.** Un lexer connaît les guillemets, les commentaires, `$(…)` et `` `…` `` (analysés dans leur contexte), `$((…))`, `${…}`, les here-documents (ignorés) et les redirections.
    - **Analyse.** Un parseur suit les positions de commande et l'imbrication : boucles (condition comprise), fonctions avec le nombre de leurs appels (une fonction récursive compte « plusieurs »), `case`, sous-shells, enveloppes (`exec`, `env`, `command`, `nohup`, `srr`), `xargs`, `find -exec`. Il suit aussi les `source` et `.` à chemin littéral, sur 8 niveaux au plus, chaque fichier une seule fois.
    - **Filet de sécurité.** Un raccourci du mode automatique compte ses appels dans `.calls/<outil>` et démarre le démon au deuxième appel, ce qui rattrape ce que l'analyse ne voit pas.
    - **`--eager`** démarre tout outil cité.
    - **Ordre et cadence.** `command_counts` rend les outils dans l'ordre de leur première utilisation : un appel dans une fonction compte au premier appel de celle-ci. `start_daemons` en démarre au plus un par CPU (`SPEEDRUNNER_START_JOBS`). Au-delà, un lanceur détaché, avec ses propres fd, enchaîne : un processus par démon lance `spawn_daemon`, puis attend que son ping réponde, c'est-à-dire que son premier spare existe. L'état du terminal (`--tty`) est calculé avant le fork, sur les fd du script. Un outil atteint avant le démarrage de son démon tourne en natif, et le verrou empêche tout doublon. Sur 2 vCPU, le premier outil a son démon trois fois plus tôt, mais le total reste le même : le CPU de préchargement ne change pas.
- Préfixe, sans inscription : `srr aws s3 ls`. `srr` est un lien vers le client ; appelé sous ce nom, il équivaut à `speedrunner run aws -- s3 ls`. Il marche pour tout outil Python ou Ruby, scripts compris, et le hook zsh préchauffe aussi l'outil qui suit `srr`.
- `speedrunner enroll` refuse ce qui n'est ni un script d'un runtime servi, ni un lanceur (§2 ter). `status`, `stop` et `prewarm` attendent un démon en cours de démarrage.

## 9. Variables d'environnement

| Variable | Lue par | Effet |
|---|---|---|
| `SPEEDRUNNER_REQUIRE_FAST=1` | client | code 124 au lieu du repli natif |
| `SPEEDRUNNER_DEBUG` | client | affiche la raison du repli, et quand le verrou est tenu |
| `SPEEDRUNNER_NO_AUTOSTART` | client | ne lance jamais de démon |
| `SPEEDRUNNER_DAEMON` | client | chemin de `speedrunner_daemon.py` (sinon : à côté du binaire, puis `../share/speedrunner/`, puis `../../../server/` pour le développement ; à défaut, la copie embarquée dans le binaire, extraite dans `$XDG_DATA_HOME/speedrunner/daemons/<empreinte>/`) |
| `SPEEDRUNNER_PRELOAD` | démon | niveaux, `1,2,3` par défaut |
| `SPEEDRUNNER_MEMO_MB` | démon | budget du niveau 3, 16 par défaut |
| `SPEEDRUNNER_IDLE_TIMEOUT` | démon | secondes d'inactivité avant l'arrêt, 4 h par défaut |
| `XDG_RUNTIME_DIR`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME` | tous | socket (repli : `/tmp/speedrunner-$UID`), cache appris, liste des outils inscrits |

Le démon lit l'env du client qui l'a lancé. Journal : `<socket>.log`, qui indique pour chaque démarrage la commande qui l'a déclenché. `speedrunner status <outil>` affiche `ready pid=… preload_ms=… modules=… levels=… learned=… blacklisted=… memo=… gen=…`. `gen` augmente à chaque enrichissement du préchargement ; les tests l'attendent.

## 10. Développer et tester

Tout tourne sur le VPS de dev (Debian 12, 2 vCPU, 1,8 Go, Python 3.11, rustc 1.63) : boucle type, `<vps>` étant son adresse :

```bash
rsync -a --delete --exclude .git --exclude target ./ root@<vps>:/root/speedrunner/repo/
ssh root@<vps> 'cd /root/speedrunner/repo/client && cargo build --release'
ssh root@<vps> 'cd /root/speedrunner/repo && HS_BIN=$PWD/client/target/release/speedrunner HS_PATH=/root/speedrunner/venv-test/bin:/root/speedrunner/venv-aws/bin python3 tests/test_matrix.py'
ssh root@<vps> 'cd /root/speedrunner/repo && HS_BIN=$PWD/client/target/release/speedrunner HS_PATH=/root/speedrunner/venv-test/bin:/root/speedrunner/venv-aws/bin python3 tests/test_shell.py'
```

Ruby (ruby 3.1 du VPS) : la gem de test est installée dans `/root/speedrunner/gems-rbtest`, et rubocop, rake et bundler dans `/root/speedrunner/gems-rb`. Pour la réinstaller après une modification, puis lancer la matrice Ruby :

```bash
cd /tmp && rm -rf rbtest-build && cp -r /root/speedrunner/repo/tests/fixture/rbtest rbtest-build && cd rbtest-build && gem build rbtest.gemspec && GEM_HOME=/root/speedrunner/gems-rbtest gem install --local --no-document rbtest-0.1.0.gem
cd /root/speedrunner/repo && RB_GEM_HOME=/root/speedrunner/gems-rbtest HS_BIN=$PWD/client/target/release/speedrunner HS_PATH=/root/speedrunner/gems-rbtest/bin:/root/speedrunner/venv-test/bin:/root/speedrunner/venv-aws/bin python3 tests/test_ruby.py -v
```

- Sans `HS_BIN`, la matrice compare le natif à lui-même, ce qui valide le harnais.
- Après une modification de `tests/fixture/hstest`, le réinstaller dans `venv-test` depuis une copie : le venv n'a pas `wheel`, il faut l'isolation de build (`cp -r tests/fixture/hstest /tmp/x && venv-test/bin/pip install --no-deps --force-reinstall /tmp/x`).
- Classes de la matrice :
  - `OutputsAndExitCodes`, `Terminal`, `Signals`, `Context`, `Randomness`, `Teardown` : fidélité ;
  - `FailOpen` : repli natif ;
  - `Preload` : chaque niveau, apprentissage, liste noire, fichier botocore modifié ;
  - `Concurrency` : 10 appels en parallèle, à chaud et à froid ;
  - `ScriptPrefix` : `srr` ;
  - `tests/test_scripts.py` : `activate`, `exec`, mode automatique ;
  - `Failures` : démon, enfant ou client tués, module modifié sur place ;
  - `Security` : dossier runtime trop ouvert ou appartenant à un autre UID, socket servi par un autre UID ;
  - `Interactive` : `input`, getpass avec stdin redirigé, `aws configure`.
- **Sécurité** : seuls les autres utilisateurs de la machine sont des attaquants. Côté client (classe `Security` de `test_matrix.py`, valable pour tous les runtimes) : dossier runtime trop ouvert ou d'un autre uid, socket servi par un autre uid, lien symbolique à la place du repli `/tmp/speedrunner-<uid>`. Côté démon (classe `Security` de chaque suite, Python, Ruby, PHP et Node) : un pair d'un autre uid n'obtient pas de réponse (le test se connecte avec l'uid effectif 65534, que rapporte `SO_PEERCRED`, mais l'uid de système de fichiers 0, pour traverser le dossier en 0700) ; lancé à la main avec la ligne de commande et l'env d'un vrai démon, mais un socket dans un mauvais dossier, le démon refuse de démarrer. Les tests qui changent d'uid demandent root ; la cible `non-root` les saute.
- **Courses** : `tests/test_stress.py`. Pendant `STRESS_SECONDS` (90 s par défaut), `STRESS_WORKERS` threads (8) appellent en parallèle les outils de test de chaque runtime installé, au terminal ou non, avec des commandes qui apprennent, échouent ou affichent. Pendant ce temps, un perturbateur arrête les démons, les tue (`kill -9`) ou touche leurs scripts pour les rendre périmés. Chaque appel doit finir, sortir comme en natif (un repli natif est permis), ne jamais sortir en 125 (sauf `kill -9` d'un démon PHP, où seul son W rapporte la fin) et ne s'exécuter qu'une fois (un marqueur par appel). `docker/test-all.sh stress stress-node`, avec un terminal attaché ; job `stress` de la CI, 120 s par cible. `STRESS_SEED` rejoue un tirage.
- Après chaque passage, vérifier qu'aucun démon ne reste : `ps -eo args | grep -c '[h]eadstart_daemon'` doit renvoyer 0.
- **macOS** (Python seulement) : la matrice, `test_shell.py` et `test_scripts.py` tournent tels quels, avec un client compilé sur le Mac (`cargo build --release`, et `--target aarch64-apple-darwin` pour l'autre architecture) et deux venvs, comme sur le VPS. Le workflow `.github/workflows/macos.yml` les lance sur `macos-15-intel` et `macos-26` (Apple Silicon) : `macos-13` n'existe plus, `macos-14` est retiré le 2 novembre 2026. Les tests sautés, faute de sens sur macOS : suivi des lanceurs (ptrace), pair d'un autre uid (pas de `setfsuid`) ; la classe `Bash` de `test_shell.py` demande bash ≥ 4.2 (Homebrew), le bash 3.2 du système n'a que le test `OldBash`.

```bash
cd client && cargo build --release && cd ..
python3 -m venv ~/speedrunner-dev/venv-test && python3 -m venv ~/speedrunner-dev/venv-aws
cp -r tests/fixture/hstest /tmp/hstest-build && ~/speedrunner-dev/venv-test/bin/pip install --no-deps /tmp/hstest-build
~/speedrunner-dev/venv-aws/bin/pip install awscli
HS_BIN=$PWD/client/target/release/speedrunner HS_PATH=$HOME/speedrunner-dev/venv-test/bin:$HOME/speedrunner-dev/venv-aws/bin python3 tests/test_matrix.py
```
- Règles de travail : tests d'abord, bench avant/après commité pour toute optimisation, petits commits, validation par l'humain à la fin de chaque étape.

## 10 bis. Distribution

Une release est un seul fichier : le client embarque les quatre démons, l'addon Node et les hooks zsh et bash (`include_bytes!`, `include_str!`).

- **Paquets** ([packaging/](../packaging/README.md)) : un `.deb` par architecture, construit, installé, désinstallé et signé par le workflow de release ; un `PKGBUILD` pour l'AUR (`speedrunner-bin`) et une formule pour un tap Homebrew. Tous livrent le binaire statique de la release tel quel. `packaging/update.sh <version>` met l'AUR et la formule à la version publiée.
  - **À chaque release**, `release.yml` appelle ensuite deux workflows, relançables à la main. `packages.yml` pousse la formule dans le tap (jeton `HOMEBREW_TAP_TOKEN`, limité au tap), vérifie `brew install` dans Linuxbrew, et propose par pull request `packaging/` à la nouvelle version : le workflow ne commite jamais sur main. `apt.yml` reconstruit de zéro un dépôt APT signé (`packaging/apt.sh`, `apt-ftparchive`, clé Ed25519 dédiée dans `APT_SIGNING_KEY`) à partir des `.deb` de toutes les releases, le déploie sur les Pages du dépôt (`https://anthonygosme.github.io/speedrunner/apt`, suite `stable` pour toutes les Debian et Ubuntu, le binaire étant statique), puis l'installe dans Debian 12 et Ubuntu 24.04.
  - Pourquoi les Pages du dépôt, déployées par Actions, plutôt qu'une branche `gh-pages` ou un dépôt à part : aucun `.deb` dans l'historique git (un clone de speedrunner les téléchargerait tous), aucun jeton de plus, et un dépôt APT qui ne dépend jamais du déploiement précédent. Le prix : l'environnement `github-pages` doit accepter les tags `v*`, et un futur site du projet devra être construit par le même workflow.
  - Seul l'AUR reste à la main (clé SSH du mainteneur). Mise en place : [packaging/README.md](../packaging/README.md), « Setup, once ».

- **Démons.** Faute de démon trouvé à côté du binaire, le client écrit sa copie embarquée dans `$XDG_DATA_HOME/speedrunner/daemons/<empreinte des trois>/`, sous un nom temporaire, puis la renomme. Une copie déjà présente ne sert que si ses octets correspondent : une mise à jour ne lance jamais le démon d'une autre version. Les dossiers voisins du binaire restent prioritaires, pour le développement : sur le VPS, on modifie `server/` sans recompiler.
- **Hook.** `eval "$(speedrunner init zsh)"` dans `~/.zshrc` remplace `source shell/speedrunner.zsh` ; de même `init bash` dans `~/.bashrc`. Le hook n'a aucun `return` au niveau global, il supporte donc d'être évalué.
- **Version.** `speedrunner --version`.
- **Releases.** Un tag `v*` lance `.github/workflows/release.yml`. Le workflow vérifie que le tag correspond à la version du `Cargo.toml`, puis compile en musl statique sur un runner x86_64 et sur un runner arm64 natif. Il vérifie ensuite que le binaire est statique et qu'il démarre, et publie `speedrunner-<arch>-unknown-linux-musl.tar.gz`, sa somme `.sha256` et `install.sh` avec `gh release create`. Les noms d'archive ne portent pas de version, pour que `releases/latest/download/` marche.
- **`install.sh`**, en sh POSIX (testé sous dash) : une ligne, `curl -fsSL https://raw.githubusercontent.com/anthonyGosme/speedrunner/main/install.sh | sh`. Il détecte l'architecture, télécharge l'archive et sa somme, refuse si elles ne concordent pas, et installe par un nom temporaire puis `mv`, avec le lien `srr`, là où le shell le trouve sans étape de plus : `/usr/local/bin` en root ; sinon `~/.local/bin` ou `~/bin` s'il est déjà dans le `PATH` ; sinon `~/.local/bin`, ajouté au `PATH` dans `~/.profile`, `~/.bashrc` et `~/.zshrc`, une fois et marqué. Le shell qui a lancé l'installation ne voit ce dernier ajout qu'une fois relancé : un processus enfant ne peut pas changer le `PATH` de son parent. Variables : `SPEEDRUNNER_VERSION`, `SPEEDRUNNER_BIN_DIR`, `SPEEDRUNNER_NO_MODIFY_PATH=1`, et `SPEEDRUNNER_BASE_URL` pour le tester contre un serveur local. Testé dans Debian : en root, en utilisateur avec et sans `~/.local/bin` dans le `PATH`, et pour une réinstallation.
- **CI.** `.github/workflows/ci.yml` lance les tests unitaires du client, puis les cinq suites dans l'image de `docker/`. `docker/run.sh tests` rend un code non nul dès qu'une suite échoue.
- **Plateformes : `docker/test-all.sh`.** Les suites sur 21 cibles, en parallèle (la moitié des CPU, `JOBS=`), avec un tableau par cible et par suite, et les journaux dans `docker/logs/`. `docker/test-all.sh alpine py3.9` n'en lance que deux, `SUITES="ruby php"` que ces suites, `PLATFORM=linux/arm64` sur une autre architecture (émulée par Docker Desktop, 5 à 10 fois plus lente ; journaux dans `docker/logs/<plateforme>/`). Une suite qui échoue montre ses `FAIL`/`ERROR` en détail, et la CI affiche le journal des cibles en échec. Le client est compilé une seule fois, en musl statique comme les releases (`rust:1-alpine`), puis monté dans chaque conteneur (`SPEEDRUNNER_BIN`).
  - **Distributions** (les cinq suites) : Debian 12 (`docker/Dockerfile`), Ubuntu 24.04 et 22.04 (`Dockerfile.ubuntu`, `UBUNTU=`), Alpine 3.20 (`Dockerfile.alpine`, musl, busybox) et Fedora 42 (`Dockerfile.fedora`).
  - **Versions des langages**, avec les images officielles (`docker/lang/`) : Python 3.9 à 3.14 (matrix, scripts), Ruby 3.1 à 3.4, PHP 8.1 à 8.4 avec pcntl, FFI et OPcache, sans `php.ini`. Ruby 3.0 passe par Ubuntu 22.04 : son image officielle repose sur Debian 11, dont les dépôts ne la servent plus.
  - **Variantes** : `non-root` (uid 1000, sur l'image Ubuntu, ouverte à tout uid) et `no-ptrace` (`--security-opt seccomp=docker/no-ptrace.json`, où un lanceur doit partir en natif, une seule fois).
  - **CI** : `.github/workflows/platforms.yml` lance chaque cible dans un job, plus Ubuntu 24.04 et Alpine sur arm64, chaque nuit, à la demande et avant chaque release (`release.yml` n'en publie aucune si une cible échoue). `ci.yml` vérifie à chaque push la compilation musl.
  - `docker/run.sh` : `tests` (les cinq suites) ou `suites matrix ruby…`. Il n'installe que les fixtures que l'image sait faire tourner, et saute les tests Laravel sans projet Laravel.
- **Ce que la première passe a trouvé** (2026-10-29), en plus des trois bugs de §11 (musl, Ruby 3.2, Ruby 3.3) : Ruby 3.4 cite les frames `'<main>'` et non plus `` `<main>' `` (§11) ; le reste tenait au harnais, qui supposait Debian. `sys.orig_argv` n'existe qu'à partir de Python 3.10 ; `SIG_IGN` s'affiche autrement avant 3.11 ; sous 3.9, botocore est un paquet à part, aux modèles compressés (`.json.gz`) ; sans `php.ini`, `$_ENV` est rempli et `display_errors` est actif ; Fedora met `/usr/sbin` avant `/usr/bin` ; et PHP 8.1 ferme ses flux avant la fin de son arrêt : le harnais pty doit attendre le processus avant de fermer le pty.
- **`speedrunner demo`** mesure le gain sur la machine même, avec les outils qu'elle a. Il ne lance que ceux d'une liste fixe (aws, pip, ansible, black, poetry, httpie, mypy, pytest, rubocop, rspec, rake, bundle, gem, rails, composer, phpunit, phpstan…), et seulement `--version` : jamais n'importe quel script du `PATH`, qui pourrait ignorer ses arguments. Pour chaque outil, il prend la médiane de 3 runs natifs, puis démarre le démon sur `/dev/null` (comme les runs), le laisse apprendre en 2 appels, compare une fois les sorties (code, stdout, stderr) et prend la médiane de 3 runs avec le chemin rapide obligatoire. Un repli natif est signalé, pas compté. Il arrête les démons qu'il a démarrés, pas les autres. Sur le VPS : 27 outils, gain médian x7,5, en 107 s.
- **À faire.** `cargo install` (les fichiers embarqués devront alors être dans le crate), Homebrew, AUR, `.deb`. Un démon d'une version précédente qui tourne encore n'est pas arrêté à la mise à jour : il sert jusqu'à son arrêt pour inactivité.

## 11. Pièges déjà rencontrés

- **awscli v1 et botocore.** awscli embarque botocore et l'expose sous le nom `botocore` grâce à un finder d'alias : `awscli.botocore.*` et `botocore.*` sont alors des objets module distincts. La CLI utilise `botocore.*`, c'est donc lui qu'il faut patcher.
- **Ordre de `sys.modules`.** CPython réinsère chaque module à la fin de son import, et `sys.modules` reflète l'ordre des fins. Rejouer cet ordre casse les imports circulaires (`awscli.paramfile` ↔ `awscli.shorthand`). Il faut l'ordre des débuts, donné par l'événement d'audit `import`.
- **Une sonde polluée ment.** Après un module refusé, ceux qui suivent paraissent sains parce que l'effet de bord a déjà eu lieu. D'où une sonde neuve après chaque refus.
- **Python 3.11 et `sys.excepthook`.** L'affichage utilise `exc.__traceback__` et non l'argument tb : il faut `exc.with_traceback(tb)`.
- **`os.environ`.** Le réaffecter casse les références que botocore garde et laisse `getenv` côté C sur l'env du démon. Il faut le modifier en place (c'est l'erreur de quicken).
- **Les fichiers `.pyc`** ne retiennent le mtime du source qu'à la seconde près : un test qui modifie un module doit forcer le mtime.
- **`/dev/tty`.** Rouvrir par `/proc/self/fd/N` le fd `/dev/tty` reçu du client renvoie au terminal de contrôle de l'appelant, donc échoue avec ENXIO dans l'enfant. Il faut faire `dup()`.
- **getpass** sans `/dev/tty` retombe sur stdin, et lit alors le mot de passe dans un pipe.
- **Petit-fils du lancement.** S'il garde les fd du client, un pipeline attend sa fin (3 s mesurées). S'il attend un verrou sans savoir qui le tient, il peut ressusciter un démon qu'on vient d'arrêter : d'où l'état `running`/`exiting <pid>` écrit dans le verrou.
- **zsh.** Les motifs `#` exigent `extended_glob` (`emulate -L zsh -o extended_glob`). Pendant la lecture de `.zshrc`, `zsh/zle` n'est pas encore chargé, et `add-zle-hook-widget` abandonne sans message si l'on ne fait pas `zmodload zsh/zle` avant.
- **rustc 1.63 (Debian 12).** libc doit rester épinglée à `=0.2.175`, les versions plus récentes exigent rustc ≥ 1.65.
- **venv-test n'a pas `wheel`.** Réinstaller la fixture avec l'isolation de build, depuis une copie.
- **Déboguer par ssh.** `pkill -f motif` tue aussi le shell distant dont la ligne de commande contient le motif. Mieux vaut tuer par PID.
- **La ligne de commande lue à l'import.** Trois formes trouvées par le panel.
  - Une référence prise à l'import, comme le `def main(args=sys.argv)` de httpie : l'enfant modifie donc `sys.argv` en place (`sys.argv[:] = …`), sans le réaffecter.
  - Un module qui lance le programme dès son chargement, comme `rails/cli`.
  - Un module qui lit les arguments au niveau global.

  Le démon remplace `sys.argv` par une `ArgvProbe` (en Ruby, un module préfixé sur la classe singleton d'`ARGV`), qui note les lectures pendant les imports. Seul `argv[0]` est permis : c'est le script, comme en natif. Un import de niveau 1 qui lit les arguments est inscrit dans `<outil>.skip-<hash>.json`, indexé par le seul script, puis le démon redémarre sans lui. Aux niveaux 2 et 3, la sonde le met en liste noire.
- **Crochets d'import posés pendant l'exécution.** Le crochet de réécriture d'assertions de pytest charge lui-même les plugins. Précharger ces modules par l'import standard les prive de la réécriture, et provoque un `PytestAssertRewriteWarning`. L'enfant n'apprend donc que les modules chargés par un loader d'importlib, ou par un finder déjà présent dans le `sys.meta_path` du parent.
- **`getopt()` en PHP** lit la copie interne de `$_SERVER` faite au démarrage de l'interpréteur, pas la variable reconstruite par l'enfant. Le démon est donc lancé avec `-d register_argc_argv=0`, et `getopt()` se rabat sur le `$argv` global. Le démon lit ses propres options dans `/proc/self/cmdline`.
- **L'état du terminal lu à l'import.** httpie (`Environment.stdin_isatty`), ansible (sa sortie en couleur) et rubocop (la gem Rainbow) se demandent à l'import si leurs flux sont des terminaux, et figent la réponse. Les sorties capturées du panel et de la matrice ne le voyaient pas : sans terminal des deux côtés, l'état figé était juste. La démo enregistrée dans un terminal l'a révélé : httpie échouait, ansible et rubocop perdaient leurs couleurs.
  - Le client passe au démon `--tty MASK[:LIGNESxCOLONNES]`, les fd 0 à 2 qui sont des terminaux chez lui. Pour `prewarm`, lancé par le hook zsh avec ses fd sur `/dev/null`, c'est tout si `/dev/tty` s'ouvre.
  - Pendant les imports, un pty brut, un par fd, remplace chacun de ces fd. Il reste bloquant, comme un terminal : ansible refuse de démarrer sur des flux non bloquants. Un module qui écrirait à l'import plus que ce que contient un pty (environ 64 Kio) bloquerait donc le démon.
  - Les lectures d'`isatty` sont notées, comme les lectures d'env. En Python, `sys.stdin`, `stdout` et `stderr` sont remplacés dès le démarrage par une sous-classe de `TextIOWrapper`, et `os.isatty` est enveloppé. En Ruby, le module est préfixé sur `IO` : `reopen` change la classe d'un objet, ce qui fait perdre un module préfixé sur sa classe singleton.
  - L'enfant refuse le chemin rapide si un fd lu n'a pas le même état chez le client.
- **Le message d'une exception Ruby non rattrapée.** L'afficheur natif de Ruby 3.1 échappe le message : il double les barres obliques inverses, nomme les caractères de contrôle (`\e`, `\r`…, sinon `\x1F`), écrit DEL `\c?`, et laisse tels quels la tabulation, le retour à la ligne et les octets non ASCII. `full_message`, qu'utilise le démon, ne l'échappe pas. Le démon échappe donc le message dans le texte de `full_message`, ligne par ligne, parce que le message peut venir d'une méthode `message` redéfinie (le `CLAide::Help` de CocoaPods), et que sur un terminal chaque ligne est entourée de codes de mise en forme. Trouvé par `speedrunner demo` sur `pod --version`. Ruby 3.2 et plus affichent le message tel quel (par `detailed_message`) : le démon n'échappe donc que sous Ruby < 3.2. Trouvé sous Ubuntu 24.04.
- **Ruby 3.3 démarre avec un thread de minuterie** : deux threads natifs dès le lancement. Le démon Ruby, qui exigeait un seul thread après le préchargement, se désactivait pour tout outil. Il compte maintenant les threads au-delà de ceux qu'il avait au démarrage ; `fork` gère ce thread de Ruby. Trouvé sous Alpine. Mais chaque `fork` remplace aussi ce thread chez le parent, et sur une machine chargée l'ancien survit jusqu'à ~2 ms (1 à 2 forks sur 3 000) : le démon voyait un troisième thread et se désactivait. Quand aucun thread Ruby n'est en plus, il recompte pendant 100 ms au plus avant de conclure. Trouvé par `platforms` (Ruby 3.4).
- **Ruby 3.4 cite les frames entre apostrophes** (`'<main>'`, avant `` `<main>' ``) : le démon ne renommait plus les frames `<top (required)>` du script. Il accepte les deux formes. Trouvé par la matrice des versions.
- **Pas de locale (PEP 538).** Dans un conteneur neuf, en cron ou en CI, sans `LANG` ni `LC_*`, CPython ajoute au démarrage `LC_CTYPE=C.UTF-8` à son propre env. Le démon comparait cet env modifié à celui du client, qui ne l'a pas : tout appel partait en natif (« startup env differs: LC_CTYPE »). Il compare maintenant le client à l'env qu'il a reçu (`/proc/self/environ`, que `setenv` ne touche pas), et donne à l'outil ce que CPython y a ajouté, comme en natif. La matrice fixe `LANG` : c'est le test d'`install.sh` dans une Ubuntu neuve qui l'a trouvé, après la v0.2.0 ; le test `NoLocale` le couvre. Alpine y échappe : sa locale C est déjà UTF-8.
- **Une image d'une autre architecture garde son tag.** Après une passe `PLATFORM=linux/arm64`, `rust:1-alpine` était l'image arm64 : la passe « native » suivante compilait un client arm64, exécuté sous QEMU, qui n'a pas `ptrace` (lanceurs en natif, tout 3 fois plus lent). `test-all.sh` passe donc toujours `--platform`.
- **Constantes propres à l'architecture.** Le démon Ruby codait en dur `O_DIRECTORY = 0o200000`, la valeur x86 ; sur arm64 (asm-generic), c'est `O_DIRECT`, et l'ouverture du dossier runtime échouait avec EINVAL : aucun démon Ruby ne démarrait sur arm. Ruby n'expose pas `File::DIRECTORY` : le démon ouvre avec `O_NOFOLLOW | O_NONBLOCK` (une FIFO posée là ne le bloque pas) puis vérifie par `fstat` que c'est un dossier. Seul `IN_CLOEXEC` (`0o2000000`, identique sur x86 et arm64) reste codé en dur. Les constantes du démon PHP sont les mêmes sur les deux. Trouvé par le job arm64 de `platforms.yml`, reproduit sous émulation.
- **ptrace et musl.** Avec musl, la crate libc donne aux constantes `PTRACE_*` le type `c_int`, `c_uint` avec la glibc : le client ne compilait plus pour la cible des releases. Trouvé sous Alpine.
- **busybox.** Son `env` n'a pas `-S` : le shebang `#!/usr/bin/env -S speedrunner exec bash` demande `coreutils-env` sous Alpine. Son composer est un script shell qui lance PHP sans `exec`, donc il n'est pas suivi et part en natif.
- **Un script nommé relativement au cwd** (`./manage.py`). Le client le passait tel quel au démon, qui ne tourne pas dans le même dossier : le démon s'arrêtait au démarrage, et deux projets Django auraient eu le même nom de démon. Le client passe donc au démon le chemin absolu (sans résoudre les liens), d'où un démon par projet, mais garde le chemin tapé pour `argv[0]`. Il faut aussi le défaire de `runpy.run_path`, qui remet le chemin du script dans `sys.argv[0]` : l'enfant remplace `runpy._ModifiedArgv0` pour ce seul appel, puis le restaure. En Ruby et PHP, un tel appel part en natif.
- **Un lanceur Python laisse SIGPIPE et SIGXFSZ ignorés** : CPython les ignore au démarrage, et l'exec le transmet. Exiger les mêmes signaux ignorés que le client faisait donc partir az en natif. La requête porte maintenant les signaux ignorés et l'umask du lanceur.
- **Trouver le fichier de `-m` sans rien importer** échoue sur les paquets espaces de noms. `PathFinder.find_spec("azure.cli", …)` lève `KeyError: 'azure'`, parce que `_NamespacePath` cherche son parent dans `sys.modules`. Le démon importe donc les paquets parents, comme runpy le fait en natif.
- **Une clé `bytes` dans `os.environ`.** `os.environ[b"X"]` lève `TypeError` en natif (certains modules de meson le tentent dans un `try`), mais l'intercepteur des lectures d'env notait la clé avant l'appel. Le tri des clés figées mêlait alors `str` et `bytes`, et chaque enfant échouait avant le go : meson, pio et copier partaient en natif. Une clé du mauvais type n'est plus notée. Trouvé par le panel des candidats.
- **Un apprentissage qui bloque le démon.** oci_cli.cli lit la ligne de commande à l'import, et 470 modules l'importent. Chaque refus relançait une sonde neuve, qui réexécutait oci_cli.cli : 93 s d'apprentissage (puppet 28 s). Pendant ce temps, le parent ne remplaçait pas le spare, et chaque appel attendait 3 s avant de partir en natif. Deux corrections :
  - un module qui importe un module refusé est refusé à l'instant, par un finder placé en tête de `sys.meta_path` dans la sonde, qui lève une `BaseException` (un `except ImportError` ne peut pas la masquer). En Ruby, `TracePoint(:script_compiled)` lève avant que le fichier refusé s'exécute. Résultat : oci 6,6 s, puppet 7 s ;
  - l'apprentissage se fait par étapes (une sonde, bornée à 0,25 s), entre lesquelles la boucle du parent tourne normalement.
- **Un thread lancé au chargement, au niveau 1.** sshkit lance au chargement le thread de nettoyage de son pool de connexions : kamal et cap désactivaient leur démon. Un import de niveau 1 qui lance un thread est maintenant sauté, comme celui qui lit la ligne de commande, et le démon redémarre sans lui. Le niveau 2 apprend ensuite ce qui n'en dépend pas : kamal x5, cap x7,7.
- **En Ruby, un fichier peut dépendre d'un fichier refusé chargé avant lui**, par l'espace de noms global, sans le `require` lui-même. fastlane.rb (refusé : il modifie l'env) pose `Fastlane::Helper = FastlaneCore::Helper` ; dotenv_helper.rb, chargé plus tard par une méthode, rouvre `module Fastlane; module Helper`. Préchargé seul, il créait un `Fastlane::Helper` à lui, et l'outil échouait (`uninitialized constant FastlaneCore::Helper::DotenvHelper`). Le parent rejoue donc dans un enfant jetable l'ordre natif complet, et compare les espaces de noms ouverts par chaque fichier pendant son chargement. Une règle plus simple (refuser tout fichier de la même gem chargé après un refusé) était sûre, mais aurait refusé presque tout rubocop. Trois pièges :
  - ne compter que les `class`/`module` du corps du fichier : un `class << self` dans une méthode (metaid.rb de puppet) s'exécute à chaque appel, depuis d'autres fichiers ;
  - retirer les adresses des noms de classes anonymes (`#<Class:0x…>::Config` dans psych) ;
  - appeler `Module#name` par `instance_method` : puppet le redéfinit sur certaines classes, qui renvoient un Symbol. Le traceur ne doit jamais lever : une exception levée dans un TracePoint remonte dans le chargement qu'il observe.
- **Un SIGCHLD qui tombe hors de `poll` ne réveille personne.** Le démon PHP voit la fin de fichier du socket de contrôle d'un enfant, son `waitpid` passe avant que l'enfant soit ramassable, puis le signal arrive pendant que PHP exécute la boucle : le `poll` suivant dormait jusqu'à 4 h, et le client attendait son W (vu une fois sur psalm, dans le panel). Tant qu'un enfant a fermé son socket de contrôle sans avoir été ramassé, le parent reregarde après 5 ms. Python (`signal.set_wakeup_fd`) et Ruby (self-pipe écrit par `trap`) ne sont pas exposés.
- **Node, `MADV_DONTFORK`.** V8 marque les pages de son tas pour qu'elles ne soient pas copiées au fork (indicateur `dc` dans smaps, à ne pas confondre avec `dd`). L'enfant plantait au premier objet JavaScript touché (SIGSEGV dans `napi_get_uv_event_loop`). L'addon remet `MADV_DOFORK` avant chaque fork.
- **Node, threads du parent.** Sans `--single-threaded`, le premier GC de l'enfant attend des workers qui n'existent pas. À la sortie normale, `pthread_cond_destroy` attend des threads qui attendaient cette variable de condition dans le parent. Après une exception non rattrapée, `uv_thread_join` échoue et Node avorte (-6). D'où la sortie par `_exit`, et le relais de stderr après une exception. Selon la machine, cette sortie plante aussi en SIGSEGV (runners de la CI, jamais sur le portable) : le relais attend donc sur tout signal fatal, pas seulement SIGABRT, et ferme le pipe pour que le thread de copie transmette la fin du rapport. Ce thread envoie lui-même X : la fin d'un appel terminé par une exception ne dépend plus du démon. Avant, un démon tué par `kill -9` juste avant l'appel (le noyau libère sa mémoire avant ses descripteurs, et son spare prend encore l'appel pendant quelques millisecondes) laissait le client en 125. Trouvé par le job `stress` de la CI.
- **Node, signaux.** Un masque de signaux ne vaut que pour le thread qui le pose : bloquer SIGCHLD pour le lire par `signalfd` le laissait aux threads de Node, qui l'ignoraient. Un enfant fini n'était jamais ramassé, et son client attendait. Le démon passe par un self-pipe que remplit un gestionnaire C.
- **Node, sortie et `signal-exit`.** jest charge `signal-exit`, qui enveloppe `process.emit` et `process.reallyExit` : un crochet sur l'événement `exit` posé dans l'enfant ne voyait pas `process.exit()`, et l'enfant bloquait dans le démontage. Le démon enveloppe les deux avant tout préchargement (ses versions sont les plus internes) ; une fin naturelle sort par un crochet de nettoyage Node-API.
- **Node, certificats racine.** Node les charge une fois, sous un verrou d'initialisation C++ ; un fork pendant ce chargement laissait l'enfant (firebase) l'attendre indéfiniment. Le démon les charge au démarrage, avant tout fork.
- **Node, ce qu'il garde en cache.** Dans un processus forké, `process.pid`, `process.ppid` et `process.cwd()` gardent les valeurs du parent : un client relayait Ctrl-C au démon, et prettier cherchait ses fichiers dans le dossier runtime. Même chose pour `Math.random`, dont l'état est dans le tas : chaque enfant tirait les mêmes nombres. L'enfant redéfinit les deux PID, fait `process.chdir(".")` après `fchdir`, et remplace `Math.random` par un xoshiro128** amorcé par le noyau. Un spare prend aussi son propre groupe de processus, que le client signale.
- **Un démon qui s'arrête attend ses enfants.** Il sortait dès l'arrêt décidé (`stop`, empreinte périmée…) : un appel PHP en cours, ou un appel Node terminé par une exception, ne recevait jamais son W, et le client sortait en 125 (« lost track ») alors que la commande s'était exécutée. Trouvé par la suite de stress (2 appels sur 8 600, puis 1 sur 6 800 en Node).
- **Un pty livre en différé.** Ce qui est écrit sur l'esclave arrive au maître un peu plus tard (le tampon « flip » du noyau). Les démons Python et Ruby lisaient le maître aussitôt l'import fini : sur une machine lente, la sortie d'un import arrivait pendant le suivant, accusé à tort d'afficher quelque chose, et le démon redémarrait. Ils lisent maintenant jusqu'à 10 ms de silence. Seulement avec un démon lancé depuis un terminal : la CI lance ses conteneurs avec `-t`.
- **`subprocess` avec `timeout`** attend par paliers croissants, plafonnés à 50 ms : une mesure de temps doit attendre sans timeout.
- **Faux serveurs dans les tests.** Un processus forké depuis le lanceur de tests doit finir par `os._exit` quoi qu'il arrive, sinon il exécute le reste de la suite en parallèle. Un faux serveur qui écrit à un client parti meurt de `BrokenPipe`. Dans les deux cas, on obtient un démon fantôme.
- **macOS, `/dev/tty`.** Sous BSD, un fd ouvert sur `/dev/tty` désigne le terminal de contrôle de *quiconque s'en sert* : dans l'enfant, qui n'en a pas, getpass échouait en EIO. Le client passe le périphérique lui-même (`/dev/ttys003`). Trouvé par la classe `Interactive`.
- **macOS, `os.set_inheritable`** passe par `ioctl(FIOCLEX)`, qui échoue en EBADF sur un fd ouvert `O_SEARCH` (le cwd reçu du client) : chaque enfant échouait avant de répondre. Le démon utilise `fcntl(F_SETFD)`.
- **macOS, chemins de socket** : 104 octets au plus (108 sous Linux), et le dossier temporaire de macOS (`/var/folders/…/T/`) est profond. Le dossier runtime par défaut, `/tmp/speedrunner-<uid>`, est court ; les tests créent leurs dossiers sous `/tmp`. Un chemin trop long fait partir l'appel en natif.
- **macOS, le canari et les threads.** Après la détection de proxy (3 threads), le canari CoreFoundation passe sur l'Intel : dans un processus qui a des threads, il ne prouve rien. La sonde vérifie donc les threads d'abord.
- **macOS, `fcntl` par FFI.** PHP le déclarait `int fcntl(int, int, int)` ; il est variadique, et sur l'arm64 d'Apple ses arguments variadiques passent par la pile : chaque spare mourait (« spare children keep dying »), sur les deux runners. Déclaré `int fcntl(int, int, ...)` sur macOS.
- **PHP 8.5 enregistre `argv` en CLI quoi que dise `register_argc_argv`** (RFC « deprecations PHP 8.5 »). Le démon PHP comptait sur `-d register_argc_argv=0` pour que `getopt()` lise le `$argv` reconstruit par l'enfant : en 8.5, `getopt()` lit le `$_SERVER['argv']` interne, celui du démon. Le démon se désactive donc sur PHP ≥ 8.5 (exécution native, sortie juste), sous Linux comme sous macOS ; la CI macOS teste PHP 8.4. Trouvé par `test_getopt` sur le runner Intel (PHP 8.5.9). Pour servir PHP 8.5, il faudrait refaire `$_SERVER` interne dans l'enfant (FFI vers `sapi_globals`, à valider par version).
- **macOS, `script(1)` BSD.** Sur une longue sortie, il transforme parfois un `\r\n` en `\r\r\n` ; et il renvoie l'écho du ^D qu'il envoie au pty quand sa propre entrée est finie, tôt ou tard. Côté natif comme côté servi : le harnais les neutralise des deux côtés.
- **`PATH` lu à l'import, et `speedrunner exec`.** pre-commit lit `PATH` en se chargeant. Son démon, démarré par `srr`, avait un `PATH` sans le dossier de liens de `exec` ; sous `exec`, chaque appel partait en natif (« import-time env differs: PATH »). Les démons Python, Ruby et Node comparent désormais ce `PATH` sans leurs propres dossiers `bin-*` du dossier runtime, qui mènent aux mêmes outils : au pire, un sous-outil lancé par l'outil n'est pas servi. Trouvé par le bench pre-commit ; test `test_tool_reading_path_at_import_served_under_exec`.
- **pre-commit mesure l'environnement en se chargeant.** `pre_commit/xargs.py` calcule à l'import la longueur maximale d'une ligne de commande (`ARG_MAX`, moins la taille de tout l'env, plafonnée à 128 Kio) et la fige comme valeur par défaut d'un argument. Il lit ainsi toutes les clés de l'env. Or git passe au hook `GIT_AUTHOR_DATE`, qui change à chaque commit : chaque appel du hook partait en natif (« import-time env differs: GIT_AUTHOR_DATE »). Adaptateur, comme pour rspec : les lectures faites depuis `_environ_size` de `pre_commit.xargs` ne sont pas notées, et l'enfant recalcule cette valeur avec l'env du client, avant de réappliquer les changements d'env de l'import, puisque `pre_commit.main` retire ses clés après avoir importé xargs. Trouvé par le test du vrai pre-commit sous git.

## 12. Limites connues

Elles ne relèvent pas d'un bug de mise en œuvre, mais de ce que le fork depuis un démon change par nature :

- graine de hachage partagée entre les enfants ;
- pas de terminal de contrôle dans l'enfant : un sous-processus qui ouvre `/dev/tty` échoue ;
- ce que la destruction de l'interpréteur écrit après le X est perdu ;
- lectures d'env via `os.environ.copy()` ou en C non vues ;
- fichiers de données lus à l'import non couverts par l'empreinte ;
- l'enfant va jusqu'au bout si le client est tué par `kill -9`.
- un outil qui lit l'état du terminal à l'import part en natif quand on l'appelle dans un autre état que celui du client qui a lancé son démon (par exemple `outil | less` après un appel au terminal) ; la taille du terminal lue à l'import n'est pas suivie ;
- une horloge lue au chargement est celle du démon ; pour rspec seul, un adaptateur la remet à l'heure du début du run.
- en Ruby, la comparaison des espaces de noms ne voit pas une dépendance à un fichier refusé par `defined?` ou par la valeur d'une constante ; une constante absente fait échouer le chargement dans la sonde, donc refuser le fichier ;
- un lanceur n'est suivi que jusqu'à un interpréteur Python, et seulement par son `exec` final (wp-cli et drush passent par deux scripts shell, puis PHP : non suivis) ; sans ptrace (conteneur restrictif, Yama `ptrace_scope` 3), il part en natif ;
- Node, `--single-threaded` : GC et compilation séquentiels dans l'enfant. Ce qui calcule beaucoup ralentit, jusqu'à 55 % (un script qui sollicite le GC) ; vite, dont des modules lancent des threads Rust au chargement et sont donc rechargés à chaque appel, est plus lent servi qu'en natif (x0,56).
- Node, un module appris est préchargé pour toutes les commandes, comme en Python : s'il modifie l'état global de Node en se chargeant (`signal-exit` enveloppe `process.emit`, `graceful-fs` remplace `process.chdir`), la modification vaut aussi pour les commandes qui ne le chargent pas. Les refuser coûtait trop : `graceful-fs` est chargé par chaque appel de npm ou firebase. Il faudrait ne précharger un tel module que s'il est chargé par tous les appels.
- Node, pas de pont `/dev/tty` dans l'enfant ; il faut Node ≥ 22.15 pour apprendre (`module.registerHooks`). Sans cela, seul le démarrage de Node est économisé.
- Node, un import qui parcourt les clés de l'environnement fait partir en natif un client dont l'ensemble des clés diffère (hors `PWD`, `OLDPWD`, `SHLVL`, `_` et `SPEEDRUNNER_*`) ; une pile capturée pendant l'évaluation du point d'entrée, et que l'outil affiche lui-même, montre les frames du démon.
- pour un lanceur, `prewarm` exécute le préambule sans argument : sans effet pour az et gcloud, mais un lanceur qui ferait autre chose que préparer son exec le ferait aussi à chaque préchauffage.
- hook git de pre-commit : servi seulement sous `speedrunner exec` ou `activate` (git lancé hors d'eux ne voit pas le lien `bash`), et si pre-commit est sur le `PATH` ou nommé. Sous eux, tout `bash` appelé par son nom passe par le client (un démarrage de plus, +4 ms mesurés dans Docker Desktop), et `$BASH` y nomme le lien, qui mène au même bash. Le démon du hook (`python -mpre_commit`) est distinct de celui du script `pre-commit` : `speedrunner status pre-commit` et `stop pre-commit` visent le second, le premier s'arrête après 4 h sans appel.
- macOS : Python, Ruby et PHP (Node part en natif) ; lanceurs en natif ; l'empreinte kqueue ne voit pas un simple changement d'attributs (`touch`, `chmod`) d'un fichier de module ; un outil qui utilise CoreFoundation en se chargeant (détection de proxy, boucle d'exécution) voit ce module rechargé à chaque appel ; le hook bash demande bash ≥ 4.2, celui du système est 3.2 (zsh, le shell par défaut, n'a pas cette limite) ; le Ruby 2.6 du système n'est pas servi (le démon demande Ruby ≥ 3.0).
- PHP ≥ 8.5 n'est pas servi : sa CLI enregistre `argv` quoi qu'il arrive, et `getopt()` lirait les arguments du démon (§11).

## 13. État et suite

| Étape | État | Résultat clé |
|---|---|---|
| 0 Baseline, go/no-go | fait | GO, l'initialisation domine |
| 1 Démon + client, niveau 1 | fait | x1,7, matrice verte |
| 2 Spare, sortie anticipée, niveaux 2 et 3 | fait | x5,4 / x5,4 / x4,2 ; pip x8,7 |
| 3 Hook zsh, anticipation, `srr` | fait | 300 ms de frappe ⇒ 37 ms au premier appel ; scripts x5,3 |
| Scripts : `activate`, `exec`, mode automatique | fait | une ligne en tête de script, x4,6 à x5 sur une boucle aws ; 11 tests |
| 4 Robustesse et sécurité | fait | 52 + 5 tests verts, plus aucun reporté |
| R0 Baseline Ruby | fait | GO : chargement 65–85 % ; plafonds x5 (rubocop) à x25 |
| R1 Démon Ruby, niveau 1 | fait | rake x12, bundle x4,5, gem x3,2 ; 29 tests Ruby verts |
| R2 Préchargement appris Ruby | fait | rubocop x3–x3,8, bundle x15, gem x8, rake x13 ; 32 tests Ruby verts |
| P0 Baseline PHP | fait | GO : 60–90 % de démarrage ; plafonds x2,5 (composer show) à x10 (phpunit), artisan x5 |
| P1 Démon PHP | fait | artisan x3,7–x3,9, phpunit x3,6, composer x2,1–x3, phpcs x2,8 ; 33 tests PHP verts |
| Perl | à évaluer | si aussi simple que PHP |
| Panel étendu (Python, Ruby, PHP) | fait | 36 CLI, 36 sorties identiques ; ansible x16, poetry x14, solargraph x13, aws x5,8 ; 145 tests verts |
| L0 CLI à lanceur : az, gcloud, `./manage.py` | fait | GO : Django x4,8–x7,7 (bug du chemin relatif corrigé) |
| L1 Suivi des lanceurs, `-m module`, `sys.path` appris | fait | az x1,9–x4,3, gcloud x3,5–x4,2, sans réglage ; matrice de 67 tests verte |
| Panel des candidats (47 commandes de plus, Docker) | fait | 45 servies, 45 sorties identiques, médiane x7,7 ; dbt x80, airflow x23, openstack x22, checkov x12 ; 6 bugs corrigés ; 70 + 37 tests verts |
| N0 Go/no-go Node | fait | GO sous conditions : fork sûr (1 000 appels sur 1 000), plafonds x3 à x18 |
| N1 Démon Node | fait | 13/13 sorties identiques ; eslint x14 et x6, next x8,6 ; matrice Node de 31 tests verte |
| Suite de stress, tests de sécurité côté démon | fait | ~38 000 appels sous `kill -9`, arrêts et péremptions, sans échec, une fois corrigés les bugs de course trouvés |
| pre-commit sous `git commit` (hook suivi par un lien `bash`) | fait | `speedrunner exec git commit` 1,11 s → 0,31 s (x3,6), pre-commit et ses hooks `language: system` servis, même sortie |
| Contre les démons dédiés | fait | eslint x7,6 (eslint_d x3,1), mypy x4,2 (dmypy x1,4) ; blackd + curl gagne |
| 4 bis Instantané du driver (niveau 4) | reporté | propre à aws |
| 5 Bench comparatif et vidéo | fait | vidéo du script de démo : 24,0 s → 5,3 s, x4,5, démons chauds (médiane de trois enregistrements : 22,8 s → 5,6 s chaud, 7,3 s à froid) |
| Release 0.3.1 | fait | binaires statiques x86_64 et aarch64, attestations de provenance signées |
| M0 macOS, go/no-go | fait | GO pour Python : plafonds x5 à x50 ; CoreFoundation tue les enfants d'un parent qui l'a utilisé |
| M1 macOS : démon Python et client | fait | Intel x4,4 (aws) à x20,6 (pip), M6 x4,2 à x16,6 ; 16/16 sorties identiques ; matrice verte sur les deux ; règle CoreFoundation (canari) ; empreinte par kqueue |
| M2 macOS : démons Ruby et PHP | fait | rubocop x4,9, rake x8,5, gem x8 (i7) ; phptest x5,7 à x8,9, composer x2,3 (CI) ; suites Ruby et PHP vertes sur Intel et Apple Silicon ; Ruby et Objective-C ; PHP 8.5 non servi |

Décision ouverte : quand le client meurt (`kill -9`), l'enfant va aujourd'hui jusqu'au bout ; lui envoyer SIGHUP serait plus proche du natif, mais moins sûr pour une commande comme `aws s3 rm`.

Décidé : nom **speedrunner**, avec `srr` comme commande courte ; double licence MIT OR Apache-2.0 ; un démon par outil, pas par environnement (fidélité et vitesse d'abord, mémoire mesurée dans le panel).

Suite :

- publier le paquet AUR et le tap Homebrew (prêts dans `packaging/`) ;
- macOS : le démon Python et le client sont portés (M1, §2 quinquies, `bench/results/…-macos-m1-mac/ANALYSE.md`). Ruby et PHP aussi (M2, `bench/results/…-macos-m2-mac/ANALYSE.md`). Restent Node (M3), servir PHP 8.5, et la distribution (binaire universel, formule Homebrew pour macOS), à décider ;
- la JVM, par des caches ou CRIU : voir [multi-runtime.md](multi-runtime.md) ;
- traduire ce document en anglais.

Un nouvel OS a été écarté sur mesure (VPS, hyperfine, 30 runs). Le temps noyau ne fait que 44 ms sur 218 pour `aws --version` et 70 ms sur 304 pour le squelette ec2. Un noyau parfait plafonnerait donc à x1,25, et ce temps noyau vient lui-même surtout de l'initialisation que speedrunner supprime (ouvertures de fichiers, allocations). Pour `wget --version`, le total n'est que de 2,2 ms.
