# Démarrage à froid : ordre de première utilisation et un démon par CPU (2026-10-29, VPS)

`speedrunner exec ./nightly.sh` avec les démons arrêtés, avec leurs caches appris. Trois runs par version, enregistrés par `RUNS=cold REPEAT=3 bench/demo/record.sh`. « Avant » : le client de `619cb2d`, qui démarre les onze démons d'un coup. « Après » : ils démarrent dans l'ordre où le script les utilise pour la première fois, au plus un par CPU (2 sur le VPS) ; le suivant part dès qu'un démon répond au ping.

| | Run 1 | Run 2 | Run 3 | Médiane |
|---|---:|---:|---:|---:|
| Avant | 7,7 s | 6,8 s | 6,5 s | 6,8 s |
| Après | 7,0 s | 7,0 s | 6,7 s | 7,0 s |

Médianes par outil, dans l'ordre du script :

| Outil | Total avant | Total après | 1er appel avant | 1er appel après |
|---|---:|---:|---:|---:|
| black (le premier) | 1,01 s | 0,55 s | 0,75 s | 0,23 s |
| pytest | 1,13 s | 1,09 s | 0,22 s | 0,08 s |
| poetry | 1,03 s | 1,32 s | 0,29 s | 0,33 s |
| rubocop, rspec, phpunit, artisan, aws, ansible-inventory, http, ansible | identiques à ±0,06 s près | | | |

- **L'ordre fonctionne** : le premier outil du script a son démon trois fois plus tôt.
- **Le total ne bouge pas, et c'est attendu sur 2 vCPU** : le CPU nécessaire au préchargement des onze démons est le même. Ce que black gagne, poetry le perd en partie. Le premier passage « avant » (8,6 / 6,9 / 7,0 s) confirme l'ampleur du bruit.
- Le changement est gardé : il ne coûte rien, il ne touche pas aux sorties, et il améliore le premier appel. Pour réduire vraiment le démarrage à froid, il faudrait que le préchargement lui-même coûte moins (instantané de processus, ou démons laissés en place plus longtemps).
