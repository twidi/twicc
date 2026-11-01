# GitLog — Plan de portage — Phase 5 — **COMPLETED**

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

---

### Phase 5 : Module Table

#### Phase 5.1 : GitLogTable.vue et TableRow.vue (structure)

**Entrée** : `modules/Table/Table.tsx`, `modules/Table/components/TableContainer/`, `modules/Table/components/TableRow/` + SCSS + contexte
**Sortie** : `table/GitLogTable.vue`, `table/components/TableRow.vue` + SCSS

- `GitLogTable.vue` :
  - `provide(TABLE_CONTEXT_KEY, { timestampFormat })` pour le format de date (défaut: `'YYYY-MM-DD HH:mm:ss'`)
  - Conteneur principal qui wrape `TableContainer`
  - Props : `timestampFormat`, `className`, `styles` (objet `GitLogTableStylingProps` avec `table`, `thead`, `tr`, `td` en CSSProperties)
  - **Imports depuis la phase 1** : `TableProps`, `GitLogTableStylingProps`, `CustomTableRowProps` sont dans `../../types.ts`. Le `CSSProperties` utilisé dans `GitLogTableStylingProps` est celui de Vue (décision phase 1.1 #2), compatible avec `:style="..."`. Le type function `CustomTableRow` (React render prop) n'a **pas** été porté (décision phase 1.1 #3) — seule l'interface `CustomTableRowProps` existe pour typer le scoped slot.
  - Supporte le scoped slot `#row` pour le custom render de ligne complète (typé avec `CustomTableRowProps`)
  - Applique `TABLE_MARGIN_TOP` (12px) et `HEADER_ROW_HEIGHT` (47px) pour l'alignement avec le graphe
  - **Note importante sur les en-têtes** : Les en-têtes du tableau ("Commit message", "Author", "Timestamp") sont rendus **dans le composant `Table`** lui-même (dans le code source React, `Table.tsx` lignes 50-83), et non dans le `Layout`. C'est différent des en-têtes "Branch / Tag" et "Graph" qui eux sont rendus dans `Layout.tsx`. Le composant `GitLogTable.vue` devra reproduire ce comportement
  - **Note** : `Table.tsx` utilise le hook `usePlaceholderData()` pour afficher des lignes de table placeholder quand il n'y a pas de données. Le graphe (`HTMLGridGraph.tsx`) utilise aussi des données de placeholder, mais via un import direct du fichier `data.ts` (pas via le hook)
  - **Note sur le filtrage** : Contrairement à `GraphCore.tsx` et `Tags.tsx`, `Table.tsx` ne réapplique **PAS** le filtre sur les commits. Il utilise directement les commits de `graphData` (déjà filtrés dans `GitLogCore`) slicés par la pagination. C'est un comportement cohérent mais distinct des deux autres modules.
- `TableContainer.vue` :
  - Composant intermédiaire (existe dans le source avec son propre SCSS module `TableContainer.module.scss`)
  - Itère sur les commits visibles et rend un `TableRow` par commit
- `TableRow.vue` :
  - Reçoit un `commit` en prop + `selected`, `previewed`, `backgroundColour`
  - Si le scoped slot `#row` est fourni : rend le slot avec les données exposées
  - Sinon : rend le layout par défaut (message | auteur | date)
  - Événements : `@click` → sélection, `@mouseenter`/`@mouseleave` → preview
  - **Note** : Pas de `cloneElement` — les handlers sont sur le wrapper `<div>` du row, pas injectés dans le slot

**Critère de validation** : Le tableau est rendu avec les bonnes lignes. Le scoped slot fonctionne.

#### Phase 5.2 : Sous-composants de cellules (données)

**Entrée** : `CommitMessageData`, `AuthorData`, `TimestampData`, `IndexStatus` (composants React)
**Sortie** : Composants Vue correspondants dans `table/components/`

- `CommitMessageData.vue` : Affiche le message du commit. Tronque si trop long. Affiche le hash en résumé.
- `AuthorData.vue` : Affiche le nom de l'auteur (et optionnellement l'email).
- `TimestampData.vue` : Affiche la date avec le format configurable (via `useTableContext` → `timestampFormat`). Utilise `dayjs` pour le formatage (relatif ou absolu).
- `IndexStatus.vue` : Affiche les compteurs du working directory : nombre de fichiers modifiés/ajoutés/supprimés avec icônes (+, ~, -).

**Critère de validation** : Chaque cellule affiche la bonne donnée avec le bon formatage.

#### Phase 5.3 : Interactions de la table (sélection, preview, synchronisation)

