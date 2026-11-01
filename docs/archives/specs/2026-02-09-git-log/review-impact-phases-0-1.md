# Review d'impact des phases 0 et 1 sur les phases suivantes

> Ce document analyse les "Decisions made during implementation" et "Resolved questions and doubts" des phases 0 et 1, et évalue leurs conséquences potentielles sur les phases 2 à 8.

---

## Résumé exécutif

Les phases 0 et 1 ont été implémentées fidèlement par rapport au plan. La grande majorité des décisions prises sont sans conséquence sur les phases suivantes. Quelques points méritent attention, listés ci-dessous par ordre d'importance décroissante.

---

## Points d'attention

### 1. `GraphColumnState` placé dans `GraphMatrixBuilder/types.ts` — Impact sur Phase 4

**Décision concernée** : Phase 1.3 decision #2 — `GraphColumnState` déplacé dans `GraphMatrixBuilder/types.ts` au lieu d'un répertoire de composant `GraphColumn/types.ts` qui n'existe pas encore.

**Impact** : Phase 4.5 (Assemblage GraphRow et GraphColumn) devra importer `GraphColumnState` depuis `../GraphMatrixBuilder/types` (ou via le barrel `../GraphMatrixBuilder`). Le plan de la phase 4 mentionne bien que `GraphColumn.vue` reçoit un `GraphColumnState` en prop, mais **ne précise pas le chemin d'import**. La décision prise en phase 1.3 documente elle-même que "When the `GraphColumn.vue` component is created in Phase 4, it will import this type from `GraphMatrixBuilder/types`".

**Action requise** : Aucune modification nécessaire. Le chemin est clair et documenté. L'agent de la phase 4 a juste besoin de savoir que `GraphColumnState` vient de `GraphMatrixBuilder/types.ts` (exporté via le barrel `index.ts`) et non d'un fichier `GraphColumn/types.ts`.

**Statut** : ✅ Pas de problème — déjà documenté dans la décision elle-même.

---

### 2. `GraphColumnProps` non porté — Impact sur Phase 4

**Décision concernée** : Phase 1.3 decision #6 et Resolved question #2 — `GraphColumnProps` n'a pas été porté car c'est un type de props de composant qui relève de la phase 4.

**Impact** : Phase 4.5 devra **définir** `GraphColumnProps` dans le composant `GraphColumn.vue` (ou dans un fichier de types adjacent). Ce type dépend de `Commit` (de `../../types`) et `GraphColumnState` (de `../GraphMatrixBuilder/types`). C'est du travail de typage standard pour un composant Vue avec `defineProps`.

**Action requise** : Aucune. Le plan de la phase 4 mentionne déjà les données que reçoit `GraphColumn.vue`. L'agent de la phase 4 créera naturellement les types de props via `defineProps` dans le `<script setup>`.

**Statut** : ✅ Pas de problème — comportement attendu et naturel.

---

### 3. React render prop types supprimés, seuls les `*Props` interfaces conservés — Impact sur Phases 4, 5, 7

**Décision concernée** : Phase 1.1 decision #3 — Les types function `CustomTooltip`, `CustomCommitNode<T>`, `CustomTableRow` (qui retournaient `ReactElement`) ont été supprimés. Seuls les interfaces `CustomTooltipProps`, `CustomCommitNodeProps<T>`, `CustomTableRowProps` sont conservés.

**Impact** : Les phases 4.6 (scoped slot `#tooltip` et `#node`), 5.1 (scoped slot `#row`), et 7 (tooltips) devront typer leurs scoped slots en utilisant ces interfaces `*Props`. C'est exactement l'intention de la décision. En Vue, le typage des slots se fait via `defineSlots()` avec les interfaces de props, pas avec des types de fonction.

**Action requise** : Aucune. Les interfaces de props conservées sont exactement ce dont les phases suivantes ont besoin pour typer les scoped slots.

**Statut** : ✅ Pas de problème — décision correcte et alignée avec les plans.

---

### 4. `HTMLGridGraphProps` simplifié (props `node` et `tooltip` supprimés) — Impact sur Phase 4.1

**Décision concernée** : Phase 1.1 decision #5 — Les props `node` et `tooltip` (render props React) ont été retirés de `HTMLGridGraphProps` car ils deviennent des scoped slots.

**Impact** : Phase 4.1 décrit que `GraphCore.vue` expose `slot #node` et `slot #tooltip`. Le plan est déjà aligné avec cette décision : les slots ne sont pas des props typées dans `HTMLGridGraphProps` mais des slots définis au niveau du composant Vue. L'interface `HTMLGridGraphProps` conservée avec ses props restantes (`showCommitNodeHashes`, `showCommitNodeTooltips`, `highlightedBackgroundHeight` + les champs hérités de `GraphPropsCommon`) est suffisante.

**Action requise** : Aucune. Le plan de la phase 4 prévoit déjà des scoped slots.

**Statut** : ✅ Pas de problème.

---

### 5. `CommitFilter` generic default changé de `object` à `unknown` — Impact potentiel mineur sur Phase 3.2

**Décision concernée** : Phase 1.1 decision #10 — Le default generic de `CommitFilter<T>` a été changé en `unknown`.

**Impact** : Phase 3.2 (`GitLog.vue`) utilise `CommitFilter` via les props `GitLogCommonProps<T>`. Puisque `GitLogCommonProps` a aussi `T = unknown` comme default, c'est cohérent. Le filtrage dans `GitLog.vue` appellera `filter.fn(commit)` de manière typée.

**Action requise** : Aucune. La cohérence est maintenue.

**Statut** : ✅ Pas de problème.

