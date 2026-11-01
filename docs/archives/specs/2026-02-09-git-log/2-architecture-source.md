# GitLog — Architecture détaillée de la librairie source

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)

## Documents de la spécification

1. [Contexte et présentation](./1-contexte.md)
2. **Architecture détaillée de la librairie source** (ce document)
3. [Décisions de portage et analyse de portabilité](./3-portage.md)
4. [Plan de portage et structure de fichiers cible](./4-plan-index.md)

---

## 3. Architecture de la librairie source

### Pattern Compound Components

Le composant principal utilise le pattern **Compound Components** de React. L'utilisateur compose l'interface en imbriquant des sous-composants :

```tsx
<GitLog entries={entries} currentBranch="main" theme="dark" colours="rainbow-dark">
  <GitLog.Tags />
  <GitLog.GraphHTMLGrid nodeSize={16} />  {/* OU GraphCanvas2D, l'un OU l'autre */}
  <GitLog.Table timestampFormat="relative" />
</GitLog>
```

Les sous-composants disponibles :
- `GitLog.Tags` / `GitLogPaged.Tags` : Colonne des branches et tags (à gauche)
- `GitLog.GraphHTMLGrid` / `GitLogPaged.GraphHTMLGrid` : Graphe en HTML Grid (CSS Grid)
- `GitLog.GraphCanvas2D` / `GitLogPaged.GraphCanvas2D` : Graphe en Canvas 2D
- `GitLog.Table` / `GitLogPaged.Table` : Tableau des métadonnées (message, auteur, date)

Le hook interne `useCoreComponents` valide qu'il y a au maximum 1 Tags, 1 Graph (HTML Grid OU Canvas 2D), 1 Table, et qu'on ne mixe pas les deux stratégies de rendu.

### Hiérarchie des composants

```
GitLog (ou GitLogPaged)
  └── GitLogCore
      └── GitContext.Provider              # Fournit les données git à tous les enfants
          └── ThemeContextProvider          # Fournit le thème (couleurs, mode)
              └── Layout                    # Mise en page flex (tags | graph | table)
                  ├── Tags                  # Colonne branches/tags (gauche)
                  │   └── BranchTag         # Pour chaque commit avec branche/tag
                  │       ├── BranchIcon
                  │       ├── BranchLabel
                  │       ├── TagIcon
                  │       ├── TagLabel
                  │       ├── Link
                  │       └── BranchTagTooltip
                  ├── GraphHTMLGrid (ou GraphCanvas2D)  # Wrapper public (modules/Graph/)
                  │   └── GraphCore                     # Core (modules/Graph/core/)
                  │       └── GraphContext.Provider
                  │           └── [HTMLGridGraph OU Canvas2DGraph]  # Stratégie (strategies/Grid/ ou Canvas/)
                  └── Table                 # Colonne tableau (droite)
                      ├── [En-têtes tableau]  # "Commit message", "Author", "Timestamp" (rendus dans Table.tsx, PAS dans Layout)
                      └── TableContext.Provider
                          └── TableContainer
                              └── TableRow (x N)
                                  ├── CommitMessageData
                                  ├── AuthorData
                                  ├── TimestampData
                                  └── IndexStatus (si index visible)
```

> **Note importante sur l'indirection du module Graph** : Il y a **3 niveaux** de composants pour le graphe :
> 1. **`GraphHTMLGrid.tsx`** (dans `modules/Graph/`) — **wrapper public** exposé à l'utilisateur. Il porte les props (`nodeSize`, `nodeTheme`, `breakPointTheme`, `orientation`, `enableResize`, `showCommitNodeHashes`, `showCommitNodeTooltips`, `highlightedBackgroundHeight`, `tooltip`, `node`) et les passe à `<GraphCore>`.
> 2. **`GraphCore.tsx`** (dans `modules/Graph/core/`) — gère le `GraphContext.Provider`, le resize, le calcul des commits visibles, et le `useColumnData`.
> 3. **`HTMLGridGraph.tsx`** (dans `modules/Graph/strategies/Grid/`) — le rendu CSS Grid proprement dit. Ne prend **aucune prop** — il utilise `useGraphContext()` et `useGitContext()` pour obtenir ses données.
>
> Le `Layout` reçoit les composants `tags`, `graph`, `table` comme des `ReactElement` via props (extraits par `useCoreComponents` depuis les children), pas des slots directs.
>
> **Note importante sur les en-têtes** : Les en-têtes "Branch / Tag" et "Graph" sont rendus dans `Layout.tsx` (conditionnés par `showHeaders` du `GitContext`), mais les en-têtes du tableau ("Commit message", "Author", "Timestamp") sont rendus **dans `Table.tsx`** lui-même (lignes 50-83), pas dans le Layout. Cette distinction est importante pour le portage.

### Contextes React (4 niveaux imbriqués)

1. **GitContext** (le plus externe)
   - Données brutes du graphe (`graphData: GraphData<T>` — commits, positions, edges)
   - État de sélection/preview (`selectedCommit`, `setSelectedCommit`, `previewedCommit`, `setPreviewedCommit`)
   - `enableSelectedCommitStyling`, `enablePreviewedCommitStyling` — activation du styling de sélection/preview
   - Indicateurs de visibilité : `showBranchesTags`, `showTable`, `showHeaders`
   - Configuration layout : `rowSpacing`, `nodeSize`, `setNodeSize`, `graphWidth`, `setGraphWidth`, `graphOrientation`, `setGraphOrientation`
   - Branche courante : `currentBranch`, `headCommit`, `headCommitHash`, `indexCommit`
   - Données remote : `remoteProviderUrlBuilder` (URLs externes), `indexStatus`
   - Pagination : `paging: GraphPaging` (avec `startIndex/endIndex`, type interne distinct du type public `GitLogPaging` avec `page/size`), `isIndexVisible`, `isServerSidePaginated`
   - Filtrage : `filter?: CommitFilter`
   - Styling : `classes?: GitLogStylingProps`
   - Fourni par `GitLogCore`

