# Étape P0 — baseline PHP et go/no-go (VPS, 2026-10-28)

PHP 8.2.35 (Debian 12). CLI : composer 2.5.5, phpcs 3.7.1 et phpunit 9.6.7 (paquets Debian, scripts PHP ordinaires), et `artisan` d'un projet Laravel 12.70 (créé avec composer). Scripts : [bench/php_baseline.sh](../../php_baseline.sh) (hyperfine, 20 runs) et [bench/php_ceiling.php](../../php_ceiling.php) (plafond par fork, 20 runs, sans client). Chaque enfant du plafond est vérifié : il doit produire autant de lignes qu'un run natif ([ceiling.txt](ceiling.txt), lignes `check`).

| Commande | Natif | Fork, sans OPcache | Fork, OPcache partagé | Gain plafond |
|---|---|---|---|---|
| `composer --version` | 45 ms | 25,7 ms | 13,5 ms | x3,3 |
| `composer show` (112 paquets) | 105 ms | 81,5 ms | 41,6 ms | x2,5 |
| `artisan --version` | 103 ms | 87,6 ms | 19,5 ms | x5,3 |
| `artisan list` | 120 ms | 98,0 ms | 25,5 ms | x4,7 |
| `phpcs --version` | 25 ms | 5,4 ms | 4,4 ms | x5,7 |
| `phpcs --standard=PSR12` sur un fichier | 44 ms | 25,0 ms | 9,5 ms | x4,6 |
| `phpunit --version` | 28 ms | 6,8 ms | 2,7 ms | x10 |

Démarrage de l'interpréteur : 7,7 ms sans `php.ini`, 21,7 ms avec celui de Debian, qui charge une trentaine d'extensions.

## Décision : GO

Le démarrage et l'initialisation pèsent de 60 à 90 % du temps natif. Deux leviers se cumulent :
- **Le fork** économise le démarrage de PHP et de ses extensions (~20 ms par appel), d'où le gros gain des petites commandes (phpcs, phpunit).
- **OPcache partagé** économise la compilation des fichiers. Il est activé dans le démon ; sa mémoire partagée est héritée au fork, si bien que ce qu'un enfant compile sert aux suivants, comme pour les workers PHP-FPM. C'est lui qui fait le gain de Laravel : `artisan` passe de ~90 à ~20 ms dans l'enfant.

## Pièges trouvés, pour l'étape P1 (démon PHP)

- **Ne rien déclarer dans le parent.** `opcache_compile_file()` « lie tôt » les classes simples dans le parent (early binding). Composer fait ensuite un `require` ordinaire de `autoload_static.php`, et l'enfant meurt sur « Cannot declare class ». Inclure d'avance les fichiers de déclarations casse de la même façon. Le premier prototype mesurait 1,7 ms pour `artisan list`, faute d'avoir vérifié la sortie des enfants. La seule voie sûre : laisser les enfants remplir OPcache.
- **stdin.** phpcs attend un court instant quand stdin n'est pas un terminal (~200 ms). Le démon transmet le stdin du client, donc le comportement natif est conservé. Les benchs doivent seulement fournir `/dev/null`.
- **`dup2`.** PHP n'en a pas, ni dans `posix` ni ailleurs. FFI, intégré au PHP de Debian et utilisable en CLI (`ffi.enable=preload`), donne celui de la libc. Les extensions `pcntl`, `sockets` (`SCM_RIGHTS`) et `posix` sont présentes par défaut.
- **Le shebang** d'un fichier inclus est bien ignoré (PHP 8.2).
- **`php artisan list`** est la forme d'appel la plus courante. Elle lance l'interpréteur `php`, un binaire natif, avec le script en argument : l'aiguillage par nom d'outil ne la voit pas. Il faudra reconnaître « interpréteur + script » (`srr php artisan`, raccourci `php` en mode script), sans quoi seul `./artisan` serait accéléré.
- **Démarrage.** Le démon doit tourner avec `-d opcache.enable_cli=1 -d opcache.validate_timestamps=1 -d opcache.revalidate_freq=0`, pour qu'un fichier modifié soit recompilé, en plus de l'empreinte par inotify.

Pour comparaison, sans démon : `opcache.file_cache` (un cache de compilation sur disque) donnerait une partie du gain d'OPcache aux runs natifs, mais rien sur le démarrage. Ce n'est pas mesuré ici.
