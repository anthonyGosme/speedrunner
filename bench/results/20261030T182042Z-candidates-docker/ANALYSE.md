# Panel des candidats — outils hors du panel, mesurés sous Docker

Machine : MacBook (Intel i7-9750H), Docker Desktop, conteneur `linux/amd64` Debian 12 (12 vCPU, 12,5 Go). Versions : Python 3.11.2, Ruby 3.1.2 et PHP 8.2. Ce ne sont pas les chiffres du VPS : à comparer entre eux, pas avec le panel principal.

Image : [bench/candidates/Dockerfile](../../candidates/Dockerfile), avec un venv par outil Python et un projet Composer par outil PHP. Commandes : [bench/candidates.py](../../candidates.py), qui reprend le protocole de `panel.py` (10 runs, démon chaud, chemin rapide exigé, sortie comparée au natif). Pour refaire la mesure :

```bash
docker build -t speedrunner-candidates bench/candidates
docker run --rm --platform linux/amd64 -v $PWD/client/target/docker-linux-amd64/speedrunner:/usr/local/bin/speedrunner:ro \
  -v $PWD/bench:/bench:ro -v $PWD/out:/out speedrunner-candidates python3 /bench/candidates.py /usr/local/bin/speedrunner /out
```

## Résultat

47 commandes, sans configuration. 45 passent par le démon, **45 sorties identiques au natif**. Gain médian **x7,7**. 37 commandes sont à x3 ou plus, 22 à x8 ou plus, 15 à x10 ou plus. Tableau complet dans `summary.md`.

| Commande | Natif | speedrunner | Gain |
|---|---|---|---|
| `dbt --help` | 3,1 s | 39 ms | **x80** |
| `copier --version` | 647 ms | 20 ms | x32 |
| `mycli --version` | 627 ms | 23 ms | x27 |
| `airflow version` | 2,7 s | 117 ms | x23 |
| `pgcli --version` | 505 ms | 22 ms | x23 |
| `openstack --version` | 1,2 s | 56 ms | x22 |
| `pipenv --version` | 599 ms | 36 ms | x16 |
| `pylint --version` | 202 ms | 15 ms | x13 |
| `checkov --version` | 3,6 s | 293 ms | x12 (3,3 s gagnées) |
| `cap --version` | 557 ms | 72 ms | x7,7 |
| `jekyll --version` | 748 ms | 106 ms | x7,0 |
| `kamal version` | 717 ms | 147 ms | x4,9 |
| `fastlane --version` | 2,6 s | 721 ms | x3,6 (1,9 s gagnées) |
| `dbt ls` | 4,6 s | 1,3 s | x3,4 (3,2 s gagnées) |
| `php bin/console list` (Symfony) | 158 ms | 40 ms | x3,9 |

Les gains les plus faibles viennent de commandes dont le temps part dans leur propre travail, pas dans le démarrage. C'est le cas de `pylint sample.py` (x2), `standardrb sample.rb` (x2,5), pint (x1,9) et behat (x2,1). oci (x2,9) en fait aussi partie : son module `oci_cli.cli` lit la ligne de commande à l'import, il ne peut donc pas être préchargé.

Non servis :

- **wp-cli et drush.** Le proxy de Composer est un script shell qui appelle, sans `exec`, un second script shell. Ce dernier finit par `exec php`. Le suivi des lanceurs ne va que jusqu'à Python : ce serait une fonctionnalité à part.
- **Retirés du panel.** `lsb_release` est un script shell sous Debian 12. reek échoue aussi en natif sous Ruby 3.1.2, sur une erreur de syntaxe dans dry-schema. Dans les deux cas, le démon a bien basculé en natif.

## Ce que le premier passage a révélé (tout est corrigé)

Au premier passage, 33 commandes sur 50 passaient par le démon, et 2 sorties différaient du natif. Toutes les causes sont corrigées et testées, voir docs/ARCHITECTURE.md §11.

1. **Une clé `bytes` lue dans `os.environ`.** Elle lève `TypeError` en natif, mais le démon la notait comme une lecture d'env, puis échouait au tri des clés. meson, pio et copier partaient en natif ; ils sont maintenant à x9, x13 et x32.
2. **Un apprentissage qui bloquait le démon.** Il durait 93 s pour oci, 28 s pour puppet, et pendant ce temps chaque appel attendait 3 s avant de partir en natif. Désormais, un module qui importe un module refusé est refusé sans le réexécuter, et l'apprentissage se fait par étapes de 0,25 s, entre lesquelles le spare est remplacé. oci apprend maintenant en 6,6 s et checkov en 18 s, sans bloquer aucun appel. `status` affiche `learning=N`, et le panel l'attend.
3. **Un thread lancé au chargement, au niveau 1.** C'est le pool de connexions de sshkit, utilisé par kamal et cap, qui désactivait leur démon. L'import fautif est maintenant sauté, et le niveau 2 apprend le reste : kamal x4,9, cap x7,7.
4. **Une vraie différence de sortie : fastlane.** En natif, `fastlane.rb` pose un alias de module, que `dotenv_helper.rb` rouvre plus tard. Mais `fastlane.rb` est refusé, parce qu'il modifie l'env. Préchargé seul, `dotenv_helper.rb` créait donc un autre module, et l'outil échouait. Le démon Ruby rejoue maintenant l'ordre natif dans un enfant jetable, puis refuse tout fichier qui ouvre d'autres espaces de noms que lors de ce rejeu. fastlane passe à x3,6, avec une sortie identique.
5. **Le panel lui-même.** Les heures précédées d'un code de couleur (`\e[0m17:15:11`, chez dbt et fastlane) n'étaient pas normalisées.
6. **Mon environnement de mesure.** kamal, puppet et jekyll échouaient aussi en natif, parce que `GEM_PATH` excluait les gems fournies avec Ruby. C'est corrigé dans `candidates.py`, et le démon avait bien basculé en natif.

Tests : la matrice passe à 70 tests Python et 37 tests Ruby. Elle est verte sur les 21 cibles de `docker/test-all.sh`. Les nouveaux tests échouent avec les démons de la v0.2.1.
