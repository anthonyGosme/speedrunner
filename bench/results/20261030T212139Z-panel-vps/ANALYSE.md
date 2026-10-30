# Panel du VPS — 20 runs alternés, statistiques complètes

Même panel que `../20261029T132042Z-panel-vps/` ([bench/panel.py](../../panel.py)), avec le code de la v0.2.1 et ses corrections suivantes. Le protocole change :

- 20 runs par commande au lieu de 10 ;
- natif et speedrunner alternés, pour qu'une dérive de la machine pèse sur les deux ;
- tous les temps sont gardés (`panel.json`) : moyenne, médiane, écart-type, min et max dans `summary.md` ;
- le gain (rapport des médianes) a un intervalle de confiance à 95 %, par bootstrap.

## Résultat

38 commandes, **38 sorties identiques au natif**. Gain : moyenne géométrique x5,1, médiane x4,6, de x1,0 (`phpstan analyse`) à x21,7 (`poetry --version`). 32 commandes à x3 ou plus, 10 à x8 ou plus. L'intervalle de confiance le plus large fait 2,2 points (rdoc, x16,9 à x19,0) : les gains annoncés ne tiennent pas au bruit.

Le premier passage s'est bloqué sur `psalm --version` : le démon PHP avait raté la fin d'un enfant (SIGCHLD reçu hors de `poll`), et le client attendait son statut. C'est corrigé (voir docs/ARCHITECTURE.md §11), et ce passage a été refait avec la correction.
