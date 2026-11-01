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

- [x] Phase 1.1: Types et constantes
- [x] Phase 1.2: Algorithmes de layout (data/)
- [x] Phase 1.3: GraphMatrixBuilder
- [x] Phase 1.4: Utilitaires purs

---

## Decisions made during implementation

### Phase 1.1

1. **All types merged into a single `types.ts` file.** The volume of types is manageable in one file (~530 lines). The file is organized in clearly delimited sections with comment headers: GitLogEntry, Commit, Theme types, Theme functions, Graph orientation, Graph props, Custom commit node, Custom tooltip, GitLog props, Styling, Pagination, Git index status, URL builder, Table props.

2. **React `CSSProperties` replaced with Vue's `CSSProperties`.** The `CSSProperties` type imported from `'react'` in the source was replaced with `import type { CSSProperties } from 'vue'`. This is used in `GitLogStylingProps.containerStyles` and `GitLogTableStylingProps` (table, thead, tr, td).

3. **React render prop types removed, only slot props interfaces kept.** The React source defines `CustomTooltip`, `CustomCommitNode<T>`, and `CustomTableRow` as function types returning `ReactElement`. Since these become scoped slots in Vue, only the corresponding `*Props` interfaces (`CustomTooltipProps`, `CustomCommitNodeProps<T>`, `CustomTableRowProps`) are kept. The function type aliases are not ported.

4. **`Canvas2DGraphProps` not ported.** The Canvas 2D strategy is explicitly excluded from the port (see portage decisions doc section 12.2), so its props type is not included.

5. **`HTMLGridGraphProps` simplified.** The `node` and `tooltip` props (which were React render props of type `CustomCommitNode<T>` and `CustomTooltip`) are removed from `HTMLGridGraphProps` since they become scoped slots and will be handled at the Vue component level, not as prop types. The remaining typed props (`showCommitNodeHashes`, `showCommitNodeTooltips`, `highlightedBackgroundHeight`) are kept along with the inherited `GraphPropsCommon` fields.

6. **`GitLogPagedProps` not ported.** The `GitLogPaged` variant is explicitly excluded from the port (section 12.2). Only `GitLogProps` and `GitLogCommonProps` are kept.

7. **`ThemeFunctions` interface included.** This interface (from `hooks/useTheme/types.ts`) describes the return type of the `useTheme` composable. It is included in `types.ts` for use by later phases. The `GetCommitNodeColoursArgs` and `CommitNodeColours` helper interfaces are also included.

8. **`GraphPaging` internal type included.** This internal pagination type (from `context/GitContext/types.ts`) is included alongside the public `GitLogPaging` type, as it will be needed by the composables in Phase 2.

9. **Neon aurora colour palettes not included in `types.ts`.** The colour palette arrays (`neonAuroraDarkColours`, `neonAuroraLightColours`) from `hooks/useTheme/types.ts` are runtime values, not types. They belong in Phase 1.4 (`utils/colors.ts`) per the plan, not in the types file.

10. **`CommitFilter` generic default changed from `object` to `unknown`.** In the React source, `CommitFilter<T>` had no default generic parameter. For consistency with `GitLogCommonProps<T = unknown>` where it is used, the default was set to `unknown`.

### Phase 1.2

1. **All 7 files copied verbatim, only import paths adapted.** The files `types.ts`, `computeRelationships.ts`, `temporalTopologicalSort.ts`, `ActiveBranches.ts`, `ActiveNodes.ts`, `GraphDataBuilder.ts`, and `index.ts` were copied from the React source with zero logic changes. Only the import paths were updated to point to the local project structure.

2. **Import path `'types/Commit'` and `'types/GitLogEntry'` replaced with `'../types'`.** The React source uses path aliases (`types/Commit`, `types/GitLogEntry`) resolved by its tsconfig `paths`. In our structure, `Commit`, `GitLogEntry`, and `CommitAuthor` are all in the parent `types.ts` file (created in Phase 1.1), so imports are changed to `import type { Commit } from '../types'` and `import type { Commit, GitLogEntry } from '../types'`.

3. **`import type` used for type-only imports.** Where the source uses `import { Commit } from '...'` for types, the ported files use `import type { Commit } from '...'` to follow TypeScript best practices and ensure no runtime import is generated for type-only references. Value imports (e.g., `ActiveBranches`, `ActiveNodes`, `EdgeType`, `FastPriorityQueue`) remain as regular imports.

