# Étape 2a — rendre la main dès le code de sortie (VPS, 2026-10-28)

Script : [bench/warm.sh](../../warm.sh), 30 runs. `before/` : étape 1 (fb56a56). `after/` : le client sort dès le message X, et la destruction de l'interpréteur continue dans l'enfant sans bloquer personne. Les deux runs se suivent sur la même machine.

| Commande | Natif | Étape 1 | Sortie anticipée | Gain sur le natif |
|---|---|---|---|---|
| `aws --version` | 215–242 ms | 131 ± 13 ms | 94 ± 7 ms | x1,64 → x2,57 |
| `aws configure list` | 214 ms | 128 ± 8 ms | 93 ± 8 ms | x1,66 → x2,29 |
| `aws ec2 … --generate-cli-skeleton` | 301–319 ms | 219 ± 31 ms | 170 ± 14 ms | x1,38 → x1,87 |

Le natif varie d'un run à l'autre (bruit du VPS) : les gains sont calculés sur le natif du même run.

## Pourquoi 37 ms gagnés et pas 57

Découpage ([phases.txt](phases.txt)) :

- L'exécution (G → X) passe de 75 à 79 ms. Avant d'envoyer X, l'enfant vide lui-même les fichiers que la destruction aurait vidés : il parcourt les objets suivis par le GC, hors ceux gelés dans le démon, et appelle `flush()` sur les écrivains bufferisés. Ce parcours coûte environ 4 ms.
- La destruction (X → W, 43 ms) tourne toujours, mais en arrière-plan. Dans hyperfine, les appels s'enchaînent sans pause : la destruction de l'appel précédent prend l'un des 2 vCPU pendant l'appel suivant. En usage interactif, l'intervalle entre deux commandes est bien plus long.

## Ce que la sortie anticipée change, et ce qui reste identique

- Le code de sortie : si vider stdout ou stderr échoue (lecteur parti), l'enfant n'envoie pas X. Comme en natif, la destruction le signale puis l'interpréteur sort en 120, et le client prend le statut d'attente W. C'est le nouveau cas `test_broken_pipe` de la matrice.
- Après X, l'enfant redirige ses fd 0, 1 et 2 vers `/dev/null`. Un pipeline (`aws … | cat`) voit donc sa fin de fichier sans attendre la destruction.
- Limite nouvelle : ce que la destruction écrirait encore sur stderr (`Exception ignored in …` levé par un `__del__`) est perdu, et ce qu'un `__del__` écrit dans un fichier arrive après le retour du client. Les verrous `flock` détenus par l'outil sont relâchés à la fin de la destruction, pas au retour du client.

Matrice : 32 tests verts sur 3 runs consécutifs, 5 reportés à l'étape 4.
