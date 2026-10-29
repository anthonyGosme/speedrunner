# Panel étendu, après le suivi des lanceurs — 2026-10-29, VPS

Même panel que `../20261029T001043Z-panel-vps/` ([bench/panel.py](../../panel.py)), plus gcloud, avec le code qui suit les lanceurs (étape L1). 10 runs par commande, médiane, démon chaud, sans réglage propre à l'outil. Machine dans `machine.txt`, sortie brute dans `run.log`.

## Résultats

**38 sorties sur 38 identiques au natif** (code de sortie, stdout, stderr). 33 commandes à x3 ou plus, 10 à x8 ou plus. Le tableau du README est généré à partir de `panel.json` (`bench/panel_readme.py`).

Ce qui change par rapport au panel précédent :

| Commande | Avant | Maintenant |
|---|---|---|
| `az --help` | 73 → 76 ms (x1,0) | 74 → 18 ms (**x4,2**) |
| `az vm --help` | 369 → 332 ms (x1,1) | 322 → 65 ms (**x5,0**) |
| `gcloud --version` | absent | 368 → 82 ms (**x4,5**) |
| `gcloud config list` | absent | 387 → 96 ms (**x4,0**) |

Avant, le démon d'az préchargeait le lanceur, qui faisait ensuite `exec python -m azure.cli` : l'enfant devenait un Python natif. Le client suit maintenant le lanceur et sert `-m azure.cli` par un démon. gcloud et `az vm --help` font désormais partie des commandes qui gagnent le plus de temps par appel (257 à 291 ms).

Les autres commandes ne changent pas au-delà du bruit (de x0,1 à x2 selon les cas, dans les deux sens). Seule `phpstan analyse` reste sans gain : son temps se passe dans l'analyse elle-même.

## Mémoire

La colonne « Daemon memory » (PSS du parent et du spare) varie beaucoup d'une passe à l'autre, et dans les deux sens, même pour des démons que ce changement ne touche pas : rubocop passe de 64 à 34 Mo sur `--version`, et de 26 à 82 Mo sur un fichier. Ces écarts ne sont donc pas attribuables au code. Les démons d'az et de gcloud préchargent beaucoup plus qu'avant (1 221 modules pour az, 640 appris pour gcloud) : 39 à 94 Mo.
