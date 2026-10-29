# speedrunner — architecture et reprise du projet

Ce document décrit ce qui est construit, comment les pièces s'emboîtent, où en est le projet et ce qu'il faut savoir pour continuer sans retomber dans les pièges déjà rencontrés. État au 2026-10-28, fin de l'étape 4.

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
client/src/main.rs        le client, Rust, seule dépendance : libc (=0.2.175)
shell/speedrunner.zsh       wrappers et anticipation pour zsh ≥ 5.3
tests/
  test_matrix.py          matrice de non-régression (brief §5), natif contre speedrunner
  test_shell.py           intégration zsh, pilotée dans un vrai pty
  test_ruby.py            matrice Ruby (rbtest), sur le harnais de test_matrix.py
  test_php.py             matrice PHP (phptest, composer, artisan)
  test_scripts.py         activate, exec, mode automatique
  fixture/hstest/         outil de test Python : une sous-commande par comportement
  fixture/rbtest/         outil de test Ruby : une gem avec un vrai binstub
bench/
  baseline.sh, phases.py, summarize.py   étape 0
  preload_ceiling.py      prototype des niveaux de préchargement, sans client
  warm.sh                 natif contre démon chaud (hyperfine)
  phase_client.py         découpe un appel : poignée de main / exécution / destruction
  anticipation.py         démon arrêté, puis prewarm, puis D ms de frappe, puis exécution
  keystroke.zsh           coût du hook zsh par frappe
  script.sh               boucles scriptées avec `srr`
  results/<date>-<étape>-vps/   chaque mesure, avec un ANALYSE.md
