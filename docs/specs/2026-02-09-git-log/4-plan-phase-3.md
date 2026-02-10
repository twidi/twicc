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

- [ ] Phase 3.1: Composant Layout.vue
- [ ] Phase 3.2: Composant GitLog.vue (racine)
