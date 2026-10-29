# Étape 0 — analyse de la baseline (VPS, 2026-10-28)

Machine : VPS KVM, AMD EPYC-Milan 2 vCPU, 1,8 Go de RAM, Debian 12, Python 3.11.2, awscli 1.46.2 (botocore 1.43.62, vendoré sous `awscli/botocore`). 30 runs par commande, `--warmup 3`, hors ligne (`AWS_EC2_METADATA_DISABLED=true`, pas de fichiers `~/.aws`). Chiffres bruts dans [summary.md](summary.md), [hyperfine.json](hyperfine.json) et [phases.jsonl](phases.jsonl).

## Résultat

| Commande | Natif (médiane) | Démarrage + sortie | Imports | `create_clidriver()` | Exécution |
|---|---|---|---|---|---|
| `aws --version` | 243 ms | 77 ms | 100 ms | 50 ms | 16 ms |
| `aws configure list` | 242 ms | 57 ms | 111 ms | 55 ms | 20 ms |
| `aws ec2 run-instances --generate-cli-skeleton` | 316 ms | 85 ms | 98 ms | 48 ms | 85 ms |

Écart-type natif : 11 à 16 ms. Plancher de l'interpréteur (`python -c pass`) : 7,6 ms. Destruction de l'interpréteur une fois awscli importé : environ 30 ms.

« Démarrage + sortie » est calculé par différence : médiane native moins les phases mesurées dans le processus. Ça inclut le démarrage de l'interpréteur, `site`, la compilation du script et la destruction de l'interpréteur à la sortie. C'est une estimation, puisque les deux séries ne tournent pas en même temps.

## Go/no-go

Critère du brief : imports et initialisation ≥ 50 % du temps.

| Commande | Démarrage + imports | + `create_clidriver()` | Gain max (imports seuls) | Gain max (+ driver) |
|---|---|---|---|---|
| `aws --version` | 73 % | 94 % | x3,7 | x15,7 |
| `aws configure list` | 69 % | 92 % | x3,2 | x12,0 |
| `aws ec2 … --generate-cli-skeleton` | 58 % | 73 % | **x2,4** | x3,7 |

**Formellement : GO sur les trois commandes.**

**Mais le critère de succès (x3 partout en chemin chaud) n'est pas atteignable en ne préchargeant que les imports.** Le « gain max » suppose un coût résiduel nul côté client et fork, donc le vrai gain sera plus bas. Sur `ec2 … --generate-cli-skeleton`, le plafond est x2,4.

Ce qui reste après les imports :

1. **`create_clidriver()`, environ 50 ms par appel.** Il crée la session botocore, lit la config, charge les plugins et construit le `CLIDriver`. Le code dépend de l'env (`AWS_PROFILE`, `AWS_CONFIG_FILE`, `AWS_DATA_PATH`…) et des fichiers `~/.aws/*`. Le précharger impose que l'empreinte couvre ces entrées, ou de séparer ce qui ne dépend pas de l'env.
2. **Le chargement des modèles de service JSON.** Un profil `cProfile` de l'exécution `ec2` donne environ 80 ms sur 117 dans `loaders.load_service_model`, dont environ 48 ms de lecture et de parsing de fichiers. Les caches du `Loader` botocore sont liés à l'instance, et une nouvelle session repart de zéro. Pour les précharger, il faudrait savoir quels services charger dans le parent, par exemple à partir de l'historique d'usage, et ça coûte de la mémoire.
3. **La destruction de l'interpréteur, environ 30 ms**, payée aussi par l'enfant forké. On peut la masquer en renvoyant le code de sortie au client *avant* la destruction, une fois `atexit` passé et les flux vidés. Risque : un outil qui compte sur la destruction pour vider un fichier non fermé. Ça doit faire l'objet d'un test.

## Ce qu'il faut décider avant l'étape 1

Un précharge limité aux imports suffit pour x3 sur `--version` et `configure list`, mais pas sur `ec2 … --generate-cli-skeleton`. Pour x3 partout, et *a fortiori* x10, il faut aussi précharger au moins une partie de `create_clidriver()` et des modèles de service. Ça agrandit le périmètre de l'invalidation : env et fichiers `~/.aws`.