---

### 6. `CSSProperties` importé de `vue` au lieu de `react` — Impact sur Phases 3, 5

**Décision concernée** : Phase 1.1 decision #2 — `CSSProperties` de React remplacé par celui de Vue.

**Impact** : Les phases 3.2 (`GitLog.vue` qui utilise `GitLogStylingProps.containerStyles`) et 5.1 (`GitLogTable.vue` qui utilise `GitLogTableStylingProps` avec `table`, `thead`, `tr`, `td`) consommeront ces types. Le `CSSProperties` de Vue est compatible avec la syntaxe `:style="..."` des templates Vue.

**Resolved question 1.1 #1** confirme que c'est le choix idiomatique.

**Action requise** : Aucune.

**Statut** : ✅ Pas de problème.

---

### 7. `ThemeFunctions` interface inclus dans `types.ts` — Impact sur Phase 2.3

**Décision concernée** : Phase 1.1 decision #7 — L'interface `ThemeFunctions` (type de retour de `useTheme`) est déjà disponible dans `types.ts`.

**Impact** : Phase 2.3 (`useTheme` composable) pourra directement typer son retour avec `ThemeFunctions` importé depuis `../types`. C'est une facilitation, pas un problème.

**Action requise** : Aucune. C'est un avantage pour la phase 2.

**Statut** : ✅ Facilitant.

---

### 8. `GraphPaging` type interne inclus dans `types.ts` — Impact sur Phase 2.1

**Décision concernée** : Phase 1.1 decision #8 — Le type interne `GraphPaging` est inclus dans `types.ts` à côté du type public `GitLogPaging`.

**Impact** : Phase 2.1 (clés d'injection et types des contextes) définira `GitContextBag` qui utilise `GraphPaging`. Ce type étant déjà disponible dans `types.ts`, l'import sera direct.

**Action requise** : Aucune.

**Statut** : ✅ Facilitant.

---

### 9. Neon aurora palettes dans `utils/colors.ts` (pas dans `types.ts`) — Impact sur Phase 2.3

**Décision concernée** : Phase 1.1 decision #9 + Phase 1.4 decision #3 — Les palettes runtime sont dans `utils/colors.ts`, pas dans `types.ts`.

**Impact** : Phase 2.3 (`useTheme`) doit importer les palettes depuis `../utils/colors` et les types de thème depuis `../types`. C'est une séparation propre (types vs valeurs runtime).

**Action requise** : Aucune. Le chemin d'import est clair.

**Statut** : ✅ Pas de problème.

---

### 10. `fastpriorityqueue` ajouté comme dépendance runtime — Impact nul

**Décision concernée** : Phase 1.2 decision #4 — `fastpriorityqueue` ajouté en dépendance prod.

**Impact** : Aucun sur les phases suivantes. Cette dépendance est consommée uniquement par `ActiveNodes.ts` qui est déjà copié et fonctionnel.

**Statut** : ✅ Aucun impact.

---

### 11. Pas de barrel `index.ts` pour `utils/` — Impact mineur sur Phases 2-6

**Décision concernée** : Phase 1.4 decision #4 — Pas de fichier de réexport `utils/index.ts`.

**Impact** : Les phases suivantes qui importent depuis `utils/` devront utiliser des imports directs vers les fichiers individuels (`../utils/formatBranch`, `../utils/colors`, `../utils/createRainbowTheme`). C'est cohérent avec le plan (section 15 ne liste pas de `utils/index.ts`).

Les phases concernées sont :
- Phase 2.3 (`useTheme`) → importera depuis `../utils/colors` et `../utils/createRainbowTheme`
- Phase 6.2 (Tags) → importera depuis `../utils/formatBranch`

**Action requise** : Aucune. L'absence de barrel est documentée et les imports directs fonctionnent.

**Statut** : ✅ Pas de problème.

---

### 12. `getMergeNodeInnerSize` et `getColumnBackgroundSize` dans `graph/utils/` (pas `utils/`) — Impact sur Phase 4

**Décision concernée** : Phase 1.4 decision #2 — Ces utilitaires sont dans `graph/utils/` conformément à la section 15, pas dans `utils/` comme le texte de la phase 1.4 le suggérait.

**Impact** : Phase 4.2 (`CommitNode.vue`) et phase 4.5 (`GraphColumn.vue` / `ColumnBackground.vue`) importeront ces fonctions depuis le bon chemin `../utils/getMergeNodeInnerSize` (relatif à `graph/components/`).

**Action requise** : Aucune. Le plan de la phase 4 et la structure de la section 15 sont alignés.

**Statut** : ✅ Pas de problème.

---

## Conclusion

**Aucune décision prise pendant l'implémentation des phases 0 et 1 ne nécessite de modification des plans des phases 2 à 8.** Toutes les décisions sont cohérentes avec le plan existant et la structure de fichiers cible (section 15).

Les décisions les plus notables pour les agents des phases suivantes sont :
1. **Phase 4** : `GraphColumnState` s'importe depuis `GraphMatrixBuilder/types` (ou le barrel `GraphMatrixBuilder`), pas depuis un répertoire de composant.
2. **Phase 2** : `ThemeFunctions` et `GraphPaging` sont déjà disponibles dans `types.ts`, ce qui simplifie le travail.
3. **Phases 2-6** : Les imports depuis `utils/` sont directs vers les fichiers individuels (pas de barrel).
4. **Phases 4, 5, 7** : Les scoped slots se typent avec les interfaces `*Props` conservées (`CustomTooltipProps`, `CustomCommitNodeProps`, `CustomTableRowProps`), pas avec des types de fonction.
