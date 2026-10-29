# Étape R2 — préchargement appris pour Ruby (VPS, 2026-10-28)

Script : [bench/ruby_warm.sh](../../ruby_warm.sh), 20 runs, après une passe d'apprentissage (chaque commande deux fois). `before/` : `HEADSTART_PRELOAD=1`. `after/` : `HEADSTART_PRELOAD=1,2`. Les deux runs se suivent, avec le même binaire.

| Commande | Natif | Niveau 1 | Niveau 2 | Gain | Plafond R0 |
|---|---|---|---|---|---|
| `rubocop --version` | 420–437 ms | 356 ms | **141 ms** | x3,0 | 87 ms |
| `rubocop --cache false sample.rb` | 714–720 ms | 666 ms | **191 ms** | x3,8 | 152 ms |
| `bundle --version` | 87–94 ms | 21 ms | **6,1 ms** | x15,4 | 3,9 ms |
| `gem --version` | 55–56 ms | 17 ms | **6,6 ms** | x8,3 | 2,9 ms |
| `rake --version` | 74–79 ms | 6,2 ms | 6,1 ms | x12,9 | 3,5 ms |

Appris ([after/daemon-status.txt](after/daemon-status.txt)) : 970 fichiers et 19 gems pour rubocop, 46 fichiers pour gem, 19 pour bundle. L'écart de 2 à 3 ms avec le plafond est le prix du produit : client, poignée de main, passage des fd, `load` du binstub.

## Comment Ruby apprend

- L'enfant suit `TracePoint(:script_compiled)` : l'événement se déclenche au *début* du chargement de chaque fichier, que celui-ci vienne de `require`, de `require_relative` ou d'un `autoload`. `$LOADED_FEATURES`, lui, suit l'ordre des fins, et le rejouer casserait les `require` circulaires (le même piège que `sys.modules` en Python). Envelopper `Kernel#require` aurait cassé `require_relative`, qui résout son chemin depuis le fichier appelant.
- Après X, l'enfant envoie au parent les fichiers chargés (chemins absolus, dans l'ordre de début ; les extensions natives `.so` en fin de liste) et les gems activées (nom et version).
- Comme en Python, le parent fait juger les fichiers par une sonde jetable, qui refuse ceux qui affichent, modifient `ENV`, lancent un thread ou échouent, et repart à neuf après chaque refus. Le parent active ensuite les gems et requiert les fichiers acceptés, puis persiste le tout dans `$XDG_CACHE_HOME/headstart/<outil>-<empreinte>.json` et remplace le spare.

## Pourquoi rubocop reste à 141 ms (plafond : 87)

La sonde refuse 3 fichiers, dont `rubocop.rb` lui-même : il charge bundler, et `bundler.rb` écrit au chargement une copie de l'environnement d'origine (`BUNDLER_ORIG_PATH`, `BUNDLER_ORIG_GEM_HOME`…). La règle actuelle (refuser tout fichier appris qui modifie l'environnement) garantit qu'aucune variable n'apparaît plus tôt qu'en natif. Option possible, à décider : accepter ces fichiers en rejouant leur diff d'environnement dans l'enfant, comme au niveau 1. Les variables qu'ils lisent (`PATH`, `GEM_HOME`…) sont déjà vérifiées : un client avec d'autres valeurs partirait en natif. Le gain attendu est de rapprocher rubocop de ses 87 ms ; le prix, une différence d'ordre (des `BUNDLER_ORIG_*` visibles dès le début de l'exécution).

Matrice Ruby : 32 tests verts sur 3 runs (dont 3 nouveaux, qui testent chaque niveau seul et combiné, le refus d'un fichier qui lance un thread ou qui affiche, une variable figée au chargement, et la survie du cache appris à un redémarrage). Python et shell restent verts, sans démon restant.
