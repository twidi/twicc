# Review d'impact de la phase 2 sur les phases suivantes

> Ce document analyse les "Decisions made during implementation" et "Resolved questions and doubts" de la phase 2, et évalue leurs conséquences potentielles sur les phases 3 à 8.

---

## Résumé exécutif

La phase 2 a été implémentée fidèlement par rapport au plan. Plusieurs décisions structurantes ont été prises, notamment autour de la réactivité (`Readonly<Ref<T>>`, `ComputedRef<T>`) et de l'approche custom pour le resize. La majorité de ces décisions sont alignées avec les plans des phases suivantes, mais **quelques points méritent une attention particulière**, notamment le typage `ComputedRef` dans `ThemeFunctions` qui impacte la consommation dans les composants, et le traitement des custom node/tooltip comme fonctions génériques dans `GraphContextBag`.

---

## Points d'attention

### 1. `ThemeFunctions` modifié : `theme`, `hoverColour`, `textColour` sont des `ComputedRef<T>` — Impact sur Phases 4, 5, 6, 7

**Décision concernée** : Phase 2.3 — `ThemeFunctions` a été modifié pour que `theme`, `hoverColour` et `textColour` soient des `ComputedRef<T>` au lieu de valeurs simples.

**Impact** : Tous les composants des phases 4-7 qui consomment `useTheme()` (directement ou via `useThemeContext()`) devront accéder à ces propriétés via `.value`. Concrètement :

- **Phase 4** (Graph) : `CommitNode.vue`, `GraphColumn.vue`, `ColumnBackground.vue` et d'autres composants utiliseront `theme.value` dans les templates ou le script. Si le plan des phases 4-7 montre des exemples de code utilisant `theme` comme une valeur simple (sans `.value`), ils sont à corriger.
- **Phase 5** (Table) : `TableRow.vue` utilise la couleur hover pour le fond des lignes → `hoverColour.value`.
- **Phase 6** (Tags) : `BranchTag.vue` et sous-composants qui pourraient utiliser `textColour` → `.value` requis.
- **Phase 7** (Tooltips) : `CommitNodeTooltip.vue` utilise probablement le thème pour le style → `.value` requis.

**Nuance** : Dans les templates Vue, les `ComputedRef` sont automatiquement déballés (unwrapped) via le mécanisme de template, donc `{{ theme }}` fonctionnerait sans `.value` dans le template. Mais dans le `<script setup>`, l'accès explicite via `.value` est nécessaire. Les fonctions qui ne sont pas réactives (`getCommitColour`, `shiftAlphaChannel`, etc.) restent des fonctions simples — pas d'impact.

**Action requise** : Les agents des phases 4-7 doivent savoir que `useTheme()` retourne un objet dont `theme`, `hoverColour` et `textColour` sont des `ComputedRef`, mais les 8 fonctions utilitaires restent des fonctions simples. **Pas de modification du plan nécessaire** — c'est un détail d'implémentation naturel que les agents géreront au moment du développement.

**Statut** : ⚠️ Point d'attention — pas de modification du plan requise, mais les agents doivent en être informés.

---

### 2. Custom node/tooltip typés comme fonctions génériques dans `GraphContextBag` — Impact sur Phase 4.1 et 4.6

**Décision concernée** : Phase 2.1 — Les types `CustomCommitNode` et `CustomTooltip` dans `GraphContextBag` sont typés comme `((props: CustomCommitNodeProps) => unknown) | undefined` et `((props: CustomTooltipProps) => unknown) | undefined`, enveloppés dans `Readonly<Ref<...>>`.

**Impact** : Le plan de la phase 4.1 indique que `GraphCore.vue` expose des scoped slots `#node` et `#tooltip`. Mais dans `GraphContextBag`, ces customs sont des **fonctions stockées dans des refs**, pas directement des slots. Il y a donc une question d'architecture à résoudre en phase 4 :

- **Option A** : `GraphCore.vue` reçoit les slots `#node` et `#tooltip`, les wrappe dans des fonctions, et les `provide` dans le `GraphContextBag` via les refs `node` et `tooltip`. C'est l'approche fidèle au code React (qui passe les render props via le contexte).
- **Option B** : Les slots `#node` et `#tooltip` sont utilisés directement au niveau du composant qui les rend (`CommitNode.vue`) sans transiter par le contexte. Dans ce cas, les champs `node` et `tooltip` du `GraphContextBag` pourraient rester `undefined` ou être supprimés.

