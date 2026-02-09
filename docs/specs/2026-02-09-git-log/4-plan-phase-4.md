# GitLog — Plan de portage — Phase 4

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

---

### Phase 4 : Module Graph (HTML Grid)

Le plus gros morceau, découpé en sous-phases granulaires. Tous les composants vont dans `src/components/GitLog/graph/components/`.

#### Phase 4.1 : GraphCore.vue et HTMLGridGraph.vue (structure)

**Entrée** : `modules/Graph/core/GraphCore.tsx`, `modules/Graph/strategies/Grid/HTMLGridGraph.tsx` + SCSS modules
**Sortie** : `graph/GraphCore.vue`, `graph/HTMLGridGraph.vue` + SCSS

- `GraphCore.vue` :
  - **Fusionne les responsabilités** de `GraphHTMLGrid.tsx` (wrapper public) et `GraphCore.tsx` (core) du code source React
  - **Props** (issues de `GraphCoreProps` dans le source) : `nodeSize`, `nodeTheme`, `breakPointTheme`, `orientation`, `enableResize`, `showCommitNodeHashes`, `showCommitNodeTooltips`, `highlightedBackgroundHeight`, slot `#node` (custom commit node), slot `#tooltip` (custom tooltip)
  - `provide(GRAPH_CONTEXT_KEY, { ... })` avec toutes les valeurs du `GraphContextBag` : les props ci-dessus + `graphWidth` (calculé comme `graphData.graphWidth + virtualColumns`), `visibleCommits`, `isHeadCommitVisible`, `columnData`
  - Wrapper qui contient le slot pour le graphe (rendu par `HTMLGridGraph.vue`)
  - Le resize est géré dans `GraphCore` (pas dans `Layout`)
- `HTMLGridGraph.vue` :
  - Le conteneur CSS Grid principal (`display: grid`, `grid-template-columns`, `grid-template-rows`)
  - Utilise `useColumnData` pour calculer la matrice de colonnes
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

- `CommitNode.vue` : Cercle du nœud de commit. HTML `<div>` avec `border-radius: 50%`. Supporte le merge node (double cercle via `nodeTheme`). Gère le scoped slot `#node` pour le custom render. Affiche le hash à côté du nœud.
- `VerticalLine.vue` : `<div>` avec `border-right` CSS colorée. Reçoit la couleur de colonne en prop.
- `HorizontalLine.vue` : `<div>` avec `border-bottom` CSS colorée. Pour les merges (lignes horizontales entre colonnes).
- `ColumnBackground.vue` : `<div>` avec background coloré semi-transparent. Pour la mise en évidence au hover/sélection.

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
- `GraphRow.vue` : Conteneur d'une ligne de la grille. Itère sur les colonnes et rend un `GraphColumn` pour chacune.
- Brancher dans `HTMLGridGraph.vue` : remplacer les divs vides de la phase 4.1 par les vrais `GraphRow`

**Critère de validation** : Le graphe complet est rendu visuellement avec nœuds, lignes, courbes. La grille CSS est correcte.

#### Phase 4.6 : Interactions du graphe (tooltip, sélection, preview)

**Entrée** : Logique d'interaction de `CommitNode` et `GraphColumn` (hover, click, tooltip)
**Sortie** : Mise à jour des composants pour ajouter les interactions

- `CommitNode.vue` : Ajouter les événements `@click` (sélection via `useSelectCommit`) et `@mouseenter`/`@mouseleave` (preview)
- Tooltip : Intégrer le tooltip au survol du nœud. Utiliser les composants WebAwesome au lieu de `react-tiny-popover`. Supporter le scoped slot `#tooltip` pour le custom render.
- `ColumnBackground.vue` : Réagir à l'état `selectedCommit` et `previewedCommit` du contexte pour colorier le fond.
- `GraphRow.vue` ou wrapper : Propager les événements hover de la ligne complète pour le preview.

**Critère de validation** : Le hover met en évidence la ligne. Le clic sélectionne un commit. Le tooltip s'affiche au survol du nœud.

---

## Tasks tracking

- [ ] Phase 4.1 : GraphCore.vue et HTMLGridGraph.vue (structure)
- [ ] Phase 4.2 : Éléments visuels de base (lignes et nœuds)
- [ ] Phase 4.3 : Éléments visuels SVG (courbes)
- [ ] Phase 4.4 : Éléments spéciaux (breakpoints, index, skeleton)
- [ ] Phase 4.5 : Assemblage GraphRow et GraphColumn
- [ ] Phase 4.6 : Interactions du graphe (tooltip, sélection, preview)
