# Étape 1 — démon + client, préchargement de niveau 1 (VPS, 2026-10-28)

Script : `bench/stage1.sh`, devenu [bench/warm.sh](../../warm.sh). `HEADSTART_REQUIRE_FAST=1` fait échouer le bench au lieu de mesurer en silence le repli natif. Fork à la demande, pas encore de spare. Le client attend le statut final du parent, qui n'arrive qu'après la destruction de l'interpréteur dans l'enfant.

## Chemin chaud (hyperfine, 30 runs)

| Commande | Natif | headstart | Gain |
|---|---|---|---|
| `aws --version` | 230 ± 12 ms | 132 ± 13 ms | x1,74 |
| `aws configure list` | 231 ± 15 ms | 132 ± 9 ms | x1,75 |
| `aws ec2 … --generate-cli-skeleton` | 301 ± 19 ms | 232 ± 17 ms | x1,30 |

## Découpage du chemin chaud ([phases.txt](phases.txt), [bench/phase_client.py](../../phase_client.py))

| Commande | connect → prêt | exécution (G → X) | destruction (X → W) |
|---|---|---|---|
| `aws --version` | 2,2 ms | 75 ms | 57 ms |
| `aws configure list` | 2,3 ms | 83 ms | 53 ms |
| `aws ec2 … --generate-cli-skeleton` | 2,2 ms | 158 ms | 80 ms |

- La poignée de main coûte 2,2 ms. Ça comprend le fork du démon, environ 40 Mo de RSS, la vérification de l'empreinte et la validation de la requête. Le spare de l'étape 2 sortira le fork du chemin critique.
- L'exécution est conforme au plafond mesuré pour le niveau 1 : 80, 85 et 141 ms.
- **La destruction de l'interpréteur fait 40 % du temps restant.** Le client peut rendre la main dès le code de sortie X, à condition de garantir d'abord ce que la destruction apporte de visible : vider les fichiers non fermés, ce que la matrice teste déjà. Pour `--version`, le temps passerait de 132 à environ 77 ms, soit x3.

## Conformité

Matrice [tests/test_matrix.py](../../../tests/test_matrix.py) : 31 tests verts, 3 runs consécutifs, 5 reportés à l'étape 4.

Limite connue : la graine de hachage (`PYTHONHASHSEED`) est fixée au démarrage de l'interpréteur, donc tous les enfants héritent de celle du démon. L'ordre d'itération des `set` de chaînes est alors le même d'un appel à l'autre, alors qu'il varie en natif. L'aléa de `random` et `uuid` est bien différent d'un appel à l'autre : CPython réensemence `random` après un fork.