```

Le code vit dans le dépôt https://github.com/anthonyGosme/speedrunner.

## 2 bis. Plusieurs runtimes

Le client lit le shebang de l'outil et choisit le démon de son runtime : `server/speedrunner_daemon.py` pour un interpréteur `python*`, `server/speedrunner_daemon.rb` pour `ruby*`. Le démon est lancé par l'interpréteur même de l'outil. Le protocole, le modèle de processus (parent, spare, enfant), le verrou, la sécurité, l'empreinte par inotify et la règle n°1 sont identiques : le démon Ruby est un portage du démon Python, avec les niveaux 1 et 2. Différences propres à Ruby :

- **Niveau 2.** L'enfant apprend les fichiers chargés par `TracePoint(:script_compiled)`, dans l'ordre de *début* de chargement, ainsi que les gems activées. Après X, il les envoie au parent. Celui-ci les fait vérifier par une sonde (mêmes règles qu'en Python), active les gems, requiert les fichiers acceptés par leur chemin absolu, puis met le tout en cache.
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

Pour un nouveau runtime : un démon `server/speedrunner_daemon.<ext>` qui parle ce protocole, une entrée dans `Runtime` (client), un outil de test et une matrice `tests/test_<runtime>.py` qui réutilise le harnais de `test_matrix.py`.

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

Socket de contrôle parent ↔ enfant : `A` (+ fd client : « j'ai accepté »), `L(json)` (« voici ce que j'ai importé et chargé », envoyé après X), `Q` (parent → spare : « retire-toi »).

**Règle n°1 (brief) : jamais de double exécution.** Avant `G`, toute anomalie (pas de socket, refus N, poignée de main ratée, empreinte périmée) mène à `exec` de la vraie CLI. Après `G`, on ne relance jamais : sans X ni W, le client affiche « lost track … not re-running it » et sort en 125. `SPEEDRUNNER_REQUIRE_FAST=1` remplace le repli natif par une sortie en 124, pour les tests et les benches.

## 5. Cycle de vie du démon

1. **Lancement par le client** (`run` qui ne trouve pas de démon, ou `prewarm`). Le client lit d'abord l'état du verrou `<socket>.lock` :
   - verrou libre : il lance ;
   - `running <pid>` : rien à faire ;
   - `exiting <pid>` : il lance un candidat qui n'attendra que la sortie de *ce* pid.

   Double fork. Le petit-fils lâche les fd du client (sinon un pipeline attendrait sa fin), prend le verrou (au plus 3 s d'attente), y écrit `running <pid>`, puis `exec` l'interpréteur du shebang avec `speedrunner_daemon.py --tool --script --socket --lock-fd 3 --origin`. Résultat : un seul démon, quel que soit le nombre de clients concurrents.
2. **Ouverture du socket avant le préchargement.** Un appel qui arrive pendant le démarrage attend le premier spare, au plus 3 s, au lieu de partir en natif. Le dossier runtime est vérifié par `fstat` sur un fd (propriétaire = nous, mode exactement 0700), et le démon travaille ensuite relativement à ce fd. Socket en 0600, umask d'origine rétabli juste après.
3. **Préchargement** (§6), vérification des threads Python et natifs (`/proc/self/task`), calcul de l'empreinte et des watches inotify (§7).
4. **Service.** Boucle du parent : spare, `on_ctl` (A/L), `reap` (W), application de l'apprentissage quand aucun enfant n'est actif.
5. **Arrêt** : `S` (stop), empreinte périmée (SIGUSR1 envoyé par un enfant), fichier surveillé modifié, 4 h d'inactivité (`SPEEDRUNNER_IDLE_TIMEOUT`), ou import appris qui se comporte mal (le démon suivant le met en liste noire). En sortant, il écrit `exiting <pid>` dans le verrou, supprime son socket (seulement si c'est encore le sien, d'après l'inode), puis relâche le verrou. Les spares voient la fin de fichier sur leur socket de contrôle et sortent.
6. **Crash** (`kill -9`) : les enfants actifs finissent et envoient leur X, le spare sort. Le prochain client trouve un socket sans écoute, le relance en arrière-plan et part en natif.

## 6. Préchargement

`SPEEDRUNNER_PRELOAD` (par défaut `1,2,3`) choisit les niveaux, chacun testé seul et combiné.

| Niveau | Mécanisme | Points clés |
|---|---|---|
| 1 | imports de haut niveau du script (AST) | `sys.path[0]` = dossier du script (sauf `-I`/`-P`). Sortie produite à l'import capturée au niveau des fd (memfd) et rejouée à chaque appel. Diff d'env de l'import et clés d'env lues à l'import enregistrés. |
| 2 | imports paresseux appris | L'enfant note l'ordre de **début** des imports (événement d'audit `import`), et non l'ordre de `sys.modules`, qui est celui des fins d'import. Une sonde jetable importe les modules un par un et refuse ceux qui affichent, modifient l'env, lancent un thread ou échouent. Elle repart à neuf après chaque refus. Les modules acceptés sont importés dans le parent, puis le spare est remplacé. |
| 3 | fichiers botocore parsés d'avance | Enveloppe `botocore.loaders.JSONFileLoader._load_file`. Un enfant prend chaque entrée au plus une fois, et seulement si le `stat` correspond. Même message de debug qu'en natif. Budget LRU de 16 Mo de JSON source (`SPEEDRUNNER_MEMO_MB`). Actif seulement si botocore est déjà importé. |
| 4 | instantané du driver (étape 4 bis) | pas commencé |

Persistance : `$XDG_CACHE_HOME/speedrunner/<outil>-<sha256 de l'empreinte>.json` = `{"modules", "blacklist", "files"}`, rechargé au démarrage. Une installation modifiée change l'empreinte, donc le fichier de cache.

Avant chaque fork, `gc.freeze()` : le GC de l'enfant ne parcourt pas le tas hérité, et n'en recopie donc pas les pages.

## 7. Ce que l'enfant vérifie et applique

Avant de répondre `R` (`why_not_fast`), sinon `N` et exécution native :

