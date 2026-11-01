# GitLog — Plan de portage — Phase 4

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

## **COMPLETED**

---

### Phase 4 : Module Graph (HTML Grid)

Le plus gros morceau, découpé en sous-phases granulaires. Tous les composants vont dans `src/components/GitLog/graph/components/`.

#### Phase 4.1 : GraphCore.vue et HTMLGridGraph.vue (structure)

**Entrée** : `modules/Graph/core/GraphCore.tsx`, `modules/Graph/strategies/Grid/HTMLGridGraph.tsx` + SCSS modules
**Sortie** : `graph/GraphCore.vue`, `graph/HTMLGridGraph.vue` + SCSS

- `GraphCore.vue` :
  - **Fusionne les responsabilités** de `GraphHTMLGrid.tsx` (wrapper public) et `GraphCore.tsx` (core) du code source React
  - **Props** (issues de `GraphCoreProps` dans le source) : `nodeSize`, `nodeTheme`, `breakPointTheme`, `orientation`, `enableResize`, `showCommitNodeHashes`, `showCommitNodeTooltips`, `highlightedBackgroundHeight`, slot `#node` (custom commit node), slot `#tooltip` (custom tooltip)
  - **Note (phase 1.1 décision #5)** : Les props `node` et `tooltip` du React source (qui étaient des render props) ont été supprimées de `HTMLGridGraphProps` en phase 1. En Vue, ce sont des scoped slots typés avec `CustomCommitNodeProps` et `CustomTooltipProps` (définis dans `../../types.ts`), pas des props du composant.
  - `provide(GRAPH_CONTEXT_KEY, { ... })` avec toutes les valeurs du `GraphContextBag` : les props ci-dessus + `graphWidth` (calculé comme `graphData.graphWidth + virtualColumns`), `visibleCommits`, `isHeadCommitVisible`, `columnData`
  - **Note (décision phase 2.1)** : `GraphContextBag` contient `node` et `tooltip` typés comme `Readonly<Ref<((props: CustomCommitNodeProps) => unknown) | undefined>>` (idem pour tooltip). Ce sont les render props React converties en refs de fonctions. Pour les alimenter depuis les scoped slots Vue `#node` et `#tooltip`, `GraphCore.vue` devra bridger les slots vers ces refs — par exemple en utilisant `useSlots()` pour détecter la présence du slot et le convertir en fonction, ou en passant le slot comme render function via `computed(() => slots.node ? (props) => slots.node!(props) : undefined)`. Ce bridging n'est nécessaire que si des composants descendants accèdent aux customs via `useGraphContext()` plutôt que par propagation de slots.
  - **Note (décision phase 2.5)** : `useColumnData` prend un paramètre `Readonly<Ref<number>>` (le **nombre** de commits visibles, pas le tableau). Il faudra donc passer un `computed(() => visibleCommits.value.length)` et non le tableau `visibleCommits` directement.
  - Wrapper qui contient le slot pour le graphe (rendu par `HTMLGridGraph.vue`)
  - Le resize est géré dans `GraphCore` (pas dans `Layout`)
- `HTMLGridGraph.vue` :
  - Le conteneur CSS Grid principal (`display: grid`, `grid-template-columns`, `grid-template-rows`)
  - Utilise `useColumnData` pour calculer la matrice de colonnes
  - **Imports depuis la phase 1** : `GraphMatrixColumns` et `GraphColumnState` s'importent depuis `../GraphMatrixBuilder` (barrel `index.ts`). Il n'existe pas de fichier `GraphColumn/types.ts` — le type `GraphColumnState` a été placé dans `GraphMatrixBuilder/types.ts` en phase 1.3.
  - Boucle sur les lignes et colonnes pour rendre les `GraphRow` / `GraphColumn`
  - Applique `marginTop: GRAPH_MARGIN_TOP` (12px) pour l'alignement
  - La hauteur des lignes inclut `rowSpacing` : `${ROW_HEIGHT + rowSpacing}px`
  - Utilise `GraphCore.module.scss` (pas de fichier `.module.scss` propre)
  - **Note importante sur la répartition des props** : Dans le code source React, il y a une **indirection à 3 niveaux** :
    1. `GraphHTMLGrid.tsx` (wrapper public dans `modules/Graph/`) — expose les props (`nodeSize`, `nodeTheme`, `breakPointTheme`, `orientation`, `enableResize`, `showCommitNodeHashes`, `showCommitNodeTooltips`, `highlightedBackgroundHeight`, `tooltip`, `node`) et les passe à `<GraphCore>`
    2. `GraphCore.tsx` (dans `modules/Graph/core/`) — reçoit ces props via `GraphCoreProps` (PAS `HTMLGridGraph`), gère le `GraphContext`, le resize, les commits visibles
    3. `HTMLGridGraph.tsx` (dans `strategies/Grid/`) — le rendu CSS Grid, qui ne prend **aucune prop** et utilise `useGraphContext()` et `useGitContext()` pour obtenir ses données
  - **Dans notre portage Vue**, nous fusionnons les niveaux 1 et 2 en `GraphCore.vue` (qui porte les props et fournit le `GraphContext`), et `HTMLGridGraph.vue` reste un composant de rendu pur sans props, alimenté par `useGraphContext()`
  - **À ce stade** : rendre les `GraphRow` et `GraphColumn` comme des divs vides pour valider la structure grid

**Critère de validation** : La grille CSS est rendue avec le bon nombre de lignes/colonnes. La structure est en place.

#### Phase 4.2 : Éléments visuels de base (lignes et nœuds)

**Entrée** : Composants React `CommitNode`, `VerticalLine`, `HorizontalLine`, `ColumnBackground` + SCSS modules
**Sortie** : Composants Vue correspondants

- `CommitNode.vue` : Cercle du nœud de commit. HTML `<div>` avec `border-radius: 50%`. Supporte le merge node (double cercle via `nodeTheme`). Gère le scoped slot `#node` pour le custom render (typé avec `CustomCommitNodeProps` de `../../types.ts`). Affiche le hash à côté du nœud. Utilise `getMergeNodeInnerSize` depuis `../utils/getMergeNodeInnerSize` (placé dans `graph/utils/` en phase 1.4, pas dans `utils/` à la racine du composant).
- `VerticalLine.vue` : `<div>` avec `border-right` CSS colorée. Reçoit la couleur de colonne en prop.
- `HorizontalLine.vue` : `<div>` avec `border-bottom` CSS colorée. Pour les merges (lignes horizontales entre colonnes).
- `ColumnBackground.vue` : `<div>` avec background coloré semi-transparent. Pour la mise en évidence au hover/sélection. Utilise `getColumnBackgroundSize` depuis `../utils/getColumnBackgroundSize` (placé dans `graph/utils/` en phase 1.4).

**Critère de validation** : Chaque composant compile et rend le bon élément DOM avec le bon style inline.

#### Phase 4.3 : Éléments visuels SVG (courbes)

**Entrée** : Composants React `CurvedEdge`, `LeftDownCurve`, `LeftUpCurve` + SCSS modules
**Sortie** : Composants Vue correspondants

- `CurvedEdge.vue` : SVG inline `<path>` avec arc elliptique (commande SVG `A`, et non une courbe de Bézier quadratique `Q`). ViewBox `"0 0 100 100"`, taille 24×24. La couleur du stroke est la couleur de la colonne.
- `LeftDownCurve.vue` : Combinaison SVG `<path>` + `<div>` lignes d'extension verticale/horizontale. Gère les différentes configurations (avec ou sans extensions).
- `LeftUpCurve.vue` : Idem en miroir vertical.

**Critère de validation** : Les courbes SVG sont rendues avec les bons chemins et couleurs.

#### Phase 4.4 : Éléments spéciaux (breakpoints, index, skeleton)

**Entrée** : Composants React `BreakPoint`, `HeadCommitVerticalLine`, `IndexPseudoCommitNode`, `IndexPseudoRow`, `SkeletonGraph`
**Sortie** : Composants Vue correspondants

- `BreakPoint.vue` : Indicateur de rupture quand des commits sont filtrés. Supporte 7 thèmes visuels (slash, dot, ring, zig-zag, line, double-line, arrow). Utilise la CSS variable `--breakpoint-colour`.
- `HeadCommitVerticalLine.vue` : Ligne pointillée entre le HEAD commit et le pseudo-commit index.
- `IndexPseudoCommitNode.vue` : Nœud avec bordure pointillée (distinct des commits normaux).
- `IndexPseudoRow.vue` : Ligne complète du pseudo-commit (wrapper du nœud index + lignes).
- `SkeletonGraph.vue` : Placeholder de chargement (grille avec des éléments grisés animés).

**Critère de validation** : Chaque composant compile et rend le bon visuel.

#### Phase 4.5 : Assemblage GraphRow et GraphColumn

**Entrée** : Composants React `GraphRow`, `GraphColumn` + tous les composants des phases 4.2-4.4
**Sortie** : `GraphRow.vue`, `GraphColumn.vue`

- `GraphColumn.vue` : C'est le composant clé d'assemblage. Reçoit un `GraphColumnState` et compose les éléments visuels selon les flags booléens (`isNode`, `isVerticalLine`, `isHorizontalLine`, `isLeftDownCurve`, etc.). Gère aussi le fond coloré (`ColumnBackground`) pour sélection/preview. **Important pour l'accessibilité** : dans le code source React, `GraphColumn` est rendu comme un `<button>` avec `tabIndex`, `onClick`, `onBlur`, `onFocus`, `onMouseOut`, `onMouseOver` — ce choix doit être préservé pour la navigation clavier.
  - **Imports depuis la phase 1** : `GraphColumnState` s'importe depuis `../GraphMatrixBuilder` (barrel) ou `../GraphMatrixBuilder/types`. `GraphColumnProps` n'a **pas** été porté en phase 1 (décision 1.3 #6) — il doit être défini ici via `defineProps` dans le `<script setup>` du composant. Ce type de props dépend de `Commit` (de `../../types`) et `GraphColumnState` (de `../GraphMatrixBuilder/types`).
  - Les utilitaires `isColumnEmpty` et `getEmptyColumnState` sont dans `../utils/isColumnEmpty` et `../utils/getEmptyColumnState` (placés dans `graph/utils/` en phase 1.3).
- `GraphRow.vue` : Conteneur d'une ligne de la grille. Itère sur les colonnes et rend un `GraphColumn` pour chacune.
- Brancher dans `HTMLGridGraph.vue` : remplacer les divs vides de la phase 4.1 par les vrais `GraphRow`

**Critère de validation** : Le graphe complet est rendu visuellement avec nœuds, lignes, courbes. La grille CSS est correcte.

#### Phase 4.6 : Interactions du graphe (tooltip, sélection, preview)

**Entrée** : Logique d'interaction de `CommitNode` et `GraphColumn` (hover, click, tooltip)
**Sortie** : Mise à jour des composants pour ajouter les interactions

- `CommitNode.vue` : Ajouter les événements `@click` (sélection via `useSelectCommit`) et `@mouseenter`/`@mouseleave` (preview)
- Tooltip : Intégrer le tooltip au survol du nœud. Utiliser les composants WebAwesome au lieu de `react-tiny-popover`. Supporter le scoped slot `#tooltip` pour le custom render (typé avec `CustomTooltipProps` de `../../types.ts` — les types function React `CustomTooltip` n'ont pas été portés, seule l'interface de props existe).
- `ColumnBackground.vue` : Réagir à l'état `selectedCommit` et `previewedCommit` du contexte pour colorier le fond.
- `GraphRow.vue` ou wrapper : Propager les événements hover de la ligne complète pour le preview.

**Critère de validation** : Le hover met en évidence la ligne. Le clic sélectionne un commit. Le tooltip s'affiche au survol du nœud.

---

## Tasks tracking

- [x] Phase 4.1 : GraphCore.vue et HTMLGridGraph.vue (structure)
- [x] Phase 4.2 : Éléments visuels de base (lignes et nœuds)
- [x] Phase 4.3 : Éléments visuels SVG (courbes)
- [x] Phase 4.4 : Éléments spéciaux (breakpoints, index, skeleton)
- [x] Phase 4.5 : Assemblage GraphRow et GraphColumn
- [x] Phase 4.6 : Interactions du graphe (tooltip, sélection, preview)

## Decisions made during implementation

### Phase 4.1

1. **React `setNodeSize`/`setGraphOrientation` removed**: The React source `GraphCore.tsx` uses a `useEffect` to call `setNodeSize(nodeSize)` and `setGraphOrientation(orientation)` on the GitContext whenever these props change. In the Vue port, this indirection is unnecessary because `GraphCore.vue` provides `nodeSize` and `orientation` directly through the `GraphContextBag` as reactive `computed` refs. Descendants that need these values access them via `useGraphContext()`.

2. **Slot-to-ref bridging for `node` and `tooltip`**: As specified in phase 2.1 decisions, `GraphContextBag` defines `node` and `tooltip` as `Readonly<Ref<((props: ...) => unknown) | undefined>>`. To bridge the Vue scoped slots `#node` and `#tooltip` into these refs, `GraphCore.vue` uses `computed(() => slots.node ? (props) => slots.node!(props) : undefined)`. This allows descendant components to access custom node/tooltip renderers via `useGraphContext()`.

3. **HTMLGridGraph.vue does not import `getEmptyColumnState` or `paging` yet**: At this stage, the commit rows render as empty divs (no GraphRow/GraphColumn). The `getEmptyColumnState` utility and `paging` context value will be used in phase 4.5 when `GraphRow` components are integrated to resolve column states from `columnData`.

4. **Template ref binding**: `GraphCore.vue` uses Vue 3's string ref pattern (`ref="graphContainerRef"`) to bind the container element to the `shallowRef<HTMLDivElement | null>` returned by `useResize()`. This is the idiomatic Vue 3 `<script setup>` approach.

### Phase 4.2

1. **Custom node rendering moved into `CommitNode`**: In the React source, the custom node render prop (`node`) is invoked at the `GraphColumn` level and completely replaces the `<CommitNode>` component. In the Vue port, per the spec, `CommitNode` handles the custom node rendering internally: when the `node` render function is provided via `useGraphContext()`, it replaces the entire default node rendering (including the styled wrapper div). This simplifies `GraphColumn` in phase 4.5 -- it will always render `<CommitNode>` regardless of whether a custom node is provided.

2. **`CommitNode` receives `rowIndex` and `columnIndex` props**: Unlike the React `CommitNode` which only receives `commit` and `colour`, the Vue `CommitNode` also receives `rowIndex` and `columnIndex`. These are needed to build the `CustomCommitNodeProps` object passed to the custom node render function, since that responsibility has moved from `GraphColumn` into `CommitNode`.

3. **Reactive `getCommitNodeColours`**: The React `CommitNode` calls `getCommitNodeColours` at the component body level (re-runs on every render). In Vue, this is wrapped in a `computed` to ensure the returned colours stay reactive when the `colour` prop changes.

4. **No interaction handlers in phase 4.2**: The React `CommitNode` includes `onClick`, `onMouseOver`, `onMouseOut`, `onBlur`, `onFocus`, `onKeyDown` handlers and tooltip/popover logic. These are deferred to phase 4.6 (Interactions) as specified in the plan. The components in this phase only handle visual rendering.

5. **BreakPoint components deferred to phase 4.4**: The React `VerticalLine` renders `<BreakPoint>` components conditionally. Since `BreakPoint` is part of phase 4.4 (special elements), the VerticalLine template includes a placeholder comment where BreakPoint components will be inserted. The line variant computation logic already accounts for breakpoints to ensure correct styling.

### Phase 4.3

1. **BreakPoint components deferred to phase 4.4**: The React `LeftDownCurve` and `LeftUpCurve` conditionally render `<BreakPoint>` components (bottom breakpoint for LeftDownCurve, top breakpoint for LeftUpCurve). Since `BreakPoint` is part of phase 4.4, the templates include placeholder comments where these components will be inserted. The `showBottomBreakPoint` and `showTopBreakPoint` props are already accepted to prepare for this integration.

2. **Computed border style for reactivity**: The React source computes `borderStyle` as a plain `const` (re-evaluated on every render). In Vue, this is wrapped in a `computed` to ensure the returned style stays reactive when the `isPlaceholder` prop changes.

3. **Extension line heights use `rowSpacing` from `useGitContext`**: Both `LeftDownCurve` and `LeftUpCurve` compute vertical extension line heights as `(ROW_HEIGHT + rowSpacing - CURVE_SIZE) / 2`, matching the React source. The `rowSpacing` value is obtained from `useGitContext()` (not `useGraphContext()`), which matches the React source pattern.

### Phase 4.4

1. **BreakPoint `style` prop renamed to `styleOverrides`**: The React `BreakPoint` component accepts a `style` prop of type `Partial<Record<BreakPointTheme, CSSProperties>>` for per-theme position overrides. In Vue, `style` is a reserved attribute name that maps to the DOM `style` attribute. To avoid conflicts, this prop is named `styleOverrides` in the Vue port. It serves the same purpose: providing per-theme CSS overrides that are merged with the common styles.

2. **BreakPoint uses a theme-to-class map instead of switch/case**: The React source uses a `switch` statement over `breakPointTheme` to render different JSX elements for each theme. In Vue, all themes render the same `<div>` element with dynamically computed classes and styles, since the visual differences are fully handled by CSS. A `themeClassMap` record maps theme names to SCSS class names (e.g., `'zig-zag'` to `'ZigZag'`).

3. **IndexPseudoRow and SkeletonGraph render placeholder divs**: Both components depend on `GraphRow` which is part of phase 4.5. For now, they compute their data (index column states and placeholder data respectively) and render placeholder empty divs in the CSS grid, matching the pattern already used in `HTMLGridGraph.vue` for commit rows. Phase 4.5 will replace these with real `GraphRow` components.

4. **HTMLGridGraph.vue updated to use SkeletonGraph and IndexPseudoRow**: The placeholder comments in `HTMLGridGraph.vue` have been replaced with actual component renders: `<SkeletonGraph v-if="visibleCommits.length === 0" />` and `<IndexPseudoRow v-if="visibleCommits.length > 0 && isIndexVisible" />`.

5. **LeftDownCurve and LeftUpCurve breakpoint style overrides are static**: The per-theme position override objects for `BreakPoint` in `LeftDownCurve` and `LeftUpCurve` are declared as plain objects (not `computed`), since they contain only static CSS values that do not depend on reactive data.

### Phase 4.5

1. **`GraphColumn` always renders `CommitNode` for non-index nodes**: In the React source, `GraphColumn` checks the `node` render prop from `useGraphContext()` and conditionally renders either a custom node or `<CommitNode>`. In the Vue port, per the phase 4.2 decision #1, `CommitNode` handles the custom node rendering internally. Therefore, `GraphColumn` always renders `<CommitNode>` for non-index nodes, without checking for a custom render function.

2. **Interaction handlers deferred to phase 4.6**: The React `GraphColumn` includes `onClick`, `onBlur`, `onFocus`, `onMouseOut`, `onMouseOver` handlers via `useSelectCommit`. The Vue `GraphColumn` renders as a `<button>` element with the correct `tabindex` and CSS class for accessibility, but does not bind interaction event handlers yet. These will be added in phase 4.6.

3. **`GraphRow` orientation normalization uses inline expression**: The React `GraphRow` computes `normalisedIndex` as a variable inside the `.map()` callback. In Vue's template `v-for`, the same normalisation (reversing the index when `orientation === 'flipped'`) is done inline in the template bindings to avoid duplicating the expression in a computed property that would need array allocation on every change.

4. **Empty column state fallback is a plain object**: The React `GraphRow` falls back to `getEmptyColumnState({ columns: graphWidth })` which returns an array, but passes it to a prop that expects a single `GraphColumnState`. This is a type-level inconsistency in the React source. In the Vue port, the fallback is a plain empty object `{}` which is a valid `GraphColumnState` and correctly represents an empty column.

5. **`HTMLGridGraph.vue` now maps commit data to `GraphRow` via `columnData`**: The placeholder empty divs from phase 4.1 have been replaced with real `<GraphRow>` components. The `getColumnsForCommit` helper function resolves column states from the `columnData` map in the graph context, accounting for pagination offset, matching the React source's data mapping logic.

6. **`IndexPseudoRow` and `SkeletonGraph` now render `GraphRow`**: The placeholder divs from phase 4.4 have been replaced with `<GraphRow>` components. `IndexPseudoRow` passes `id=0` and the computed index column states. `SkeletonGraph` iterates over `placeholderData` from `usePlaceholderData` and renders one `GraphRow` per placeholder entry.

### Phase 4.6

1. **CSS-based tooltip instead of WebAwesome `wa-tooltip`/`wa-popover`**: The spec recommends WebAwesome components for tooltips, but after analysis, neither `wa-tooltip` nor `wa-popover` is a good fit. `wa-tooltip` renders content inside a shadow DOM with its own background/border styling, which conflicts with `CommitNodeTooltip`'s custom themed background and border. `wa-popover` opens on click (not hover) and requires a button anchor via the `for` attribute. Instead, a CSS-positioned tooltip is used: an absolutely positioned div above the commit node, controlled by a local `showTooltip` ref toggled on mouseover/mouseout. This approach can be replaced with WebAwesome components later if needed.

2. **Both `CommitNode` and `GraphColumn` handle interaction events**: Matching the React source, both components call `useSelectCommit()` and bind mouse/keyboard event handlers. `GraphColumn` handles events on its `<button>` element for all column types (even non-node columns). `CommitNode` handles events on its inner `<div role="button">` for node-specific interactions (tooltip visibility, commit URL navigation, keyboard Enter). Events from `CommitNode` bubble up to `GraphColumn`.

3. **`CommitNode` handles tooltip visibility and custom tooltip rendering**: The tooltip is shown/hidden via a local `showTooltip` ref. When `showCommitNodeTooltips` (from graph context) is enabled and `showTooltip` is true, either the custom tooltip render function (from the `tooltip` ref in graph context) or the default `CommitNodeTooltip` component is rendered. The `CustomTooltipProps` are computed from the commit and its node colours.

4. **`GraphRow` does not need changes**: The React `GraphRow` renders as a fragment without event handlers. Hover propagation for the full row is achieved through each `GraphColumn`'s `<button>` element calling `selectCommitHandler.onMouseOver(commit)` on mouseover. Since all columns in a row share the same commit, hovering over any column triggers the preview for the entire row. No wrapper or additional event handling is needed.

5. **`ColumnBackground` does not need changes**: The existing `ColumnBackground.vue` is a pure presentational component that receives its `colour` prop from `GraphColumn`. The selection/preview state reactivity is handled entirely in `GraphColumn`'s computed properties (`showSelectedBackground`, `showPreviewBackground`) which read `selectedCommit` and `previewedCommit` from the git context. `ColumnBackground` does not need to access context directly.

6. **`CommitNode` adds `role="button"`, `tabindex`, and keyboard support**: Following the React source pattern, the default node div has `role="button"`, `tabindex="0"`, and a `@keydown` handler for Enter key navigation. It also shows a `title="View Commit"` attribute when a commit URL is available.

7. **`CommitNode` uses `.stop` modifiers on all event handlers to prevent double-firing**: `CommitNode`'s `<div>` is nested inside `GraphColumn`'s `<button>`, and both call `selectCommitHandler` methods for click, mouseover, mouseout, focus, and blur events. In Vue, reactivity is synchronous, so without stopping propagation the click handler fires twice in sequence: the first call toggles selection on, and the bubbled second call toggles it back off. The `.stop` modifier on `@click`, `@mouseover`, `@mouseout`, `@focus`, and `@blur` prevents events from bubbling to `GraphColumn`'s `<button>`, ensuring each interaction is processed exactly once. This differs from React where batched state updates prevent the double-toggle issue.

## Resolved questions and doubts

### Phase 4.1

1. **Q: How should the resize container width be applied?** A: The `useResize` composable returns a `width` ref (which is actually `graphWidth` from `useGitContext()`). This is applied as an inline style `width: ${width}px` on the container div, matching the React source's `style={{ width }}` pattern.

### Phase 4.2

1. **Q: Where should the custom node render responsibility live?** A: The spec says `CommitNode` handles the `#node` scoped slot. In the React source, `GraphColumn` handles it by conditionally rendering either the custom node or `<CommitNode>`. In the Vue port, `CommitNode` takes this responsibility. When a custom node render function exists in the graph context, `CommitNode` renders it instead of the default styled div. This means `GraphColumn` (phase 4.5) always renders `<CommitNode>`.

2. **Q: Should `VerticalLine` render `BreakPoint` components?** A: No, not in this phase. `BreakPoint` is defined in phase 4.4. The `VerticalLine` template has a placeholder comment where `BreakPoint` components will be added. The line styling logic already handles breakpoint states correctly to ensure the visual structure is prepared.

### Phase 4.4

1. **Q: How should the React `style` prop on BreakPoint be handled in Vue?** A: Renamed to `styleOverrides` to avoid conflict with Vue's native `style` attribute. The type remains `Partial<Record<BreakPointTheme, CSSProperties>>` and is merged with the common breakpoint styles.

2. **Q: Should IndexPseudoRow and SkeletonGraph render real GraphRow components?** A: Not yet. `GraphRow` is part of phase 4.5. These components compute their data (column states, placeholder data) and render placeholder divs. The `GraphRow` integration will happen in phase 4.5.

### Phase 4.5

1. **Q: Should `GraphColumn` handle custom node rendering?** A: No. Per the phase 4.2 decision, `CommitNode` handles custom node rendering internally via the `node` function from `useGraphContext()`. `GraphColumn` always renders `<CommitNode>` for non-index nodes, keeping the component simpler.

2. **Q: How should `GraphColumn` handle the button accessibility attributes without interaction handlers?** A: The `<button>` element is rendered with the correct `tabindex`, `id`, `data-testid`, and CSS class. Event handlers (`@click`, `@mouseout`, `@mouseover`, `@blur`, `@focus`) are deferred to phase 4.6. The button structure is in place and ready for interaction wiring.

### Phase 4.6

1. **Q: Should we use `wa-tooltip` or `wa-popover` for commit node tooltips?** A: Neither is a good fit. `wa-tooltip` imposes its own shadow DOM styling (background, border, padding) which conflicts with `CommitNodeTooltip`'s themed styles. `wa-popover` opens on click rather than hover and requires a button anchor. A CSS-positioned tooltip approach was chosen instead, using an absolutely positioned div rendered conditionally on hover. This can be replaced with WebAwesome components if they add hover-triggered popover support or more flexible tooltip styling in the future.

2. **Q: Does `GraphRow` need hover event handlers?** A: No. The React `GraphRow` is a fragment with no event handlers. Hover-based preview is achieved at the `GraphColumn` level -- each column's `<button>` calls `selectCommitHandler.onMouseOver(commit)` on mouseover, and since all columns in a row share the same commit, the preview highlights the entire row.

3. **Q: Does `ColumnBackground` need direct access to selection/preview context?** A: No. `ColumnBackground` is a pure presentational component. The selection/preview logic lives in `GraphColumn`, which decides whether to render a `ColumnBackground` with the appropriate colour based on `selectedCommit` and `previewedCommit` from the git context.

### Post-implementation review fixes

1. **Off-by-one error in `getColumnsForCommit` (HTMLGridGraph.vue)**: When `paging` is undefined (no pagination), the row index was computed as 0-based (`index`), but `columnData` uses 1-based row indices. Fixed by using `index + 1` in the no-paging branch.

2. **Custom node render interactivity (CommitNode.vue)**: When a custom `node` render function is provided via graph context, the rendered `<component>` had no event handlers. Events would bubble to `GraphColumn` without `.stop`, the tooltip would never show, and the commit URL would never open. Fixed by wrapping the custom node component in a transparent interactive `<div>` with the same event handlers and `.stop` modifiers as the default node. A `customNodeWrapper` CSS class provides the positioning and z-index without visual styles (no border, background, or border-radius).

3. **Missing `.stop` modifier on `@keydown` (CommitNode.vue)**: The `@keydown` handler on the default node div lacked the `.stop` modifier, allowing the Enter key to bubble to `GraphColumn`'s button and double-toggle selection. Fixed by adding `.stop` to `@keydown.stop="handleKeyDown"`.

4. **Inconsistent prop naming: `color` vs `colour`**: Several components used American English `color` for prop names while the codebase convention (types.ts, useTheme.ts) uses British English `colour`. Renamed `color` props to `colour` in `CommitNodeTooltip.vue`, `BreakPoint.vue`, `LeftDownCurve.vue`, and `LeftUpCurve.vue`, and updated all callers (`CommitNode.vue`, `GraphColumn.vue`, `VerticalLine.vue`).

5. **SCSS import alias inconsistency in `BreakPoint.vue`**: The SCSS module was imported as `classes` while all other components use `styles`. Renamed the import to `styles` and updated all references.
