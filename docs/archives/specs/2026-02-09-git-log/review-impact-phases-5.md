# Review d'impact de la phase 5 sur les phases suivantes

> Ce document analyse les "Decisions made during implementation" et "Resolved questions and doubts" de la phase 5, et evalue leurs consequences potentielles sur les phases 6 et 8 (la phase 7 etant supprimee).

---

## Resume executif

La phase 5 (Module Table) a ete implementee de facon globalement fidele au plan, mais deux decisions posent des problemes significatifs pour les phases suivantes :

1. **`nodeSize` hardcode a `DEFAULT_NODE_SIZE`** dans `TableRow.vue` au lieu d'etre lu depuis un contexte partage. C'est une **regression par rapport au comportement React** causee par une erreur d'analyse lors de la review de la phase 3, qui avait retire `nodeSize` du `GitContextBag` en estimant a tort que seul le module Graph en avait besoin. Il faut corriger l'architecture pour que `nodeSize` soit accessible a la table.

2. **SVGs importes comme URLs et rendus via `<img>` tags**. Pour la phase 5 (`IndexStatus`), c'est acceptable car les SVGs concernes (`pencil.svg`, `plus.svg`, `minus.svg`) ont des couleurs hardcodees dans le fichier SVG et ne necessitent pas de coloration dynamique. En revanche, cette approche est **incompatible avec la phase 6** car `branch.svg`, `tag.svg` et `git.svg` utilisent `currentColor` et sont colores dynamiquement via `fill`/`stroke` inline dans le code React. L'approche `<img>` ne le permet pas. La phase 6 devra utiliser des SVG inline.

---

## Points d'attention

### 1. `nodeSize` hardcode au lieu de dynamique -- Regression architecturale -- Correction a planifier (non bloquant pour phase 6)

**Decisions concernees** : Phase 5.1 decision #1 et Phase 5.3 decision #3 -- `TableRow.vue` utilise `DEFAULT_NODE_SIZE` (20) au lieu de lire `nodeSize` depuis un contexte.

**Constat** : Dans le code React source, `nodeSize` est present dans **les deux** contextes :
- `GitContextBag` (contexte global, accessible par tous les modules dont la table)
- `GraphContextBag` (contexte du graphe)

Le `TableRow.tsx` React lit `nodeSize` depuis `useGitContext()` et l'utilise pour calculer dynamiquement la hauteur du gradient de background des lignes selectionnees/previewees :
```typescript
const { nodeSize } = useGitContext()
const height = nodeSize + BACKGROUND_HEIGHT_OFFSET
```

