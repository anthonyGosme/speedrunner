# macOS — M2 : les démons Ruby et PHP

Question : les démons Ruby et PHP, portés sur macOS, servent-ils à l'identique, et avec quel gain ?

## Gain, chemin chaud

`bench/macos/warm.py` (vrai client, démon chaud, runs alternés, chemin rapide obligatoire).

**Ruby**, sur le portable (macOS 26.7, i7-9750H, Ruby 3.4.1 de Homebrew ; rubocop 1.92.2 et rake 13.4.2 dans un `GEM_HOME` à part), 20 runs :

| Commande | Natif | Servi | Gain |
|---|---:|---:|---:|
| `rubocop --version` | 1 083 ms | 221 ms | x4,9 |
| `rubocop sample.rb` | 2 211 ms | 449 ms | x4,9 |
| `rake --version` | 232 ms | 27 ms | x8,5 |
| `gem --version` | 148 ms | 18,5 ms | x8,0 |
| `bundle --version` | 261 ms | 31 ms | x8,5 |

**PHP**, faute de PHP sur les deux Mac (Homebrew n'a plus de paquets précompilés pour macOS 26 Intel ; le Mac mini n'a pas Homebrew), mesuré sur les runners GitHub, PHP 8.4 de Homebrew, 10 runs : **indicatif**, une machine partagée.

| Commande | Intel (`macos-15-intel`) | Apple Silicon (`macos-26`) |
|---|---|---|
| `phptest argv x` | 123 → 21,5 ms, x5,7 | 88,5 → 9,9 ms, x8,9 |
| `composer --version` | 178 → 77 ms, x2,3 | (pas de composer sur l'image) |

Toutes les sorties identiques au natif. Détail : `warm-ruby-intel.jsonl`, `warm-php-ci-*.jsonl`.

## Fidélité

| Suite | Portable (Intel) | `macos-15-intel` | `macos-26` (Apple Silicon) |
|---|---|---|---|
| `test_ruby.py` | 42 tests, verte | verte (Ruby 3.3) | verte (Ruby 3.4) |
| `test_php.py` | — | 37 tests, 2 sautés (Laravel), verte (PHP 8.4) | verte (PHP 8.4) |
| `test_stress.py`, Ruby compris | ~6 200 appels en 60 s, vert | vert | vert |

Le Mac mini n'a que le Ruby 2.6 du système, que le démon ne sert pas (Ruby ≥ 3.0), et pas de PHP : Apple Silicon est couvert par la CI. Sous Linux (Docker), les suites Ruby et PHP restent vertes (Debian, Alpine, Ruby 3.1 à 3.4, PHP 8.1 à 8.4).

## Ce que M2 a trouvé

- **Ruby et Objective-C.** Un processus Ruby a toujours un second thread (sa minuterie). Après un fork, le runtime Objective-C tue (SIGABRT) l'enfant qui utilise une classe pour la première fois, même si le parent n'a jamais touché CoreFoundation. Vu sur le portable et le Mac mini avec un simple appel à `SCDynamicStoreCopyProxies` dans l'enfant. Avec `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` au lancement du démon (le client le pose, le démon le retire de son env), l'enfant marche, et le canari détecte toujours un parent qui a utilisé CoreFoundation (statut 11, sans rapport de crash). Tests : classe `CoreFoundation` de `test_ruby.py`.
- **Une boucle d'exécution, en Ruby, lance aussi un thread** : le fichier est refusé pour « starts a thread at load » ou « uses CoreFoundation at load », selon ce qui est vu d'abord ; dans les deux cas, il n'est pas gardé.
- **PHP, `fcntl` variadique.** Déclaré par FFI avec un prototype fixe, il recevait n'importe quoi sur l'arm64 d'Apple, qui passe les arguments variadiques sur la pile : chaque spare mourait.
- **PHP 8.5** enregistre `argv` en CLI quoi que dise `register_argc_argv` : `getopt()` aurait lu les arguments du démon. Vu sur le runner Intel (PHP 8.5.9) par `test_getopt`, et reproduit sous Linux avec l'image `php:8.5-cli`. Le démon se désactive sur PHP ≥ 8.5, sous Linux comme sous macOS (sortie juste, en natif). Servir PHP 8.5 demandera de refaire le `$_SERVER` interne dans l'enfant.
- **`script(1)` BSD** : `\r` doublé et écho du ^D, tantôt côté natif, tantôt côté servi ; neutralisés par le harnais.
