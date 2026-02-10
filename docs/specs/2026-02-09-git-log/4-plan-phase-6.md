# GitLog — Plan de portage — Phase 6

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

---

### Phase 6 : Module Tags

#### Phase 6.1 : Icônes SVG et utilitaires

**Entrée** : `assets/` (icônes SVG), `modules/Tags/utils/formatBranch.ts`
**Sortie** : `assets/` copiés, `formatBranch.ts` déjà copié en phase 1.4

- Copier les fichiers SVG manquants : `branch.svg`, `git.svg`, `merge.svg`, `tag.svg` dans `assets/`. Les fichiers `minus.svg`, `pencil.svg`, `plus.svg` sont **déjà en place** (copiés en phase 5.2 pour `IndexStatus`).
- Vérifier que `formatBranch.ts` est bien en place (phase 1.4) — il est dans `utils/formatBranch.ts` (import direct : `../utils/formatBranch`, pas de barrel `utils/index.ts`)
- **Note sur l'import SVG** : Deux approches distinctes selon le SVG (cf [review d'impact phase 5, point #2](./review-impact-phases-5.md#2-svg-importes-comme-urls-avec-img----acceptable-pour-phase-5-incompatible-pour-phase-6)) :
  - **`branch.svg`, `tag.svg`, `git.svg`** : Ces SVGs utilisent `fill="currentColor"` ou `stroke="currentColor"` et sont colorés dynamiquement dans le code React source (via `fill: textColour` / `stroke: textColour` en inline style). Ils doivent être **inlinés directement dans le `<template>`** de leurs composants Vue respectifs (`BranchIcon.vue`, `TagIcon.vue`, `GitIcon.vue`). Un `<img>` ne permet pas de modifier le `fill`/`stroke` interne d'un SVG. Les fichiers SVG source servent de référence mais le contenu SVG est copié dans le template du composant.
  - **`pencil.svg`, `plus.svg`, `minus.svg`** : Ont des couleurs hardcodées et ne nécessitent pas de coloration dynamique. Ils restent importés comme URLs et rendus via `<img>` (approche déjà en place depuis la phase 5).
  - Le nombre d'instances de ces icônes en page est faible (3 à 9 sur 50 commits, uniquement sur les branch tips, tags et index). L'inline SVG ne pose aucun problème de performance.

**Critère de validation** : Les SVG à coloration dynamique (`branch.svg`, `tag.svg`, `git.svg`) sont rendus inline dans le template et acceptent une coloration via props/styles. `formatBranch` fonctionne.

#### Phase 6.2 : GitLogTags.vue et sous-composants

**Entrée** : `modules/Tags/Tags.tsx`, `modules/Tags/components/` (BranchTag, BranchTagTooltip, BranchLabel, TagLabel, BranchIcon, TagIcon, GitIcon, IndexLabel, Link) + SCSS
**Sortie** : `tags/GitLogTags.vue`, `tags/components/` + SCSS

- `GitLogTags.vue` : Colonne de gauche. Itère sur les commits visibles. Pour chaque commit qui est un branch tip ou a un tag, affiche un `BranchTag`.
- `BranchTag.vue` : Badge compact avec icône (branche ou tag) + label. Tooltip au hover via positionnement CSS (même approche que `CommitNode` en phase 4.6, avec positionnement à droite au lieu de top/bottom).
- `BranchLabel.vue` : Texte formaté du nom de branche (via `formatBranch`).
- `TagLabel.vue` : Texte formaté du nom de tag.
- `BranchIcon.vue`, `TagIcon.vue`, `GitIcon.vue` : Composants d'icônes SVG.
- `IndexLabel.vue` : Label spécial pour le pseudo-commit index.
- `Link.vue` : Lien optionnel vers le provider git distant (utilise la prop `urls`).
- `BranchTagTooltip.vue` : Contenu du tooltip affiché au survol d'un badge de branche/tag.

**Critère de validation** : Les tags/branches s'affichent à côté des bons commits. Les icônes sont visibles. Le tooltip fonctionne.

---

## Tasks tracking

- [ ] Phase 6.1 : Icônes SVG et utilitaires
- [ ] Phase 6.2 : GitLogTags.vue et sous-composants
