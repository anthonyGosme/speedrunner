# État de l'art : JumpTheGun, quicken, python-preloaded

Étude faite le 2026-10-28 par lecture des sources (clones `--depth 1` des dépôts GitHub). **Rien n'a été exécuté.** Les comportements décrits ci-dessous viennent de la lecture du code et restent à confirmer par des tests sur la machine Linux.

## Synthèse

| | JumpTheGun | quicken | python-preloaded |
|---|---|---|---|
| Version lue | 0.2.0 (`f1a658c`, 2025-12-14) | 0.1.0 + master (`2019-07-07`, abandonné) | main (`2025-06-12`) |
| Licence | **Ambiguë** : `LICENSE` et README = MIT, `pyproject.toml` et classifier PyPI = Apache-2.0. Inclut `filelock` 3.9.0 (Unlicense) | MIT | MIT |
| Taille (hors tests) | ~880 lignes Python + 204 lignes Bash | ~3 100 lignes | ~780 lignes |
| Transport | Socket Unix (0.2.0 ; le README parle encore de TCP) | Socket Unix | Socket Unix |
| Client | Bash | Python | Python |
| Stdio | Multiplexé dans le socket, ligne par ligne | fd passés (`SCM_RIGHTS`, via `multiprocessing.reduction`) | Nouveau PTY par appel, proxy |
| Fork | Après `accept()`, à la demande | `multiprocessing` sur boucle asyncio | Après `accept()`, à la demande |
| Invalidation | Aucune (timeout d'inactivité 4 h) | Version de la lib, gid, groupes, mtime/ctime du script | Aucune |
| Contrôle d'accès | Socket 0600, dossier 0700 | Dossier vérifié (propriétaire + 0700) après `open` | Aucun |
| Env transmis | Oui | Oui | **Non** |
| Code de sortie | Oui, avec bugs (voir plus bas) | Oui | **Non** (toujours 0) |

**Côté licences** : tout est permissif. On peut reprendre des idées et des petits morceaux en gardant les mentions de copyright. Pour JumpTheGun, la licence est ambiguë (MIT ou Apache-2.0). Si on reprend du code, appliquer les deux mentions ou demander à l'auteur. Rien ici n'est assez gros ni assez juste pour mériter une copie : **on réécrit**.

## Constat bloquant pour notre brief : `aws` v1 n'a pas d'entry point

Vérifié dans le `setup.py` d'aws-cli (branche `develop` = v1, version 1.46.2) : `aws` est déclaré via `scripts=['bin/aws', ...]`, **pas** via `entry_points={'console_scripts': ...}`. `importlib.metadata.entry_points()` ne trouvera donc rien pour `aws`. C'est pour cela que JumpTheGun code `aws → awscli.clidriver:main` en dur.

En plus, `bin/aws` exécute du code dépendant de l'environnement *avant* l'import :

```python
if os.environ.get('LC_CTYPE', '') == 'UTF-8':
    os.environ['LC_CTYPE'] = 'en_US.UTF-8'
import awscli.clidriver
```

Si on appelle `awscli.clidriver.main()` directement, cette ligne est sautée et on obtient un écart de comportement silencieux.

**Proposition, à valider** : à l'enrôlement, résoudre le script réellement exécuté (`command -v aws`, shebang pointant vers le Python du venv).
- *Préchargement* : importer les modules dont le script dépend. Pour un script `console_scripts` généré par pip, c'est le module de l'entry point. Pour un `scripts=`, on exécute le script avec `__name__ != "__main__"`.
- *Par requête* : exécuter le script lui-même avec `runpy.run_path(path, run_name="__main__")`. Les imports sont déjà dans `sys.modules`, donc l'appel coûte peu. Le code de haut niveau du script (ici la ligne `LC_CTYPE`) est rejoué avec l'env du client, et `sys.exit(main())` garde sa sémantique native.

Cette approche unifie les deux cas et reste « zéro configuration ». À confirmer par la mesure à l'étape 0, et à faire figurer dans le brief si elle est retenue.

Autre point : awscli v1 exige maintenant **Python ≥ 3.10**. La machine Debian 12 a 3.11.2, ça convient.

## Pièges relevés, et ce qu'on en fait

### 1. Environnement modifié pendant l'import (JumpTheGun)
JumpTheGun calcule le diff de `os.environ` avant/après l'import de l'outil, puis le réapplique sur l'env du client. Notre brief dit seulement « remplace `os.environ` ». Si on se contente de ça, on efface les variables posées par les imports.
→ **Repris.** On capture le diff à l'import et on le réapplique. Test dédié dans la matrice.

### 2. Références à `sys.stdout`/`sys.stderr` figées à l'import
- JumpTheGun remplace `sys.stdout`/`sys.stderr` *pendant* l'import pour que les handlers `logging` créés à ce moment pointent vers ses objets.
- quicken parcourt `logging.Logger.manager.loggerDict` et rebranche les `StreamHandler` qui pointent sur les anciens flux.

Chez nous, `dup2` sur 0/1/2 garde les objets valides, puisque les fd restent les mêmes. Mais les `TextIOWrapper` du démon ont été créés sur un fd non-TTY. Ils sont donc en *block buffering*, alors qu'en natif sur un TTY stdout est *line-buffered*. Conséquence : l'entrelacement stdout/stderr diffère sur un terminal, et une sortie peut être perdue si le processus finit par `os._exit`.
→ **À faire.** Après `dup2`, reconfigurer les flux (`sys.stdout.reconfigure(line_buffering=isatty)`) au lieu de les recréer, pour garder valides les références prises à l'import. Ajouter un test d'entrelacement stdout/stderr sur TTY.

### 3. Sémantique de sortie (JumpTheGun bogué, quicken corrigé)
JumpTheGun fait `exit_code = exc.code if isinstance(exc.code, int) else 1`. Conséquences :
- `sys.exit()` / `sys.exit(None)` donne **1** au lieu de 0 ;
- `sys.exit("message")` donne 1 *sans afficher le message* sur stderr.

quicken corrige le cas `None` (bpo-35727), mais pas le cas de la chaîne.
→ **À faire.** Reproduire exactement la sémantique de CPython. Avec `runpy` + le `sys.exit(main())` du script, on doit passer par le même chemin : `SystemExit` remonte et on l'interprète comme `Py_Exit` (`None` → 0, `int` → code, sinon on affiche sur stderr et on renvoie 1). Il faut aussi lancer `atexit` et vider les flux avant de renvoyer le code. Cas à ajouter à la matrice.

### 4. Transport de stdio : ne pas multiplexer, ne pas faire de PTY
- **JumpTheGun** relit la sortie ligne par ligne en Bash (`read`/`echo`). On perd les octets NUL et le binaire, `isatty()` vaut toujours faux (donc pas de couleurs, mauvaise taille de terminal), et stdin ne se lit qu'en `readline`.
- **python-preloaded** crée un PTY par appel : stdout et stderr sont fusionnés, `tcgetattr(0)` plante quand stdin est un pipe, et l'env n'est pas transmis.

→ **Ça confirme notre choix** : passer les fd 0/1/2 par `SCM_RIGHTS`, comme quicken.

### 5. L'enfant n'a pas de terminal de contrôle
Avec le passage de fd, l'enfant vit dans la session du démon (`setsid`). Il a les fd du TTY, mais pas de *controlling terminal*. Conséquences :
- `open("/dev/tty")` échoue. `getpass` se replie sur stdin et affiche un `GetPassWarning`, et l'écho n'est pas coupé. Or `aws configure`, en mode interactif, lit les clés au clavier.
- Ctrl-C vient du TTY vers le groupe de processus au premier plan, c'est-à-dire le client. Le client doit donc relayer les signaux (c'est déjà prévu).