- l'UID du pair (`SO_PEERCRED`), le gid et les groupes du client identiques à ceux du démon ;
- le chemin du script ;
- l'empreinte : `stat` du script, de l'interpréteur, des dossiers de `sys.path` et des `RECORD`, plus un `select()` sur la file inotify des fichiers de modules ;
- l'env de démarrage : `PYTHON*`, `LC_ALL`, `LC_CTYPE`, `LANG` identiques à ceux du démon ;
- l'env figé à l'import : les clés modifiées *ou lues* par le code de haut niveau doivent avoir la même valeur qu'au démarrage du démon. Exemples réels : botocore fige `AWS_NEW_RETRIES_2026` et un chemin dérivé de `HOME`, et gettext fige la langue.

Après `G` (`apply_request`, puis `run_tool`) :

- `dup2` des fd 0/1/2, `fchdir` sur le cwd du client, umask du client ;
- `os.environ` mis à jour **en place** (`clear()` + `update()`), puis réapplication du diff de l'import ;
- dispositions de signaux natives : SIGINT → KeyboardInterrupt, SIG_IGN hérités, masque vide ;
- buffering de stdout/stdin selon `isatty()`, sans recréer les objets ;
- pont `/dev/tty` : `os.open`, `open` et `io.open` de `/dev/tty` rendent une copie du fd du client ;
- sortie de l'import rejouée, puis `runpy.run_path(script, run_name="__main__")` ;
- sortie à la manière de CPython (`SystemExit` None/int/autre, exception affichée avec la trace limitée aux frames du script, KeyboardInterrupt qui se termine par SIGINT) ;
- dernier `atexit` (enregistré en premier) : vidage des écrivains bufferisés et de stdout/stderr, puis `X`, puis fd 0/1/2 vers `/dev/null`, puis `L`. Si vider stdout échoue, pas de X : la destruction sort en 120, comme en natif.

## 8. Shell et scripts

