# Étape L0 : CLI lancées par un lanceur (az, gcloud, Django) — 2026-10-29, VPS

10 runs par commande, médiane, démon chaud. Script : [bench/launchers.sh](../../launchers.sh). Machine dans `machine.txt`.

## Pourquoi ces trois outils

- **az** (Azure CLI) ne gagnait rien dans le panel. Son lanceur pip est un script Python qui pose `AZ_INSTALLER`, puis fait `exec python -m azure.cli`. Le démon précharge donc le lanceur, pas ce qu'il exécute.
- **gcloud** est un script shell. Il exporte `CLOUDSDK_*` et `PYTHONIOENCODING`, puis lance le Python 3.14 embarqué avec `-S lib/gcloud.py`.
- **Django** : `./manage.py` est un script nommé relativement au projet.

## Méthode

speedrunner ne suit pas encore les lanceurs. Côté rapide, on exécute donc un remplaçant de ce que le lanceur exécute : [bench/launchers/az-sr](../../launchers/az-sr) pour az, et pour gcloud une copie de `gcloud.py` avec un shebang, posée dans son `lib/`. Les deux côtés reçoivent l'env que poserait le lanceur.

Côté natif, on lance la vraie commande, lanceur compris. Le gain inclut donc aussi le lanceur, que le vrai chemin rapide sauterait.

« Imports » est la part de la médiane native passée dans les imports de premier niveau, mesurée avec `-X importtime`. Cette mesure gonfle un peu les chiffres : un run de `gcloud --version` dépasse ainsi 100 %.

## Résultats

| Commande | Natif | Imports | speedrunner | Gain |
|---|---|---|---|---|
| `./manage.py --version` | 191 ms | 96 % | 25 ms | **x7,7** |
| `./manage.py check` | 192 ms | 79 % | 36 ms | **x5,4** |
| `./manage.py help` | 172 ms | 78 % | 28 ms | **x6,1** |
| `./manage.py showmigrations` | 208 ms | 79 % | 44 ms | **x4,8** |
| `az version` | 137 ms | 76 % | 75 ms | x1,8 |
| `az vm create --help` | 415 ms | 59 % | 116 ms | **x3,6** |
| `az group list` (non connecté, code 1) | 377 ms | 72 % | 92 ms | **x4,1** |
| `gcloud --version` | 325 ms | 76 % | 192 ms | x1,7 |
| `gcloud config list` | 369 ms | 66 % | 213 ms | x1,7 |
| `gcloud compute instances create --help` | 1503 ms | 46 % | 1023 ms | x1,5 |

Les 13 commandes, y compris celles du passage ci-dessous, donnent les mêmes stdout, stderr et code de sortie qu'en natif (`identity.txt`).

**GO pour les trois.** Les imports pèsent de 46 à 96 % du temps natif. Les gains mesurés vont de x3,6 à x7,7, sauf pour gcloud.

## gcloud : les imports appris ne voient pas son `sys.path`

`gcloud.py` ajoute `lib/` et `lib/third_party/` à `sys.path` au moment où il s'exécute. Le niveau 1, lui, n'importe que ce que le script importe : `os`, `sys` et `contextlib`. Quant à la sonde qui juge les modules appris, elle tourne avec le `sys.path` du démon. Résultat : 515 des 757 modules appris échouent avec `ModuleNotFoundError` et partent en liste noire.

Pour estimer le gain une fois ce problème corrigé, on a refait la mesure avec les deux dossiers dans `PYTHONPATH`, des deux côtés (`GCLOUD_PYTHONPATH=1`, résultats dans `…-gcloud-pythonpath/`) :

| Commande | Natif | speedrunner | Gain |
|---|---|---|---|
| `gcloud --version` | 331 ms | 83 ms | **x4,0** |
| `gcloud config list` | 346 ms | 79 ms | **x4,4** |
| `gcloud compute instances create --help` | 1391 ms | 370 ms | **x3,8** |

Correction envisagée :
1. L'enfant envoie, avec ses modules appris, les entrées que l'exécution a ajoutées à `sys.path`.
2. La sonde et le parent importent avec ces entrées ajoutées le temps de l'import.
3. Ils restaurent ensuite `sys.path`, que le script reposera lui-même.

## Bug trouvé : `./manage.py`

`srr ./manage.py` transmettait le chemin relatif au démon. Or le démon tourne ailleurs : il s'arrêtait au démarrage, chaque appel partait en natif, et deux projets auraient partagé le même nom de démon.

Correction : le démon reçoit le chemin absolu, ce qui donne un démon par projet, et l'outil voit toujours `sys.argv[0] == "./manage.py"`. Le test `RelativeScript` de la matrice couvre ce cas.

## Ce qui manque pour qu'un utilisateur profite des gains d'az et de gcloud

Le client doit suivre le lanceur jusqu'à ce qu'il exécute, dans deux cas :
- un script Python dont le rôle est `os.exec*(sys.executable, …, "-m", module)` (az installé par pip), avec l'env qu'il pose ;
- un script shell qui finit par `exec "$PYTHON" … script.py "$@"` (gcloud, mais aussi l'az des paquets Debian et RPM : `…/bin/python3 -Im azure.cli "$@"`), avec l'env qu'il exporte.

Pas question d'exécuter le code du lanceur dans le démon : effets de bord, double exécution. Il faut le reconnaître, ou l'apprendre d'une exécution native (`/proc/<pid>/cmdline` et `environ` de ce qu'il exécute), puis le contrôler par l'empreinte.
