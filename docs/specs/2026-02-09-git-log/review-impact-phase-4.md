# Review d'impact de la phase 4 sur les phases suivantes

> Ce document analyse les "Decisions made during implementation" et "Resolved questions and doubts" de la phase 4, et evalue leurs consequences potentielles sur les phases 5 a 8.

---

## Resume executif

La phase 4 (Module Graph - HTML Grid) est la plus volumineuse du plan avec 6 sous-phases. Elle a ete implementee de facon fidele au plan, avec plusieurs decisions architecturales notables. La decision la plus impactante pour les phases suivantes concerne le **choix d'un tooltip CSS-positionne au lieu des composants WebAwesome** (phase 4.6, decision #1). Apres review, cette decision a ete **validee** : les tooltips resteront en CSS interne pour le portage, et la question d'une migration WebAwesome sera reevaluee une fois le portage termine. En consequence, la **phase 7 est supprimee** et la **phase 6.2 a ete mise a jour**. Les autres decisions sont des clarifications d'implementation qui facilitent le travail des phases suivantes.

---

## Points d'attention

### 1. Tooltip CSS au lieu de WebAwesome -- Impact majeur sur Phase 7

**Decisions concernees** : Phase 4.6 decision #1 et resolved question #1 -- Un tooltip CSS-positionne a ete choisi au lieu de `wa-tooltip` ou `wa-popover`.

**Constat** : L'analyse effectuee en phase 4.6 a revele que :
- `wa-tooltip` impose son propre shadow DOM avec background/border/padding, ce qui entre en conflit avec les styles themes custom de `CommitNodeTooltip`
- `wa-popover` s'ouvre au clic (pas au hover) et necessite un bouton ancre via l'attribut `for`
- La solution retenue est un `<div>` positionne en absolu, controle par un `showTooltip` ref local toggle sur `mouseover`/`mouseout`

**Impact sur phase 7** : ~~Le plan de la phase 7 prevoyait un "Audit et remplacement des tooltips" par les composants WebAwesome.~~

**Decision post-review** : L'approche tooltip CSS interne est validee pour le portage. La phase 7 est **supprimee**. La question d'une migration WebAwesome sera reevaluee une fois le portage complet termine, au vu du rendu reel. Les modifications suivantes ont ete appliquees :
- `4-plan-phase-7.md` : marque comme SUPPRIMEE
- `4-plan-phase-6.md` : `BranchTag` tooltip mis a jour pour utiliser l'approche CSS (au lieu de WebAwesome)
- `4-plan-index.md` : reference de la phase 7 barree

**Statut** : ~~`ATTENTION`~~ `RESOLU` -- Phase 7 supprimee, phase 6.2 mise a jour.

---

### 2. `CommitNodeTooltip.vue` deja implemente -- Impact sur Phase 7.2

**Decisions concernees** : Phase 4.2 (creation du composant) et Phase 4.6 decision #3 (integration dans `CommitNode`).

**Constat** : `CommitNodeTooltip.vue` existe deja avec :
- Son SCSS module (`CommitNodeTooltip.module.scss`)
- Les props `commit`, `borderColour`, `backgroundColour`
- L'integration dans `CommitNode.vue` avec rendu conditionnel (`showTooltip` + `showCommitNodeTooltips` du graph context)
- Support du custom tooltip via la ref `tooltip` du `GraphContextBag`

**Impact** : La phase 7.2 prevoit de "Creer le contenu par defaut du tooltip de commit : hash, message, auteur, date, branche" et de "Styliser avec les couleurs du theme". C'est exactement ce qui a ete fait. La phase 7.2 est **entierement redondante** pour le tooltip du `CommitNode`.

**Action requise** : ~~Retirer ou requalifier la phase 7.2.~~ Fait -- phase 7 supprimee.

**Statut** : ~~`ATTENTION`~~ `RESOLU` -- Phase 7 supprimee (phase 7.2 etait deja implementee).

---

### 3. Custom node render dans `CommitNode` au lieu de `GraphColumn` -- Impact sur Phase 5 (pattern a suivre)

**Decisions concernees** : Phase 4.2 decision #1, Phase 4.5 decision #1 -- Le rendu custom node est gere dans `CommitNode` et non dans `GraphColumn`.

**Constat** : Dans le code React source, `GraphColumn` decide de rendre soit un custom node soit `<CommitNode>`. Dans le port Vue, `CommitNode` gere toujours le rendu custom en interne. `GraphColumn` rend toujours `<CommitNode>`.