2. **ThemeContext** (intermédiaire)
   - `theme: ThemeMode` — mode du thème (`'light'` | `'dark'`)
   - `colours: string[]` — palette de couleurs résolue (tableau de couleurs RGB)
   - Ne contient PAS les fonctions de theming (`hoverColour`, `textColour`, `getGraphColumnColour`, etc.) qui sont exposées par le hook `useTheme()` (voir section 7)
   - Fourni par `ThemeContextProvider`
   - **Note sur le mécanisme de "bootstrap"** : `ThemeContextProvider` appelle `useTheme()` pour résoudre les couleurs (notamment `shiftAlphaChannel` pour le dark mode). Ce n'est pas une vraie dépendance circulaire mais plutôt un appel au contexte du **niveau parent** : quand `ThemeContextProvider` appelle `useTheme()`, le `ThemeContext` qu'il s'apprête à `provide` n'existe **pas encore**. `useTheme()` consomme donc le `ThemeContext` **parent** (s'il existe) via `useThemeContext()`, qui retourne les valeurs par défaut de React (`createContext(...)` initial values) puisqu'aucun `ThemeContext.Provider` parent n'est présent. En parallèle, `useTheme()` consomme aussi `useGitContext()` (le `GitContext` qui vient d'être `provide`d par `GitLogCore`). **Point critique** : dans ce contexte de bootstrap, `ThemeContextProvider` n'a besoin de `useTheme()` que pour la fonction `shiftAlphaChannel`, qui ne dépend que de `theme` (le mode dark/light, passé comme prop à `ThemeContextProvider`) et **pas** de `colours` (la palette, qui est justement en train d'être calculée). Lors du portage Vue, il pourrait être plus simple de passer `theme` directement à `shiftAlphaChannel` plutôt que via le contexte, évitant ainsi la pseudo-dépendance circulaire. **Impact pour le portage Vue** : en Vue, `inject()` sans `provide` parent retournera `undefined` (ou la valeur par défaut si fournie via le second argument). Il faudra peut-être restructurer cette logique ou fournir des valeurs par défaut explicites au `inject()` du `ThemeContext` dans `useTheme`

3. **GraphContext** (dans le graphe seulement)
   - `showCommitNodeHashes: boolean` — afficher les hashes à côté des nœuds
   - `showCommitNodeTooltips: boolean` — afficher les tooltips au survol des nœuds
   - `node?: CustomCommitNode` — nœud de commit personnalisé (render prop)
   - `highlightedBackgroundHeight?: number` — hauteur en px du fond coloré pour les lignes sélectionnées/prévisualisées
   - `nodeTheme: NodeTheme` — thème visuel des nœuds (`'default'` | `'plain'`)
   - `breakPointTheme: BreakPointTheme` — thème des indicateurs de rupture
   - `nodeSize: number` — diamètre en px des nœuds de commit
   - `graphWidth: number` — nombre max de branches concurrentes. **Note** : Dans `GraphCore.tsx`, la valeur passée au contexte est `graphData.graphWidth + virtualColumns` (où `virtualColumns` est calculé par `useColumnData`), pas simplement `graphData.graphWidth`.
   - `orientation: GraphOrientation` — orientation du graphe
   - `visibleCommits: Commit[]` — commits visibles après pagination
   - `isHeadCommitVisible: boolean` — le HEAD commit est-il visible
   - `columnData: RowIndexToColumnStates` — matrice de colonnes (données HTML Grid)
   - `tooltip?: CustomTooltip` — tooltip personnalisé (render prop)
   - Fourni par `GraphCore`

4. **TableContext** (dans le tableau seulement)
   - Format de timestamp
   - Fourni par `Table`

### Arborescence complète du code source de la bibliothèque

