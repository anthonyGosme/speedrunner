# Étape 2c — niveau 2, imports appris (VPS, 2026-10-28)

Script : [bench/warm.sh](../../warm.sh), 30 runs, après une passe d'apprentissage (chaque commande deux fois). `before/` : niveau 1 avec spare (4b2041b). `after/` : `HEADSTART_PRELOAD=1,2`.

| Commande | Niveau 1 | Niveau 2 | Gain sur le natif | Plafond du prototype |
|---|---|---|---|---|
| `aws --version` | 95 ± 11 ms | 40 ± 4 ms | x2,31 → x5,39 | x7,4 |
| `aws configure list` | 89 ± 10 ms | 43 ± 3 ms | x2,54 → x5,53 | x6,2 |
| `aws ec2 … --generate-cli-skeleton` | 149 ± 11 ms | 116 ± 12 ms | x2,10 → x2,74 | x3,2 |

Le démon apprend 263 modules dès le premier `aws --version`, sans aucun refus. Les deux autres commandes n'en ajoutent pas : `awscli.handlers` importe toutes les personnalisations. Le démon pèse 46 Mo de RSS, et l'apprentissage prend 117 ms en arrière-plan (sonde comprise).

Découpage ([phases.txt](phases.txt)) : pour `--version`, l'exécution (G → X) tombe à 34 ms. L'écart restant avec le plafond du prototype est le coût du produit : poignée de main, passage des fd, mise à jour de l'env, rejeu du script par `runpy`. Pour ec2, il reste 96 ms d'exécution : c'est le parsing du modèle JSON d'ec2, que vise le niveau 3.

## Comment le niveau 2 apprend

1. L'enfant note, grâce à l'événement d'audit `import`, l'ordre dans lequel chaque import *commence*. `sys.modules` les range dans l'ordre où ils *finissent* (CPython réinsère le module en fin d'exécution), et les rejouer dans cet ordre casse les imports circulaires d'awscli (`awscli.paramfile` ↔ `awscli.shorthand`).
2. Après le X, l'enfant envoie au parent la liste des modules nouveaux.
3. Quand plus aucun enfant ne tourne, le parent fait vérifier la liste par une **sonde** : un enfant jetable qui importe les modules un par un. Il refuse un module qui affiche quelque chose, modifie l'env, lance un thread ou échoue. Après un refus, la sonde s'arrête et une sonde neuve juge la suite, car le module refusé a pu laisser des traces.
4. Le parent importe les modules acceptés, en revérifiant les mêmes critères. Il les persiste ensuite dans `$XDG_CACHE_HOME/headstart/<outil>-<empreinte>.json`, avec la liste noire, puis remplace le spare.
5. Au démarrage, le démon réimporte directement la liste du cache.

## Écart de fidélité trouvé, corrigé pour tous les niveaux

Le code de haut niveau d'un module peut figer une variable d'environnement à l'import. aws en lit 7 pendant ses imports de niveau 1 :

- `AWS_NEW_RETRIES_2026` : botocore calcule `NEW_RETRIES_ENABLED` une fois pour toutes ;
- `HOME` : `JSONFileCache.CACHE_DIR` vaut `expanduser("~/.aws/boto/cache")` ;
- `AWS_DATA_PATH` : modifié à l'import, donc déjà couvert ;
- `LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG` : gettext choisit ses traductions à l'import.

Depuis l'étape 1, un client qui passait une autre valeur que le démon pour l'une de ces variables obtenait un résultat différent du natif. Le démon enregistre désormais les clés lues pendant les imports, en interceptant `os._Environ.__getitem__`. Si la valeur du client diffère, la requête part en exécution native. Test : `FailOpen.test_env_read_at_import_differs`. Il échouait avant la correction : `None` au lieu de `client value`.

Limite : une lecture faite par `os.environ.copy()` ou par `getenv` en C n'est pas vue.

Matrice : 36 tests verts (hors le cas `1,3`, qui attend le niveau 3).
