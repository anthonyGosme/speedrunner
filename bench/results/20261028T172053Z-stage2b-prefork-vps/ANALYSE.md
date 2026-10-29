# Étape 2b — spare pré-forké (VPS, 2026-10-28)

Script : [bench/warm.sh](../../warm.sh), 30 runs. `before/` : fork à la demande (567836a, avec la sortie anticipée). `after/` : un enfant « spare » est forké d'avance et attend sur `accept()`. Quand il prend une connexion, il passe au parent une copie du fd client et le parent forke aussitôt le spare suivant. Le parent appelle `gc.freeze()` avant chaque fork.

| Commande | Fork à la demande | Spare | Gain sur le natif |
|---|---|---|---|
| `aws --version` | 96 ± 7 ms | 82 ± 9 ms | x2,31 → x2,70 |
| `aws configure list` | 98 ± 9 ms | 90 ± 9 ms | x2,22 → x2,46 |
| `aws ec2 … --generate-cli-skeleton` | 175 ± 20 ms | 152 ± 13 ms | x1,74 → x2,18 |

## Découpage de `aws --version` ([phases.txt](phases.txt))

| Phase | Fork à la demande | Spare |
|---|---|---|
| connect → prêt | 2,2 ms | 0,8 ms |
| exécution (G → X) | 84 ms | 79 ms |
| destruction en arrière-plan (X → W) | 46 ms | 23 ms |

- Le fork sort du chemin critique : la poignée de main passe de 2,2 à 0,8 ms.
- Le gain dépasse ces 1,4 ms. C'est `gc.freeze()` : le GC de l'enfant ne parcourt plus le tas hérité, ce qui évite d'en recopier les pages. L'exécution gagne ~5 ms et la destruction est deux fois plus courte. Cette destruction plus courte compte aussi dans hyperfine : elle prend moins de CPU à l'appel suivant.
- Les deux effets ne sont pas mesurés séparément.

## Comportement

- Le parent ne fait plus `accept()` lui-même, sauf en mode désactivé, où il répond sans forker.
- Un spare retiré (mode désactivé, et à l'étape 2c quand le préchargement change) reçoit `Q` sur son socket de contrôle et sort. S'il a pris une connexion entre-temps, il la sert normalement.
- Quand le démon s'arrête, le spare voit la fin de fichier sur son socket de contrôle et sort. Aucun processus ne reste après la matrice.
- Trois spares morts d'affilée sans avoir servi désactivent le chemin rapide, pour éviter une boucle de fork.

Matrice : 32 tests verts sur 3 runs consécutifs.