La décision actuelle (stocker des fonctions dans le contexte) oriente vers l'**option A**. Le plan de la phase 4.1 mentionne bien que `GraphCore.vue` fournit le `GraphContext` avec toutes les valeurs de `GraphContextBag`, incluant implicitement `node` et `tooltip`. Mais il ne détaille pas **comment** les slots Vue sont convertis en fonctions pour le contexte. C'est un point que l'agent de la phase 4 devra résoudre (probablement via `useSlots()` ou en passant les slots comme render functions dans le `provide`).

**Action requise** : Le plan de la phase 4.1 pourrait bénéficier d'une note explicite sur la façon dont les scoped slots `#node` et `#tooltip` sont bridgés vers les refs de fonctions dans `GraphContextBag`. **Pas bloquant**, mais rend l'implémentation moins évidente pour un agent.

**Statut** : ⚠️ Point d'attention — clarification souhaitable dans le plan de la phase 4.1.

---

### 3. `Readonly<Ref<T>>` uniforme pour toutes les propriétés réactives des context bags — Impact sur Phase 3.2

**Décision concernée** : Phase 2.1 — Toutes les propriétés réactives des context bags utilisent `Readonly<Ref<T>>` sans exception.

**Impact** : En phase 3.2, `GitLog.vue` va `provide(GIT_CONTEXT_KEY, { ... })`. Chaque valeur fournie devra être un `Readonly<Ref<T>>`. Concrètement, pour les valeurs issues des props :

- Les props de composant Vue (via `defineProps`) produisent des `Readonly<...>` automatiquement pour les types primitifs, mais la transformation exacte (props → refs) dépend de comment elles sont manipulées. L'agent devra probablement utiliser `toRef(props, 'propName')` ou `computed(() => props.propName)` pour créer les refs à fournir dans le contexte.
- Les valeurs d'état interne (comme `selectedCommit`, `graphWidth`) seront des `ref()` classiques qui satisfont `Readonly<Ref<T>>` naturellement.

Ce n'est pas un problème en soi, mais la phase 3.2 a un volume de câblage important entre props → refs → provide. La décision d'uniformiser sur `Readonly<Ref<T>>` rend ce câblage prévisible mais potentiellement verbeux.

**Action requise** : Aucune modification du plan nécessaire. L'agent de la phase 3 trouvera naturellement la bonne approche (`toRef`, `computed`, ou `ref`).

**Statut** : ✅ Pas de problème — pattern standard Vue.

---

### 4. `GitContextBag` non générique (pas de `<T>`) — Impact potentiel sur Phases 3, 4, 5

**Décision concernée** : Phase 2.1 resolved question — `GitContextBag` n'est pas générique. Le type `Commit` (= `CommitBase & T` avec `T = object`) est utilisé directement.

**Impact** : Si un consommateur du composant `GitLog` veut passer des commits enrichis avec des données custom (ex: `Commit & { jiraTicket: string }`), le type `Commit` dans les context bags ne portera pas cette information générique. Concrètement :

- **Phase 3.2** (`GitLog.vue`) : Les props `entries` sont typées `GitLogEntry[]` (qui contient `Commit`). Si l'utilisateur passe un type plus spécifique, l'information supplémentaire ne sera pas propagée dans le contexte.
- **Phase 4.6** : Le scoped slot `#tooltip` reçoit un `CustomTooltipProps` qui contient un `Commit`. Si le consommateur a des données custom, il devra caster.
- **Phase 5.1** : Le scoped slot `#row` reçoit un `CustomTableRowProps` avec un `Commit`. Même situation.

La décision note que "This can be revisited if a concrete need arises". Pour un POC, c'est acceptable. Si le besoin d'un type générique apparaît plus tard, il faudra modifier les context bags, les composables d'injection, et potentiellement les signatures des scoped slots.

**Action requise** : Aucune pour l'instant. Point à garder en tête pour une éventuelle évolution post-POC.

**Statut** : ✅ Acceptable pour le POC — limitation documentée.

---

### 5. `SelectCommitHandler` défini localement dans `useSelectCommit.ts` — Impact sur Phase 4.5

**Décision concernée** : Phase 2.4 — L'interface `SelectCommitHandler` est exportée depuis `useSelectCommit.ts` (pas dans un fichier `types.ts` séparé).

**Impact** : Phase 4.5 (`GraphColumn.vue`) et 4.6 (interactions) utiliseront `useSelectCommit()` pour les handlers d'interaction. L'import sera :
```typescript
import { useSelectCommit, type SelectCommitHandler } from '../composables/useSelectCommit'
```