```
packages/library/src/
  index.ts                          # Point d'entrée, exporte tout
  types.ts                          # Types publics principaux (GitLogProps, GitLogCommonProps, etc.)
  GitLog.tsx                        # Composant <GitLog /> principal
  GitLogPaged.tsx                   # Composant <GitLogPaged /> (server-side pagination)

  assets/                           # Icônes SVG (branch, git, merge, tag, etc.)

  constants/
    constants.ts                    # ROW_HEIGHT=40, DEFAULT_NODE_SIZE=20, etc.

  types/
    Commit.ts                       # Type Commit<T>, CommitAuthor
    GitLogEntry.ts                  # Type GitLogEntry<T> (données d'entrée)
    svg.d.ts                        # Déclaration de types pour les imports SVG
    vite-env.d.ts                   # Référence aux types Vite client

  context/
    GitContext/                     # Contexte React principal (données git)
      index.ts                     # Réexport
      GitContext.ts
      useGitContext.ts
      types.ts
    ThemeContext/                   # Contexte React pour le theming
      index.ts                     # Réexport
      ThemeContext.ts
      ThemeContextProvider.tsx
      useThemeContext.ts
      types.ts

  data/                            # *** ALGORITHMES PURS (aucune dépendance React) ***
    index.ts                       # Réexport
    GraphDataBuilder.ts            # Algorithme principal de positionnement des commits
    ActiveBranches.ts              # Gestion des branches actives (colonnes)
    ActiveNodes.ts                 # File de priorité pour les nœuds actifs
    computeRelationships.ts        # Construit les maps parents/children
    temporalTopologicalSort.ts     # Tri topologique temporel (DFS)
    types.ts                       # GraphData, GraphEdge, CommitNodeLocation, EdgeType

  components/
    GitLogCore/                    # Composant interne commun
      index.ts                     # Réexport
      GitLogCore.tsx
      useCoreComponents.ts         # Validation et extraction des children
      types.ts
    Layout/                        # Mise en page (flex: tags | graph | table)
      index.ts                     # Réexport
      Layout.tsx
      Layout.module.scss
      types.ts

  hooks/
    useResize/                     # Hook pour le redimensionnement du graphe
      index.ts                     # Réexport
      useResize.ts
      types.ts                     # ResizeState
    useSelectCommit/               # Hook pour la sélection/preview des commits
      index.ts                     # Réexport
      useSelectCommit.ts
      types.ts                     # SelectCommitHandler
    useTheme/                      # Hook de theming + génération arc-en-ciel
      index.ts                     # Réexport
      useTheme.ts
      createRainbowTheme.ts
      types.ts                     # ThemeMode, NodeTheme, ThemeColours, ThemeFunctions, etc.

  modules/
    Graph/                         # MODULE GRAPHE (le cœur du rendu visuel)
      index.ts                     # Réexport
      types.ts                     # GraphOrientation, Canvas2DGraphProps, HTMLGridGraphProps
      constants.ts                 # GRAPH_MARGIN_TOP = 12 (marge en haut du graphe pour alignement)
      GraphCanvas2D.tsx            # Wrapper public Canvas 2D
      GraphHTMLGrid.tsx            # Wrapper public HTML Grid
      context/                     # GraphContext (taille nœuds, commits visibles, etc.)
        index.ts                   # Réexport
        GraphContext.ts
        useGraphContext.ts
        types.ts
      core/                        # GraphCore : composant partagé entre stratégies
        index.ts                   # Réexport
        GraphCore.tsx
        GraphCore.module.scss
      strategies/
        Canvas/                    # STRATÉGIE CANVAS 2D
          index.ts                 # Réexport
          Canvas2DGraph.tsx
          Canvas2DGraph.module.scss
          CanvasRenderer.ts        # Classe de rendu (impératif, 2D context, pur TS)
          types.ts                 # Types du Canvas
        Grid/                      # STRATÉGIE HTML GRID
          index.ts                 # Réexport
          types.ts                 # CustomCommitNode<T>, CustomCommitNodeProps<T> (types publics des render props)
          HTMLGridGraph.tsx         # (pas de .module.scss propre, utilise GraphCore.module.scss)
          hooks/
            useColumnData/         # Calcul matrice colonnes
              index.ts             # Réexport
              useColumnData.ts     # Hook principal (appelle GraphMatrixBuilder)
              types.ts             # Types du hook
            usePlaceholderData/    # Données skeleton (chargement)
              index.ts             # Réexport
              usePlaceholderData.ts
              data.ts              # Données statiques de placeholder (commits factices)
              types.ts
          GraphMatrixBuilder/      # *** ALGORITHME PUR - matrice de rendu ***
            index.ts               # Réexport
            GraphMatrixBuilder.ts
            GraphMatrix.ts
            GraphMatrixColumns.ts
            GraphEdgeRenderer.ts
            BranchingEdgeRenderer.ts
            VirtualEdgeRenderer.ts
            types.ts
          utility/
            isColumnEmpty.ts       # Vérifie si une colonne est vide
            getEmptyColumnState.ts # Retourne l'état par défaut d'une colonne vide
          components/              # Composants visuels du grid (chacun avec index.ts + .module.scss)
            GraphRow/
            GraphColumn/           # + GraphColumn.module.scss
            CommitNode/            # + CommitNode.module.scss
            CommitNodeTooltip/     # + CommitNodeTooltip.module.scss
            ColumnBackground/      # + ColumnBackground.module.scss
            VerticalLine/          # + VerticalLine.module.scss
            HorizontalLine/        # + HorizontalLine.module.scss
            CurvedEdge/            # + CurvedEdge.module.scss
            LeftDownCurve/         # + LeftDownCurve.module.scss
            LeftUpCurve/           # + LeftUpCurve.module.scss
            HeadCommitVerticalLine/ # + HeadCommitVerticalLine.module.scss
            IndexPseudoCommitNode/ # + IndexPseudoCommitNode.module.scss
            IndexPseudoRow/
            BreakPoint/            # + BreakPoint.module.scss
            SkeletonGraph/
      utils/
        getColumnBackgroundSize.ts
        getMergeNodeInnerSize.ts

    Table/                         # MODULE TABLE (métadonnées des commits)
      index.ts                     # Réexport
      Table.tsx
      Table.module.scss
      types.ts                     # TableProps, CustomTableRow, GitLogTableStylingProps
      constants.ts                 # HEADER_ROW_HEIGHT = 47, TABLE_MARGIN_TOP = 12
      context/                     # TableContext (format timestamp)
        index.ts                   # Réexport
        TableContext.ts
        useTableContext.ts
        types.ts
      components/
        TableContainer/            # + TableContainer.module.scss
        TableRow/                  # + TableRow.module.scss
        CommitMessageData/         # + CommitMessageData.module.scss
        AuthorData/                # + AuthorData.module.scss
        TimestampData/             # + TimestampData.module.scss
        IndexStatus/               # + IndexStatus.module.scss

    Tags/                          # MODULE TAGS (branches et tags)
      index.ts                     # Réexport
      Tags.tsx
      Tags.module.scss
      utils/
        formatBranch.ts
      components/
        BranchTag/                 # + BranchTag.module.scss
        BranchTagTooltip/          # + BranchTagTooltip.module.scss
        BranchLabel/               # + BranchLabel.module.scss
        BranchIcon/                # + BranchIcon.module.scss
        TagIcon/                   # + TagIcon.module.scss
        TagLabel/                  # + TagLabel.module.scss
        GitIcon/                   # + GitIcon.module.scss
        IndexLabel/                # + IndexLabel.module.scss
        Link/                      # + Link.module.scss
```

