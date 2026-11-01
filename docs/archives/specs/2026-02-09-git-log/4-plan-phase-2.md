# GitLog — Plan de portage — Phase 2

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

---

### Phase 2 : Créer les composables Vue

Convertir les hooks React en composables Vue (Composition API). Chaque composable est créé dans `src/components/GitLog/composables/`.

> **Ordre des sous-phases** : L'ordre est important car `useTheme` dépend de `useGitContext` et `useThemeContext` (qui sont des composables d'injection). Les composables d'injection (2.2) doivent donc être créés avant `useTheme` (2.3). De même, `useSelectCommit` (2.4) consomme `useGitContext`.

#### Phase 2.1 : Clés d'injection et types des contextes

**Entrée** : Les types de contextes React (`context/GitContext/types.ts`, `context/ThemeContext/types.ts`, `modules/Graph/context/types.ts`, `modules/Table/context/types.ts`)
**Sortie** : `src/components/GitLog/composables/keys.ts` + types associés

- Définir les `InjectionKey<T>` pour chaque contexte : `GIT_CONTEXT_KEY`, `THEME_CONTEXT_KEY`, `GRAPH_CONTEXT_KEY`, `TABLE_CONTEXT_KEY`
- Définir les interfaces TypeScript correspondantes (`GitContextBag`, `ThemeContextBag`, `GraphContextBag`, `TableContextBag`)
- Ces types décrivent les valeurs `provide`d — les propriétés réactives (`Ref`, `ComputedRef`, `Readonly`) et les fonctions
- **Note (implémenté en phase 1)** : Les types `GraphPaging` et `ThemeFunctions` (nécessaires pour `GitContextBag` et `ThemeContextBag`) sont déjà définis et exportés depuis `../types.ts`. Il ne faut pas les redéfinir ici, simplement les importer. De même pour `CommitFilter`, `GitLogStylingProps`, `GitLogPaging`, etc.

**Critère de validation** : `tsc --noEmit` passe. Les types sont cohérents avec les types de la phase 1.

#### Phase 2.2 : Composables d'injection (useGitContext, useThemeContext, useGraphContext, useTableContext)

**Entrée** : Les hooks `useContext` React correspondants
**Sortie** : 4 fichiers dans `composables/` (ou un seul regroupé)

- Chaque composable fait un `inject(KEY)` avec un garde `if (!ctx) throw new Error(...)`
- Typage strict avec les interfaces de la phase 2.1

**Critère de validation** : Chaque composable compile et lance une erreur si utilisé hors contexte.

#### Phase 2.3 : Composable useTheme

**Entrée** : `hooks/useTheme/useTheme.ts`, `hooks/useTheme/types.ts`, `createRainbowTheme.ts` (déjà copié)
**Sortie** : `src/components/GitLog/composables/useTheme.ts`

- Convertir le hook React en composable Vue :
  - `useMemo` → `computed()`
  - Les couleurs hover/texte calculées en `computed` à partir du mode (`'dark'` | `'light'`)
  - `getGraphColumnColour(index)` : fonction qui indexe dans la palette avec modulo
  - `generateRainbowGradient` : appel du `createRainbowTheme` copié en phase 1.4
- Le composable consomme `useThemeContext()` (pour `theme` et `colours`) ET `useGitContext()` (pour `graphData.positions`)
- **Imports depuis la phase 1** :
  - Les palettes prédéfinies (`neonAuroraDarkColours`, `neonAuroraLightColours`) s'importent depuis `../utils/colors` (pas depuis `../types` — ce sont des valeurs runtime, pas des types)
  - La fonction `createRainbowTheme` s'importe depuis `../utils/createRainbowTheme` (pas de barrel `utils/index.ts`, imports directs vers les fichiers individuels)
  - L'interface `ThemeFunctions` (type de retour du composable) est déjà définie dans `../types.ts`, ainsi que `GetCommitNodeColoursArgs` et `CommitNodeColours`
