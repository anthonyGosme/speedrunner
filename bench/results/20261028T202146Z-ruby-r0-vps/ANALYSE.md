# Étape R0 — baseline Ruby et go/no-go (VPS, 2026-10-28)

Ruby 3.1.2 (Debian 12), RubyGems 3.3.15. rubocop 1.92.1, rake 13.4.2 et bundler sont installés dans un `GEM_HOME` dédié (`/root/headstart/gems-rb`). Scripts : [bench/ruby_baseline.sh](../../ruby_baseline.sh) (hyperfine, 20 runs) et [bench/ruby_ceiling.rb](../../ruby_ceiling.rb) (plafond par fork, 20 runs, sans client).

## Mesures

| Commande | Natif | Démarrage + `require` de la bibliothèque | Part | Plafond par fork, niveau « appris » | Gain plafond |
|---|---|---|---|---|---|
| `rubocop --version` | 414 ms | 352 ms | 85 % | 87 ms | x4,8 |
| `rubocop --cache false sample.rb` | 730 ms | 357 ms | 49 % | 152 ms | x4,8 |
| `rake --version` | 79 ms | 62 ms | 78 % | 3,5 ms | x22 |
| `bundle --version` | 96 ms | 70 ms | 73 % | 3,9 ms | x25 |
| `gem --version` | 56 ms | 36 ms | 65 % | 2,9 ms | x19 |

Le démarrage nu de l'interpréteur coûte 6,6 ms, et 40 ms avec RubyGems, chargé par défaut.

Plafond par niveau ([ceiling.txt](ceiling.txt)), en millisecondes :

| Commande | Rien de préchargé | + bibliothèque principale | + tout ce qu'un premier run a chargé |
|---|---|---|---|
| `rubocop --version` | 393 | 87 | 87 |
| `rubocop sample.rb` | 699 | 397 | 152 |
| `rake --version` | 28 | 3,6 | 3,5 |
| `bundle --version` | 57 | 22 | 3,9 |
| `gem --version` | 20 | 20 | 2,9 |

## Décision : GO

- Le critère du brief (chargement et initialisation ≥ 50 % du temps) est atteint pour 4 commandes sur 5 au seul `require` de la bibliothèque principale. `rubocop sample.rb` est à 49 %, mais les features chargées paresseusement (cops, analyseur) portent la part réelle à 79 % : le plafond « appris » passe de 730 à 152 ms. L'équivalent Ruby du niveau 2 sera donc indispensable dès R1/R2, comme pour Python.
- Les petites CLI (`rake`, `bundle`, `gem`) tombent à 3–4 ms dans l'enfant : presque tout leur temps est le démarrage de Ruby et de RubyGems. Le plafond ne compte ni la poignée de main (~1,7 ms côté Python) ni le client : on peut viser x10 et plus sur ces commandes.
- Mémoire du parent préchargé : 38 Mo pour rubocop (52 Mo au niveau appris), 16 à 20 Mo pour les autres.

## Constats pour l'étape R1 (démon Ruby)

- **rubocop ferme son `$stdout` en fin de travail.** Le prototype plantait en vidant les flux après coup (`IOError: closed stream`). Le démon devra vider `STDOUT`/`STDERR` et les objets de remplacement, en tolérant qu'ils soient fermés, avant d'envoyer X.
- **rubocop écrit sa sortie à la fin**, au vidage de `$stdout` à la sortie du processus. C'est le même enjeu que pour Python : l'enfant doit tout vider avant d'envoyer X.
- **Le binstub RubyGems** (`Gem.use_gemdeps`, `Gem.activate_bin_path`) doit être rejoué tel quel dans l'enfant, comme `runpy` rejoue le script Python. Le prototype contourne cette étape en chargeant directement l'exécutable de la gem.
- **Le repli du `$LOADED_FEATURES` appris** : le prototype fait `require` des chemins absolus dans l'ordre de `$LOADED_FEATURES`, sans échec ici. L'ordre de ce tableau (fin ou début de chargement) reste à vérifier, à cause du piège rencontré en Python.