→ **À ajouter à la matrice** : prompt interactif (`aws configure` sans argument), et outil qui ouvre `/dev/tty`. Si l'écart est inévitable, détecter le cas et basculer en natif.

### 6. Dossier runtime détournable (JumpTheGun)
En repli `/tmp`, JumpTheGun cherche `jumpthegun-$USER-??????` par glob et **réutilise le premier dossier trouvé sans en vérifier le propriétaire**. Un autre utilisateur local peut créer ce dossier à l'avance, remplacer le socket et recevoir l'env du client, donc des identifiants AWS. quicken fait ce qu'il faut : il `open` le dossier, puis `fstat` sur le fd, vérifie `st_uid == getuid()`, `S_ISDIR` et le mode `0700` exact, puis travaille relativement à ce fd.
→ **Repris.** Le schéma de quicken s'applique à `/tmp/speedrunner-$UID/`, côté démon comme côté client Rust. On vérifie aussi `SO_PEERCRED` des deux côtés : le client vérifie que le démon a bien notre UID.

### 7. Course au démarrage (quicken)
quicken prend un verrou inter-processus pendant « connexion ou démarrage ». Sans verrou, 10 invocations à froid en parallèle, ou un `prewarm` zsh simultané à un `run`, lancent plusieurs démons sur le même socket.
→ **À faire.** `flock` sur un fichier du dossier runtime pendant le démarrage du démon. Le test « 10 invocations en parallèle » doit partir d'un démon arrêté.