- **Note** : `ThemeContextProvider` dans le code source appelle `useTheme()`, ce qui crée une dépendance circulaire à bien comprendre lors de l'implémentation
- Retourne les **11 valeurs/fonctions** suivantes :
  - `theme` — mode du thème (passthrough)
  - `hoverColour` — couleur de fond au hover (computed)
  - `textColour` — couleur du texte (computed)
  - `hoverTransitionDuration` — durée de transition hover (constante: 0.3)
  - `shiftAlphaChannel(rgb, opacity)` — blend linéaire couleur avec noir (dark) ou blanc (light)
  - `reduceOpacity(rgb, opacity)` — convertit `rgb()` en `rgba()`
  - `getGraphColumnColour(columnIndex)` — couleur de colonne (avec modulo)
  - `getCommitColour(commit)` — couleur d'un commit selon sa position
  - `getCommitNodeColours(args)` — `{ backgroundColour, borderColour }` pour un nœud
  - `getGraphColumnSelectedBackgroundColour(columnIndex)` — couleur de fond sélection
  - `getTooltipBackground(commit)` — couleur de fond tooltip

**Critère de validation** : Le composable compile. Les `computed` et fonctions sont correctement typés.

#### Phase 2.4 : Composable useSelectCommit

**Entrée** : `hooks/useSelectCommit/useSelectCommit.ts`
**Sortie** : `src/components/GitLog/composables/useSelectCommit.ts`