- `shell/speedrunner.zsh` lit `${XDG_CONFIG_HOME:-~/.config}/speedrunner/tools` et définit `aws() { command speedrunner run aws -- "$@" }` pour chaque outil inscrit, sauf alias ou fonction de l'utilisateur. Une fonction `speedrunner` recharge les wrappers juste après `speedrunner enroll`.
- Anticipation : widget sur `zle-line-pre-redraw`. Une fois par ligne, quand le premier mot (après d'éventuels `VAR=valeur`) est complet et inscrit, il lance `speedrunner prewarm <outil> &!`. Le coût est d'environ 2 µs par frappe dans les cas courants, et l'unique fork (~0,5 ms) part en arrière-plan.
- **Scripts, sans préfixe** : `eval "$(speedrunner activate [outils…])"` en tête du script, ou `speedrunner exec [-t outil]… commande` (y compris en shebang : `#!/usr/bin/env -S speedrunner exec bash`).
  - **Principe.** Un dossier `$XDG_RUNTIME_DIR/speedrunner/bin-<hash>/` contient des liens nommés d'après les outils, qui pointent tous vers le client ; il est placé en tête du `PATH`. Appelé sous le nom d'un outil, le client le sert : `aws args` équivaut à `speedrunner run aws -- args`. Pour trouver le vrai outil, `resolve_in_path` saute tout candidat qui se résout en notre propre binaire, ce qui exclut toute boucle. Le `PATH` étant hérité, `xargs`, `make` et les sous-scripts sont couverts.
  - **Mode automatique**, sans outil nommé : dossier `bin-auto-<hash du PATH>`, un lien par script Python ou Ruby trouvé sur le `PATH`, avec la règle de recherche de `PATH` (la première entrée d'un nom l'emporte). Le dossier n'est reparcouru que si un dossier du `PATH` change : son tampon est dans `.auto`. Les outils inscrits sont préchauffés tout de suite, et ceux que le script appellera plus d'une fois aussi. Pour le savoir, `script::command_counts` ([client/src/script.rs](../client/src/script.rs)) lit le script : celui du shell parent (`/proc/<ppid>/cmdline`, `-c TEXTE` ou le fichier ; à défaut, le fd 255 de bash) pour `activate`, ou celui de la commande pour `exec`.
    - **Lexique.** Un lexer connaît les guillemets, les commentaires, `$(…)` et `` `…` `` (analysés dans leur contexte), `$((…))`, `${…}`, les here-documents (ignorés) et les redirections.
    - **Analyse.** Un parseur suit les positions de commande et l'imbrication : boucles (condition comprise), fonctions avec le nombre de leurs appels (une fonction récursive compte « plusieurs »), `case`, sous-shells, enveloppes (`exec`, `env`, `command`, `nohup`, `srr`), `xargs`, `find -exec`. Il suit aussi les `source` et `.` à chemin littéral, sur 8 niveaux au plus, chaque fichier une seule fois.
    - **Filet de sécurité.** Un raccourci du mode automatique compte ses appels dans `.calls/<outil>` et démarre le démon au deuxième appel, ce qui rattrape ce que l'analyse ne voit pas.
    - **`--eager`** démarre tout outil cité.
    - **Ordre et cadence.** `command_counts` rend les outils dans l'ordre de leur première utilisation : un appel dans une fonction compte au premier appel de celle-ci. `start_daemons` en démarre au plus un par CPU (`SPEEDRUNNER_START_JOBS`). Au-delà, un lanceur détaché, avec ses propres fd, enchaîne : un processus par démon lance `spawn_daemon`, puis attend que son ping réponde, c'est-à-dire que son premier spare existe. L'état du terminal (`--tty`) est calculé avant le fork, sur les fd du script. Un outil atteint avant le démarrage de son démon tourne en natif, et le verrou empêche tout doublon. Sur 2 vCPU, le premier outil a son démon trois fois plus tôt, mais le total reste le même : le CPU de préchargement ne change pas.
- Préfixe, sans inscription : `srr aws s3 ls`. `srr` est un lien vers le client ; appelé sous ce nom, il équivaut à `speedrunner run aws -- s3 ls`. Il marche pour tout outil Python ou Ruby, scripts compris, et le hook zsh préchauffe aussi l'outil qui suit `srr`.
- `speedrunner enroll` refuse ce qui n'est pas un script Python. `status`, `stop` et `prewarm` attendent un démon en cours de démarrage.

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
- Après une modification de `tests/fixture/hstest`, le réinstaller dans `venv-test` depuis une copie (commande dans le brief §7).
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
- Après chaque passage, vérifier qu'aucun démon ne reste : `ps -eo args | grep -c '[h]eadstart_daemon'` doit renvoyer 0.
- Règles du brief : tests d'abord, bench avant/après commité pour toute optimisation, petits commits, validation par l'humain à la fin de chaque étape.

## 10 bis. Distribution

Une release est un seul fichier : le client embarque les trois démons et le hook zsh (`include_bytes!`, `include_str!`).

- **Démons.** Faute de démon trouvé à côté du binaire, le client écrit sa copie embarquée dans `$XDG_DATA_HOME/speedrunner/daemons/<empreinte des trois>/`, sous un nom temporaire, puis la renomme. Une copie déjà présente ne sert que si ses octets correspondent : une mise à jour ne lance jamais le démon d'une autre version. Les dossiers voisins du binaire restent prioritaires, pour le développement : sur le VPS, on modifie `server/` sans recompiler.
- **Hook.** `eval "$(speedrunner init zsh)"` dans `~/.zshrc` remplace `source shell/speedrunner.zsh`. Le hook n'a aucun `return` au niveau global, il supporte donc d'être évalué.
- **Version.** `speedrunner --version`.
- **Releases.** Un tag `v*` lance `.github/workflows/release.yml`. Le workflow vérifie que le tag correspond à la version du `Cargo.toml`, puis compile en musl statique sur un runner x86_64 et sur un runner arm64 natif. Il vérifie ensuite que le binaire est statique et qu'il démarre, et publie `speedrunner-<arch>-unknown-linux-musl.tar.gz`, sa somme `.sha256` et `install.sh` avec `gh release create`. Les noms d'archive ne portent pas de version, pour que `releases/latest/download/` marche.
- **`install.sh`**, en sh POSIX (testé sous dash) : une ligne, `curl -fsSL https://raw.githubusercontent.com/anthonyGosme/speedrunner/main/install.sh | sh`. Il détecte l'architecture, télécharge l'archive et sa somme, refuse si elles ne concordent pas, et installe dans `~/.local/bin` par un nom temporaire puis `mv`, avec le lien `srr`. Il affiche la ligne à ajouter au `.zshrc` sans toucher au fichier. Variables : `SPEEDRUNNER_VERSION`, `SPEEDRUNNER_BIN_DIR`, et `SPEEDRUNNER_BASE_URL` pour le tester contre un serveur local.
- **CI.** `.github/workflows/ci.yml` lance les tests unitaires du client, puis les cinq suites dans l'image de `docker/`. `docker/run.sh tests` rend un code non nul dès qu'une suite échoue.
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
- **`subprocess` avec `timeout`** attend par paliers croissants, plafonnés à 50 ms : une mesure de temps doit attendre sans timeout.
- **Faux serveurs dans les tests.** Un processus forké depuis le lanceur de tests doit finir par `os._exit` quoi qu'il arrive, sinon il exécute le reste de la suite en parallèle. Un faux serveur qui écrit à un client parti meurt de `BrokenPipe`. Dans les deux cas, on obtient un démon fantôme.

## 12. Limites connues

Elles sont listées dans le brief §5 (« Limites connues »). Les principales :

- graine de hachage partagée entre les enfants ;
- pas de terminal de contrôle dans l'enfant : un sous-processus qui ouvre `/dev/tty` échoue ;
- ce que la destruction de l'interpréteur écrit après le X est perdu ;
- lectures d'env via `os.environ.copy()` ou en C non vues ;
- fichiers de données lus à l'import non couverts par l'empreinte ;
- l'enfant va jusqu'au bout si le client est tué par `kill -9`.
- un outil qui lit l'état du terminal à l'import part en natif quand on l'appelle dans un autre état que celui du client qui a lancé son démon (par exemple `outil | less` après un appel au terminal) ; la taille du terminal lue à l'import n'est pas suivie ;
- une horloge lue au chargement est celle du démon ; pour rspec seul, un adaptateur la remet à l'heure du début du run.

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
| 4 bis Instantané du driver (niveau 4) | reporté | propre à aws |
| 5 Bench comparatif et vidéo (vhs ou asciinema) | à faire | |

Décisions ouvertes, côté humain (brief §8) :

- nom : décidé, **speedrunner**, avec `srr` (« speedrunner ») comme commande courte ;
- dépôt public ;
- comportement quand le client meurt.

Décidé : double licence MIT OR Apache-2.0 ; un démon par outil, pas par environnement (fidélité et vitesse d'abord, mémoire mesurée dans le panel).

Pistes au-delà du PoC :

- Ruby, avec la même architecture ;
- Node et la JVM, par des caches ou CRIU : voir [multi-runtime.md](multi-runtime.md) ;
- macOS, en second.

Un nouvel OS a été écarté sur mesure (VPS, hyperfine, 30 runs). Le temps noyau ne fait que 44 ms sur 218 pour `aws --version` et 70 ms sur 304 pour le squelette ec2. Un noyau parfait plafonnerait donc à x1,25, et ce temps noyau vient lui-même surtout de l'initialisation que speedrunner supprime (ouvertures de fichiers, allocations). Pour `wget --version`, le total n'est que de 2,2 ms.
