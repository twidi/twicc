# Review d'impact de la phase 3 sur les phases suivantes

> Ce document analyse les "Decisions made during implementation" et "Resolved questions and doubts" de la phase 3, et evalue leurs consequences potentielles sur les phases 4 a 8.

---

## Resume executif

La phase 3 a ete implementee de facon fidele au plan, avec quelques decisions architecturales notables. Le composant racine `GitLog.vue` (~340 lignes au total, ~320 de script) reste en un seul fichier comme decide. Les decisions prises sont globalement coherentes avec les phases suivantes, mais **plusieurs points meritent une attention particuliere**, notamment le pattern `setGraphWidth` qui ecrit dans un `ref` intermediaire plutot que directement dans `graphWidthValue`, et le fait que `Layout.vue` consomme `useGitContext()` et `useTheme()` (ce qui le couple au systeme de provide/inject du parent).

---

## Points d'attention

### 1. `Layout.vue` consomme `useGitContext()` et `useTheme()` -- Impact sur Phase 4.1

**Decisions concernees** : Phase 3.1 #4 (`textColour` est un `ComputedRef<string>`) et le choix d'utiliser `useGitContext()` dans `Layout.vue` pour acceder a `showHeaders` et `classes`.

**Constat** : `Layout.vue` fait `const { classes, showHeaders } = useGitContext()` et `const { textColour } = useTheme()`. Cela signifie que `Layout.vue` est un **consommateur** du `GitContext` et du `ThemeContext`, pas seulement un composant de mise en page "pur".

**Impact** : En phase 4.1, `GraphCore.vue` fera son propre `provide(GRAPH_CONTEXT_KEY, ...)`. Le `GraphCore.vue` est rendu a l'interieur du slot `#graph` de `Layout.vue`, qui est lui-meme dans `GitLog.vue`. La chaine de provide/inject est donc :
```
GitLog.vue (provide GIT_CONTEXT + THEME_CONTEXT)
  -> Layout.vue (inject GIT_CONTEXT + THEME_CONTEXT)
     -> slot #graph -> GraphCore.vue (provide GRAPH_CONTEXT, inject GIT_CONTEXT)
```

Ce n'est pas un probleme en soi, mais l'agent de la phase 4 doit comprendre que `Layout.vue` est deja un consommateur des contextes, et que `GraphCore.vue` recevra bien les contextes fournis par `GitLog.vue` puisqu'il est rendu dans le sous-arbre de composants de ce dernier.

**Action requise** : Aucune modification du plan necessaire. Pattern standard Vue.

**Statut** : `OK` Pas de probleme -- architecture coherente.

---

### 2. `entries` type comme `Commit[]` au lieu de `GitLogEntry[]` -- Impact sur Phase 8.2 et documentation

**Decision concernee** : Phase 3.2 #3 -- Les props utilisent `Commit` au lieu de `GitLogEntry`.

**Constat** : La prop `entries` de `GitLog.vue` est typee `Commit[]`. Or `Commit` etend `CommitBase` (qui contient `children: string[]`, `isBranchTip: boolean` en plus des champs de `GitLogEntry`). Cela signifie que le consommateur du composant doit fournir des objets avec ces champs supplementaires.

**Impact** :
- **Phase 8.2** (Fake data) : Les donnees de test devront fournir des objets conformes a `Commit` (avec `children` et `isBranchTip`), pas seulement `GitLogEntry`. C'est un detail mais le plan de la phase 8.2 mentionne "Types `GitLogEntry`, `GitLogIndexStatus`" comme entree, sans mentionner `Commit`. L'agent de la phase 8.2 devra fournir des `Commit[]` au lieu de `GitLogEntry[]`.
- **Phase 8.1** (Export public) : L'API publique exporte `GitLogEntry` comme type d'entree attendu pour les consommateurs. Mais en pratique, les consommateurs devront fournir des `Commit[]`. Cela peut creer une confusion. Soit on accepte que `entries` prenne des `Commit[]` (et on documente), soit il faudrait que le composant accepte des `GitLogEntry[]` et fasse l'enrichissement en `Commit[]` lui-meme (via `computeRelationships` qui fait deja cette transformation).

