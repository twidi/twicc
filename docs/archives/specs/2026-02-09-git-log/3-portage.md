# GitLog — Décisions de portage et analyse de portabilité

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)

## Documents de la spécification

1. [Contexte et présentation](./1-contexte.md)
2. [Architecture détaillée de la librairie source](./2-architecture-source.md)
3. **Décisions de portage et analyse de portabilité** (ce document)
4. [Plan de portage et structure de fichiers cible](./4-plan-index.md)

---

## 12. Décisions de portage

### 12.1. Ce qu'on porte

| Fonctionnalité | Décision | Notes |
|----------------|----------|-------|
| Stratégie HTML Grid | ✅ Oui | Seule stratégie retenue |
| Module Tags | ✅ Oui | Colonne branches/tags à gauche |
| Module Table | ✅ Oui | Colonnes message/auteur/date à droite |
| Pseudo-commit index | ✅ Oui | Pour voir le diff du working directory |
| Filtrage (`filter`) | ✅ Oui | Avec notre propre input de recherche |
| Resize du graphe | ✅ Oui | Conditionné par la prop `enableResize` (défaut `false` dans le source). Décider si on garde cette prop ou si on active le resize par défaut |
| Theming dark/light | ✅ Oui | |
| Palettes de couleurs | ✅ Oui | Prédéfinies + custom |
| Sélection/Preview de commits | ✅ Oui | Pour afficher le diff au clic |
| URLs externes | ✅ Oui | |
| Orientation flipped | ✅ Oui | |
| Custom nodes (scoped slot) | ✅ Oui | |
| Custom table rows (scoped slot) | ✅ Oui | |
| Custom tooltips (scoped slot) | ✅ Oui | Via nos composants WebAwesome |
| Pagination client-side | ✅ Oui | Le back retourne tout, pagination client |
| Algorithmes TS pur (data/) | ✅ Oui | Copie directe |
| GraphMatrixBuilder | ✅ Oui | Copie directe |
| SCSS Modules | ✅ Oui | Fonctionnent nativement avec Vite+Vue |

### 12.2. Ce qu'on NE porte PAS

| Fonctionnalité | Raison |
|----------------|--------|
| Stratégie Canvas 2D | Pas besoin, HTML Grid suffit |
| `react-tiny-popover` | Remplacé par nos composants WebAwesome |
| `@uidotdev/usehooks` | Remplacé par `@vueuse/core` si nécessaire |
| `classnames` | Syntaxe native Vue `:class` |
| Compound Component pattern (Children.forEach) | Named slots Vue, plus simple |
| `cloneElement` | Scoped slots Vue, non-problème |
| `GitLogPaged` (composant séparé server-side) | Un seul composant, pagination client-side |
| Storybook (package demo) | Pas besoin |

### 12.3. Décisions techniques

| Sujet | Décision |
|-------|----------|
| **Données** | Le back-end retourne tous les commits d'un coup |
| **Pagination** | Client-side uniquement, via prop `paging` ou Virtual Scroller |
| **Virtual Scroll** | Notre propre Virtual Scroller wrappera le composant |
| **Tooltips/Popovers** | Nos composants WebAwesome |
| **CSS** | SCSS Modules conservés tels quels, inline styles adaptés pour Vue |
| **Contextes React → Vue** | `provide/inject` avec Composition API |
| **Hooks React → Vue** | Composables Vue (`computed`, `ref`, `watch`, etc.) |
| **Render props → Vue** | Scoped slots |
| **Compound Components → Vue** | Named slots |
| **Dépendances conservées** | `dayjs`, `fastpriorityqueue` |
| **Langage** | TypeScript pour tout le composant GitLog (`.ts` + `<script setup lang="ts">`). Le reste du projet reste en JavaScript. Un `tsconfig.json` scopé au dossier `src/components/GitLog/` est ajouté, sans impact sur le code JS existant. `typescript` est ajouté en devDependency. |

---

## 13. Analyse de portabilité vers Vue 3

### 13.1. Code TypeScript pur réutilisable directement (~50-60%)

Ces fichiers n'ont **aucune dépendance React** et seront copiés quasi tels quels :

**Algorithmes de layout (cœur de la lib)** :
```
data/
  GraphDataBuilder.ts
  ActiveBranches.ts
  ActiveNodes.ts
  computeRelationships.ts
  temporalTopologicalSort.ts
  types.ts
```

**Construction de la matrice de rendu** :
```
GraphMatrixBuilder/
  GraphMatrixBuilder.ts
  GraphMatrix.ts
  GraphMatrixColumns.ts
  GraphEdgeRenderer.ts
  BranchingEdgeRenderer.ts
  VirtualEdgeRenderer.ts
  types.ts
```

