# GitLog — Plan de portage — Phase 1

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

---

### Phase 1 : Copier le TypeScript pur

Copier les fichiers sans dépendance React, en les plaçant dans la structure cible Vue. Adapter les chemins d'import internes, mais ne modifier aucune logique.

#### Phase 1.1 : Types et constantes

**Entrée** : Fichiers source du repo `@tomplum/react-git-log` (packages/library/src)
**Sortie** : Fichiers créés dans `src/components/GitLog/`

- Copier `types/GitLogEntry.ts` → `types.ts` (section GitLogEntry)
- Copier `types/Commit.ts` → `types.ts` (section Commit)
- Copier `constants/constants.ts` → `constants.ts`
- Copier les types publics pertinents de `types.ts` (root) — `GitLogProps`, palettes, etc.
- Fusionner tous les types dans un seul `types.ts` ou les organiser en sous-fichiers selon le volume
- Supprimer toute référence à React (`ReactElement`, `CSSProperties` de React, etc.) et adapter vers des types Vue/DOM natifs si nécessaire

**Critère de validation** : `tsc --noEmit` passe sans erreur sur les fichiers créés.

#### Phase 1.2 : Algorithmes de layout (data/)

**Entrée** : `packages/library/src/data/`
**Sortie** : `src/components/GitLog/data/`

- Copier `GraphDataBuilder.ts`, `ActiveBranches.ts`, `ActiveNodes.ts`, `computeRelationships.ts`, `temporalTopologicalSort.ts`, `types.ts`, `index.ts` (fichier de réexport)
- Adapter les imports pour pointer vers les types locaux (phase 1.1)
- Vérifier qu'aucun import React ne subsiste

**Critère de validation** : `tsc --noEmit` passe. Les fichiers n'ont aucune dépendance React.

#### Phase 1.3 : GraphMatrixBuilder

**Entrée** : `packages/library/src/modules/Graph/strategies/Grid/GraphMatrixBuilder/`
**Sortie** : `src/components/GitLog/graph/GraphMatrixBuilder/`

- Copier tous les fichiers : `index.ts` (fichier de réexport), `GraphMatrixBuilder.ts`, `GraphMatrix.ts`, `GraphMatrixColumns.ts`, `GraphEdgeRenderer.ts`, `BranchingEdgeRenderer.ts`, `VirtualEdgeRenderer.ts`, `types.ts`
- Adapter les imports (les types `GraphData`, `GraphEdge`, etc. viennent de `../../data/types`)
- Copier aussi les utilitaires de grid nécessaires : `isColumnEmpty.ts`, `getEmptyColumnState.ts` → destination : `src/components/GitLog/graph/utils/`

**Critère de validation** : `tsc --noEmit` passe. Aucune dépendance React.

#### Phase 1.4 : Utilitaires purs

**Entrée** : Fichiers utilitaires éparpillés dans la lib source
**Sortie** : `src/components/GitLog/utils/`

- Copier `createRainbowTheme.ts` → `utils/createRainbowTheme.ts`
- Copier les palettes prédéfinies (neon-aurora-dark, neon-aurora-light) → `utils/colors.ts`
- Copier `formatBranch.ts` → `utils/formatBranch.ts`
- Copier `getMergeNodeInnerSize.ts` → `utils/getMergeNodeInnerSize.ts`
- Copier `getColumnBackgroundSize.ts` → `utils/getColumnBackgroundSize.ts`
- Adapter les imports

**Critère de validation** : `tsc --noEmit` passe. Tous les fichiers sont du TS pur sans React.

---

## Tasks tracking

- [ ] Phase 1.1: Types et constantes
- [ ] Phase 1.2: Algorithmes de layout (data/)
- [ ] Phase 1.3: GraphMatrixBuilder
- [ ] Phase 1.4: Utilitaires purs
