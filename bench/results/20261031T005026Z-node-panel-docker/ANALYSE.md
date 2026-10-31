# Panel Node — refait avec le démon corrigé

Même panel que `../20261030T230750Z-node-panel-docker/` (voir son ANALYSE.md pour la machine, l'image et le protocole), après trois corrections du démon trouvées en ajoutant jest et firebase à la démo :

- **La sortie à travers `signal-exit`.** jest charge `signal-exit`, qui enveloppe `process.emit` et `process.reallyExit`. L'enfant ne voyait pas son `process.exit()` et bloquait dans le démontage de Node. Le démon enveloppe désormais ces deux fonctions avant tout préchargement.
- **Les certificats racine.** Node les charge dans un thread à lui, sous un verrou. Un fork fait pendant ce chargement laissait l'enfant (firebase) l'attendre indéfiniment. Le démon attend la fin du chargement avant tout fork.
- **Une sonde bloquée** coûte 3 s par module au lieu de 30 s par sonde.

## Résultat

13 commandes, **13 servies, 13 sorties identiques au natif**. Les gains ne changent pas : eslint x14 (`--version`) et x6,0 (un fichier), next x8,6, firebase x6,7, npm x3,8, jest x3,4. vite est maintenant servi, mais il est plus lent qu'en natif (x0,56). Son bundler lance des threads au chargement : le démon ne peut donc pas le précharger, et chaque appel le recharge, avec un V8 mono-thread.
