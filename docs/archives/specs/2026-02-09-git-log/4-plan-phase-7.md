# GitLog — Plan de portage — Phase 7

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

## SUPPRIMÉE

**Raison** : La phase 4.6 a implémenté les tooltips du `CommitNode` via un positionnement CSS interne (div absolue contrôlée par un ref `showTooltip`). Cette approche a été validée post-review phase 4. Les tooltips de `BranchTag` (phase 6.2) utiliseront la même approche CSS.

La question d'une éventuelle migration vers les composants WebAwesome pour les tooltips sera réévaluée une fois le portage complet terminé, au vu du rendu réel.

**Contenu déjà couvert par d'autres phases** :
- `CommitNodeTooltip.vue` (contenu par défaut du tooltip de commit) : implémenté en phase 4.2/4.6
- Tooltip de `BranchTag` : sera implémenté en phase 6.2 avec l'approche CSS
- Scoped slot `#tooltip` pour custom render : fonctionnel depuis la phase 4.6

---

## Tasks tracking

- [x] ~~Phase 7.1 : Audit et remplacement des tooltips~~ SUPPRIMÉE
- [x] ~~Phase 7.2 : Tooltip par défaut du commit~~ SUPPRIMÉE (déjà implémenté en phase 4)