> **Note importante sur `GraphMatrixColumns`** : `GraphMatrixColumns.ts` contient une **classe** (pas juste des types) qui sert de wrapper autour de `GraphColumnState[]`. Elle expose des méthodes utilitaires (`update()`, `hasCommitNode()`) et des accesseurs (`columns`, `length`). Cette classe est utilisée intensivement par le `GraphMatrixBuilder` et par `HTMLGridGraph.tsx`. Le type `RowIndexToColumnStates` utilisé dans le hook `useColumnData` est défini comme `Map<number, GraphMatrixColumns>` (et non `Map<number, GraphColumnState[]>`). Il faut comprendre cette classe comme une dépendance critique lors du portage.

**Types** :
```
types/Commit.ts
types/GitLogEntry.ts
data/types.ts
```

**Utilitaires** :
```
hooks/useTheme/createRainbowTheme.ts
modules/Graph/utils/getMergeNodeInnerSize.ts
modules/Graph/utils/getColumnBackgroundSize.ts
modules/Graph/strategies/Grid/utility/isColumnEmpty.ts
modules/Graph/strategies/Grid/utility/getEmptyColumnState.ts
modules/Tags/utils/formatBranch.ts
constants/constants.ts
```

### 13.2. Hooks React → Composables Vue

| Hook React | Occurrences | Équivalent Vue | Difficulté |
|------------|-------------|----------------|------------|
| `useMemo` | ~40+ | `computed()` | FACILE |
| `useCallback` | ~15 | Fonction simple | FACILE |
| `useState` | ~10 | `ref()` | FACILE |
| `useRef` | ~4 | `ref()` ou `useTemplateRef()` | FACILE |
| `useContext` / `createContext` | 4 | `provide/inject` | MOYEN |
| `useEffect` | ~5 | `watch()` / `onMounted()` | MOYEN |
| `Children.forEach` + `isValidElement` | 1 | Named slots (plus besoin) | SUPPRIMÉ |
| `cloneElement` | 1 | Scoped slots (plus besoin) | SUPPRIMÉ |

**Custom hooks à convertir en composables Vue** :

| Hook | Fichier | Complexité |
|------|---------|------------|
| `useTheme` | hooks/useTheme/useTheme.ts | ÉLEVÉ (expose 11 fonctions/valeurs, dépend de `useThemeContext` ET `useGitContext`) |
| `useResize` | hooks/useResize/useResize.ts | MOYEN (utilise `useMouse` de `@uidotdev/usehooks` et consomme `useGitContext()` pour `graphWidth`/`setGraphWidth`) |
| `useSelectCommit` | hooks/useSelectCommit/useSelectCommit.ts | MOYEN |
| `useColumnData` | modules/Graph/strategies/Grid/hooks/useColumnData | MOYEN (consomme `useGitContext()` avec de nombreuses propriétés et appelle `GraphMatrixBuilder` avec des paramètres complexes) |
| `usePlaceholderData` | modules/Graph/strategies/Grid/hooks/usePlaceholderData | FACILE. **Note** : le hook `usePlaceholderData()` est utilisé par `Table.tsx` pour les lignes placeholder. Le graphe (`HTMLGridGraph.tsx`) n'utilise **pas** ce hook — il importe directement les données statiques (`placeholderCommits`) depuis `data.ts`. |
| `useGitContext` | context/GitContext/useGitContext.ts | FACILE (`inject()`) |
| `useThemeContext` | context/ThemeContext/useThemeContext.ts | FACILE (`inject()`) |
| `useGraphContext` | modules/Graph/context/useGraphContext.ts | FACILE (`inject()`) |

### 13.3. Dépendances externes à remplacer

| Package React | Usage | Remplacement Vue |
|---------------|-------|------------------|
| `react-tiny-popover` | Tooltips sur les nœuds et les tags. **Configurations distinctes** : `CommitNode` utilise `positions={['top', 'bottom']}`, `padding={0}`, `ArrowContainer` et `z-index: 20` ; `BranchTag` utilise `positions='right'`. | Nos composants WebAwesome (reproduire les positionnements distincts) |
| `@uidotdev/usehooks` | Hook `useMouse` dans useResize | `@vueuse/core` (`useMouse`) ou implémentation custom |
| `classnames` | Composition de classes CSS conditionnelles | Syntaxe native Vue `:class="{ active: isActive }"` |
| `dayjs` | Formatage de dates | **Conservé** (pas spécifique React) |
| `fastpriorityqueue` | File de priorité dans ActiveNodes | **Conservé** (pas spécifique React) |

### 13.4. Patterns React les plus complexes à adapter

**Par ordre de difficulté décroissante** :

1. **Compound Component + Children Analysis** (`useCoreComponents.ts`) — Utilise `Children.forEach` et `isValidElement` pour analyser les enfants et les distribuer. **En Vue** : remplacé par des **named slots**, ce qui est plus simple et idiomatique. Le hook `useCoreComponents` disparaît complètement.