**Historique de l'erreur** :
- **Review phase 3 (point #10)** : L'agent reviewer a decide de retirer `nodeSize` (et `graphOrientation`) du `GitContextBag`, estimant que "ces valeurs ne concernent que le module Graph, pas le composant racine". L'analyse etait incomplete : elle n'a pas identifie que `TableRow` (phase 5) accede aussi a `nodeSize` via le `GitContext` dans le code React source.
- **Review phase 4 (point #7)** : A confirme la decision de la phase 3 sans reevaluer les besoins des modules Table et Tags.
- **Phase 5 implementation** : L'agent, ne trouvant pas `nodeSize` dans `GitContextBag`, a utilise `DEFAULT_NODE_SIZE` en hardcode et a rationalise ce choix comme "eviter le couplage table -> graph context". C'est une rationalisation a posteriori d'un probleme architectural en amont.

**Impact concret** : Si un consommateur passe `nodeSize={30}` a `<GitLogGraphHTMLGrid>`, les noeuds du graphe grossissent mais le gradient de fond dans la table reste base sur 20px. Cela cree un desalignement visuel entre la bande coloree de la table et la hauteur des noeuds du graphe.

**Perimetre d'impact** : Apres verification, `nodeSize` n'est utilise que par le module Graph (phase 4) et le module Table (phase 5). Le module Tags (phase 6) ne l'utilise **pas du tout**, et les phases 7 (supprimee) et 8 non plus. La correction n'est donc **pas bloquante** pour continuer les phases suivantes.

**Action requise** : Remettre `nodeSize` dans le `GitContextBag` (dans `composables/keys.ts`) et le fournir depuis `GitLog.vue`, comme c'est le cas dans le code React source. Puis modifier `TableRow.vue` pour lire `nodeSize` depuis `useGitContext()` au lieu d'utiliser la constante. `GraphCore.vue` peut continuer a le fournir aussi dans le `GraphContextBag` pour les composants du graphe. Cette correction peut etre faite a tout moment (avant la phase 8 au plus tard, pour que la validation visuelle soit correcte).

**Statut** : `ATTENTION` -- Regression a corriger, mais non bloquante pour la phase 6. Peut etre planifiee comme tache independante avant la phase 8.

---

### 2. SVG importes comme URLs avec `<img>` -- Acceptable pour phase 5, incompatible pour phase 6

**Decision concernee** : Phase 5.2 decisions #1 et #2 -- Les SVGs sont importes comme URLs (`import pencilIcon from './pencil.svg'`) et rendus via `<img>` tags.

**Constat** : Le code React source utilise le suffixe `?react` (Vite SVGR plugin) pour importer les SVGs comme composants React. La phase 5 a choisi d'utiliser le support natif de Vite pour importer les SVGs comme URLs, sans plugin additionnel.

**Analyse des SVGs concernes** :

| SVG | Utilise par | Couleurs dans le fichier SVG | Coloration dynamique en React | Approche `<img>` OK ? |
|-----|-----------|------------------------------|-------------------------------|----------------------|
| `pencil.svg` | IndexStatus (phase 5) | Hardcodees (#FBB429, #FF757C...) | Non | **Oui** |
| `plus.svg` | IndexStatus (phase 5) | Hardcodees (#60de44, #4D4D4D) | Non | **Oui** |
| `minus.svg` | IndexStatus (phase 5) | Hardcodees (#FF757C, #4D4D4D) | Non | **Oui** |
| `branch.svg` | BranchIcon (phase 6) | `fill="currentColor"` | **Oui** : `fill: textColour` | **Non** |
| `tag.svg` | TagIcon (phase 6) | `stroke="currentColor"` | **Oui** : `stroke: textColour` | **Non** |
| `git.svg` | GitIcon (phase 6) | `fill="currentColor"` | **Oui** : `fill: shiftAlphaChannel(textColour, 0.8)` | **Non** |

**Conclusion** : L'approche `<img>` de la phase 5 est correcte pour `IndexStatus` car ces SVGs ont des couleurs figees. Mais elle ne doit **pas** etre generalisee a la phase 6. Les icones `BranchIcon`, `TagIcon` et `GitIcon` utilisent `currentColor` dans leurs SVGs et sont colorees dynamiquement par le composant React parent via des styles inline `fill`/`stroke`. Un `<img>` ne permet pas de modifier le `fill` ou `stroke` interne d'un SVG.

**Impact sur phase 6.1** : Le plan de la phase 6.1 prevoit :
> "Pour Vue avec Vite, il faudra soit utiliser `vite-svg-loader` (ou un plugin equivalent), soit les convertir manuellement en composants Vue SFC."

Cette recommandation reste pertinente pour `branch.svg`, `tag.svg` et `git.svg`. L'approche recommandee est d'**inliner le contenu SVG directement dans le `<template>`** des composants Vue `BranchIcon.vue`, `TagIcon.vue`, `GitIcon.vue`. C'est simple, sans dependance de plugin, et permet d'appliquer `fill`/`stroke` via les props ou le style inline. C'est d'ailleurs ce que sont deja ces composants dans le code React : de simples wrappers qui rendent un SVG avec un style dynamique.

**Note sur la performance** : Ces icones ne sont **pas** rendues pour chaque commit. Elles n'apparaissent que sur les commits qui sont des branch tips, des tags, ou le pseudo-commit index. Sur une page de 50 commits, on parle typiquement de **3 a 9 instances** d'icones au total. Chaque SVG est tres simple (1-2 paths). L'impact DOM est de ~15-35 noeuds supplementaires, totalement negligeable. Le inline SVG ne pose aucun probleme de performance.

**Action requise** : Mettre a jour la phase 6.1 :
- Les SVGs `branch.svg`, `tag.svg`, `git.svg` doivent etre **inlines dans le template** de leurs composants Vue respectifs (`BranchIcon.vue`, `TagIcon.vue`, `GitIcon.vue`), pas importes comme URLs
- Le critere de validation "Les SVG sont importables comme composants Vue" doit etre reformule : "Les SVG sont rendus comme contenu inline dans le template et acceptent une coloration dynamique via props/styles"
- `pencil.svg`, `plus.svg`, `minus.svg` peuvent rester en approche `<img>` (deja en place depuis la phase 5)
- La mention "vite-svg-loader" peut etre retiree au profit de l'approche inline (plus simple, zero dependance)

**Statut** : ~~`ATTENTION`~~ `RESOLU` -- Le plan de la phase 6.1 a ete mis a jour pour specifier l'approche SVG inline pour les icones a coloration dynamique.

---

### 3. Repertoire `assets/` cree avec 3 SVGs -- Impact sur Phase 6.1

**Decision concernee** : Phase 5.2 decision #2 -- Le repertoire `assets/` a ete cree sous `GitLog/` avec `pencil.svg`, `plus.svg`, `minus.svg`.

**Constat** : Le plan de la phase 6.1 prevoit de "Copier les fichiers SVG : `branch.svg`, `git.svg`, `merge.svg`, `tag.svg`, `minus.svg`, `pencil.svg`, `plus.svg`". Or 3 des 7 fichiers (`minus.svg`, `pencil.svg`, `plus.svg`) sont **deja en place** dans `assets/`.

**Impact** : La phase 6.1 devra seulement copier les 4 SVGs manquants (`branch.svg`, `git.svg`, `merge.svg`, `tag.svg`). De plus, vu que `branch.svg`, `tag.svg` et `git.svg` seront inlines dans les composants Vue (cf point #2), ils n'ont meme pas besoin d'etre dans `assets/` — le contenu SVG sera directement dans le `<template>`. Seul `merge.svg` reste a copier dans `assets/` s'il est utilise via `<img>`.

**Action requise** : Aucune modification formelle du plan necessaire. L'agent de la phase 6.1 doit verifier ce qui existe deja avant de copier.

**Statut** : `OK` Impact mineur -- information pour l'agent.

---

### 4. `data-testid` ajoutes dans les composants table -- Impact informatif sur Phase 6.2

**Decision concernee** : Phase 5.3 decision #4 -- Ajout de `data-testid` sur les lignes de la table (`vue-git-log-table-row-${index}`), et sur les elements de `IndexStatus.vue`.

**Constat** : La review de la phase 3 (point #12) notait l'absence de `data-testid` car "le projet n'utilise pas de tests (cf CLAUDE.md)". La phase 5 a neanmoins ajoute ces attributs pour rester fidele au code React source (qui les inclut). La phase 4 avait aussi ajoute des `data-testid` (cf `GraphColumn.vue`).

**Impact sur phase 6.2** : ~~L'agent de la phase 6 devrait suivre le meme pattern et ajouter des `data-testid`.~~ En fait, le projet n'utilise pas de tests automatises et ces attributs n'ont pas lieu d'etre. L'agent de la phase 6 ne doit **pas** ajouter de `data-testid`. Ceux des phases 4 et 5 seront retires ulterieurement (cf section "A voir plus tard" dans le [plan index](./4-plan-index.md)).

**Action requise** : Ne pas ajouter de `data-testid` en phase 6.

**Statut** : `OK` Pas de `data-testid` a ajouter.

---

### 5. Iteration dans `GitLogTable` et non dans `TableContainer` -- Impact nul

**Question resolue** : Phase 5.1 resolved question #2 -- L'iteration sur les commits visibles se fait dans `GitLogTable.vue`, pas dans `TableContainer.vue`.

**Constat** : Le plan de la phase 5.1 mentionnait pour `TableContainer` : "Itere sur les commits visibles et rend un `TableRow` par commit". En realite, c'est `GitLogTable.vue` qui itere (via deux `v-for` : un pour les placeholders, un pour les donnees reelles). `TableContainer.vue` est un simple wrapper de layout (CSS Grid vs div selon `hasCustomRow`).

**Impact** : Aucun impact sur les phases suivantes. Le module Tags (phase 6) n'utilise pas `TableContainer`. L'architecture interne de la table est autonome.

**Action requise** : Aucune.

**Statut** : `OK` Aucun impact.

---

### 6. `hasCustomRow` boolean prop pour la detection du slot custom -- Impact nul

**Decision concernee** : Phase 5.1 decision #2 -- Un boolean `hasCustomRow` est passe a `TableContainer` au lieu d'une detection de slot.

**Constat** : Ce pattern est specifique au module Table. Le module Tags (phase 6) n'a pas de concept equivalent de "custom row". Le pattern est interne et n'affecte pas les interfaces publiques.

**Impact** : Aucun impact sur les phases suivantes.

**Action requise** : Aucune.

**Statut** : `OK` Aucun impact.

---

### 7. Interactions cablees eagerly en phase 5.1 -- Impact sur Phase 5.3 (deja resolu)

**Decision concernee** : Phase 5.1 decision #4 et Phase 5.3 decision #1 -- Les event handlers ont ete integres des la phase 5.1, rendant la phase 5.3 largement redondante pour les interactions.

**Constat** : La phase 5.3 devait porter sur les interactions (selection, preview, synchronisation). En pratique, tout etait deja fait en phase 5.1. La phase 5.3 a simplement **verifie** le bon fonctionnement.

**Impact** : Aucun impact sur les phases suivantes. C'est une note de processus interne.

**Action requise** : Aucune.

**Statut** : `OK` Aucun impact.

---

### 8. Hash non affiche dans `CommitMessageData` -- Impact sur Phase 8.4

**Decision concernee** : Phase 5.2 decision #5 -- Le plan mentionnait "Affiche le hash en resume" pour `CommitMessageData`, mais le code React source ne le fait pas. L'implementation suit le code React.

**Constat** : Le hash du commit est affiche dans les tooltips des noeuds du graphe (`CommitNodeTooltip.vue`), pas dans la cellule du message de la table. C'est fidele au comportement original.

**Impact sur phase 8.4** : L'agent de validation visuelle ne devrait **pas** s'attendre a voir un hash dans la colonne "Commit message" du tableau. Si le hash doit etre visible dans la table, ce serait un ajout post-portage, pas un bug.

**Action requise** : Aucune.

**Statut** : `OK` Comportement fidele au source.

---

### 9. `shouldRenderHyphenValue` passe comme `is-placeholder` aux sous-composants -- Impact nul

**Decision concernee** : Phase 5.2 decision #4 -- Logique `shouldRenderHyphenValue` ajoutee a `TableRow.vue`.

**Constat** : Les composants `AuthorData` et `TimestampData` recoivent un prop `is-placeholder` qui determine si un tiret `-` est affiche au lieu des donnees reelles. C'est utilise pour le pseudo-commit "index" et les lignes placeholder.

**Impact** : Aucun impact sur les phases suivantes. C'est interne au module Table.

**Action requise** : Aucune.

**Statut** : `OK` Aucun impact.

---

### 10. `dayjs` plugins limites a `utc` et `relativeTime` -- Impact nul

**Question resolue** : Phase 5.2 resolved question #2 -- Le plugin `advancedFormat` de dayjs n'est pas necessaire.

**Constat** : Seuls les plugins `utc` et `relativeTime` sont etendus dans `TimestampData.vue`, conformement au code source React.

**Impact** : Si la phase 8.3 teste des formats de date avances (via la prop `timestampFormat`), certains tokens de format pourraient ne pas fonctionner sans `advancedFormat`. En pratique, les formats standards de dayjs (`YYYY-MM-DD HH:mm:ss`) fonctionnent sans plugin additionnel. C'est un non-probleme pour les usages prevus.

**Action requise** : Aucune.

**Statut** : `OK` Aucun impact.

---

## Conclusion

**Deux decisions de la phase 5 necessitent des corrections** :

1. ~~**`nodeSize` hardcode**~~ `A CORRIGER` : Remettre `nodeSize` dans le `GitContextBag` (il en avait ete retire a tort lors de la review de la phase 3). Modifier `TableRow.vue` pour lire `nodeSize` depuis `useGitContext()` au lieu de `DEFAULT_NODE_SIZE`. C'est une regression par rapport au comportement React source. Apres verification, `nodeSize` n'est pas utilise par les phases 6-8, donc cette correction est **non bloquante** et peut etre planifiee comme tache independante avant la phase 8 (validation visuelle).

2. **Phase 6.1 a mettre a jour** : Les SVGs a coloration dynamique (`branch.svg`, `tag.svg`, `git.svg`) doivent etre **inlines dans le template** de leurs composants Vue (pas en `<img>`). L'approche `<img>` de la phase 5 est correcte pour `IndexStatus` (SVGs a couleurs hardcodees) mais ne doit pas etre generalisee.

Les autres decisions sont coherentes avec les plans existants et ne necessitent pas de modifications.

Les decisions les plus notables pour les agents des phases suivantes sont :
- **Correction `nodeSize` (tache independante, avant phase 8)** : Ajouter `nodeSize` dans `GitContextBag`, le fournir dans `GitLog.vue`, et modifier `TableRow.vue` pour le consommer. Non bloquant pour la phase 6.
- **Phase 6** : Inliner les SVGs a coloration dynamique dans les templates Vue. `pencil.svg`/`plus.svg`/`minus.svg` restent en `<img>` (deja en place). 3 SVGs sont deja dans `assets/` (ne copier que les manquants). Ne pas ajouter de `data-testid` (ceux des phases 4-5 seront retires plus tard).
- **Phase 8** : La validation visuelle (phase 8.4) devrait verifier le bon fonctionnement du gradient de background dans la table avec differentes valeurs de `nodeSize`. Ne pas s'attendre a voir un hash dans la colonne "Commit message" de la table.
