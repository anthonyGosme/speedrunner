# Étape 4 — invalidation sur les fichiers des modules (VPS, 2026-10-28)

## Le trou

Jusqu'ici, l'empreinte couvrait le script, l'interpréteur, les dossiers de `sys.path` et les fichiers `RECORD`. Une modification *sur place* d'un module déjà chargé n'y changeait rien : même inode, même taille, dossier inchangé. C'est le cas d'un éditeur qui réécrit le fichier, ou d'une installation éditable en développement. Le démon servait alors l'ancien code. Test : `Failures.test_stale_after_module_edit`, qui échouait (`v1` au lieu de `v2`).

## Deux façons de le fermer

| Approche | Coût par requête | Mesure |
|---|---|---|
| `stat` de chaque fichier de module à chaque requête | 1,4 ms pour 601 fichiers | poignée de main 1,8 → 3,6–3,7 ms |
| inotify sur ces fichiers, et un `select()` par requête | négligeable | poignée de main 1,6–1,8 ms ([phases.txt](phases.txt)) |

Le brief fixe un budget de moins de 1 ms par vérification. D'où inotify (via `ctypes`, la bibliothèque standard n'a pas de binding). Le démon surveille chaque fichier de module chargé, aux niveaux 1 et 2. Le noyau met l'événement en file pendant l'écriture elle-même, et personne ne lit jamais cette file. Un événement en attente signifie donc « périmé » pour tous : pour l'enfant qui vérifie avant de répondre R, et pour le parent, qui s'arrête aussitôt. Le démon suivant démarre au prochain appel ou au prochain `prewarm`. Un fichier impossible à surveiller (limite de watches atteinte) reste vérifié par `stat` à chaque requête.

Limite : un fichier de données lu au moment de l'import par du code de haut niveau, et qui n'est pas un module, n'est pas couvert.