Les composants qui n'ont besoin que du type (pas du composable) devront tout de même importer depuis ce fichier. C'est mineur et cohérent avec le pattern établi (cf. `ColumnData` dans `useColumnData.ts`, `ResizeState` dans `useResize.ts`, etc.).

**Action requise** : Aucune.

**Statut** : ✅ Pas de problème — pattern cohérent.

---

### 6. `useColumnData` accepte `Readonly<Ref<number>>` au lieu d'un nombre simple — Impact sur Phase 4.1

**Décision concernée** : Phase 2.5 — Le paramètre `visibleCommits` est typé `Readonly<Ref<number>>` plutôt qu'un `number` plain.

**Impact** : En phase 4.1, `GraphCore.vue` appellera `useColumnData(visibleCommitsRef)` en passant un ref, pas un nombre. Le plan de la phase 4.1 mentionne que `GraphCore.vue` fournit `visibleCommits` dans le `GraphContextBag` — c'est un `Readonly<Ref<Commit[]>>` (un tableau de commits, pas un nombre). Il y a donc une subtilité : `useColumnData` prend le **nombre** de commits visibles (un `Ref<number>`), pas le tableau lui-même.

L'agent de la phase 4 devra créer un `computed(() => visibleCommitsArray.value.length)` ou équivalent pour passer au composable. C'est naturel mais mérite d'être noté.

**Action requise** : Aucune modification du plan. L'agent de la phase 4 fera la conversion naturellement.

**Statut** : ✅ Pas de problème — conversion triviale.

---

### 7. `useColumnData` retourne deux `ComputedRef` (pas des valeurs simples) — Impact sur Phase 4.1

**Décision concernée** : Phase 2.5 — `columnData` et `virtualColumns` sont des `ComputedRef`.

**Impact** : Phase 4.1 (`GraphCore.vue`) fournit `columnData` et `graphWidth` (calculé comme `graphData.graphWidth + virtualColumns`) dans le `GraphContextBag`. Puisque `virtualColumns` est un `ComputedRef<number>`, le calcul de `graphWidth` pour le contexte sera :

```typescript
const graphWidth = computed(() => graphData.value.graphWidth + virtualColumns.value)
```

Le `graphWidth` dans `GraphContextBag` est un `Readonly<Ref<number>>`, et un `ComputedRef<number>` satisfait cette contrainte. C'est cohérent.

**Action requise** : Aucune.

**Statut** : ✅ Pas de problème — types compatibles.

---

### 8. `placeholderColumns` renommé depuis `columns` — Impact mineur sur Phase 4.4

**Décision concernée** : Phase 2.6 — Le nom d'export a été changé de `columns` à `placeholderColumns` dans `graph/placeholderData.ts`.

**Impact** : Phase 4.4 (`SkeletonGraph.vue`) importera les données de placeholder directement depuis `graph/placeholderData.ts`. L'import sera `placeholderColumns` et non `columns`. Le plan de la phase 4.4 ne mentionne pas de nom d'import spécifique, donc ce renommage est transparent.

**Action requise** : Aucune.

**Statut** : ✅ Pas de problème — renommage clarificateur.

---

### 9. `useResize` avec listeners custom (pas `useMouse` de vueuse) — Impact sur Phase 4.1

**Décision concernée** : Phase 2.7 — Implémentation custom avec `addEventListener`/`removeEventListener` au lieu de `useMouse` de `@vueuse/core`.

**Impact** : Le plan de la phase 4.1 mentionne que `GraphCore.vue` gère le resize. L'implémentation custom de `useResize` est auto-suffisante : elle expose `width`, `ref` et `startResizing`. Le composant `GraphCore.vue` devra :

1. Binder `ref` au container du graphe (`:ref="resize.ref"`)
2. Afficher un drag handle conditionnel (`v-if="enableResize"`) avec `@mousedown="resize.startResizing"`
3. Utiliser `resize.width` pour le style de largeur

L'approche custom est transparente pour le consommateur. **Un point notable** : contrairement à l'approche `useMouse` qui aurait donné accès à la position de la souris en permanence, l'approche custom ne track la souris que pendant le drag. Il n'y a pas de cas dans les phases suivantes qui nécessiterait le tracking continu de la souris, donc pas d'impact.

**Action requise** : Aucune.

**Statut** : ✅ Pas de problème — approche transparente pour les phases suivantes.

---

### 10. `width` dans `ResizeState` est un pass-through de `graphWidth` du contexte — Impact sur Phase 4.1

**Décision concernée** : Phase 2.7 — `width` est directement `graphWidth` du `GitContext`, sans wrapping supplémentaire.

