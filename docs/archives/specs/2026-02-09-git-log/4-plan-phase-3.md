# GitLog — Plan de portage — Phase 3

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

---

### Phase 3 : Composant racine + Layout

#### Phase 3.1 : Composant Layout.vue

**Entrée** : `components/Layout/Layout.tsx`, `Layout.module.scss`
**Sortie** : `src/components/GitLog/Layout.vue`, `Layout.module.scss`

- Template Vue avec 3 zones flex : `<slot name="tags" />`, `<slot name="graph" />`, `<slot name="table" />`
- Copier le SCSS module tel quel (ou avec adaptations mineures)
- Le layout est un container flex horizontal avec les 3 colonnes
- Le slot `#graph` a une largeur contrôlée par `useResize` (via prop ou inject)
- **Note** : Le drag handle de resize est dans `GraphCore` (pas dans `Layout`) — voir section 9.6
- **Note importante** : Dans le code source (`Layout.tsx` lignes 22-36), `Layout` gère aussi le rendu conditionnel des **en-têtes** ("Branch / Tag", "Graph") dans les zones tags et graph, conditionné par `showHeaders` du `GitContext`. Le composant `Layout.vue` devra reproduire ce comportement (les headers ne sont pas dans les sous-composants Tags/Graph mais dans le Layout lui-même).

**Critère de validation** : Le composant compile. Les 3 slots sont rendus dans un layout flex.

#### Phase 3.2 : Composant GitLog.vue (racine)

**Entrée** : `GitLog.tsx`, `GitLogCore/GitLogCore.tsx`, phase 2 (composables), phase 3.1 (Layout)
**Sortie** : `src/components/GitLog/GitLog.vue`

