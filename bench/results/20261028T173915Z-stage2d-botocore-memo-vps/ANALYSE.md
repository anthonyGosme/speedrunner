# Étape 2d — niveau 3, fichiers de données botocore (VPS, 2026-10-28)

Script : [bench/warm.sh](../../warm.sh), 30 runs, après la passe d'apprentissage. `before/` : `HEADSTART_PRELOAD=1,2` (b784b59). `after/` : `HEADSTART_PRELOAD=1,2,3`.

| Commande | Niveau 2 | Niveau 3 | Gain sur le natif | Plafond du prototype |
|---|---|---|---|---|
| `aws --version` | 40 ± 5 ms | 47 ± 7 ms, voir plus bas | x6,03 → x4,80 | x7,4 |
| `aws configure list` | 44 ± 3 ms | 46 ± 3 ms | x5,25 → x5,39 | x5,8 |
| `aws ec2 … --generate-cli-skeleton` | 107 ± 10 ms | 77 ± 5 ms | x3,18 → x4,18 | x4,5 |

- **ec2** : l'exécution (G → X) passe de 96 à 70 ms ([phases.txt](phases.txt)). Le parent a parsé à l'avance les 6 fichiers que les enfants ont chargés : le modèle ec2, ses waiters et paginators, `cli.json` et le modèle sdb. Le démon pèse alors 61 Mo de RSS, contre 46 Mo au niveau 2.
- **`--version`** n'a pas reculé. Son exécution reste à 33,5 ms dans les deux cas. En refaisant la mesure (60 runs, démon neuf à chaque ligne, configurations alternées), on obtient 39–43 ms dans les deux cas ([recheck-version.txt](recheck-version.txt)). L'écart du tableau, c'est le bruit du VPS.

## Comment le niveau 3 reste fidèle

- L'adaptateur enveloppe `botocore.loaders.JSONFileLoader._load_file`, et pas `awscli.botocore.*`, qui sont des modules distincts. Le chemin est donc toujours résolu par botocore, ce qui respecte `AWS_DATA_PATH` et l'ordre de recherche.
- Un enfant ne prend une entrée qu'une fois, et seulement si `stat` (mtime_ns, taille, inode, périphérique) correspond toujours. Sinon il relit le fichier. L'objet qu'il reçoit est sa copie privée, héritée du fork : jamais partagé avec un autre appel.
- Le message de debug `Loading JSON file: …` est émis comme en natif, ce qui garde `aws --debug` identique.
- Budget : 16 Mo de JSON source (`HEADSTART_MEMO_MB`), éviction du moins récemment utilisé. La liste des fichiers est persistée avec les modules appris et reparsée au démarrage.

Tests ajoutés : `Preload.test_aws_levels_match_native`, qui compare au natif chaque niveau seul puis combiné (`1`, `2`, `3`, `1,2`, `1,3`, `1,2,3`), et `Preload.test_memo_rereads_modified_data_file`. Ce dernier précharge une copie du modèle sts placée dans `AWS_DATA_PATH`, la modifie sans changer ni l'inode ni la taille, puis vérifie que la sortie suit. Matrice : 37 tests verts sur 3 runs, 5 reportés à l'étape 4.