### 8. Invalidation : crédits du processus
quicken redémarre le démon si le gid ou les groupes du client changent (`newgrp`, ajout à un groupe). Un enfant forké hérite des crédits du *démon*, pas de ceux du client. Il refuse aussi de tourner si réel ≠ effectif ≠ saved uid/gid.
→ **À ajouter à l'empreinte** : uid, gid, groupes supplémentaires et umask. quicken transmet l'umask avec la requête, ce qui manque à notre brief.

### 9. Client qui meurt
quicken tue l'enfant (`SIGKILL`) quand la connexion du client se ferme. JumpTheGun ne fait rien. Le brief ne tranche pas.
→ **Décision à prendre** : si le client Rust meurt (`kill -9`, terminal fermé), on peut envoyer `SIGHUP` à l'enfant, ce qui imite la fermeture du terminal, ou le laisser finir. Pour `aws s3 rm`, laisser finir est plus sûr que tuer à mi-chemin. Ce n'est pas une double exécution dans les deux cas.

### 10. Fork et threads / asyncio
- quicken lance son serveur asyncio *après* avoir figé l'état importé, parce qu'asyncio n'est pas fork-safe (bpo-21998).
- JumpTheGun ne vérifie pas les threads : sa doc dit seulement que ça casse.

→ Ça confirme le parent mono-thread et la vérification `threading.active_count() == 1` du brief. Il faut aussi vérifier, après l'import d'awscli, qu'aucun thread natif ne tourne (`/proc/self/task`), car `threading` ne voit pas les threads C.

### 11. Ramassage des zombies
JumpTheGun et python-preloaded ne font `waitpid(WNOHANG)` qu'à la connexion suivante : les zombies restent tant qu'aucun appel n'arrive.
→ Sur `SIGCHLD` (self-pipe), boucler `waitpid(-1, WNOHANG)`.

## Bug apparent dans le client JumpTheGun 0.2.0 (lu, non exécuté)

Dans `src/jumpthegun.sh` :
- `[[ ! -f "$unique_paths_base.sock" ]]` : `-f` est faux pour un socket, donc on retombe toujours en natif ;
- `exec 3<>"$unique_path_base.sock"` : il manque le `s` du nom de variable, et avec `set -u` ça plante. De toute façon, Bash ne sait pas ouvrir un socket Unix par son chemin.

Si c'est confirmé, le chemin rapide de la 0.2.0 ne s'exécute jamais. Pour une comparaison chiffrée avec JumpTheGun, il faudra mesurer la 0.1.1 (TCP).

## Chiffres publiés (non vérifiés)

- python-preloaded : `import tensorflow` passe de 8,3 s (NFS, cache chaud) à 262 ms.
- quicken renvoie vers des benchs CI Azure. Le lien est probablement mort (projet inactif depuis 2019).

## Ce que ça change pour le brief

1. **§3 Démon** : remplacer « via `importlib.metadata.entry_points()` » par la résolution du script réel, plus `runpy` (voir plus haut). Pour `aws` v1, le mécanisme prévu ne marche pas.
2. **§3 Enfant** : transmettre aussi l'**umask**, réappliquer le **diff d'env pris à l'import**, reconfigurer le **buffering** des flux.
3. **§3 Invalidation** : ajouter uid/gid/groupes à l'empreinte.
4. **§3 Sockets** : vérifier le dossier runtime par `fstat` sur fd (propriétaire, 0700) côté démon *et* côté client. Le client vérifie l'UID du démon.
5. **§5 Matrice** : ajouter sémantique de `SystemExit` (`None`, `int`, `str`), entrelacement stdout/stderr sur TTY, prompt interactif et `/dev/tty`, démarrage concurrent à froid, client tué pendant l'exécution.
6. **§7 Décision ouverte** : comportement de l'enfant quand le client meurt.
