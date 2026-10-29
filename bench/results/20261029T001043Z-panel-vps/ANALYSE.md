# Panel étendu, code final (2026-10-29, VPS)

Même panel que `20261028T230354Z-panel-vps`, refait après trois changements : le code de sortie natif est enregistré pour chaque commande, httpie reçoit `--ignore-stdin`, et l'état du terminal lu à l'import est suivi. Machine dans `machine.txt`, tableau dans `summary.md` et dans le README.

- **Sorties : 36 sur 36 identiques au natif**, toutes avec un code de sortie natif 0. Au passage précédent, httpie était mesuré sur son chemin d'erreur (code 1 des deux côtés) : il lisait son corps de requête sur stdin.
- **Coût du suivi du terminal : nul.** Les temps speedrunner restent dans le bruit : médiane −0,3 ms, moyenne −0,1 ms d'une commande à l'autre, hors httpie. Le pty de remplacement ne sert que pendant les imports du démon, et l'enfant ne fait qu'un `isatty` par fd lu à l'import. httpie passe de 19 à 55 ms parce qu'il fait maintenant son vrai travail (requête construite et affichée), x3,6.
- Les autres gains sont inchangés : ansible x14, poetry x15, rdoc x12, solargraph x12, rspec x9, rake x12, bundle x15, aws x5,7.
