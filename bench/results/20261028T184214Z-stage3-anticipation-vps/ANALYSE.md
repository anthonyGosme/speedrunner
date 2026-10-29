# Étape 3 — anticipation pendant la frappe (VPS, 2026-10-28)

Script : [bench/anticipation.py](../../anticipation.py), 15 runs par case. Le temps est mesuré de la création du processus à sa fin, ce qui ajoute ~1 ms de surcoût Python à chaque ligne. Chaque démarrage de démon réutilise ce que les runs précédents ont appris (niveaux 2 et 3), comme pour un utilisateur. Entre parenthèses : le nombre de runs passés par le chemin rapide.

| Commande | Natif | Démon arrêté | prewarm + 0 ms | prewarm + 100 ms | **prewarm + 300 ms** | Démon chaud |
|---|---|---|---|---|---|---|
| `aws --version` | 230 ± 9 ms | 273 ± 38 (0/15) | 270 ± 28 (0/15) | 165 ± 16 (15/15) | **37 ± 3 (15/15)** | 36 ± 3 |
| `aws configure list` | 239 ± 13 ms | 269 ± 28 (0/15) | 268 ± 34 (0/15) | 168 ± 12 (15/15) | **44 ± 4 (15/15)** | 45 ± 6 |
| `aws ec2 … --generate-cli-skeleton` | 328 ± 22 ms | 343 ± 31 (0/15) | 355 ± 24 (0/15) | 209 ± 18 (15/15) | **69 ± 4 (15/15)** | 68 ± 2 |

Démarrage d'un démon jusqu'au premier spare, avec le cache appris (263 modules, 6 fichiers botocore) : 227–255 ms.

## Lecture

- **Scénario de référence de l'étape 3** (démon arrêté, `prewarm`, puis 300 ms de frappe) : le premier appel est aussi rapide qu'avec un démon déjà chaud. On obtient x6,2 sur `--version`, x5,4 sur `configure list` et x4,8 sur le squelette ec2. Les 300 ms de frappe couvrent le démarrage du démon.
- **Avec 100 ms de frappe**, le démon n'est pas prêt. L'appel attend le premier spare sur le socket, ouvert avant le préchargement, puis passe par le chemin rapide. Il prend 165 ms au lieu de 230 : encore x1,4.
- **Sans délai, ou démon arrêté**, l'appel part en natif pendant que le démon démarre en arrière-plan. Il coûte alors 15 à 40 ms de plus que le natif : sur 2 vCPU, l'outil natif et le démon qui démarre se disputent le processeur. C'est le prix du premier appel après un arrêt (4 h d'inactivité par défaut).

## Coût par frappe du hook zsh ([keystroke.txt](keystroke.txt))

Fonction appelée directement, 20 000 fois. Une fonction vide dans la même boucle coûte 10,0 µs.

| Cas | µs par frappe | Dont le hook |
|---|---|---|
| Premier mot incomplet (`aw`) | 11,7 | ~2 |
| Ligne déjà préchauffée (ligne longue) | 11,9 | ~2 |
| Autre commande (`ls -la /tmp/some/dir`) | 22,0 | ~12 |
| Préfixes `VAR=valeur`, autre commande | 30,7 | ~21 |
| Outil inscrit : déclenche le prewarm (une fois par ligne) | 507 | ~500, le fork |

Aucun fork à chaque touche. Le seul fork, ~0,5 ms, part une fois par ligne, en arrière-plan.

## Bugs trouvés par ce bench, corrigés (58a8a32)

- Le petit-fils qui attendait le verrou de démarrage gardait les fd du client. Un appel capturé (ou un pipeline) attendait alors jusqu'à 3 s sa fin de fichier : un premier run de ce bench mesurait 3 045 ms pour `prewarm + 0`. Test : `Concurrency.test_run_while_daemon_starts_keeps_no_pipe_open`.
- Ce même petit-fils pouvait démarrer un second démon après l'arrêt du premier. Désormais, un socket présent sous un verrou tenu signifie « un démon tourne », et l'absence de socket « un démon sort ».

Après ce bench, aucun démon ne reste. Matrice (39 tests) et tests shell (5) verts sur 3 runs consécutifs.
