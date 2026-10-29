# Usage scripté : `sr aws …` dans une boucle (VPS, 2026-10-28)

Script : [bench/script.sh](../../script.sh), 3 tours ([script.csv](script.csv)). Le démon est arrêté avant chaque boucle headstart, avec le cache appris conservé. Rien n'est tapé, donc rien n'est anticipé : seul le premier appel paie le démarrage du démon.

| Boucle | Natif | `sr aws …` | Gain |
|---|---|---|---|
| 50 × `aws --version` à la suite | 11,3–12,6 s | 2,1–2,5 s | **x5,3** |
| 50 × `aws --version`, 4 en parallèle (`xargs -P 4`) | 7,5–8,2 s | 2,4–2,6 s | **x3,1** |
| 20 × squelette ec2 à la suite | 6,3–6,6 s | 1,8–1,9 s | **x3,6** |

- En séquentiel, le premier appel part en natif pendant que le démon démarre. Les suivants sont au régime chaud.
- En parallèle, le gain plafonne : le VPS n'a que 2 vCPU, et il n'y a qu'un spare à la fois. Les appels simultanés attendent chacun que le parent ait forké le spare suivant.

## Pourquoi `sr`

Les fonctions wrapper de `shell/headstart.zsh` n'existent que dans un zsh interactif. Un script bash, un `Makefile` ou cron lancent le vrai `aws`. `sr` est un lien vers le client : appelé sous ce nom, il équivaut à `headstart run`, sans `run` ni `--`. On préfixe donc la commande, comme avec `time` ou `nice` : `sr aws s3 ls`.

Tests : classe `ScriptPrefix` de la matrice. Elle vérifie que la sortie est identique au natif en chemin rapide forcé, les codes de sortie dans une boucle bash, le stdin passé par `<<<`, et l'usage sans argument.
