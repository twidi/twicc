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

- [ ] Phase 2.1: Clés d'injection et types des contextes
- [ ] Phase 2.2: Composables d'injection (useGitContext, useThemeContext, useGraphContext, useTableContext)
- [ ] Phase 2.3: Composable useTheme
- [ ] Phase 2.4: Composable useSelectCommit
- [ ] Phase 2.5: Composable useColumnData
- [ ] Phase 2.6: Composable usePlaceholderData
- [ ] Phase 2.7: Composable useResize