**Impact** : `GraphCore.vue` utilise `useResize()` pour obtenir `width`. C'est la même ref que `graphWidth` du `GitContext`. Il faut que l'agent de la phase 4 comprenne que `useResize().width` et `useGitContext().graphWidth` sont la **même ref**. Modifier l'une modifie l'autre. Ce n'est pas un problème mais un point de compréhension architecturale.

**Action requise** : Aucune.

**Statut** : ✅ Pas de problème — cohérence d'état partagé intentionnelle.

---

### 11. `RowIndexToColumnStates` défini dans `keys.ts` (pas dans `GraphMatrixBuilder/types.ts`) — Impact sur Phase 4.1

**Décision concernée** : Phase 2.1 — L'alias de type `RowIndexToColumnStates` est dans `composables/keys.ts`.

**Impact** : Phase 4.1 (`GraphCore.vue`) et d'autres composants du graphe qui ont besoin de ce type devront l'importer depuis `../composables/keys`. Ce n'est pas dans `GraphMatrixBuilder/types.ts` comme on pourrait s'y attendre intuitivement. C'est un choix cohérent (le type est lié à la couche composable/contexte), mais l'agent de la phase 4 doit le savoir.

**Action requise** : Aucune modification du plan nécessaire. L'agent le trouvera par les imports existants.

**Statut** : ✅ Pas de problème — chemin d'import clair.

---

### 12. `placeholderData` retourné comme `ComputedRef` malgré des données statiques — Impact nul

**Décision concernée** : Phase 2.6 — Le composable retourne un `ComputedRef` même si les données sous-jacentes sont statiques.

**Impact** : Phase 5.1 (`GitLogTable.vue`) consomme `usePlaceholderData()` et accédera aux données via `.value`. Le `ComputedRef` est compatible avec `Readonly<Ref<T>>` et s'intègre naturellement dans le pattern réactif de Vue. Aucun impact fonctionnel.

**Action requise** : Aucune.

**Statut** : ✅ Aucun impact.

---

### 13. `useCallback` remplacé par des fonctions simples partout — Impact positif sur Phases 4-7

**Décision concernée** : Phases 2.3, 2.4 — Toutes les fonctions des composables sont des fonctions simples, pas des refs ou des wrapped callbacks.

**Impact** : Cela simplifie la consommation dans les phases suivantes. Les fonctions comme `getCommitColour(commit)`, `shiftAlphaChannel(rgb, opacity)`, ou les handlers `onMouseOver(commit)`, `onClick(commit)` sont directement appelables sans `.value`. C'est le pattern idiomatique Vue.

Les composants des phases 4-7 pourront utiliser ces fonctions dans les templates (`@click="selectCommitHandler.onClick(commit)"`) et dans le script sans aucune indirection.

**Action requise** : Aucune.

**Statut** : ✅ Facilitant — simplifie l'implémentation.

---

## Conclusion

**Aucune décision prise pendant l'implémentation de la phase 2 ne nécessite de modification des plans des phases 3 à 8.** Deux points méritent cependant une attention particulière :

1. **Point d'attention principal (§2)** : Le bridging entre les scoped slots Vue (`#node`, `#tooltip`) et les refs de fonctions dans `GraphContextBag` n'est pas explicité dans le plan de la phase 4. L'agent devra comprendre comment passer les slots du parent vers le contexte injecté. Une note clarificatrice dans le plan de la phase 4.1 serait bienvenue.

2. **Point d'attention secondaire (§1)** : `ThemeFunctions.theme`, `.hoverColour` et `.textColour` sont des `ComputedRef<T>`, ce qui implique l'accès via `.value` dans les scripts des composants. C'est standard Vue mais l'agent doit en être conscient.

Les décisions les plus notables pour les agents des phases suivantes sont :
- **Phase 3** : Les valeurs du `provide(GIT_CONTEXT_KEY, ...)` doivent être des `Readonly<Ref<T>>`. Utiliser `toRef`, `computed`, ou `ref` pour convertir les props.
- **Phase 4** : `useColumnData` prend un `Readonly<Ref<number>>` (nombre de commits, pas le tableau). `RowIndexToColumnStates` s'importe depuis `composables/keys.ts`. Le bridging slots → fonctions pour `node`/`tooltip` dans `GraphContextBag` est à implémenter.
- **Phases 4-7** : Les fonctions utilitaires de `useTheme()` sont des fonctions simples (pas de `.value`), mais `theme`, `hoverColour`, `textColour` sont des `ComputedRef` (`.value` en script, auto-unwrap en template).
