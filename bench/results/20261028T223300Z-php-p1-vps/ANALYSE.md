# Étape P1 — démon PHP (VPS, 2026-10-28)

Script : [bench/php_warm.sh](../../php_warm.sh), hyperfine, 20 runs, démons chauds. Les mesures speedrunner passent par `env SPEEDRUNNER_REQUIRE_FAST=1` (~1 ms de plus) : c'est un biais défavorable à speedrunner.

| Commande | Natif | speedrunner | Gain | Plafond P0 |
|---|---|---|---|---|
| `php artisan --version` (Laravel 12) | 110,8 ± 5,8 ms | 28,5 ± 3,6 ms | **x3,9** | 19,5 ms |
| `php artisan list` | 121,4 ± 6,9 ms | 32,6 ± 4,0 ms | **x3,7** | 25,5 ms |
| `phpunit --version` | 25,1 ± 1,5 ms | 6,9 ± 0,3 ms | **x3,6** | 2,7 ms |
| `composer --version` | 53,1 ± 3,8 ms | 18,0 ± 6,1 ms | x3,0 | 13,5 ms |
| `phpcs --version` | 25,3 ± 1,5 ms | 8,8 ± 0,3 ms | x2,9 | 4,4 ms |
| `phpcs --standard=PSR12` sur un fichier | 44,7 ± 2,3 ms | 15,7 ± 1,3 ms | x2,8 | 9,5 ms |
| `composer show` | 107,2 ± 6,5 ms | 51,6 ± 3,5 ms | x2,1 | 41,6 ms |

L'écart avec le plafond (4 à 9 ms) se décompose ainsi :
- le client et la poignée de main ;
- l'application du contexte : `dup2`, `putenv` et `$_SERVER` ;
- surtout, l'arrêt complet de PHP (fonctions de shutdown, destructeurs, libération de la mémoire). En PHP, rien ne donne le code de sortie avant cet arrêt : le client attend donc le statut d'attente W, sans pouvoir prendre le raccourci X des démons Python et Ruby. C'est la piste d'optimisation suivante.

## Fidélité

Matrice [tests/test_php.py](../../../tests/test_php.py) : 33 tests verts sur 3 runs consécutifs. Les suites Python, Ruby, shell et scripts restent vertes, sans démon restant. Elle couvre :
- sorties et codes de sortie (`exit`, `exit("texte")`, exception non rattrapée chaînée, erreur fatale) ;
- trace d'exception selon `display_errors` et `log_errors` ;
- terminal, signaux, contexte (cwd, umask, `getenv`, `$_SERVER`, `$argv`, variables globales du script, dispositions de signaux) ;
- la forme `php script` (chemin relatif et absolu, `php artisan`) ;
- aléa, concurrence, fichiers non fermés, repli natif (`PHP_INI_SCAN_DIR`), fichier modifié relu, pannes, saisie masquée par `stty`.

Écarts trouvés et corrigés en route :
- **Code de sortie après le gestionnaire d'exception.** Quand un gestionnaire utilisateur a traité une exception, PHP sort en 0, et non en 255. Le gestionnaire du démon termine donc par `exit(255)`, et les fonctions de shutdown s'exécutent encore, comme en natif.
- **`display_errors=stderr`.** L'erreur affichée sur stderr ne commence pas par une ligne vide, contrairement à celle affichée sur stdout.
- **Signal ignoré hérité.** PHP ne le voit pas dans sa table (`pcntl_signal_get_handler` répond 0). Le démon l'ignore donc au niveau de la libc, et non par `pcntl_signal`.
- **Conversions FFI.** Un tableau C se passe en pointeur par `FFI::addr`.

## Limites propres à PHP

- `get_included_files()[0]` est le fichier du démon, et les classes `Speedrunner\*` sont déclarées.
- `ini_get('opcache.enable_cli')` vaut 1, contre 0 en natif.
- `fopen('/dev/tty')` échoue dans l'enfant, faute de terminal de contrôle. PHP ne permet pas de détourner cette ouverture comme en Python et en Ruby, mais `stty` sur stdin fonctionne.
- `php -d …` ou `php -r …` partent en natif.