**Entrée** : Composants de la phase 5.1 + contexte Git
**Sortie** : Mise à jour des composants pour les interactions

- Synchroniser la sélection/preview avec le graphe : la table et le graphe partagent le même `selectedCommit` et `previewedCommit` via le `GitContext`
- Hover sur une ligne → preview dans le graphe (et inversement)
- Clic sur une ligne → sélection (et inversement)
- Fond coloré (gradient linéaire) basé sur la couleur de colonne du commit dans le graphe

**Critère de validation** : Hover sur la table met en évidence la ligne correspondante dans le graphe. Clic dans l'un sélectionne dans l'autre.

---

## Tasks tracking

- [x] Phase 5.1 : GitLogTable.vue et TableRow.vue (structure)
- [x] Phase 5.2 : Sous-composants de cellules (données)
- [x] Phase 5.3 : Interactions de la table (sélection, preview, synchronisation)

## Decisions made during implementation
<!-- Free format -->

Phase 5.1:
1. **`nodeSize` for background gradient**: The React `TableRow` gets `nodeSize` from `useGitContext()`, but in the Vue port `nodeSize` is only in `GraphContextBag` (not `GitContextBag`). `TableRow.vue` uses `DEFAULT_NODE_SIZE` (20) from constants instead. This matches the default behavior and avoids coupling the table to the graph context. If a custom `nodeSize` needs to be reflected in the table's background gradient, this can be revisited in Phase 5.3.
2. **Custom row detection in `TableContainer`**: The React `TableContainer` receives a `row` render prop to detect custom row mode (switches from CSS Grid to plain div). In Vue, since the custom row is a scoped slot on `GitLogTable`, a `hasCustomRow` boolean prop is passed down from `GitLogTable` to `TableContainer` rather than using slot detection (the `#row` slot lives on `TableRow`, not `TableContainer`).
3. **Default cell layout in `TableRow`**: Phase 5.1 renders basic text for message/author/date cells. The dedicated cell components (`CommitMessageData`, `AuthorData`, `TimestampData`) will replace these in Phase 5.2.
4. **Interactions wired eagerly**: Even though Phase 5.3 focuses on interactions, the event handlers (`@click`, `@mouseover`, `@mouseout`) and the `useSelectCommit` composable are already integrated in `TableRow.vue` because they are integral to the row structure and the scoped slot API (`selected`, `previewed`, `backgroundColour` props). This avoids having to restructure the component in Phase 5.3.
5. **Table does not re-filter commits**: Consistent with the React source, `GitLogTable.vue` uses `graphData.commits` directly (already filtered in `GitLogCore`/`GitLog.vue`) and only applies pagination slicing. No additional `filter` call is made, unlike `GraphCore.vue` and the Tags module.
6. **`v-if`/`v-for` separation on placeholder rows**: The placeholder `<TableRow>` block uses a wrapping `<template v-if="tableData.length === 0">` with `v-for` on the inner `<TableRow>`, following the Vue 3 style guide Priority B rule against combining `v-if` and `v-for` on the same element.

Phase 5.3:
1. **Interactions already complete from Phase 5.1**: As noted in Phase 5.1 decision #4, the event handlers (`@click`, `@mouseover`, `@mouseout`) and `useSelectCommit` composable were already integrated in `TableRow.vue`. Phase 5.3 verified that selection/preview synchronization with the graph is fully functional: both `TableRow.vue` and `GraphColumn.vue`/`CommitNode.vue` use the same `useSelectCommit()` composable, which reads/writes `selectedCommit` and `previewedCommit` from the shared `GitContext`. Hovering a table row highlights the graph (and vice versa); clicking selects (and vice versa).
2. **Background color gradient matches React source**: The `backgroundColour` computed and `backgroundStyles` computed in `TableRow.vue` replicate the React `TableRow.tsx` logic exactly: selected rows use the commit's column color with reduced opacity (0.15), previewed rows use `hoverColour`, and the gradient creates a vertical band matching the node height (DEFAULT_NODE_SIZE + BACKGROUND_HEIGHT_OFFSET) centered in the row.
3. **`nodeSize` remains as `DEFAULT_NODE_SIZE`**: The Phase 5.1 decision to use `DEFAULT_NODE_SIZE` (20) instead of reading `nodeSize` from context was reviewed and confirmed. In the React source, `nodeSize` comes from `GitContextBag`, but in the Vue port it is architecturally scoped to `GraphContextBag`. Using the constant avoids coupling the table module to the graph context, and the default value (20) matches the React default. This is acceptable because `nodeSize` is always set at the graph level and the table only needs to align its gradient height with the graph's row height, which uses the same constant.
4. **Added `data-testid` attributes**: The React source includes `data-testid` on the row container (`react-git-log-table-row-${index}`). This was missing in the Vue port and has been added as `vue-git-log-table-row-${index}` on both the custom row and default row `<div>` elements, matching the pattern used in other Vue components (e.g., `GraphColumn.vue`).