**Impact** : Ce pattern est **analogue** a ce que la phase 5.1 devra faire pour le custom table row. Le plan de la phase 5.1 prevoit deja que `TableRow.vue` gere le scoped slot `#row` en interne : "Si le scoped slot `#row` est fourni : rend le slot avec les donnees exposees. Sinon : rend le layout par defaut." C'est le meme pattern. Pas de modification necessaire.

**Action requise** : Aucune. Le pattern est deja aligne.

**Statut** : `OK` Coherent.

---

### 4. `.stop` modifiers pour eviter le double-toggle -- Impact informatif sur Phase 5.3

**Decisions concernees** : Phase 4.6 decision #7 et post-implementation review fix #3 -- Tous les event handlers de `CommitNode` utilisent `.stop` pour eviter la double-execution.

**Constat** : En Vue, la reactivite synchrone fait que les events qui remontent (bubble) de `CommitNode` (div `role="button"`) vers `GraphColumn` (element `<button>`) causent un double-toggle de la selection. Le `.stop` modifier est applique sur `@click`, `@mouseover`, `@mouseout`, `@focus`, `@blur`, et `@keydown`.

**Impact sur phase 5** : La phase 5.3 (interactions de la table) devra gerer les memes types d'interactions (click, hover) pour la selection et le preview. Si `TableRow.vue` contient des sous-elements interactifs (comme `CommitMessageData` avec un lien cliquable), le meme probleme de bubble pourrait se poser. L'agent de la phase 5 doit etre conscient de ce pattern et appliquer `.stop` si necessaire pour eviter les doubles toggles.

Cependant, en pratique, la table a une architecture plus simple : les handlers sont sur le `<div>` wrapper de `TableRow`, et les sous-composants de cellules (`CommitMessageData`, `AuthorData`, `TimestampData`) sont purement presentationnels. Le risque de bubble est moindre.

**Action requise** : Aucune modification du plan, mais l'agent de la phase 5 doit etre informe du pattern `.stop` au cas ou des interactions se superposeraient.

**Statut** : `OK` Point informatif -- pas de modification requise.

---

### 5. Prop `colour` en anglais britannique -- Impact sur Phases 5, 6

**Decision concernee** : Post-implementation review fix #4 -- Renommage de `color` vers `colour` pour suivre la convention de la codebase.

**Constat** : La convention du codebase (heritee du code source React) utilise l'orthographe britannique `colour` partout : `types.ts`, `useTheme.ts`, et maintenant tous les composants du graphe. Les props des composants de la phase 4 utilisent `colour`, `borderColour`, `backgroundColour`, etc.

**Impact** :
- **Phase 5** (Table) : `TableRow.vue` recevra une prop `backgroundColour` (pas `backgroundColor`). Le plan mentionne "fond colore (gradient lineaire) base sur la couleur de colonne du commit dans le graphe". L'agent devra suivre la convention `colour`.
- **Phase 6** (Tags) : `BranchTag.vue` et ses sous-composants utiliseront probablement des props liees aux couleurs. La convention `colour` doit etre respectee.

**Action requise** : Aucune modification du plan. L'agent doit simplement suivre la convention `colour` deja etablie.

**Statut** : `OK` Point de convention -- facile a suivre.

---

### 6. `BreakPoint.vue` : prop `style` renommee en `styleOverrides` -- Impact mineur sur Phase 6

**Decision concernee** : Phase 4.4 decision #1.

**Constat** : `style` est un attribut reserve en Vue. Le renommage en `styleOverrides` evite le conflit.

**Impact** : `BreakPoint.vue` n'est pas utilise dans les phases 5 (Table) ou 6 (Tags). Il est specifique au module Graph. Pas d'impact direct sur les phases suivantes.

Cependant, c'est un bon pattern a garder en tete : si des composants des phases 5-6 doivent accepter des overrides de style en prop, ils devraient utiliser `styleOverrides` (ou un nom similaire) plutot que `style` pour eviter le meme conflit.

**Action requise** : Aucune.

**Statut** : `OK` Aucun impact direct.

---

### 7. `nodeSize` et `orientation` props gerees par `GraphCore.vue` dans `GraphContextBag` -- Confirme la correction phase 3

**Decision concernee** : Phase 4.1 decision #1 -- Les `setNodeSize`/`setGraphOrientation` React ont ete supprimes.