- **Attention** : Contrairement à ce qu'on pourrait attendre, `useSelectCommit` ne gère PAS d'état propre. Il consomme `useGitContext()` pour lire `selectedCommit`/`previewedCommit` et appeler `setSelectedCommit`/`setPreviewedCommit`.
- Retourne un objet `{ selectCommitHandler: { onMouseOver, onMouseOut, onClick } }` — noter le **niveau d'imbrication** : les handlers sont dans un sous-objet `selectCommitHandler`, pas directement au niveau racine du retour :
  - `onMouseOver(commit)` — met le commit en preview (sauf s'il est déjà sélectionné)
  - `onMouseOut()` — efface le preview
  - `onClick(commit)` — toggle la sélection du commit

**Critère de validation** : Le composable compile et expose les bonnes signatures.

#### Phase 2.5 : Composable useColumnData

**Entrée** : `modules/Graph/strategies/Grid/hooks/useColumnData/`
**Sortie** : `src/components/GitLog/composables/useColumnData.ts`

- Convertir le hook qui appelle `GraphMatrixBuilder` (phase 1.3) pour calculer la matrice de colonnes
- `useMemo` → `computed()` — le calcul de la matrice est un `computed` basé sur `GraphData` et les commits visibles
- Gère le cas pagination (slice des commits)
- **Note** : `useColumnData` ne gère **PAS** le cas placeholder/skeleton. C'est `HTMLGridGraph.tsx` qui gère le rendu du skeleton en vérifiant `visibleCommits.length === 0` et en rendant `<SkeletonGraph />`. Le hook `useColumnData` ne fait que construire la matrice via `GraphMatrixBuilder`
- **Note sur le skeleton du graphe** : `HTMLGridGraph.tsx` **n'utilise PAS le hook** `usePlaceholderData()` pour le skeleton — il importe directement les données statiques (`placeholderCommits`) depuis `data.ts` (import direct du fichier, pas du hook). C'est `Table.tsx` qui utilise le hook `usePlaceholderData()` pour ses lignes placeholder.
- **Important** : Le hook retourne **deux valeurs** : `columnData` (la matrice de colonnes) et `virtualColumns` (nombre de colonnes virtuelles). `virtualColumns` est utilisé dans `GraphCore.tsx` pour ajuster `graphWidth` : `graphWidth + virtualColumns`
- **Note sur le type de `columnData`** : Le type réel est `RowIndexToColumnStates = Map<number, GraphMatrixColumns>`, où `GraphMatrixColumns` est une **classe** wrapper autour de `GraphColumnState[]` avec des méthodes utilitaires (`update()`, `hasCommitNode()`) et des accesseurs (`columns`, `length`). Cette classe est copiée en phase 1.3 avec le reste du `GraphMatrixBuilder/` et doit être utilisée telle quelle.
- **Imports depuis la phase 1** : `GraphMatrixBuilder`, `GraphMatrixColumns`, et `GraphColumnState` s'importent tous depuis `../graph/GraphMatrixBuilder` (barrel `index.ts` qui réexporte `types.ts` et `GraphMatrixBuilder.ts`). Note : `GraphColumnState` a été placé dans `graph/GraphMatrixBuilder/types.ts` en phase 1.3 (et non dans un répertoire de composant).

**Critère de validation** : Le composable compile. Le `computed` retourne le bon type (`Map<number, GraphMatrixColumns>`) et `virtualColumns`.

#### Phase 2.6 : Composable usePlaceholderData

**Entrée** : `modules/Graph/strategies/Grid/hooks/usePlaceholderData/` (dont `usePlaceholderData.ts`, `data.ts`, `types.ts`)
**Sortie** : `src/components/GitLog/composables/usePlaceholderData.ts` + `src/components/GitLog/graph/placeholderData.ts`

- Copier les données statiques de placeholder (`data.ts` — commits factices pour le skeleton graph). **Note** : ces données sont utilisées de deux façons dans le code source : (1) importées directement par `HTMLGridGraph.tsx` pour le `SkeletonGraph` (import du fichier `data.ts`, pas du hook), et (2) via le hook `usePlaceholderData()` par `Table.tsx` pour les lignes placeholder du tableau.
- Convertir le hook qui fournit les données de placeholder (utilisé par `Table.tsx`)

**Critère de validation** : Le composable compile. Les données de placeholder sont disponibles.

#### Phase 2.7 : Composable useResize

**Entrée** : `hooks/useResize/useResize.ts`
**Sortie** : `src/components/GitLog/composables/useResize.ts`

- Le hook original utilise `useMouse` de `@uidotdev/usehooks` pour tracker le mouvement de la souris pendant le drag
- **Le hook consomme aussi `useGitContext()`** pour lire `graphWidth` et appeler `setGraphWidth`. Cela signifie que `useResize` ne peut pas être implémenté indépendamment du `GitContext` — il doit être utilisé dans un composant descendant du `provide(GIT_CONTEXT_KEY, ...)`
- Option 1 : utiliser `useMouse` de `@vueuse/core`
- Option 2 : implémentation custom avec `mousedown`/`mousemove`/`mouseup` listeners
- Expose `width` (ref réactif — largeur actuelle du graphe), `ref` (template ref pour l'élément du graphe), `startResizing` (handler pour le mousedown du drag handle). **Note** : l'état interne `dragging` n'est pas exposé dans l'API publique du hook. La prop `enableResize` (défaut `false`) conditionne l'affichage du drag handle dans `GraphCore`. La largeur est contrainte entre 200px et 800px.

**Critère de validation** : Le composable compile. La logique de resize est fonctionnelle.

---

## Tasks tracking

- [x] Phase 2.1: Clés d'injection et types des contextes
- [x] Phase 2.2: Composables d'injection (useGitContext, useThemeContext, useGraphContext, useTableContext)
- [x] Phase 2.3: Composable useTheme
- [x] Phase 2.4: Composable useSelectCommit
- [x] Phase 2.5: Composable useColumnData
- [x] Phase 2.6: Composable usePlaceholderData
- [x] Phase 2.7: Composable useResize

## Decisions made during implementation

### Phase 2.1

- **`Readonly<Ref<T>>` for all reactive properties**: All context bag properties that represent reactive state or computed values use `Readonly<Ref<T>>`. This prevents consumers from accidentally mutating the ref directly -- mutations go through the provided setter functions. This applies uniformly to props-derived values, computed values, and internal state exposed as read-only.
- **Plain functions for setters/callbacks**: Functions like `setSelectedCommit`, `setPreviewedCommit`, `setGraphWidth`, `setNodeSize`, `setGraphOrientation` remain as plain functions (not wrapped in `Ref`), matching the Vue composable pattern where functions don't need reactivity wrappers.
- **`RowIndexToColumnStates` defined in keys.ts**: The `RowIndexToColumnStates` type alias (`Map<number, GraphMatrixColumns>`) is defined in `keys.ts` rather than in `graph/GraphMatrixBuilder/types.ts`, because it is specific to the context/composable layer and not to the matrix builder itself. The React source defines it in `hooks/useColumnData/types.ts`, so placing it in the composables layer is a reasonable analogue.
- **Custom node/tooltip as function types, not Vue-specific render types**: In the React source, `CustomCommitNode` and `CustomTooltip` are functions returning `ReactElement`. For the Vue port, the `GraphContextBag` uses generic function signatures `(props: CustomCommitNodeProps) => unknown` and `(props: CustomTooltipProps) => unknown`. The actual slot-based mechanism will be handled at the component level in later phases; these types serve as placeholders for the injection layer.

### Phase 2.2

- **Separate files rather than a single grouped file**: The spec allowed "4 fichiers dans `composables/` (ou un seul regroupé)". Each composable is in its own file (`useGitContext.ts`, `useThemeContext.ts`, `useGraphContext.ts`, `useTableContext.ts`) for consistency with the one-composable-per-file pattern established in the project and to keep each file focused on a single responsibility.
- **Named function declarations**: Used `export function useXxxContext()` rather than `export const useXxxContext = () =>` arrow functions. This produces clearer stack traces when the error is thrown and is consistent with Vue composable conventions.

### Phase 2.3

- **`ThemeFunctions` interface updated for Vue reactivity**: The `ThemeFunctions` interface in `types.ts` was modified so that `theme`, `hoverColour`, and `textColour` use `ComputedRef<T>` instead of plain values. In the React source, these are `useMemo` return values (plain values recalculated on dependency change). In Vue, the equivalent is `computed()` which returns `ComputedRef<T>`. Keeping plain types would lose reactivity. Functions (`shiftAlphaChannel`, `getCommitColour`, etc.) remain as plain functions since they read reactive refs at call time and don't need to be reactive themselves.
- **Named function declarations for all functions**: Consistent with Phase 2.2, the composable uses `export function useTheme()` and all internal helper functions use `function` declarations rather than arrow functions, for clearer stack traces and consistency.
- **`useCallback` replaced by plain functions**: In the React source, `useCallback` is used for stable function references across renders. In Vue, there is no equivalent concern since composables run once and functions capture reactive refs by closure. Plain functions reading `themeRef.value`, `coloursRef.value`, and `graphData.value` at call time are the idiomatic approach.
- **`theme` computed wrapping**: The composable returns `theme` as a `computed(() => themeRef.value)` rather than passing through `themeRef` directly. This ensures the return type matches `ComputedRef<ThemeMode>` (from the `ThemeFunctions` interface) and provides a clean layer of indirection from the injection source.

### Phase 2.4

- **`SelectCommitHandler` interface defined locally**: The React source defines `SelectCommitHandler` in a separate `types.ts` file within the hook's directory. Since the Vue composables follow a flat file structure (one file per composable, no sub-directories), the interface is exported directly from `useSelectCommit.ts`. This avoids an extra file for a single small interface.
- **`useCallback` replaced by plain functions**: Consistent with Phase 2.3, the React `useCallback` wrappers are replaced by plain named function declarations. In Vue, composables run once and the functions close over reactive refs, so stable references are guaranteed without any special mechanism.
- **Named function declarations for handlers**: Used `function handleMouseOver`, `function handleMouseOut`, `function handleClickCommit` rather than arrow functions, consistent with the pattern established in Phase 2.2 and 2.3.

### Phase 2.5

- **`visibleCommits` parameter as `Readonly<Ref<number>>`**: The React hook receives `visibleCommits` as a plain `number` via a props object (`GraphColumnDataProps`). In the Vue composable, it is accepted as a `Readonly<Ref<number>>` so it participates in the `computed` dependency tracking. The caller (the graph component) will pass a reactive ref.
- **Return type uses `ComputedRef` for both values**: Both `columnData` and `virtualColumns` are returned as `ComputedRef` rather than plain values. This ensures downstream consumers (e.g., the graph component providing `GraphContextBag`) get reactive values that update automatically when the input data changes.
- **Single internal `computed` split into two derived computeds**: The matrix building logic runs in a single `computed` (to avoid running the expensive builder twice), and its result is split into two separate `ComputedRef` properties via derived computeds. This keeps the return type clean while avoiding redundant computation.
- **`ColumnData` interface defined locally**: Following the Phase 2.4 pattern, the return type interface (`ColumnData`) is defined directly in `useColumnData.ts` rather than in a separate types file, since it is a small interface specific to this composable.
- **Named function declaration**: Consistent with Phases 2.2-2.4, uses `export function useColumnData()` for clearer stack traces.

### Phase 2.6

- **Static data split into a separate file (`graph/placeholderData.ts`)**: Following the spec's requirement that `HTMLGridGraph` imports the static data directly (not via the composable), the placeholder commits and columns are exported from `graph/placeholderData.ts`. The composable (`composables/usePlaceholderData.ts`) imports from that file and adds the `isPlaceholderSkeleton` flag. This keeps the two usage paths cleanly separated.
- **`placeholderColumns` renamed from `columns`**: The React source exports the column data as `columns`, which is too generic. Renamed to `placeholderColumns` for clarity and to avoid naming conflicts.
- **`PlaceholderDatum` and `PlaceholderData` interfaces defined locally**: Following the Phase 2.4/2.5 pattern, the types are exported directly from `usePlaceholderData.ts` rather than in a separate types file.
- **`placeholderData` returned as `ComputedRef`**: Although the input data is static, wrapping in `computed()` is consistent with the other composables' pattern. The `ComputedRef` type in the `PlaceholderData` interface signals to consumers that this is a reactive value accessed via `.value`.
- **Named function declaration**: Consistent with Phases 2.2-2.5, uses `export function usePlaceholderData()`.

### Phase 2.7

- **Custom mouse listeners instead of `useMouse` from `@vueuse/core`**: Although `@vueuse/core` is installed, the React source uses `useMouse` which tracks mouse position continuously on every frame, even when not dragging. A custom approach that only attaches `mousemove`/`mouseup` listeners to `window` when the user starts dragging (and removes them on mouse-up) is more efficient: zero overhead when not resizing, and no reactive mouse coordinate refs to maintain.
- **No internal `dragging` state ref**: The React source uses a `dragging` boolean state and a `useEffect` that watches `[dragging, mouse.x]`. Since the Vue implementation attaches/detaches event listeners on demand, there is no need for an explicit `dragging` ref. The presence of the `mousemove` listener itself represents the dragging state.
- **`shallowRef` for the container element**: The template ref for the graph container uses `shallowRef<HTMLDivElement | null>(null)` rather than `ref()`, since DOM elements should not be deeply reactive.
- **`onUnmounted` cleanup**: The composable registers an `onUnmounted` hook to remove any lingering event listeners, guarding against the case where the component unmounts while the user is mid-drag.
- **`ResizeState` interface defined locally**: Following the Phase 2.4-2.6 pattern, the return type interface is exported directly from `useResize.ts`.
- **`width` passes through `graphWidth` from context**: The `width` property in the return value is `graphWidth` from `useGitContext()`, which is already `Readonly<Ref<number>>`. No wrapping in `computed()` is needed.
- **Named function declaration**: Consistent with Phases 2.2-2.6, uses `export function useResize()`.

## Resolved questions and doubts

### Phase 2.1

- **Should `GitContextBag` be generic (`<T>`) like the React source?** No. The React source uses `GitContextBag<T>` to allow custom data on commits, but in the Vue port, the generic parameter adds complexity to `InjectionKey` typing (Vue's `inject()` does not propagate generics naturally). Since the consuming application will use its own specific commit type at the component level, the injection layer uses `Commit` (which is already `CommitBase & T` with `T = object` by default). This can be revisited if a concrete need arises.
- **`Readonly` from Vue vs TypeScript built-in?** Vue does not export a `Readonly` type; we use TypeScript's built-in `Readonly<T>` utility type combined with Vue's `Ref<T>`. The pattern `Readonly<Ref<T>>` makes the `.value` property read-only at the type level.

### Phase 2.2

- No questions or doubts arose during this phase. The implementation is a straightforward Vue equivalent of the React `useContext` pattern: `inject` + guard + return.

### Phase 2.4

- No questions or doubts arose during this phase. The logic is a direct translation of the React hook with reactive `.value` access replacing plain value reads.

### Phase 2.3

- **Should reactive properties in `ThemeFunctions` be `Ref` or `ComputedRef`?** `ComputedRef` is the correct choice. These values are derived from other reactive sources (theme mode, colours array) and are read-only. `ComputedRef<T>` is the Vue type for `computed()` return values and is a subtype of `Readonly<Ref<T>>`, so consumers can read `.value` but cannot write to them.
### Phase 2.5

- No questions or doubts arose during this phase. The logic is a direct translation of the React hook: the `useMemo` becomes a `computed`, and all reactive context values are accessed via `.value` inside the computed body.

- **Why not import `generateRainbowGradient` from `createRainbowTheme.ts`?** The spec mentions `createRainbowTheme` as a potential import, but `useTheme` does not call `generateRainbowGradient` directly. In the React source, it is the `ThemeContextProvider` that resolves the palette (calling `generateRainbowGradient` for rainbow themes). `useTheme` only consumes the already-resolved `colours` array from `useThemeContext()`. The import will be needed when implementing the provider component in a later phase.

### Phase 2.6

- No questions or doubts arose during this phase. The implementation is a direct translation of the React hook: `useMemo` becomes `computed`, and the static data is imported from the shared `placeholderData.ts` file.

### Phase 2.7

- No questions or doubts arose during this phase.