Phase 5.2:
1. **SVG icons as `<img>` tags**: The React source imports SVGs as React components via `?react` suffix (Vite SVGR plugin). The Vue project does not have an SVG component plugin, so icons (pencil, plus, minus) are imported as URL strings (`import pencilIcon from './pencil.svg'`) and rendered with `<img>` tags. This is Vite's native SVG handling and requires no additional plugins.
2. **SVG assets directory created**: The `assets/` directory under `GitLog/` did not exist yet. Created it and copied the 3 SVG files needed by `IndexStatus` (pencil.svg, plus.svg, minus.svg) from the React source.
3. **`dayjs` already installed**: `dayjs` was already a dependency in the project (`^1.11.19`), so no installation was needed.
4. **`shouldRenderHyphenValue` computed added**: Added a `shouldRenderHyphenValue` computed to `TableRow.vue` matching the React source logic (`commit.hash === 'index' || isPlaceholder`), used by both `AuthorData` and `TimestampData` to render `-` instead of actual data for index and placeholder rows.
5. **No hash summary in CommitMessageData**: The spec mentions "Affiche le hash en resume" for `CommitMessageData`, but the React source does not render a hash summary in this component. The component only renders the commit message and conditionally the `IndexStatus`. The hash is displayed elsewhere (in the graph node tooltips). The implementation follows the React source behavior.

## Resolved questions and doubts
<!-- Free format -->

Phase 5.1:
- **Q: How to handle the `row` render prop (React) in Vue?** A: The `#row` scoped slot is defined on `GitLogTable.vue` and passed through to each `TableRow.vue`. `TableRow.vue` checks `$slots.row` to decide whether to render the custom slot content or the default layout. `TableContainer.vue` receives a `hasCustomRow` boolean prop to switch between CSS Grid and plain div rendering modes.
- **Q: Should the spec's mention of "iterates over visible commits and renders a TableRow per commit" be in TableContainer or GitLogTable?** A: In the React source, `TableContainer` is purely a layout wrapper that receives children. The iteration over commits happens in `Table.tsx` (which is `GitLogTable.vue` in the Vue port). This is preserved in the Vue port: `GitLogTable.vue` iterates and renders `TableRow` instances, which are passed as default slot content to `TableContainer.vue`.

Phase 5.3:
- **Q: Is the selection/preview synchronization between table and graph already working?** A: Yes. Phase 5.1 wired up the full interaction chain: `TableRow.vue` uses `useSelectCommit()` which calls `setSelectedCommit`/`setPreviewedCommit` on the shared `GitContext`. The graph's `GraphColumn.vue` and `CommitNode.vue` also use `useSelectCommit()` and read from the same `GitContext`. The reactive `selectedCommit` and `previewedCommit` refs are shared, so changes from either side propagate automatically.
- **Q: Should `nodeSize` be read from context for the table's background gradient?** A: No. In the Vue port, `nodeSize` is in `GraphContextBag` (not `GitContextBag`), and the table module should not depend on the graph context. Using `DEFAULT_NODE_SIZE` (20) is correct because it matches the React default, and the table gradient only needs to align vertically with the graph row layout, which also uses the same base constant.
- **Q: Are there any missing interactions from the React source?** A: Only the `data-testid` attribute was missing and has been added. All event handlers, state computations, background styling, and scoped slot API are fully ported. The React `cloneElement` pattern for custom rows is replaced by the wrapper `<div>` approach (decided in Phase 5.1).

Phase 5.2:
- **Q: How to handle SVG icon imports without a Vite SVGR plugin?** A: Vite natively supports importing SVGs as URLs. The icons are imported as URL strings and rendered via `<img>` tags with appropriate `alt` attributes. This avoids adding a plugin dependency and works with the existing Vite configuration. The SCSS module classes still control sizing.
- **Q: Does `TimestampData` need `advancedFormat` dayjs plugin?** A: The React source imports `utc` and `relativeTime` plugins but not `advancedFormat` in `TimestampData.tsx` itself (the context doc mentions it as a dependency of the library). The Vue implementation only extends dayjs with `utc` and `relativeTime`, matching the actual usage in the component.