4. **`fastpriorityqueue` added as a runtime dependency.** The `ActiveNodes.ts` file depends on `fastpriorityqueue` for its priority queue implementation. This package was added to `frontend/package.json` as a production dependency (`npm install fastpriorityqueue`). The package ships with its own TypeScript declarations (`.d.ts`).

5. **`ActiveBranches.ts` has no external imports.** This file is self-contained pure TypeScript with no imports at all, copied as-is.

6. **`data/types.ts` defines graph-specific types separate from `../types.ts`.** The `data/types.ts` file defines `GraphData<T>`, `GraphEdge`, `CommitNodeLocation`, and `EdgeType` which are internal graph data structures. These are distinct from the component-level types in `../types.ts` and imported from there only for the `Commit` type reference.

### Phase 1.3

1. **All 8 files copied verbatim, only import paths adapted.** The files `types.ts`, `GraphMatrixBuilder.ts`, `GraphMatrix.ts`, `GraphMatrixColumns.ts`, `GraphEdgeRenderer.ts`, `BranchingEdgeRenderer.ts`, `VirtualEdgeRenderer.ts`, and `index.ts` were copied from the React source with zero logic changes. Only the import paths were updated to point to the local project structure.

2. **`GraphColumnState` interface moved into `GraphMatrixBuilder/types.ts`.** In the React source, `GraphColumnState` is defined in `modules/Graph/strategies/Grid/components/GraphColumn/types.ts` (a component types file). Since the `GraphColumn` Vue component does not exist yet (Phase 4), and `GraphColumnState` is a pure data interface with no React dependency, it was placed in `graph/GraphMatrixBuilder/types.ts` alongside the other GraphMatrixBuilder types. This avoids creating a premature component directory just for a type, and keeps the type close to its primary consumers (`GraphMatrixColumns`, `isColumnEmpty`, `getEmptyColumnState`). When the `GraphColumn.vue` component is created in Phase 4, it will import this type from `GraphMatrixBuilder/types`.

3. **Grid utility files placed in `graph/utils/`.** The files `isColumnEmpty.ts` and `getEmptyColumnState.ts` were placed in `graph/utils/` as specified in the plan. Their imports of `GraphColumnState` were updated to point to `../GraphMatrixBuilder/types`.

4. **Import paths follow relative `../../data/types` pattern.** The React source uses path aliases (e.g., `import { CommitNodeLocation } from 'data'`, `import { Commit } from 'types/Commit'`). All imports were converted to relative paths: `../../data/types` for graph data types (`CommitNodeLocation`, `GraphEdge`) and `../../types` for core types (`Commit`).

5. **`import type` used for type-only imports.** Consistent with Phase 1.2, all type-only imports use the `import type` syntax. Value imports (classes like `GraphMatrix`, `GraphMatrixColumns`, `GraphEdgeRenderer`, `BranchingEdgeRenderer`, `VirtualEdgeRenderer`, and functions like `isColumnEmpty`, `getEmptyColumnState`) remain as regular imports since they are used at runtime.

6. **`GraphColumnProps` not included.** The `GraphColumnProps` interface (which references `Commit` and `GraphColumnState`) from the React source's `GraphColumn/types.ts` was not ported. It is a component props type that will be defined when the `GraphColumn.vue` component is created in Phase 4.

### Phase 1.4

1. **All 5 utility files copied verbatim, zero logic changes.** The files `createRainbowTheme.ts`, `colors.ts`, `formatBranch.ts`, `getMergeNodeInnerSize.ts`, and `getColumnBackgroundSize.ts` were copied from the React source with no logic modifications. None of these files had any imports to adapt — they are all self-contained pure TypeScript.

2. **`getMergeNodeInnerSize.ts` and `getColumnBackgroundSize.ts` placed in `graph/utils/`, not `utils/`.** The Phase 1.4 task description says output is `src/components/GitLog/utils/`, but the definitive target file structure (section 15 of the plan index) places these two graph-specific utilities in `graph/utils/` alongside the existing `isColumnEmpty.ts` and `getEmptyColumnState.ts` from Phase 1.3. The section 15 structure was followed as the authoritative reference.