**Analyse approfondie** : En regardant le code, `computeRelationships` prend des `GitLogEntry[]` en entree (via le barrel `data/index.ts`) et produit des `Commit[]` en sortie (avec `children` et `isBranchTip` calcules). Donc le pipeline **devrait** accepter des `GitLogEntry[]` en entree. La decision d'utiliser `Commit[]` pour la prop semble etre une simplification qui contourne le fait que `computeRelationships` retourne des `Commit`. Mais cela force les consommateurs a pre-enrichir leurs donnees.

En pratique, puisque `Commit` est un type structurel qui **etend** `GitLogEntry`, le TypeScript acceptera un `Commit[]` la ou un `GitLogEntry[]` est attendu (covariance). Mais l'inverse n'est pas vrai : passer un `GitLogEntry[]` a une prop typee `Commit[]` causera une erreur de type.

**Action requise** : Point a traiter. Deux options :
1. **Changer la prop `entries` en `GitLogEntry[]`** : C'est l'approche correcte au niveau API. Le composant fait deja l'enrichissement via `computeRelationships`. Cela necessiterait un ajustement mineur du typage dans `GitLog.vue`.
2. **Garder `Commit[]` et documenter** : Acceptable pour le POC, mais les types `children` et `isBranchTip` devront etre fournis par le consommateur (meme s'ils sont recalcules en interne).

**Statut** : ~~`ATTENTION`~~ `RESOLU` -- **Corrige post-review** : la prop `entries` est maintenant typee `GitLogEntry[]`. Le pipeline interne fait l'enrichissement en `Commit[]` via `computeRelationships`. La phase 8.2 pourra fournir des `GitLogEntry[]` simples.

---

### 3. `graphContainerWidth` / `graphWidthValue` / `setGraphWidth` -- Impact sur Phase 4.1

**Decision concernee** : Phase 3.2 #4 -- `graphContainerWidth` comme ref separee avec `graphWidthValue` en computed.

**Constat** : Le pattern implemente est :
```typescript
const graphContainerWidth = ref<number | undefined>(undefined)

const graphWidthValue = computed(() => {
  if (graphContainerWidth.value !== undefined) return graphContainerWidth.value
  if (props.defaultGraphWidth && props.defaultGraphWidth >= smallestAvailableGraphWidth.value)
    return props.defaultGraphWidth
  return smallestAvailableGraphWidth.value
})

// Dans le GitContextBag :
graphWidth: graphWidthValue,  // readonly computed
setGraphWidth: (width: number) => { graphContainerWidth.value = width },
```

**Impact sur phase 4** : `useResize()` (composable phase 2) fait :
```typescript
const { graphWidth, setGraphWidth } = useGitContext()
```
et utilise `setGraphWidth(newWidth)` pour mettre a jour la largeur. Cela ecrit dans `graphContainerWidth` (via le setter), et `graphWidthValue` (expose comme `graphWidth` dans le contexte) se recalcule. C'est un circuit reactif indirect :

`setGraphWidth(n)` -> `graphContainerWidth.value = n` -> `graphWidthValue` recalcul -> `graphWidth` du contexte mis a jour.

L'agent de la phase 4 n'a pas besoin de comprendre ce mecanisme interne, puisqu'il consomme simplement `graphWidth` (lecture) et `setGraphWidth` (ecriture) du contexte. Mais il doit savoir que :
- `graphWidth` dans le contexte est un `ComputedRef<number>` (pas un `ref<number>` simple)
- Il y a un fallback sur `defaultGraphWidth` puis `smallestAvailableGraphWidth` si aucun resize n'a eu lieu
- Le `MIN_WIDTH` (200) et `MAX_WIDTH` (800) sont dans `useResize.ts`, pas dans `GitLog.vue`

**Action requise** : Aucune modification du plan necessaire. Le composable `useResize` gere correctement les bornes.

**Statut** : `OK` Pas de probleme -- le mecanisme est transparent pour les consommateurs.

---

### 4. `useSelectCommit` et `useResize` non appeles dans `GitLog.vue` -- Impact sur Phase 4.1 et 4.6

**Question resolue** : Phase 3.2 resolved question #1.

**Constat** : La spec initiale mentionnait "Utiliser `useSelectCommit` pour la logique de selection" et "Utiliser `useResize` pour la logique de resize" dans `GitLog.vue`. En realite, ces composables ne sont pas appeles dans `GitLog.vue` car ils consomment `useGitContext()` (qui necessite que le contexte soit deja `provide`). A la place, `GitLog.vue` implemente les handlers directement (`handleSelectCommit`, `handlePreviewCommit`) et les passe dans le `GitContextBag`.

**Impact** :
- **Phase 4.1** : `GraphCore.vue` appellera `useResize()` normalement. Le composable accede au contexte fourni par `GitLog.vue`. Pas de surprise.
- **Phase 4.6** : Les composants d'interaction (`CommitNode.vue`, `GraphColumn.vue`) appelleront `useSelectCommit()`. Le composable accede au `GitContext` et invoque `setSelectedCommit` / `setPreviewedCommit`, qui sont les handlers definis dans `GitLog.vue`. Le circuit est : composant enfant -> `useSelectCommit()` -> `setSelectedCommit` (du contexte) -> `handleSelectCommit` (de `GitLog.vue`) -> maj du `ref` + appel du callback `onSelectCommit`.
- **Phase 5.3** : Meme pattern pour la table.

Tout cela est coherent. Le plan de la phase 4.1 mentionne deja cette note dans la spec de la phase 3 (la decision est cross-referencee).

**Action requise** : Aucune.

**Statut** : `OK` Pas de probleme -- le circuit reactif est correct et transparent.

---

### 5. `bootstrapShiftAlphaChannel` inligne au lieu de `useTheme` -- Impact sur Phase 7

**Question resolue** : Phase 3.2 resolved question #2.

**Constat** : `GitLog.vue` definit une fonction locale `bootstrapShiftAlphaChannel(rgb, opacity, theme)` au lieu d'appeler `useTheme().shiftAlphaChannel()`. C'est pour eviter la dependance circulaire (le `ThemeContext` n'est pas encore `provide` quand on calcule les couleurs resolues). La fonction est semantiquement identique a `shiftAlphaChannel` de `useTheme.ts` mais prend un parametre `theme` explicite au lieu de le lire depuis le contexte.

**Impact** :
- **Phase 7** (Tooltips) : Les composants de tooltip qui utilisent `useTheme()` pour les couleurs consommeront la version du composable (pas la version bootstrap). Pas d'impact.
- **Maintenance** : Il y a maintenant une **duplication de logique** entre `bootstrapShiftAlphaChannel` (dans `GitLog.vue`) et `shiftAlphaChannel` (dans `useTheme.ts`). Si l'algorithme de melange doit changer, il faudra le changer aux deux endroits. Ce n'est pas un probleme pour les phases suivantes, mais c'est un point de dette technique a noter.

**Action requise** : Aucune pour les phases suivantes. Point de dette technique mineur a documenter.

**Statut** : `OK` Pas de probleme pour les phases suivantes -- dette technique mineure (duplication).

---

### 6. Detection de slots via `useSlots()` pour `showBranchesTags` / `showTable` -- Impact sur Phases 4, 5, 6

**Question resolue** : Phase 3.2 resolved question #5.

**Constat** : `GitLog.vue` utilise `useSlots()` pour detecter la presence des slots `tags` et `table`, et expose `showBranchesTags: computed(() => !!slots.tags)` et `showTable: computed(() => !!slots.table)` dans le `GitContext`.

**Impact** :
- **Phase 4** (Graph) : Le graphe peut adapter son rendu selon que les colonnes tags/table sont presentes. C'est utilise dans `GraphCore` ou `HTMLGridGraph` pour ajuster la grille. Pas de changement par rapport au plan.
- **Phase 5** (Table) : Le composant `GitLogTable.vue` sera rendu dans le slot `#table`. Le `showTable` sera `true` quand le slot est fourni. Pas de surprise.
- **Phase 6** (Tags) : Idem pour `GitLogTags.vue` dans le slot `#tags` et `showBranchesTags`.

Le pattern est propre. La detection est reactive (les `computed` se recalculent si les slots changent dynamiquement, meme si en pratique les slots sont generalement statiques).

**Action requise** : Aucune.

**Statut** : `OK` Pas de probleme.

---

### 7. `isServerSidePaginated` hardcode a `false` -- Impact nul

**Question resolue** : Phase 3.2 resolved question #6.

**Constat** : `isServerSidePaginated` est `computed(() => false)` dans le `GitContextBag`. Puisque `GitLogPaged` n'est pas porte, c'est correct.

**Impact** : Les composants des phases 4-6 qui lisent `isServerSidePaginated` du contexte recevront toujours `false`. Le plan ne mentionne aucune logique conditionnelle basee sur cette valeur dans les sous-composants (la condition etait dans `headCommit` de `GitLogCore.tsx`, deja geree dans `GitLog.vue`).

**Action requise** : Aucune.

**Statut** : `OK` Aucun impact.

---

### 8. Single component `GitLog.vue` (pas de `GitLogCore.vue`) -- Impact sur Phase 8 et maintenabilite

**Decision concernee** : Phase 3.2 #1 -- Tout dans un seul fichier au lieu de separer `GitLog.vue` et `GitLogCore.vue`.

**Constat** : Le fichier fait ~340 lignes total (~320 de script), en dessous du seuil de ~300 lignes mentionne dans le plan. Le code est bien structure avec des sections clairement separees (props, state, pipeline, head/index, pagination, width, handlers, theme, provides, template).

**Impact** :
- **Phase 8.3** (Vue de test) : Le consommateur importe et utilise `<GitLog>` directement. Pas de `GitLogCore` a connaitre. C'est plus simple.
- **Phase 8.1** (Export) : Un seul composant racine a exporter. Le plan est deja aligne.
- **Maintenance** : Si des features sont ajoutees (ex: support server-side pagination), le fichier risque de depasser le seuil. Mais pour le POC, c'est acceptable.

**Action requise** : Aucune.

**Statut** : `OK` Pas de probleme.

---

### 9. `ThemeContext` fourni avant `GitContext` -- Impact nul

**Question resolue** : Phase 3.2 resolved question #4.

**Constat** : L'ordre des `provide` est `THEME_CONTEXT_KEY` puis `GIT_CONTEXT_KEY`. Comme note dans la question resolue, l'ordre des `provide` n'affecte pas le mecanisme d'`inject` de Vue.

**Impact** : Aucun sur les phases suivantes. Les composants enfants peuvent `inject` n'importe quel contexte independamment de l'ordre de `provide`.

**Action requise** : Aucune.

**Statut** : `OK` Aucun impact.

---

### 10. `nodeSize` et `graphOrientation` geres comme `ref()` internes dans `GitLog.vue` -- Impact sur Phase 4.1 et Phase 8.3

**Constat** : `GitLog.vue` definissait `nodeSize` et `graphOrientation` comme des refs internes exposees dans le `GitContextBag` avec des setters. Mais ces valeurs ne concernent que le module Graph, pas le composant racine, et le plan de la phase 4 prevoit que `GraphCore.vue` recoit ces valeurs comme props. Cela creait une double source de verite potentielle.

**Resolution** : **Corrige post-review** -- `nodeSize`, `setNodeSize`, `graphOrientation`, `setGraphOrientation` ont ete retires du `GitContextBag` (dans `keys.ts`) et de `GitLog.vue`. Ces valeurs seront gerees exclusivement par `GraphCore.vue` en phase 4, via ses props et le `GraphContextBag`. Le calcul `smallestAvailableGraphWidth` dans `GitLog.vue` utilise maintenant la constante `DEFAULT_NODE_SIZE` directement. Le consommateur configurera `nodeSize` et `orientation` via les props de `<GitLogGraphHTMLGrid>`, conformement au code React source.

**Statut** : ~~`ATTENTION`~~ `RESOLU` -- Plus de double source de verite. Source unique = props de `GraphCore.vue` -> `GraphContextBag`.

---

### 11. Le pipeline de donnees applique le filtre **avant** `GraphDataBuilder.build()` -- Impact sur Phase 4 et Phase 5

**Constat** : Dans `GitLog.vue`, le pipeline est :
```typescript
const filteredCommits = props.filter?.(sortedCommits) ?? sortedCommits
const graphDataBuilder = new GraphDataBuilder({
  commits: sortedCommits,
  filteredCommits,  // passe au builder
  ...
})
```

Le filtre est applique au niveau du pipeline de donnees, puis les `filteredCommits` et les `sortedCommits` complets sont passes au `GraphDataBuilder`. Le builder gere l'affichage des breakpoints pour les commits filtres.

Le `graphData.commits` contient les commits **filtres** (cf `commits: filteredCommits` dans le `GraphData` construit).

**Impact** :
- **Phase 4** (Graph) : Le graphe recoit `graphData` via le contexte, qui contient deja les commits filtres. `GraphCore.vue` n'a pas besoin de re-filtrer. Le plan de la phase 4 ne mentionne pas de re-filtrage dans le graphe, donc c'est coherent.
- **Phase 5** (Table) : La note du plan de la phase 5 indique que "Table.tsx ne reapplique PAS le filtre sur les commits. Il utilise directement les commits de `graphData` (deja filtres dans `GitLogCore`) slices par la pagination." C'est exactement le comportement implemente. Pas de probleme.
- **La pagination est appliquee apres le filtre** ? En fait, dans l'implementation, le filtre est applique dans le pipeline compute (avant le build), et la pagination (`pageIndices`) est calculee mais n'est **pas** appliquee au tableau de commits dans le pipeline. Le `paging` est expose dans le contexte comme un objet `{ startIndex, endIndex }` et c'est aux composants enfants de l'utiliser pour slicer. C'est le pattern attendu.

**Action requise** : Aucune.

**Statut** : `OK` Coherent avec le plan des phases suivantes.

---

### 12. Pas de `data-testid` -- Impact nul

**Decision concernee** : Phase 3.1 #2.

**Constat** : Pas de `data-testid` puisque le projet n'utilise pas de tests (cf CLAUDE.md).

**Impact** : Aucun sur les phases suivantes. Si des tests e2e sont ajoutes dans le futur, il faudra rajouter ces attributs.

**Action requise** : Aucune.

**Statut** : `OK` Aucun impact.

---

## Conclusion

**Aucune decision prise pendant l'implementation de la phase 3 ne necessite de modification des plans des phases 4 a 8.** Les deux points d'attention identifies ont ete corriges post-review :

1. ~~**Point d'attention principal (no. 2)**~~ `RESOLU` : La prop `entries` a ete corrigee de `Commit[]` vers `GitLogEntry[]`.

2. ~~**Point d'attention secondaire (no. 10)**~~ `RESOLU` : `nodeSize` et `graphOrientation` ont ete retires du `GitContextBag` et de `GitLog.vue`. Ils seront geres exclusivement par `GraphCore.vue` (phase 4).

Les decisions les plus notables pour les agents des phases suivantes sont :
- **Phase 4** : `graphWidth` dans le contexte est un `ComputedRef<number>` (pas un `ref` simple). Le setter `setGraphWidth` ecrit dans un `ref` intermediaire (`graphContainerWidth`). Le bridging slots -> fonctions pour `node`/`tooltip` dans `GraphContextBag` reste a implementer (cf review phase 2, point #2).
- **Phase 4** : `nodeSize` et `orientation` sont geres exclusivement par `GraphCore.vue` via ses props et le `GraphContextBag`. Ils ne sont plus dans le `GitContextBag`.
- **Phase 4** : `smallestAvailableGraphWidth` dans `GitLog.vue` utilise `DEFAULT_NODE_SIZE` (constante). Si `GraphCore` a besoin de recalculer avec un `nodeSize` custom, il devra le faire dans son propre scope.
- **Phase 8.2** : Les fake data peuvent etre des `GitLogEntry[]` simples (pas besoin de `children`/`isBranchTip`).
- **Phase 8.3** : Le consommateur configure `nodeSize` et `orientation` via les props de `<GitLogGraphHTMLGrid>`, pas de `<GitLog>`.
