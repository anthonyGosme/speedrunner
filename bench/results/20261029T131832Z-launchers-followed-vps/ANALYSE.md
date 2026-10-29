# Étape L1 : le client suit les lanceurs (az, gcloud) — 2026-10-29, VPS

10 runs par commande, médiane, démon chaud. Script : [bench/launchers.sh](../../launchers.sh). Machine dans `machine.txt`. Suite de l'étape L0 (`../20261029T094347Z-launchers-vps/`), qui mesurait un remplaçant de ce que le lanceur exécute.

## Ce qui change

Les deux côtés lancent maintenant la vraie commande : `az …` et `gcloud …` en natif, `speedrunner run az -- …` et `speedrunner run gcloud -- …` côté rapide. Sans réglage propre à l'outil.

- Le client reconnaît un lanceur, c'est-à-dire un script dont la dernière commande est un `exec` (shell) ou un `os.exec…(…)` (Python). Il l'exécute sous ptrace. Quand le lanceur exécute un interpréteur Python, le noyau l'arrête avant la première instruction de celui-ci. Le client lit alors sa ligne de commande, son env, son umask et ses signaux ignorés, puis le tue. Rien de l'outil n'a tourné.
- Ce que le lanceur exécutait (`python … lib/gcloud.py args`, `python -m azure.cli args`) est servi par le démon de cette cible, démarré avec l'env que le lanceur a construit. En repli, le client exécute exactement cette commande : le préambule du lanceur ne tourne jamais deux fois.
- Le démon sait servir `-m module` (az). Les imports appris voient les entrées que l'outil ajoute lui-même à `sys.path` (gcloud) : 640 modules appris, alors que 515 échouaient à l'étape L0.

## Résultats

| Commande | Natif | Imports | speedrunner | Gain |
|---|---|---|---|---|
| `az version` | 138 ms | 74 % | 74 ms | x1,9 |
| `az vm create --help` | 379 ms | 65 % | 91 ms | **x4,2** |
| `az group list` (non connecté, code 1) | 367 ms | 66 % | 86 ms | **x4,3** |
| `gcloud --version` | 357 ms | 72 % | 86 ms | **x4,2** |
| `gcloud config list` | 365 ms | 62 % | 106 ms | **x3,5** |
| `gcloud compute instances create --help` | 1405 ms | 79 % | 388 ms | **x3,6** |
| `./manage.py --version` | 170 ms | 84 % | 29 ms | **x5,8** |
| `./manage.py check` | 185 ms | 74 % | 36 ms | **x5,2** |
| `./manage.py help` | 171 ms | 69 % | 24 ms | **x7,0** |
| `./manage.py showmigrations` | 217 ms | 59 % | 55 ms | **x4,0** |

Les 10 commandes donnent les mêmes stdout, stderr et code de sortie qu'en natif (`identity.txt`).

Comparé à l'estimation de L0 : az fait aussi bien ou mieux (x4,2–x4,3 au lieu de x3,6–x4,1), et gcloud tombe dans la fourchette estimée (x3,5–x4,2 au lieu de x3,8–x4,4). Le préambule du lanceur, qui tourne en natif à chaque appel, coûte 6,6 ms pour gcloud (mesuré en remplaçant son `exec` final par `exit 0`). Pour az, c'est un démarrage de Python : 7,5 ms.

Django n'a pas changé de chemin : ses écarts avec L0 sont le bruit du VPS.

## Ce qui reste

- `az version` ne gagne que x1,9. Les 74 ms restantes se passent dans l'exécution d'az lui-même, pas dans son lanceur.
- gcloud : 137 modules restent en liste noire parce qu'ils lisent la ligne de commande à l'import. Ils sont donc importés à chaque appel.
- Seuls les lanceurs qui finissent par un `exec` d'un interpréteur Python sont suivis. Un lanceur qui lance Python sans `exec`, et reste son parent, part en natif.