2. **`cloneElement`** (`TableRow.tsx`) — Injecte des event handlers dans un élément React retourné par le custom row render prop. **En Vue** : le wrapper `<div>` parent porte les handlers, ou on utilise des scoped slots avec les handlers exposés. **Non-problème.**

3. **Inline Styles Dynamiques Massifs** — La lib utilise énormément les inline styles pour les couleurs/dimensions. En Vue, la syntaxe est légèrement différente (`:style="{ ... }"` au lieu de `style={{ ... }}`), mais c'est un travail mécanique de conversion.

4. **Static Properties Pattern** (`GitLog.Tags = Tags`) — Pas d'équivalent en Vue pour attacher des composants comme propriétés statiques. Les sous-composants seront importés séparément.

### 13.5. Exemples de conversion React → Vue

**Contexte React → provide/inject Vue :**

```tsx
// React
const GitContext = createContext<GitContextBag>(...)
const useGitContext = () => useContext(GitContext)

// Provider
<GitContext.Provider value={value}>
  {children}
</GitContext.Provider>
```

```vue
<!-- Vue -->
<script setup lang="ts">
import { provide, computed, ref } from 'vue'
import { GIT_CONTEXT_KEY } from './keys'

const selectedCommit = ref<Commit>()
provide(GIT_CONTEXT_KEY, {
  selectedCommit: readonly(selectedCommit),
  setSelectedCommit: (c?: Commit) => { selectedCommit.value = c },
})
</script>
<template>
  <slot />
</template>
```

```typescript
// Composable Vue
import { inject } from 'vue'
import { GIT_CONTEXT_KEY } from './keys'

export const useGitContext = () => {
  const ctx = inject(GIT_CONTEXT_KEY)
  if (!ctx) throw new Error('useGitContext must be used within GitLog')
  return ctx
}
```

**Hook React → Composable Vue :**

```tsx
// React
const hoverColour = useMemo(() => {
  if (theme === 'dark') return 'rgba(70,70,70,0.8)'
  return 'rgba(231, 231, 231, 0.5)'
}, [theme])
```

```typescript
// Vue
const hoverColour = computed(() => {
  if (theme.value === 'dark') return 'rgba(70,70,70,0.8)'
  return 'rgba(231, 231, 231, 0.5)'
})
```

**Compound Component → Named Slots :**

```tsx
// React
<GitLog entries={entries} currentBranch="main">
  <GitLog.Tags />
  <GitLog.GraphHTMLGrid nodeSize={20} />
  <GitLog.Table />
</GitLog>
```

```vue
<!-- Vue -->
<GitLog :entries="entries" current-branch="main">
  <template #tags><GitLogTags /></template>
  <template #graph><GitLogGraphHTMLGrid :node-size="20" /></template>
  <template #table><GitLogTable /></template>
</GitLog>
```

**Render Prop → Scoped Slot :**

```tsx
// React
<GitLog.GraphHTMLGrid
  node={({ nodeSize, colour }) => (
    <div style={{ width: nodeSize, border: `2px solid ${colour}` }} />
  )}
/>
```

```vue
<!-- Vue -->
<GitLogGraphHTMLGrid>
  <template #node="{ nodeSize, colour }">
    <div :style="{ width: `${nodeSize}px`, border: `2px solid ${colour}` }" />
  </template>
</GitLogGraphHTMLGrid>
```

### 13.6. Estimation de complexité par module

| Module | Fichiers | Complexité | Raison |
|--------|----------|------------|--------|
| Data Layer (algorithmes) | 8 | NULLE | TypeScript pur, copie directe |
| GraphMatrixBuilder | 7 | NULLE | TypeScript pur, copie directe |
| Types & Constants | 5+ | NULLE | TypeScript pur, copie directe |
| Utilitaires (couleurs, etc.) | 5+ | NULLE | TypeScript pur, copie directe |
| Contextes → provide/inject | 3 | MOYEN | Restructuration des types réactifs |
| Custom Hooks → Composables | 7 | MOYEN à ÉLEVÉ | `useMemo`→`computed`, `useState`→`ref`. `useTheme` est le plus complexe (11 fonctions/valeurs, double dépendance `useThemeContext` + `useGitContext`). `useSelectCommit` ne gère pas d'état propre mais consomme `useGitContext`. |
| GitLogCore + Layout | 2 | ÉLEVÉ | Compound component → named slots |
| GraphHTMLGrid + Grid components | 15+ | ÉLEVÉ | JSX → templates Vue, inline styles |
| Table components | 6+ | ÉLEVÉ | Render props → scoped slots |
| Tags components | 8+ | MOYEN | Composants simples, quelques hooks |
| CSS Modules | 32 | FACILE | SCSS modules fonctionnent nativement en Vue avec Vite |
