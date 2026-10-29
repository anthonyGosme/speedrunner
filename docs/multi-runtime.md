# Étendre speedrunner à d'autres runtimes : proposition

Rédigé le 2026-10-28, après l'étape 1 (Python). Rien n'est encore mesuré pour ces runtimes. Les gains ci-dessous sont des ordres de grandeur tirés de l'état de l'art, **à confirmer par une étape 0 par runtime** (go si l'initialisation pèse au moins 50 % du temps, comme pour aws).

## Ce qui se réutilise tel quel

- **Le client Rust et le protocole** (prise en charge, octet « go », passage des fd, relais des signaux, statut de sortie) ne savent rien de Python. Il suffit d'aiguiller sur le shebang ou sur le binaire résolu vers le bon démon : aujourd'hui `read_shebang` n'accepte que `python*`.
- **La matrice de tests** : même harnais. On lui ajoute un outil de test par runtime, l'équivalent de `hstest`.
- **L'anticipation dans le shell** (étape 3) vaut pour tous les runtimes.

## Ce qui ne se réutilise pas : le fork

Le cœur de speedrunner, c'est `fork()` d'un processus déjà initialisé. Ça ne marche que si le runtime est mono-thread au moment du fork. Python l'est, Ruby l'est presque (et sait se réparer après un fork), Node et la JVM ne le sont jamais : ils démarrent des threads internes (GC, compilation, I/O) dès le lancement, et après un `fork()` seul le thread appelant survit. Il faut donc une autre technique pour ces deux-là.

## Runtime par runtime

| | Ruby | Node | JVM | Binaires natifs (wget, curl…) |
|---|---|---|---|---|
| Technique | Fork server, comme Python | Cache de compilation V8, puis instantané V8 ou CRIU | Cache AOT/CDS, puis CRaC ou CRIU | Aucune |
| Précédent | Spring et Zeus (Rails) font déjà précharge + fork | `NODE_COMPILE_CACHE` (Node ≥ 22.1), `--build-snapshot` (Node ≥ 18.8) | AppCDS (`-XX:+AutoCreateSharedArchive`, JDK ≥ 19), cache AOT (JEP 483, JDK ≥ 24), CRaC (builds Azul Zulu, BellSoft Liberica) | — |
| Gain attendu | Du même ordre que Python, x3 et plus si l'initialisation domine | Cache : modeste, ~x1,2 à x1,5. Instantané : élevé, mais pas pour tous les outils | Cache : modeste, ~x1,3 à x2. CRaC : très élevé (Spring Boot 4 s → 38 ms) | Nul : 2,6 ms mesurés pour `wget --version` |
| Zéro configuration ? | Oui | Cache : oui. Instantané : non, il faut empaqueter l'outil | Cache : difficile, voir plus bas. CRaC : non, JDK spécifique | — |
| Coût | Moyen : un démon Ruby d'environ 500 lignes, sans dépendance, qui parle le même protocole | Cache : faible. Instantané : élevé et fragile | Cache : moyen. CRaC : élevé | — |

### Ruby : l'extension naturelle

Même architecture que Python : un démon Ruby (bibliothèque standard) précharge le binstub de l'outil, forke un enfant par appel, reçoit les fd par `UNIXSocket#recv_io` et met à jour `ENV` en place (`ENV.replace`).

Pièges propres à Ruby, à ajouter à la matrice :
- les binstubs RubyGems (`Gem.activate_bin_path`) et `bundle exec` : le préchargement doit activer les mêmes gems que le natif ;
- des gems qui lancent des threads au chargement : même règle qu'en Python, désactivation et repli natif ;
- `at_exit`, `exit!`, `abort`, `SignalException`, `$0` et `$PROGRAM_NAME` ;
- `Process.setproctitle` et les gems qui interrogent leur PID au chargement.

Cibles de référence possibles : `rubocop --version`, `bundle --version`, `pod --version` (CocoaPods).

### Node : un gain sûr mais modeste, puis un saut risqué

1. **Cache de compilation V8** : le client lance le vrai `node` avec `NODE_COMPILE_CACHE` pointant vers un dossier à nous. V8 valide le cache (hash du source, version), donc la sortie ne change pas. Aucun démon nécessaire, sûr, mais le gain est partiel : il évite la compilation, pas l'exécution des modules. Exige Node ≥ 22.1, alors que Debian 12 fournit Node 18.
2. **Instantané V8 du tas initialisé** (`node --snapshot-blob … --build-snapshot`) : chaque appel démarre un processus neuf depuis l'instantané, sans fork. Pour l'outil, il faut l'empaqueter en un seul fichier, et ça ne marche pas avec les modules natifs (`.node`) ni avec certaines API au moment de la capture. On perd le zéro configuration, et une partie des outils ne passera pas.
3. **CRIU** : voir plus bas.

### JVM : le cache est simple sur le papier, l'injection ne l'est pas

1. **Cache AOT ou CDS** : il faut ajouter des options à la ligne de commande `java`. Les variables `JAVA_TOOL_OPTIONS` et `JDK_JAVA_OPTIONS` le permettent, mais la JVM affiche alors `Picked up …` sur stderr, ce qui viole « zéro écart de sortie ». Il faudrait plutôt réécrire l'appel à `java`, alors que les outils JVM (gradle, mvn, sbt…) passent souvent par leurs propres scripts shell. Faisable outil par outil, pas en zéro configuration.
2. **CRaC** : restauration d'un instantané pris après l'initialisation. C'est le gain le plus spectaculaire, mais il faut un JDK CRaC (pas le JDK Debian) et que l'outil supporte la restauration : fichiers et sockets ouverts au moment de l'instantané.

### CRIU : la seule piste vraiment « n'importe quelle commande »

On capture n'importe quel processus après son initialisation, puis on le restaure à chaque appel. C'est indépendant du langage. Mais :
- il faut root ou `CAP_CHECKPOINT_RESTORE` (Linux ≥ 5.9) : inacceptable pour un outil grand public, acceptable sur notre VPS pour un essai ;
- le temps de restauration croît avec la mémoire du processus (des dizaines de ms) ;
- les PID, les fd, le terminal et les variables d'env doivent être recâblés à chaque restauration ;
- l'image contient la mémoire du processus, donc potentiellement des secrets : il faut la protéger comme un fichier sensible.

À réserver à un essai exploratoire, pas au produit.

**Première mesure (VPS, 2026-10-28, CRIU 3.17.1 de Debian 12, root).** Processus Python ayant importé `awscli.clidriver`, 36 Mo de RSS, en attente :

- `criu check` : le noyau 6.1 du VPS a tout ce qu'il faut ;
- capture : 42 ms, image de 25 Mo ;
- restauration (`criu restore -d`) : **~21 ms** de médiane sur 10 essais, de 19 à 40 ms ;
- deux restaurations simultanées de la même image : la seconde échoue (`Can't fork for <pid>: File exists`), parce que CRIU restaure le PID d'origine. Pour servir des appels concurrents, il faudrait un espace de noms PID par restauration.

À titre de comparaison, la prise en charge par fork du démon actuel coûte 2,2 ms : CRIU est environ 10 fois plus lent pour reprendre l'état, mais le reste de l'exécution est identique. Il n'apporte donc rien à Python, ni aux binaires natifs (wget démarre en 2,6 ms, moins qu'une restauration). Il devient intéressant là où on ne peut pas forker et où l'initialisation coûte bien plus de 20 ms : Node, JVM. Pas encore mesuré : la restauration de bout en bout, avec injection d'argv, d'env et des fd du client (`--inherit-fd`).

## Recommandation

1. **Finir Python d'abord** (étape 2, niveau 2 en priorité). C'est ce qui rend le gain général : sans lui, pip ne gagne que x1,2.
2. **Ruby ensuite** : même architecture, gain du même ordre, coût moyen. Commencer par une étape 0 (baseline de `rubocop` et `bundle`).
3. **Node et JVM en « cache seulement »** pour démarrer : gain modeste mais sûr et presque gratuit pour Node, plus délicat pour la JVM. Il faut dire clairement que **x3 n'y est probablement pas atteignable sans instantané** : instantané V8, CRaC ou CRIU.
4. **CRIU en essai exploratoire**, si l'on veut savoir ce que coûte vraiment le « n'importe quelle commande ».
5. **Binaires natifs exclus** : rien à gagner, et le client ajoute 0,7 ms. `speedrunner enroll` doit refuser de les inscrire.

Ce choix change le §2 du brief (Node, JVM/CRaC et CRIU y sont hors périmètre de l'étape 1) : à décider avant de l'y reporter.
