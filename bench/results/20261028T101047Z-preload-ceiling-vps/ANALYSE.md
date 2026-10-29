# Plafond du chemin chaud selon le niveau de préchargement (VPS, 2026-10-28)

Script : [bench/preload_ceiling.py](../../preload_ceiling.py). Même machine et même environnement hors ligne que la [baseline](../20261028T100416Z-baseline-vps/ANALYSE.md). 30 runs par commande, 3 de chauffe.

On mesure le temps entre `fork()` dans un parent préchargé et le ramassage de l'enfant. L'enfant exécute `create_clidriver()` puis `driver.main(args)`, stdio sur `/dev/null`, et sort par `os._exit` après avoir vidé ses flux, sans détruire l'interpréteur. **Le prototype n'a ni client, ni socket, ni passage de fd.** C'est donc un plafond, pas le produit.

## Niveaux

- **imports** : seulement les imports de `bin/aws` (`awscli.clidriver`).
- **learned** : en plus, les modules que trois exécutions d'apprentissage (une par commande de référence) ont importés paresseusement. Il y en a 268, dont `awscli.handlers` importé par `load_plugins`. Aucun échec d'import.
- **memo** : en plus, les fichiers JSON de botocore chargés pendant l'apprentissage (6 fichiers, dont le `service-2.json` d'ec2, 4 Mo). Ils sont parsés dans le parent. Chaque enfant consomme une entrée au plus une fois, après vérification `stat` (mtime_ns, taille, inode). Sinon il relit le fichier.

## Résultats (médianes)

| Commande | Natif | imports | learned | memo | Gain max (memo) |
|---|---|---|---|---|---|
| `aws --version` | 243 ms | 80 ms | 33 ms | 33 ms | **x7,4** |
| `aws configure list` | 242 ms | 85 ms | 39 ms | 42 ms | **x5,8** |
| `aws ec2 … --generate-cli-skeleton` | 316 ms | 141 ms | 100 ms | 70 ms | **x4,5** |

Écart-type entre 1 et 8 ms. Les résultats de `learned` et `memo` sur `configure list` sont dans le bruit l'un de l'autre. RSS du parent : 36 Mo après les imports, 43 Mo avec `learned`, 57 Mo avec `memo`.

## Lecture

- **x3 sur les trois commandes devient atteignable**, avec de la marge, dès qu'on précharge les imports paresseux et les modèles JSON. Les deux restent indépendants de l'environnement du client : les imports dépendent des fichiers installés, et le cache JSON est indexé par chemin résolu et `stat`.
- **x10 n'est pas atteint.** Pour `--version`, il reste environ 33 ms dans l'enfant :
  - `create_clidriver()` « chaud », environ 26 ms (session botocore et enregistrement de 368 handlers) ;
  - `list_available_services`, environ 8 ms (859 `listdir`) ;
  - le reste de `main`.

  Ces étapes dépendent de l'env (`AWS_PROFILE`, `AWS_CONFIG_FILE`, `AWS_DATA_PATH`…) et des fichiers `~/.aws`. Pour aller vers x10, il faudrait un instantané du driver, indexé sur les entrées réellement lues (voir le brief, §3).

## Piège découvert

awscli installe un `MetaPathFinder` qui fait de `botocore` un alias de `awscli.botocore`. Mais `import awscli.botocore.loaders` et `import botocore.loaders` donnent **deux objets module distincts**, chargés deux fois depuis le même fichier. Le CLI utilise `botocore.*`. Un patch appliqué à `awscli.botocore.loaders` n'a aucun effet : le premier essai de `memo` n'a mis en cache aucun fichier pour cette raison. Tout adaptateur doit viser le nom de module que le code utilise réellement.
