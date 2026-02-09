# GitLog — Plan de portage — Phase 6

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

---

### Phase 6 : Module Tags

#### Phase 6.1 : Icônes SVG et utilitaires

**Entrée** : `assets/` (icônes SVG), `modules/Tags/utils/formatBranch.ts`
**Sortie** : `assets/` copiés, `formatBranch.ts` déjà copié en phase 1.4

- Copier les fichiers SVG : `branch.svg`, `git.svg`, `merge.svg`, `tag.svg`, `minus.svg`, `pencil.svg`, `plus.svg`
- Vérifier que `formatBranch.ts` est bien en place (phase 1.4)
- **Note sur l'import SVG** : Dans le code React, les SVG sont importés avec le suffix `?react` de Vite (ex: `import Pencil from 'assets/pencil.svg?react'` dans `IndexStatus.tsx`), ce qui les transforme en composants React. Pour Vue avec Vite, il faudra soit utiliser `vite-svg-loader` (ou un plugin équivalent), soit les convertir manuellement en composants Vue SFC.

**Critère de validation** : Les SVG sont importables comme composants Vue. `formatBranch` fonctionne.

#### Phase 6.2 : GitLogTags.vue et sous-composants

**Entrée** : `modules/Tags/Tags.tsx`, `modules/Tags/components/` (BranchTag, BranchTagTooltip, BranchLabel, TagLabel, BranchIcon, TagIcon, GitIcon, IndexLabel, Link) + SCSS
**Sortie** : `tags/GitLogTags.vue`, `tags/components/` + SCSS

- `GitLogTags.vue` : Colonne de gauche. Itère sur les commits visibles. Pour chaque commit qui est un branch tip ou a un tag, affiche un `BranchTag`.
- `BranchTag.vue` : Badge compact avec icône (branche ou tag) + label. Tooltip au hover via composants WebAwesome (au lieu de `react-tiny-popover`).
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