> **Note** : Chaque sous-dossier de composant contient un fichier `index.ts` de réexport et souvent un fichier `types.ts` pour les types locaux (non listés individuellement dans l'arborescence ci-dessus pour les sous-composants, sauf quand mentionné explicitement). Les fichiers `.module.scss` listés entre parenthèses sont les SCSS modules associés au composant du dossier.
>
> **Note** : Les fichiers de tests (`*.spec.ts`, `*.spec.tsx`), les snapshots (`__snapshots__/`) et le dossier `_test/` (contenant des helpers de test, des données de test comme `sleep/` et `sleep-paginated/`, ainsi que `vitest.setup.ts`) ne sont **pas listés** dans l'arborescence ci-dessus car ils ne seront pas portés. Le dossier `_test/` contient notamment `_test/data/gitLogParser.ts` (et son spec), `_test/data/sleep/` (données de test), `_test/data/sleep-paginated/` (données paginées), `_test/elements/` (helpers de test) et `_test/stubs.ts`.

---

## 4. Modèle de données

### 4.1. Données d'entrée : `GitLogEntry<T>`

C'est l'interface que le consommateur fournit. Définie dans `packages/library/src/types/GitLogEntry.ts`.

```typescript
export interface GitLogEntryBase {
  hash: string           // SHA unique du commit (ex: "abc123")
  branch: string         // Nom complet de la branche (ex: "refs/heads/main")
  parents: string[]      // SHA des commits parents (vide pour le premier commit)
  message: string        // Message du commit
  committerDate: string  // Date du commit (ISO 8601 ou format compatible)
  author?: CommitAuthor  // Auteur (optionnel)
  authorDate?: string    // Date de création originale (optionnelle)
}

// Le type est générique : T permet d'ajouter des champs personnalisés
export type GitLogEntry<T = object> = GitLogEntryBase & T

export interface CommitAuthor {
  name?: string
  email?: string
}
```

| Champ | Type | Requis | Description |
|-------|------|--------|-------------|
| `hash` | `string` | Oui | SHA unique du commit |
| `branch` | `string` | Oui | Ref Git complète (`refs/heads/main`, `refs/remotes/origin/feat`, `refs/tags/v1.0`) |
| `parents` | `string[]` | Oui | SHA des parents (vide pour le premier commit, 2+ pour un merge) |
| `message` | `string` | Oui | Message du commit |
| `committerDate` | `string` | Oui | Date du commit |
| `author` | `CommitAuthor` | Non | `{ name?, email? }` |
| `authorDate` | `string` | Non | Date de création originale (diffère si rebase/amend) |

### 4.2. Données internes : `Commit<T>`

En interne, la lib enrichit chaque `GitLogEntry` avec des champs calculés automatiquement par `computeRelationships()`. Défini dans `packages/library/src/types/Commit.ts`.

```typescript
export interface CommitBase {
  hash: string
  parents: string[]
  children: string[]       // ← calculé automatiquement
  branch: string
  message: string
  author?: CommitAuthor
  authorDate?: string
  committerDate: string
  isBranchTip: boolean     // ← calculé automatiquement
}

export type Commit<T = object> = CommitBase & T
```

| Champ ajouté | Calcul |
|--------------|--------|
| `children: string[]` | Inverse des `parents` — pour chaque commit, la liste des commits qui le référencent comme parent |
| `isBranchTip: boolean` | Si `headCommitHash` est fourni, seul le commit avec ce hash est marqué `true`. Sinon, tout commit sans enfant est marqué `true`. |

### 4.3. Représentation des branches

Les branches sont de simples chaînes au format ref Git complet :
- `refs/heads/main` — branche locale
- `refs/remotes/origin/feature/xyz` — branche distante
- `refs/tags/v1.0.0` — tag

La fonction `formatBranch()` retire les préfixes pour l'affichage :
```typescript
const formatBranch = (branchName: string) => {
  return branchName
    .replace('refs/heads/', '')
    .replace('refs/remotes/origin/', '')
    .replace('refs/tags/', '')
}
```

La détection tag vs branche se fait par `commit.branch.includes('tags/')`.

### 4.4. Données internes du graphe : `GraphData<T>`

Calculé par le pipeline de données, utilisé en interne. Défini dans `packages/library/src/data/types.ts`.

```typescript
export interface GraphData<T = unknown> {
  children: Map<string, string[]>           // hash → hashes enfants
  parents: Map<string, string[]>            // hash → hashes parents
  hashToCommit: Map<string, Commit<T>>      // hash → détails commit
  graphWidth: number                        // Nb max de branches concurrentes
  positions: Map<string, CommitNodeLocation> // hash → position [row, col]
  edges: GraphEdge[]                        // Toutes les arêtes à dessiner
  commits: Commit<T>[]                      // Commits triés
}

// Position d'un commit dans le graphe : [rowIndex, columnIndex]
export type CommitNodeLocation = [number, number]

export interface GraphEdge {
  from: CommitNodeLocation     // Position source
  to: CommitNodeLocation       // Position cible
  type: EdgeType               // Normal ou Merge
  rerouted: boolean            // true si l'arête a été reroutée (filtre actif)
}

export enum EdgeType {
  Normal = 'Normal',
  Merge = 'Merge'
}
```

### 4.5. Props communes : `GitLogCommonProps<T>`

L'interface `GitLogCommonProps<T>` (dans `types.ts` à la racine) regroupe les props partagées entre `GitLogProps` et `GitLogPagedProps`. **Note** : `currentBranch` n'est PAS dans `GitLogCommonProps` — il est défini uniquement dans `GitLogProps` (car `GitLogPaged` utilise `branchName` à la place). De même, `GitLogPagedProps` n'a **PAS** de prop `paging` (contrairement à `GitLogProps`) — c'est une différence clé entre les deux composants. Les props partagées sont :

```typescript
interface GitLogCommonProps<T> {
  entries: GitLogEntry<T>[]
  filter?: CommitFilter<T>
  theme?: ThemeMode
  colours?: ThemeColours | string[]
  showHeaders?: boolean                      // Affiche les en-têtes de colonnes ("Graph", "Commit message", etc.) — défaut: false
  rowSpacing?: number                        // Espacement entre les lignes (défaut: 0)
  urls?: GitLogUrlBuilder<T>
  defaultGraphWidth?: number                 // Largeur initiale du graphe en pixels (défaut: 300)
  indexStatus?: GitLogIndexStatus
  showGitIndex?: boolean
  onSelectCommit?: (commit?: Commit<T>) => void
  onPreviewCommit?: (commit?: Commit<T>) => void
  enableSelectedCommitStyling?: boolean      // Active le styling de sélection (défaut: true)
  enablePreviewedCommitStyling?: boolean     // Active le styling de preview (défaut: true)
  classes?: GitLogStylingProps               // Classes CSS custom pour le container
}
```

### 4.6. Pagination interne : `GraphPaging` vs `GitLogPaging`

La lib utilise **deux types distincts** pour la pagination :

- **`GitLogPaging`** (type public, dans `types.ts`) : utilisé par le consommateur avec `page` (numéro de page) et `size` (taille de page)
- **`GraphPaging`** (type interne, dans `context/GitContext/types.ts`) : utilisé en interne dans le `GitContextBag` avec `startIndex` et `endIndex` (indices de début/fin)

La conversion `page/size` → `startIndex/endIndex` est faite dans `GitLogCore`.

```typescript
// Public
export interface GitLogPaging {
  size: number   // Nombre de lignes par page
  page: number   // Numéro de page (commence à 0)
}

// Interne
export interface GraphPaging {
  startIndex: number  // Index de début (zero-based)
  endIndex: number    // Index de fin (zero-based)
}
```

### 4.7. État de la matrice de rendu : `GraphColumnState`

Pour la stratégie HTML Grid, chaque cellule de la grille a un état :

```typescript
export interface GraphColumnState {
  isNode?: boolean
  isHorizontalLine?: boolean
  isVerticalLine?: boolean
  isVerticalIndexLine?: boolean
  isLastRow?: boolean
  isFirstRow?: boolean
  mergeSourceColumns?: number[]
  isColumnAboveEmpty?: boolean
  isColumnBelowEmpty?: boolean
  isLeftDownCurve?: boolean
  isLeftUpCurve?: boolean
  isPlaceholderSkeleton?: boolean
  isTopBreakPoint?: boolean
  isBottomBreakPoint?: boolean
}
```

### 4.8. Types secondaires

Les types suivants existent dans le code source et sont utilisés par les hooks, composants et props. Ils ne sont pas détaillés dans les sections précédentes mais sont importants pour le portage :

| Type | Fichier source | Rôle |
|------|---------------|------|
| `ThemeFunctions` | `hooks/useTheme/types.ts` | Interface des 11 valeurs/fonctions retournées par `useTheme()` |
| `SelectCommitHandler` | `hooks/useSelectCommit/types.ts` | Interface retournée par `useSelectCommit` — contient un champ `selectCommitHandler: { onMouseOver, onMouseOut, onClick }` (un niveau d'imbrication : le hook retourne `{ selectCommitHandler: { ... } }`) |
| `ResizeState` | `hooks/useResize/types.ts` | Interface du retour de `useResize` (`width`, `ref`, `startResizing`) |
| `GetCommitNodeColoursArgs` | `hooks/useTheme/types.ts` | Arguments pour `getCommitNodeColours()` : `{ columnColour: string }` |
| `CommitNodeColours` | `hooks/useTheme/types.ts` | Retour de `getCommitNodeColours()` (`backgroundColour`, `borderColour`) |
| `GraphMatrixBuilderProps` | `GraphMatrixBuilder/types.ts` | Props d'entrée du `GraphMatrixBuilder` |
| `GitLogPagedProps<T>` | `types.ts` (racine) | Props du composant `GitLogPaged` (server-side pagination) |
| `GitLogUrls` | `types.ts` (racine) | Objet retourné par le builder d'URLs (`commit`, `branch`) |
| `GitLogUrlBuilderArgs<T>` | `types.ts` (racine) | Arguments passés au builder d'URLs |
| `GitLogUrlBuilder<T>` | `types.ts` (racine) | Type du builder d'URLs (fonction) |

### 4.9. Format de données pour alimenter la lib

La lib attend un format spécifique. Le demo utilise cette commande git pour générer les données :

```bash
git log --all --format="hash:%h,parents:%p,branch:%D,msg:%s,cdate:%ci,adate:%ai,author:%an,email:%ae"
```

Avec un parser regex :
```typescript
const logRegex =
  /^hash:(?<hash>\S+),parents:(?<parents>.*?),branch:(?<branch>\S*),msg:(?<message>.+),cdate:(?<committerDate>[\d\- :+]+),adate:(?<authorDate>[\d\- :+]+),author:(?<author>.+),email:(?<email>.+)/
```

Côté back-end, il faudra un endpoint qui retourne les commits en JSON dans le format `GitLogEntry[]`.

---

## 5. Pipeline de traitement des données

Le traitement se fait en 4 étapes, dont les 3 premières sont en **TypeScript pur** (aucune dépendance React) :

```
GitLogEntry[] (données fournies par l'utilisateur)
    │
    ▼
computeRelationships()
    → parents: Map<hash, hash[]>
    → children: Map<hash, hash[]>
    → hashToCommit: Map<hash, Commit>
    │
    ▼
temporalTopologicalSort()
    → commits triés (DFS + tri par committerDate décroissant)
    │
    ▼
GraphDataBuilder.build()
    → positions: Map<hash, [row, col]>
    → edges: GraphEdge[]
    → graphWidth: number (nb max de branches concurrentes)
    │
    ▼
GraphData (stocké dans GitContext, distribué à tous les sous-composants)
```

Pour la stratégie HTML Grid, une étape supplémentaire :

```
GraphData
    │
    ▼
GraphMatrixBuilder (via useColumnData hook)
    → Matrice de colonnes avec états (isNode, isVerticalLine, isCurve, etc.)
```

### Algorithme de positionnement (`GraphDataBuilder`)

1. **Tri temporel-topologique** : Les commits sont d'abord triés par `committerDate` décroissant, puis un DFS est effectué sur cette liste triée
2. **Attribution des colonnes** : Algorithme glouton qui :
   - Remplace le commit enfant (de type branche, pas merge) dans sa colonne quand possible — un commit prend la colonne de son enfant-branche le plus proche
   - Insère dans la colonne la plus proche sinon
   - Gère les branches actives via `ActiveBranches`
   - Évite les collisions via `ActiveNodes` (file de priorité avec `fastpriorityqueue`)
3. **Calcul des edges** : Pour chaque commit, trace un edge vers chaque parent visible
   - Support du reroutage : quand un parent est filtré, on cherche l'ancêtre visible le plus proche via la classe `AncestorFinder` (DFS avec cache)
4. **Types d'edges** : `Normal` (branche linéaire) ou `Merge` (commit de merge)

La branche `currentBranch` est toujours assignée à la colonne 0 (à gauche en mode `normal`, à droite en mode `flipped`).

**Point important sur le `GraphDataBuilder`** : Le builder reçoit **deux listes** en entrée : `sortedCommits` (tous les commits triés) et `filteredCommits` (les commits après application du filtre). Le builder utilise `sortedCommits` pour le **positionnement** de tous les commits dans des colonnes (afin de maintenir la structure spatiale du graphe), mais utilise `filteredCommits` pour la **construction des edges** (via `findEdges()`). Lors du `findEdges()`, les positions sont remappées pour ne garder que les commits filtrés avec des indices de ligne consécutifs (`i + 1`). Ainsi, `graphData.commits` contient les commits déjà filtrés.

**Triple filtrage** : Le filtre est appliqué à **trois endroits** distincts :
1. Dans `GitLogCore.tsx` : le filtre est appliqué sur les commits triés **avant** la construction du graphe (`GraphDataBuilder.build()`) pour déterminer les `filteredCommits` passés au builder. Le résultat (`graphData.commits`) contient donc déjà les commits filtrés.
2. Dans `GraphCore.tsx` : le filtre est appliqué à nouveau sur les commits de `graphData` **après** la pagination (`paging.startIndex/endIndex`) pour déterminer les `visibleCommits`
3. Dans `Tags.tsx` : le filtre est appliqué sur les données de la colonne des tags pour n'afficher que les tags des commits visibles après filtrage
- **Note** : `Table.tsx` ne réapplique **PAS** le filtre — il utilise directement les commits de `graphData` (déjà filtrés au point 1) slicés par la pagination. Ce comportement est cohérent puisque `graphData.commits` est déjà filtré.

---

## 6. Stratégies de rendu du graphe

La lib offre **deux stratégies mutuellement exclusives** pour le rendu du graphe git.

### 6.1. Stratégie HTML Grid (retenue pour le port)

**Principe** : Le graphe est rendu en **CSS Grid** avec des éléments HTML/CSS/SVG individuels pour chaque cellule.

**Fonctionnement** :
1. Le graphe est modélisé comme une **matrice** (lignes × colonnes) via `GraphMatrixBuilder`
2. Chaque cellule de la grille CSS correspond à une colonne d'un commit
3. La grille utilise `grid-template-columns: repeat(N, 1fr)` et `grid-template-rows: repeat(M, ${ROW_HEIGHT + rowSpacing}px)` — la hauteur de chaque ligne inclut le `rowSpacing` configurable (défaut: 0)
4. Un `marginTop: GRAPH_MARGIN_TOP` (12px) est appliqué au conteneur pour l'alignement avec les éléments adjacents (tags, table)
5. Chaque cellule (`GraphColumn`) compose différents éléments visuels selon son état. **Note importante pour l'accessibilité** : `GraphColumn` est rendu comme un élément `<button>` (avec `tabIndex`, `onClick`, `onBlur`, `onFocus`, `onMouseOut`, `onMouseOver`), ce qui assure la navigation clavier et le focus. Ce choix doit être préservé lors du portage Vue.
6. **Note** : `HTMLGridGraph` n'a pas de fichier `.module.scss` propre, il utilise `GraphCore.module.scss`

**Éléments visuels et leur technologie** :

| Élément | Technologie | Description |
|---------|-------------|-------------|
| `CommitNode` | HTML `<div>` avec CSS (border-radius: 50%) | Cercle du nœud de commit |
| `VerticalLine` | HTML `<div>` avec `border-right` CSS | Lignes verticales de branches |
| `HorizontalLine` | HTML `<div>` avec `border-bottom` CSS | Lignes horizontales (merge) |
| `CurvedEdge` | SVG `<path>` inline | Courbes pour les branchements/merges |
| `LeftDownCurve` | SVG `<path>` + HTML `<div>` (lignes) | Courbe gauche-bas + lignes d'extension |
| `LeftUpCurve` | SVG `<path>` + HTML `<div>` (lignes) | Courbe gauche-haut + lignes d'extension |
| `ColumnBackground` | HTML `<div>` avec background CSS | Fond coloré pour sélection/preview |
| `BreakPoint` | HTML `<div>` avec CSS | Indicateurs de rupture (filtre actif) |
| `IndexPseudoCommitNode` | HTML `<div>` avec bordure pointillée | Nœud fictif pour le working directory |

**Les courbes SVG** utilisent des arcs elliptiques (commande SVG `A`, et non des courbes de Bézier quadratiques `Q`) :
- Taille SVG fixe : 24×24 pixels (`CURVE_SIZE`), viewBox `"0 0 100 100"`
- Courbe gauche→bas : `<path d="M 0,53 A 50,50 0 0,1 50,100" />`

**Avantages** :
- Éléments DOM accessibles (sélectionnables, cliquables, inspectables)
- Support des tooltips natifs
- Affichage des hashes de commit à côté des nœuds
- Support des nœuds de commit personnalisés (`node` prop)
- Support des tooltips personnalisés (`tooltip` prop)
- Meilleur pour l'accessibilité (boutons, focus, keyboard navigation)

**Inconvénients** :
- Plus lourd en DOM (beaucoup d'éléments pour chaque cellule)
- Potentiellement moins performant pour de très grands graphes

### 6.2. Stratégie Canvas 2D (NON retenue pour le port)

Pour mémoire : un seul élément `<canvas>` avec rendu impératif via `CanvasRenderingContext2D`. Le `CanvasRenderer.ts` est du TypeScript pur. Plus performant mais pas de custom nodes/tooltips, pas de hashes, accessibilité limitée.

**Note** : Si des problèmes de performance apparaissent avec le HTML Grid sur de gros repos, le `CanvasRenderer.ts` pourrait être porté ultérieurement comme alternative.

---

## 7. Système de theming

### 7.1. Modes dark / light

```typescript
type ThemeMode = 'light' | 'dark'
```

Le dark mode atténue automatiquement les couleurs via `shiftAlphaChannel(colour, 0.6)`, qui effectue un **blend** (mélange linéaire) de la couleur avec du noir (`rgb(0,0,0)` en dark mode) ou du blanc (`rgb(255,255,255)` en light mode), et non un simple changement d'opacité. Le résultat est une couleur `rgb()` opaque. Le mode adapte aussi les couleurs de fond :
- Hover : `rgba(70,70,70,0.8)` en dark, `rgba(231,231,231,0.5)` en light
- Texte : `rgb(255,255,255)` en dark, `rgb(0,0,0)` en light

### 7.2. Palettes de couleurs prédéfinies

```typescript
type ThemeColours = 'rainbow-dark' | 'rainbow-light' | 'neon-aurora-dark' | 'neon-aurora-light'
```

| Nom | Description |
|-----|-------------|
| `rainbow-light` | Arc-en-ciel généré dynamiquement (HSL, N couleurs) pour thème clair |
| `rainbow-dark` | Idem avec opacité réduite (0.6) pour thème sombre |
| `neon-aurora-dark` | 9 couleurs néon prédéfinies pour thème sombre |
| `neon-aurora-light` | 9 couleurs prédéfinies pour thème clair |
| `string[]` | Palette personnalisée (tableau de couleurs `rgb()`) |

**Palette neon-aurora-dark** (9 couleurs) :
```typescript
['rgb(0, 255, 128)', 'rgb(41, 121, 255)', 'rgb(201, 81, 238)', 'rgb(255, 160, 0)',
 'rgb(0, 184, 212)', 'rgb(103, 58, 183)', 'rgb(224, 33, 70)', 'rgb(0, 121, 107)', 'rgb(255, 193, 7)']
```

**Génération rainbow** : `generateRainbowGradient(n)` crée N couleurs uniformément réparties sur le spectre HSL (0-360°).

### 7.3. Attribution des couleurs

Les couleurs sont attribuées **par colonne du graphe** (pas par branche). Chaque colonne reçoit une couleur par index. Si le tableau a moins de couleurs que de colonnes, elles bouclent (modulo).

```typescript
const getGraphColumnColour = (columnIndex: number) => {
  const colour = colours[columnIndex]
  return colour || colours[columnIndex % colours.length]
}
```

Les nœuds de commit ont un background obtenu par **blend** (`shiftAlphaChannel`) à 0.15 de la couleur de colonne (mélange linéaire avec noir en dark mode ou blanc en light mode, produisant une couleur `rgb()` opaque — ce n'est PAS une simple opacité `rgba()`), et une bordure à la couleur pleine.

### 7.4. Fonctions de theming (`useTheme`)

Le hook `useTheme()` consomme `useThemeContext()` (pour `theme` et `colours`) ET `useGitContext()` (pour `graphData.positions`) et expose **11 valeurs/fonctions** :

| Retour | Type | Description |
|--------|------|-------------|
| `theme` | `ThemeMode` | Mode du thème (passthrough depuis ThemeContext) |
| `hoverColour` | `string` | Couleur de fond au hover (computed selon dark/light) |
| `textColour` | `string` | Couleur du texte (computed selon dark/light) |
| `hoverTransitionDuration` | `number` | Durée de la transition hover (constante: 0.3) |
| `shiftAlphaChannel(rgb, opacity)` | `(string, number) => string` | Blend linéaire d'une couleur avec noir (dark) ou blanc (light) |
| `reduceOpacity(rgb, opacity)` | `(string, number) => string` | Convertit `rgb()` en `rgba()` avec l'opacité donnée |
| `getGraphColumnColour(columnIndex)` | `(number) => string` | Couleur de la colonne (avec modulo si dépassement) |
| `getCommitColour(commit)` | `(Commit) => string` | Couleur d'un commit selon sa position dans le graphe |
| `getCommitNodeColours(args)` | `(args) => { backgroundColour, borderColour }` | Couleurs du nœud (fond = blend `shiftAlphaChannel` à 0.15, bordure = couleur pleine). `args` est de type `{ columnColour: string }` |
| `getGraphColumnSelectedBackgroundColour(columnIndex)` | `(number) => string` | Couleur de fond pour une colonne sélectionnée (0.15 opacité) |
| `getTooltipBackground(commit)` | `(Commit) => string` | Couleur de fond du tooltip (blend à 0.2 dark, 0.1 light) |

Ces fonctions sont essentielles pour le rendu et utilisées dans de nombreux composants (nœuds, lignes, fonds, tooltips, table).

### 7.5. Thèmes de nœuds

```typescript
type NodeTheme = 'default' | 'plain'
```
- `default` : Les merge commits ont un cercle intérieur plein (double cercle), visuellement distincts
- `plain` : Tous les nœuds sont identiques (cercle simple)

### 7.6. Thèmes de breakpoints

7 styles pour les indicateurs de rupture dans le graphe (quand un filtre masque des commits) :

```typescript
type BreakPointTheme = 'slash' | 'dot' | 'ring' | 'zig-zag' | 'line' | 'double-line' | 'arrow'
```

---

## 8. Système de pagination

### 8.1. Pagination client-side (`<GitLog>`)

- Prop `paging: { page: number, size: number }`
- Toutes les données sont chargées en mémoire
- La pagination est un simple `slice()` sur les commits triés
- Pas de virtual scroll intégré

```typescript
interface GitLogPaging {
  size: number  // Nombre de lignes par page
  page: number  // Numéro de page (commence à 0)
}
```

**Note** : En interne, la pagination client-side utilise un type `GraphPaging` avec `startIndex/endIndex` (voir section 4.6), distinct du type public `GitLogPaging` ci-dessus. La conversion est faite dans `GitLogCore`.

### 8.2. Pagination server-side (`<GitLogPaged>`)

- L'utilisateur gère l'état et charge les données page par page
- Nécessite `headCommitHash` et `branchName` en props supplémentaires
- Les edges virtuels (off-page) sont dessinés vers le haut/bas du graphe via `VirtualEdgeRenderer`

### 8.3. Décision pour notre port

**Le back-end retourne toutes les données d'un coup.** La pagination est gérée côté client. Notre propre Virtual Scroller wrappera le composant pour gérer le scroll sur de gros historiques :

1. Le back retourne tous les commits
2. L'algorithme de layout calcule les positions pour tous les commits
3. Le Virtual Scroller ne rend que les lignes visibles dans le viewport
4. On utilise la prop `paging` ou notre propre mécanisme de fenêtrage

---

## 9. Fonctionnalités détaillées

### 9.1. Pseudo-commit index (Working Directory)

Un pseudo-commit "index" est affiché au-dessus du HEAD commit. Il représente le working directory et l'index git.

```typescript
const indexCommit: Commit = {
  hash: 'index',
  branch: headCommit.branch,
  parents: [headCommit.hash],
  children: [],
  authorDate: today,
  message: '// WIP',
  committerDate: today,
  isBranchTip: false
}
```

- Peut afficher le nombre de fichiers modifiés/ajoutés/supprimés via `indexStatus`
- Désactivable via `showGitIndex={false}` (défaut : `true` — le pseudo-commit index est affiché par défaut)
- Le nœud est affiché avec une bordure pointillée (distinct des commits normaux)

```typescript
interface GitLogIndexStatus {
  modified: number
  added: number
  deleted: number
}
```

**Usage retenu** : Cliquer sur le pseudo-commit "index" permettra de voir le diff du working directory (fichiers non encore commités).

### 9.2. Sélection et Preview de commits

- **Preview** (hover) : Fond coloré sur la ligne survolée
- **Sélection** (click) : Fond coloré persistant avec couleur de la colonne
- Callbacks exposés : `onSelectCommit`, `onPreviewCommit`
- Désactivables individuellement via `enableSelectedCommitStyling`, `enablePreviewedCommitStyling`

```typescript
onSelectCommit?: (commit?: Commit<T>) => void   // click
onPreviewCommit?: (commit?: Commit<T>) => void   // hover
```

`commit` est `undefined` quand la sélection/preview est annulée.

### 9.3. Filtrage avec reroutage des edges

La prop `filter` permet de filtrer les commits affichés tout en **préservant la structure du graphe** :

```typescript
type CommitFilter<T> = (commits: Commit<T>[]) => Commit<T>[]

// Exemple : recherche dans les messages
filter={(commits) => commits.filter(c => c.message.includes('feat'))}
```

Quand un commit parent est filtré, l'algorithme reroute automatiquement les edges vers l'ancêtre visible le plus proche (DFS avec cache). Les points de rupture (breakpoints) sont affichés entre les commits pour indiquer les discontinuités.

### 9.4. URLs externes

La prop `urls` permet de construire des liens vers le provider git distant :

```typescript
urls={({ commit }) => ({
  commit: `https://github.com/user/repo/commit/${commit.hash}`,
  branch: `https://github.com/user/repo/tree/${commit.branch}`
})}
```

**Note** : Quand un commit a une URL configurée et qu'on clique dessus, le nœud effectue **deux actions simultanément** : il sélectionne le commit (via `onSelectCommit`) **et** ouvre l'URL dans un nouvel onglet. Ce double comportement est à conserver ou adapter lors du portage.

### 9.5. Orientation du graphe

```typescript
type GraphOrientation = 'normal' | 'flipped'
```
- `normal` : La branche principale est à gauche
- `flipped` : La branche principale est à droite (miroir horizontal)

### 9.6. Resize du graphe

Un drag handle sur le bord droit de la colonne graphe permet d'élargir ou rétrécir la zone du graphe. Le tableau à droite s'ajuste en conséquence. Utile quand il y a beaucoup de branches parallèles. Le resize est géré dans `GraphCore` (pas dans `Layout`).

Le resize est conditionné par la prop `enableResize` (dans `GraphPropsCommon`, défaut `false`). Le drag handle n'est affiché que si `enableResize` est `true`. La largeur est contrainte entre 200px et 800px (limites définies dans `useResize.ts`). **Note** : `useResize` consomme `useGitContext()` pour lire et mettre à jour `graphWidth` et `setGraphWidth`, ce qui en fait une dépendance directe au `GitContext`.

**Note** : Le `graphWidth` initial est calculé dans `GitLogCore.tsx` comme `graphData.graphWidth * (nodeSize + NODE_BORDER_WIDTH * 2)`, et `defaultGraphWidth` n'est utilisé que s'il est supérieur ou égal à cette valeur minimale. Il y a un **TODO dans le code source** (`GitLogCore.tsx` ligne 82) : `// TODO: Are we using graphWidth here or just ditching enableResize?` — ce qui suggère que l'implémentation du resize est peut-être incomplète dans la lib source elle-même. Ceci peut avoir un impact sur le portage.

---

## 10. Points de customisation

La lib offre **3 points d'extension via render props** (fonctions en tant que props) :

### 10.1. Custom Commit Node

Remplace le rendu des nœuds de commit dans le graphe HTML Grid :

```typescript
type CustomCommitNode<T> = (props: CustomCommitNodeProps<T>) => ReactElement

interface CustomCommitNodeProps<T> {
  commit: Commit<T>           // Détails du commit
  colour: string              // Couleur de la colonne
  rowIndex: number            // Index de la ligne (zero-based)
  columnIndex: number         // Index de la colonne (zero-based)
  nodeSize: number            // Diamètre en pixels
  isIndexPseudoNode: boolean  // true pour le pseudo-node de l'index Git
}
```

### 10.2. Custom Tooltip

Remplace le rendu des tooltips au survol des nœuds :

```typescript
type CustomTooltip = (props: CustomTooltipProps) => ReactElement<HTMLElement>

interface CustomTooltipProps {
  commit: Commit              // Commit survolé
  borderColour: string        // Couleur de bordure basée sur le thème
  backgroundColour: string    // Couleur de fond basée sur le thème
}
```

**Détails d'implémentation des popovers** : Les deux usages de `react-tiny-popover` dans le code source ont des configurations différentes :
- **`CommitNode.tsx`** : `<Popover positions={['top', 'bottom']} padding={0}>` avec `<ArrowContainer>` et un `z-index: 20` sur le contenu. Le `PopoverState` est utilisé pour positionner la flèche.
- **`BranchTag.tsx`** : `<Popover positions='right'>` — le positionnement est à droite (pas en haut/bas comme pour les commits).

Ces différences de positionnement devront être reproduites lors du remplacement par les composants WebAwesome.

### 10.3. Custom Table Row

Remplace le rendu des lignes du tableau :

```typescript
type CustomTableRow = (props: CustomTableRowProps) => ReactElement<HTMLElement>

interface CustomTableRowProps {
  commit: Commit              // Données du commit
  selected: boolean           // true si le commit est sélectionné
  previewed: boolean          // true si le commit est en preview (hover)
  backgroundColour: string    // Couleur de fond (change selon l'état)
}
```

### 10.4. Ce qui n'est PAS customisable dans la lib d'origine

- Les lignes verticales/horizontales/courbes du graphe
- Le layout des colonnes du graphe (toujours CSS Grid)
- Le composant Tags (pas de render prop pour les labels de branches)
- La hauteur des lignes (`ROW_HEIGHT = 40px`, constante fixe)
- Les polices (il faut utiliser les classes CSS)
- Le tooltip des tags de branches (composant `BranchTagTooltip` fixe)

### 10.5. Adaptation pour Vue : scoped slots au lieu de render props

En Vue, les 3 render props seront naturellement convertis en **scoped slots** :

```vue
<GitLogGraphHTMLGrid>
  <template #node="{ commit, colour, nodeSize, rowIndex, columnIndex, isIndexPseudoNode }">
    <div :style="{ width: `${nodeSize}px`, height: `${nodeSize}px`, border: `2px solid ${colour}` }" />
  </template>
  <template #tooltip="{ commit, borderColour, backgroundColour }">
    <MonComposantTooltip :commit="commit" />
  </template>
</GitLogGraphHTMLGrid>

<GitLogTable>
  <template #row="{ commit, selected, previewed, backgroundColour }">
    <div>{{ commit.message }}</div>
  </template>
</GitLogTable>
```

---

## 11. Système de styling CSS

### 11.1. SCSS Modules

Le styling structurel utilise des **SCSS Modules** (fichiers `.module.scss`). Il y a 32 fichiers SCSS modules dans la bibliothèque. Ils sont compilés par Vite avec le plugin `sass`, et les classes sont automatiquement scopées.

Les SCSS Modules fonctionnent nativement avec Vite + Vue (à condition d'avoir `sass` en devDependency). Aucune configuration supplémentaire nécessaire.

### 11.2. Inline styles dynamiques

La majorité du styling dynamique (couleurs, dimensions) est faite via des **inline styles** (`style={...}`), pas via des CSS variables. Exemples :

- Nœuds de commit : `backgroundColor`, `border`, `width`, `height`
- Lignes verticales : `borderRight`
- Lignes du tableau : `lineHeight`, `color`, `background` (linear-gradient)
- Fonds de sélection/preview : `backgroundColor` avec opacité

### 11.3. CSS Variables

L'usage est très limité — un seul cas : `--breakpoint-colour` dans `BreakPoint.tsx/module.scss`.

### 11.4. Pas de CSS-in-JS

La lib n'utilise ni styled-components, ni emotion. Le styling est un hybride de :
- **SCSS Modules** pour le layout structurel et les classes fixes
- **Inline styles** pour les valeurs dynamiques (couleurs, dimensions)
- **`classnames`** (package) pour la composition conditionnelle de classes CSS

### 11.5. Points d'injection CSS pour le consommateur

**Au niveau du composant racine** (via la prop `classes` de `GitLogCommonProps`) :

```typescript
interface GitLogStylingProps {
  containerClass?: string
  containerStyles?: CSSProperties
}
```

**Au niveau du composant Table** (dans `modules/Table/types.ts`, via la prop `styles` du composant `Table`) :

```typescript
interface GitLogTableStylingProps {
  table?: CSSProperties
  thead?: CSSProperties
  tr?: CSSProperties
  td?: CSSProperties
}
```

Le composant `Table` accepte aussi une prop `className` pour ajouter une classe CSS au conteneur du tableau.

### 11.6. Constantes visuelles fixes

**Constantes globales** (dans `constants/constants.ts`) :
```typescript
const ROW_HEIGHT = 40              // Hauteur de chaque ligne en px (NON configurable)
const DEFAULT_NODE_SIZE = 20       // Diamètre par défaut d'un nœud (configurable via prop)
const NODE_BORDER_WIDTH = 2        // Largeur de bordure d'un nœud
const BACKGROUND_HEIGHT_OFFSET = 16 // Padding autour du nœud
const CURVE_SIZE = 24              // Taille des courbes SVG
```

**Constantes du module Graph** (dans `modules/Graph/constants.ts`) :
```typescript
const GRAPH_MARGIN_TOP = 12        // Marge en haut du graphe pour alignement avec tags/table
```

**Constantes du module Table** (dans `modules/Table/constants.ts`) :
```typescript
const HEADER_ROW_HEIGHT = 47       // Hauteur de la ligne d'en-tête pour alignement avec le graphe
const TABLE_MARGIN_TOP = 12        // Marge en haut du tableau pour alignement avec le graphe
```