- Props (correspondant à `GitLogCommonProps` + `GitLogProps`) : `entries`, `currentBranch`, `theme`, `colours`, `paging`, `filter`, `showGitIndex`, `indexStatus`, `headCommitHash`, `urls`, `onSelectCommit`, `onPreviewCommit`, `showHeaders`, `rowSpacing`, `defaultGraphWidth`, `enableSelectedCommitStyling`, `enablePreviewedCommitStyling`, `classes` (GitLogStylingProps), et les props de styling
- **Imports depuis la phase 1** :
  - Tous les types de props (`GitLogCommonProps`, `GitLogProps`, `GitLogStylingProps`, `CSSProperties` de Vue, `CommitFilter`, `GitLogPaging`, `GitLogIndexStatus`, etc.) sont dans `./types.ts`
  - Note : `CSSProperties` est importé de `vue` (décision phase 1.1 #2). `GitLogStylingProps.containerStyles` et les types de styling de table utilisent ce type Vue natif.
  - Les types function React (`CustomTooltip`, `CustomCommitNode`, `CustomTableRow`) n'existent plus — seuls les interfaces `*Props` (`CustomTooltipProps`, `CustomCommitNodeProps`, `CustomTableRowProps`) sont disponibles pour typer les scoped slots
- Dans le `<script setup>` :
  1. Appeler le pipeline de données en `computed` : `computeRelationships()` → `temporalTopologicalSort()` → `GraphDataBuilder.build()` — ces fonctions/classes s'importent depuis `./data` (barrel `data/index.ts`)
  2. Gérer le pseudo-commit index (si `showGitIndex` est true)
  3. Gérer la pagination client-side (`paging` prop → `slice()`)
  4. Gérer le filtrage (`filter` prop)
  5. `provide(GIT_CONTEXT_KEY, { ... })` avec toutes les données calculées
     - **Note (décision phase 2.1)** : Toutes les propriétés réactives de `GitContextBag` sont typées `Readonly<Ref<T>>`. Les props du composant devront être converties en refs réactives pour satisfaire ce typage (via `toRef(props, 'propName')` ou `computed(() => props.propName)`). Les valeurs d'état interne (`selectedCommit`, `graphWidth`, etc.) sont des `ref()` classiques qui satisfont `Readonly<Ref<T>>` naturellement. Les setters restent des fonctions simples (pas de Ref).
  6. `provide(THEME_CONTEXT_KEY, { ... })` avec le thème (via `useTheme`)
  7. Utiliser `useSelectCommit` pour la logique de sélection
  8. Utiliser `useResize` pour la logique de resize
- Template : `<Layout>` avec les 3 slots passés en `<slot>`

**Note sur le volume de logique** : Ce composant cumule les responsabilités de `GitLog.tsx`, `GitLogCore.tsx`, et partiellement `ThemeContextProvider.tsx` du code source React. Il risque d'être volumineux. Si le fichier devient trop gros (> ~300 lignes de script), envisager d'extraire un `GitLogCore.vue` intermédiaire qui gère le pipeline de données et les `provide`, tandis que `GitLog.vue` ne gère que les props publiques et le template avec `<Layout>`.

**Critère de validation** : Le composant compile. Le pipeline de données tourne en `computed`. Les `provide` sont en place. Le layout est rendu avec les 3 zones.

---

## Tasks tracking

- [x] Phase 3.1: Composant Layout.vue
- [x] Phase 3.2: Composant GitLog.vue (racine)

## Decisions made during implementation
<!-- Free format -->

### Phase 3.1

1. **Container id**: Changed from `react-git-log` to `vue-git-log` to reflect the Vue port.
2. **No `data-testid`**: The React source uses `data-testid='react-git-log'` for testing. Since the project does not use tests (per CLAUDE.md), this attribute was omitted.
3. **Slot visibility via `v-if="$slots.xxx"`**: The React Layout renders each zone (tags, graph, table) only if the corresponding prop is provided (`{tags && ...}`). In Vue, this is handled by checking `$slots.tags`, `$slots.graph`, `$slots.table` with `v-if`, which achieves the same conditional rendering.
4. **`textColour` is a `ComputedRef<string>`**: In the template, `textColour` is used directly (Vue auto-unwraps refs in templates). The `:style` binding uses the unwrapped string value.
5. **Graph width not managed in Layout**: Confirmed from source analysis that the graph column width is controlled by `GraphCore` (which sets `style={{ width }}` on its container div), not by Layout. Layout only provides the flex container; the graph zone has no explicit width styling in the SCSS.

### Phase 3.2

1. **Single component instead of GitLog + GitLogCore**: The React source splits logic between `GitLog.tsx` (thin wrapper) and `GitLogCore.tsx` (all logic). In Vue, since there is no compound component pattern to handle (`useCoreComponents` is replaced by named slots), all logic fits cleanly in a single `GitLog.vue` file (~240 lines of script). No need for a `GitLogCore.vue` intermediate component.
2. **Theme colour resolution inlined**: The React source has a separate `ThemeContextProvider.tsx` component. In Vue, the colour resolution logic is inlined directly in `GitLog.vue` using a standalone `bootstrapShiftAlphaChannel` function, avoiding the circular dependency issue described in the architecture doc.
3. **~~Props use `Commit` instead of `GitLogEntry`~~** *(corrigé post-review)* : La prop `entries` était initialement typée `Commit[]`, mais cela forçait le consommateur à fournir les champs `children` et `isBranchTip` alors que `computeRelationships` les recalcule en interne. **Corrigé** : la prop est maintenant typée `GitLogEntry[]`, ce qui correspond à l'API publique attendue. `GitLogEntry` a été ajouté aux imports de `./types`. TypeScript compile sans erreur (`vue-tsc --noEmit` : 0 errors).
4. **`graphContainerWidth` as separate ref**: Rather than combining the default/computed width with the resize-updated width in a single ref (as in React's `useState`), a separate `ref<number | undefined>` tracks explicit resize operations while a `computed` derives the effective width. This avoids losing the ability to react to `defaultGraphWidth` prop changes.
5. **~~`nodeSize` and `graphOrientation` as internal refs in GitLog.vue~~** *(corrigé post-review)* : Ces valeurs étaient initialement des refs internes de `GitLog.vue`, exposées dans le `GitContextBag` avec des setters. Mais elles ne concernent que le module Graph, pas le composant racine. **Corrigé** : `nodeSize`, `setNodeSize`, `graphOrientation`, `setGraphOrientation` ont été retirés du `GitContextBag` et de `GitLog.vue`. Ces valeurs seront gérées par `GraphCore.vue` en phase 4 (props + `GraphContextBag`). Le calcul `smallestAvailableGraphWidth` utilise maintenant `DEFAULT_NODE_SIZE` directement. TypeScript compile sans erreur.

## Resolved questions and doubts
<!-- Free format -->

### Phase 3.1

1. **Does Layout need `useResize`?** No. The spec mentions that the `#graph` slot has a width controlled by `useResize`, but after examining the React source, `useResize` is consumed in `GraphCore.tsx` (which wraps the graph content and applies the width via inline style). Layout itself does not use `useResize` — it only provides the flex container structure. The `.graph` class in the SCSS has no width-related properties.

### Phase 3.2

1. **Why `useSelectCommit` and `useResize` are not called directly in GitLog.vue?** The spec mentions using `useSelectCommit` and `useResize` in the root component. However, both composables consume `useGitContext()` internally, which requires the GitContext to already be `provide`d by a parent component. Since `GitLog.vue` is the component that **provides** the GitContext, it cannot also consume it. Instead, the selection and preview logic is implemented directly in `GitLog.vue` via `handleSelectCommit` and `handlePreviewCommit` functions, which are passed as setters to the GitContextBag. The `useSelectCommit` and `useResize` composables are used in downstream child components (e.g., graph components) that consume the context.
2. **`useTheme` not called for ThemeContext bootstrap**: The spec mentions using `useTheme` for the theme context. However, as noted in the architecture doc (section 4, ThemeContext), the React `ThemeContextProvider` calls `useTheme()` only to access `shiftAlphaChannel` during bootstrap — and `shiftAlphaChannel` only depends on the theme mode, not on the colours being resolved. To avoid the circular dependency (providing a context that consumes itself), a standalone `bootstrapShiftAlphaChannel` function is used directly in the component for colour resolution. The `useTheme` composable is used by downstream components that need the full theme API.
3. **Graph width managed via `ref` + `computed` combination**: The React source uses `useState` for `graphWidth` but the initial value is calculated from `defaultGraphWidth` and `smallestAvailableGraphWidth`. In Vue, a `ref<number | undefined>` (`graphContainerWidth`) stores the width set by resize operations, while a `computed` (`graphWidthValue`) derives the effective width by falling back to `defaultGraphWidth` or `smallestAvailableGraphWidth` when no resize has occurred. This cleanly separates the "user has resized" state from the "default width" computation.
4. **ThemeContext provided before GitContext**: The `provide(THEME_CONTEXT_KEY, ...)` call is placed before `provide(GIT_CONTEXT_KEY, ...)`. Both are in the same `setup()` scope and order of `provide` calls does not affect Vue's injection mechanism. Child components can inject either context regardless of provide order.
5. **Slot presence detection via `useSlots()`**: The React source detects child component presence via `useCoreComponents` (which analyzes `Children.forEach`). In Vue, `useSlots()` is used to detect which named slots are provided, and the result feeds `showBranchesTags` and `showTable` in the GitContextBag.
6. **`isServerSidePaginated` hardcoded to `false`**: Since we only port `GitLog` (client-side pagination), not `GitLogPaged` (server-side pagination), `isServerSidePaginated` is always `false`. The server-side pagination branch in `headCommit` computation is removed accordingly.

---

## Status: **COMPLETED**

All tasks implemented, reviewed (per-task + in-depth final review), and validated (TypeScript: 0 errors, Python tests: 98/98 pass).
