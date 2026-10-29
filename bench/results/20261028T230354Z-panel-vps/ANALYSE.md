# Panel étendu — 36 CLI connues, Python, Ruby et PHP (2026-10-28, VPS)

Commande : `RUNS=10 python3 bench/panel.py <client> <dossier>` (machine dans `machine.txt`). Chaque outil est appelé par `speedrunner run <outil> -- …`, comme `srr`, sans aucune configuration. Pour chaque commande, le panel mesure :

- la médiane de 10 runs, en natif puis avec un démon chaud, chemin rapide obligatoire ;
- l'identité de la sortie (code, stdout, stderr) par rapport au natif ;
- la PSS du démon, parent et spare compris.

Tableau complet : `summary.md`, et le README (généré par `bench/panel_readme.py`).

## Résultat

- **Sorties : 36 sur 36 identiques au natif.**
- **Gains : 31 commandes sur 36 à x2 ou plus, 26 à x3 ou plus, 11 à x8 ou plus.**
  - Python, temps gagné par appel : ansible-doc 328 ms, poetry 282 ms (x14), ansible 232 ms (x16), aws 183 à 223 ms (x5,8), httpie 170 ms (x9,9), pip 133 ms (x7,3).
  - Ruby : solargraph 716 ms (x13), rubocop sur un fichier 547 ms (x3,6), pod 344 ms (x8,2), rdoc x17, rspec x12, rake x13, bundle x12.
  - PHP : gains plus faibles en absolu, car le natif est déjà court (OPcache partagé, rien d'exécuté dans le parent). artisan x4,0 à x4,4, rector 108 ms gagnées.
- **Sans gain** :
  - `az` est un script shell qui fait `exec python -m azure.cli`. Seul le démarrage du shell est servi. La forme `python -m` est à traiter plus tard.
  - `phpstan analyse` passe son temps dans l'analyse.
- **Mémoire** : de 12 à 80 Mo par démon ; 30 à 60 Mo en général.

## Écarts trouvés par le premier passage, corrigés

Le premier passage (même jour, non commité) avait cinq sorties différentes. Ses temps étaient aussi faussés par des paliers de 50 ms : `subprocess` avec `timeout` attend par paliers croissants. Les cinq écarts :

| Outil | Cause | Correction |
|---|---|---|
| httpie | `def main(args=sys.argv)` fige la liste à l'import | l'enfant modifie `sys.argv` en place |
| rails | `rails/cli` lance le programme dès son chargement | lectures d'`ARGV` notées pendant les imports ; un `require` de niveau 1 qui le lit va dans `<outil>.skip-<hash>.json`, et le démon redémarre sans lui ; la sonde du niveau 2 le refuse |
| pytest | plugins préchargés avant le crochet de réécriture d'assertions | l'enfant n'apprend que les modules chargés par un loader standard ou un finder du parent |
| psalm | `getopt()` lit la copie interne de `$_SERVER` | démon PHP lancé avec `-d register_argc_argv=0` |
| az | message de premier lancement | run natif d'échauffement dans le panel |

La lecture des arguments est surveillée en Python (`ArgvProbe`, sous-classe de `list`) comme en Ruby (module préfixé sur `ARGV`). Seul `argv[0]` est permis. Fixtures et tests ajoutés : `hsrun` et `rbrun` (programme lancé au chargement), `lazy_argv` (liste noire), `argv-default`, `hook`, `phpgetopt`.

## Un démon par environnement Python ? (`union.txt`, `bench/union.py`)

Mesure faite en important, dans un seul processus, ce que préchargent les 11 démons Python de `venv-panel` : imports de niveau 1, puis modules appris.

| | 11 démons | 1 démon |
|---|---:|---:|
| Mémoire (PSS) | 341 Mo | 131 Mo (−62 %) |
| Fork d'un spare | 1,4 à 3,2 ms | 6,8 ms |
| Imports en échec à cause de l'union | — | 0 |

Pip échoue sur 10 modules seul comme dans l'union : ce n'est pas un conflit, son pip vient d'un autre venv.

- **Coût en temps.** Un fork plus long ne touche pas l'appel servi, puisque le spare est forké d'avance. Il ne retarde que l'appel suivant, quand deux appels se suivent à moins de ~7 ms. Ce n'est pas le cas en interactif, ni dans une boucle de script, dont les appels durent eux-mêmes plus de 10 ms.
- **Risque de fidélité.** Le vrai risque est ailleurs : les modules de l'outil A seraient présents pour l'outil B (patchs à l'import, filtres `warnings`, configuration `logging`). De plus, les clés d'env figées à l'import par l'un des outils enverraient en natif les appels de tous les outils.
- **À trancher.** Mémoire divisée par 2,6 contre ce risque, que seul un panel de fidélité sur l'union mesurerait.