3. **Neon aurora colour palettes extracted into `utils/colors.ts`.** In the React source, `neonAuroraDarkColours` and `neonAuroraLightColours` are defined in `hooks/useTheme/types.ts` alongside type definitions. As decided in Phase 1.1 (decision #9), these runtime values were deferred to Phase 1.4 and are now placed in `utils/colors.ts` as specified in section 15 of the plan.

4. **No barrel `index.ts` created for `utils/`.** The target file structure (section 15) does not specify an `index.ts` re-export file for `utils/`. Consumers will import directly from individual files (e.g., `import { formatBranch } from '../utils/formatBranch'`).

## Resolved questions and doubts

### Phase 1.1

1. **Q: Should `GitLogTableStylingProps` use Vue's `CSSProperties` or a plain `Record` type?** A: Vue's `CSSProperties` from the `vue` package is used, which is the idiomatic choice for inline styles in Vue components. This type maps to `StyleValue` internally and supports both camelCase and kebab-case property names.

2. **Q: Should the `TableProps.row` prop (a React render prop) be included in the Vue types?** A: No. In Vue, custom table rows will be provided via scoped slots, not props. Only the `CustomTableRowProps` interface is kept (describing the slot's scope), not the `CustomTableRow` function type.

3. **Q: Should `GitContextBag` be included in this phase?** A: No. `GitContextBag` is the context bag type used internally by the React context system. It will be defined in Phase 2 when the composables and provide/inject keys are created. The types it depends on (`GraphPaging`, `CommitFilter`, `GitLogStylingProps`, etc.) are all available in `types.ts` for that phase.

### Phase 1.2

1. **Q: Should `GraphDataBuilderProps` be exported from `data/index.ts`?** A: Yes, it is exported via `export * from './GraphDataBuilder'` which re-exports everything from that module, including both the class and the props interface. This is consistent with the React source.

2. **Q: Should the `AncestorFinder` class and `CacheProps` interface in `GraphDataBuilder.ts` be exported?** A: No. They are module-private (not exported) in the React source and remain module-private in the port. They are internal implementation details of the `GraphDataBuilder`.

3. **Q: Does `fastpriorityqueue` need `@types/fastpriorityqueue`?** A: No. The package ships its own TypeScript declarations file (`FastPriorityQueue.d.ts`), so no separate `@types` package is needed.

### Phase 1.3

1. **Q: Where should `GraphColumnState` be defined?** A: In the React source, it lives in `GraphColumn/types.ts` (a component directory). Since GraphColumn.vue does not exist yet, the type was placed in `GraphMatrixBuilder/types.ts` where its primary consumers are. It is exported from the barrel `index.ts` and will be importable by the GraphColumn component when it is created in Phase 4.

2. **Q: Should `GraphColumnProps` be ported in this phase?** A: No. `GraphColumnProps` is a component props type that depends on `Commit` and `GraphColumnState`. It belongs with the `GraphColumn.vue` component in Phase 4, not with the pure TypeScript algorithms.

3. **Q: Should the `VirtualEdgeRendererProps` interface be exported?** A: Yes. It is exported alongside the `VirtualEdgeRenderer` class from `VirtualEdgeRenderer.ts`, consistent with the React source where both are exported from the module. This follows the same pattern as `GraphMatrixBuilderProps` being exported from `types.ts`.

4. **Q: Should `BranchingEdgeRendererProps` be exported?** A: No. In the React source, this interface is module-private (defined in the same file as the class but not exported from `index.ts`). It remains module-private in the port.

### Phase 1.4

1. **Q: Should `getMergeNodeInnerSize.ts` and `getColumnBackgroundSize.ts` go in `utils/` or `graph/utils/`?** A: They go in `graph/utils/` as defined by the target file structure (section 15 of the plan index). These are graph-specific utilities, and the plan index structure groups them with `isColumnEmpty.ts` and `getEmptyColumnState.ts`. The Phase 1.4 task description's generic `utils/` output path was overridden by the more specific section 15 structure.

2. **Q: Do any of the utility files require import path adaptations?** A: No. All 5 files are entirely self-contained with zero imports. `createRainbowTheme.ts` contains only local helper functions (`hslToRgb`). `colors.ts` only defines constant arrays. `formatBranch.ts`, `getMergeNodeInnerSize.ts`, and `getColumnBackgroundSize.ts` are simple pure functions with no dependencies.
