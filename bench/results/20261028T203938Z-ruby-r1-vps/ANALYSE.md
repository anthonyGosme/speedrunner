# Étape R1 — démon Ruby, niveau 1 (VPS, 2026-10-28)

Script : [bench/ruby_warm.sh](../../ruby_warm.sh), hyperfine, 20 runs, démons chauds, `HEADSTART_REQUIRE_FAST=1`. Ruby 3.1.2.

| Commande | Natif | headstart | Gain | Plafond R0 (enfant forké, « appris ») |
|---|---|---|---|---|
| `rake --version` | 81,5 ± 9,0 ms | 6,7 ± 1,1 ms | **x12,2** | 3,5 ms |
| `bundle --version` | 99,4 ± 7,8 ms | 21,9 ± 2,3 ms | **x4,5** | 3,9 ms (22 ms au niveau « bibliothèque ») |
| `gem --version` | 61,1 ± 5,9 ms | 18,8 ± 1,8 ms | **x3,2** | 2,9 ms (20 ms au niveau « bibliothèque ») |
| `rubocop --version` | 439 ± 21 ms | 364 ± 25 ms | x1,2 | 87 ms |
| `rubocop --cache false sample.rb` | 764 ± 184 ms | 689 ± 32 ms | x1,1 | 152 ms |

- Le niveau 1 précharge les `require` de haut niveau du binstub, active la gem comme le ferait le binstub (`Gem.activate_bin_path`), puis précharge les `require` de haut niveau de l'exécutable de la gem. Les chiffres suivent le plafond « bibliothèque » de R0.
- **rubocop gagne peu.** Son exécutable ne requiert que `rubocop/server` à haut niveau ; le gros `require 'rubocop'` est dans une condition, et les cops sont chargés à la demande. C'est le rôle de R2 (requires appris), qui devrait rapprocher rubocop de son plafond de 87 ms, et `bundle`/`gem` du leur (3 à 4 ms).

## Fidélité

Matrice Ruby [tests/test_ruby.py](../../../tests/test_ruby.py) : 29 tests verts sur 3 runs consécutifs, avec l'outil de test `rbtest`, une gem installée avec un vrai binstub RubyGems. Elle couvre sorties et codes de sortie (`exit`, `exit(false)`, `abort`, `exit!`, exception, Interrupt), terminal, signaux, contexte, aléa, concurrence, fichiers non fermés, repli natif (env lu ou modifié au chargement, `RUBYOPT`, version de gem en argument `_x.y.z_`), pannes, `gets` et `IO.console.getpass`. La matrice Python (52 tests) et les tests shell (5) restent verts avec le client multi-runtime.

Écarts trouvés et corrigés en route :
- **Ordre de sortie.** Ruby affiche une exception non rattrapée *après* les `at_exit`. Le dernier `at_exit` du démon l'affiche donc lui-même (`full_message`), avant d'envoyer X.
- **Trace d'exception.** Les frames du démon sont retirées, et celles du binstub renommées de `<top (required)>` en `<main>`. La trace est alors identique au natif.
- **Encodages.** `IO#reopen` reprend les encodages de l'IO source : sans correction, `STDIN.external_encoding` passait de UTF-8 à nil.
- **Terminal.** L'enfant n'a pas de terminal : `IO.console` et `File.open("/dev/tty")` sont redirigés vers le `/dev/tty` du client, comme en Python.
- **Test du pipe cassé.** Il était non déterministe en natif (course entre l'écriture et la sortie du lecteur) ; il est maintenant réécrit. Ruby sort en 2, silencieusement, quand une écriture sur stdout échoue en EPIPE.