**Constat** : En phase 3 review (point #10), `nodeSize` et `graphOrientation` avaient ete retires du `GitContextBag`. La phase 4.1 confirme que ces valeurs vivent dans le `GraphContextBag` fourni par `GraphCore.vue`, alimentees par les props. Les composants enfants y accedent via `useGraphContext()`.

**Impact** : Confirme que la correction post-review de la phase 3 etait la bonne approche. Pas d'impact supplementaire.

- **Phase 8.3** : Le consommateur passe `nodeSize` et `orientation` via les props de `<GitLogGraphHTMLGrid>` (qui est `GraphCore.vue`). C'est deja documente dans la review de la phase 3.

**Action requise** : Aucune.

**Statut** : `OK` Confirme la decision de la phase 3.

---

### 8. Off-by-one fix dans `getColumnsForCommit` -- Impact informatif

**Decision concernee** : Post-implementation review fix #1 -- Les row indices dans `columnData` sont 1-based.

**Constat** : `columnData` (retourne par `useColumnData`) utilise des indices de lignes **1-based** (pas 0-based). Cela a cause un bug en phase 4 et a ete corrige.

**Impact** : Si des composants des phases suivantes (phase 5 ou 6) ont besoin d'acceder a `columnData` pour resoudre les colonnes d'un commit, ils devront utiliser des indices 1-based. En pratique, seul le module Graph utilise `columnData` directement. La table et les tags ne resolvent pas de colonnes eux-memes.

**Action requise** : Aucune.

**Statut** : `OK` Specifique au module Graph.

---

### 9. SCSS import alias uniformise a `styles` -- Impact sur Phases 5, 6

**Decision concernee** : Post-implementation review fix #5 -- L'import SCSS module utilise `styles` uniformement.

**Constat** : Tous les composants du graphe importent leur SCSS module comme `import styles from './Component.module.scss'` (pas `classes` ou autre alias).

**Impact** : Les phases 5 et 6 qui creent de nouveaux composants avec des SCSS modules doivent suivre cette convention : `import styles from './Component.module.scss'`. C'est un detail mineur mais contribue a la coherence du code.

**Action requise** : Aucune modification du plan. Convention a suivre.

**Statut** : `OK` Convention de code.

---

### 10. Tooltip de `BranchTag` encore a implementer -- Impact sur Phase 6.2

**Decision concernee** : Phase 4.6 resolved question #1 (scope du tooltip CSS).

**Constat** : Le tooltip CSS a ete implemente uniquement pour `CommitNode`. Le tooltip de `BranchTag` (qui dans le code React utilise `react-tiny-popover` avec `positions='right'`) est prevu en phase 6.2. Le plan de la phase 6.2 dit "Tooltip au hover via composants WebAwesome (au lieu de `react-tiny-popover`)".

**Impact** : Etant donne la conclusion de la phase 4.6 sur l'incompatibilite de WebAwesome pour les tooltips, l'agent de la phase 6.2 devrait probablement utiliser la meme approche CSS-positionnee que `CommitNode`, adaptee avec un positionnement a droite au lieu de en haut/en bas. Le plan de la phase 6.2 mentionne encore "composants WebAwesome" mais cette approche a ete invalidee.

**Action requise** : ~~Mettre a jour la mention "composants WebAwesome" dans le plan de la phase 6.2.~~ Fait -- le plan de la phase 6.2 a ete mis a jour pour utiliser l'approche CSS-positionnee.

**Statut** : ~~`ATTENTION`~~ `RESOLU` -- Plan de la phase 6.2 mis a jour.

---

## Conclusion

**Une seule decision de la phase 4 necessitait une adaptation des plans des phases suivantes** : le choix du tooltip CSS au lieu de WebAwesome. Les modifications ont ete appliquees :

1. ~~**Phase 7 a requalifier**~~ `FAIT` : Phase 7 supprimee (tooltips CSS valides, `CommitNodeTooltip` deja implemente).
2. ~~**Phase 6.2 a mettre a jour**~~ `FAIT` : Mention "composants WebAwesome" remplacee par "positionnement CSS".

Les autres decisions (custom node dans `CommitNode`, `.stop` modifiers, convention `colour`, SCSS `styles` alias, etc.) sont coherentes avec les plans existants et ne necessitent pas de modifications.

Les decisions les plus notables pour les agents des phases suivantes sont :
- **Phase 5** : Suivre la convention `colour` (pas `color`). Etre conscient du pattern `.stop` pour les event handlers imbriques.
- **Phase 6** : Le tooltip de `BranchTag` utilise l'approche CSS-positionnee (comme `CommitNode`), avec positionnement a droite.
- **Phase 7** : Supprimee.
- **Phase 8** : Pas d'impact supplementaire au-dela de ce qui etait deja documente dans les reviews precedentes. La phase 8.1 n'a plus besoin de referencer la phase 7 dans "Tous les composants des phases 1-7" -- c'est maintenant "phases 1-6".
