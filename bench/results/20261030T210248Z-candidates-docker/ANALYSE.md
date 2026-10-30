# Panel des candidats — 20 runs alternés, statistiques complètes

Même panel que `../20261030T182042Z-candidates-docker/` (voir son ANALYSE.md pour la machine, l'image et les outils), avec le protocole de `panel.py` refait : 20 runs alternés, tous les temps gardés, intervalle de confiance à 95 % du gain.

## Résultat

47 commandes, 45 servies, **45 sorties identiques**. Gain : moyenne géométrique x7,3, médiane x7,5, de x1,8 (pint) à x90,5 (`dbt --help`, intervalle x83,8 à x93,2). 38 commandes à x3 ou plus, 21 à x8 ou plus, 15 à x10 ou plus. wp-cli et drush ne sont toujours pas servis (lanceurs shell vers PHP). Ce passage a utilisé le démon PHP d'avant la correction du SIGCHLD manqué, sans s'y bloquer.
