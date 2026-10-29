# Scripts : une ligne d'activation au lieu d'un préfixe par ligne (VPS, 2026-10-28)

Script : [bench/script.sh](../../script.sh), 3 tours ([script.csv](script.csv)), démon arrêté avant chaque boucle speedrunner (cache appris conservé). Durée totale de la boucle :

| Boucle | Natif | `srr aws …` sur chaque ligne | `eval "$(speedrunner activate aws)"` | `eval "$(speedrunner activate)"`, automatique |
|---|---|---|---|---|
| 50 × `aws --version` | 10,6–11,6 s | 2,3–2,6 s | 2,2–2,3 s (une valeur à 3,7 s) | 2,3–2,4 s |
| idem, 4 en parallèle (`xargs -P 4`) | 7,3–8,0 s | 2,4–2,5 s | 2,4–2,6 s | 2,4–2,6 s |
| 20 × `aws ec2 … --generate-cli-skeleton` | 6,4–6,7 s | 1,7–1,9 s | 1,7–1,9 s | 1,9–2,1 s |

- **Une ligne d'activation vaut un préfixe sur chaque ligne** : x4,6 à x5 en séquentiel, x3 en parallèle. Les appels passés par `xargs` sont couverts, puisque c'est le `PATH` qui porte les raccourcis.
- **Le mode automatique** trouve aws seul dans le `PATH`. Il coûte ~0,2 s de plus sur la boucle ec2 : par construction, les deux premiers appels d'un outil partent en natif, et le démon ne démarre qu'au deuxième. Un outil appelé une seule fois ne lance donc aucun démon.
- **Coût de l'activation automatique** : elle parcourt 85 outils Python et Ruby trouvés sur le `PATH` du VPS en 17 ms la première fois, puis en 2 ms grâce au cache (rien n'est reparcouru tant qu'aucun dossier du `PATH` ne change).

Tests : [tests/test_scripts.py](../../../tests/test_scripts.py), 11 tests. Ils vérifient que chaque appel passe par le démon, `xargs` compris, la forme shebang, l'absence de boucle sans démon, le refus des binaires natifs, l'idempotence, la détection automatique limitée aux scripts Python et Ruby, le fait qu'un appel unique ne démarre rien, et que les runs suivants sont rapides.
