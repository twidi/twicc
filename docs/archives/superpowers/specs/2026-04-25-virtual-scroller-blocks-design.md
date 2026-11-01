# Virtual scroller — regroupement des items en blocs

## Contexte

La liste virtualisée d'une session (`SessionItemsList.vue` + `VirtualScroller.vue` + `useVirtualScroll.js`) rend les `visualItem` un par un dans un `VirtualScrollerItem`. Le rendu visuel "carte commune" pour une suite d'items non-`user_message` (content_items, assistant_message, tools, etc.) repose sur des sélecteurs CSS dans `frontend/src/components/SessionItem.vue` (lignes 470-507) qui examinent les `.virtual-scroller-item` actuellement présents dans le DOM :

- `:not(:has(.session-item[data-kind="user_message"]))` → un item non-user
- `+ .virtual-scroller-item:not(:has(...))` → l'item suivant non-user
- `:not(:has(+ .virtual-scroller-item))` ou `:has(+ ... user_message)` → le dernier non-user d'une séquence

Ces règles posent des bordures, paddings, radius et ombres différents sur le **premier**, le **dernier** et les **intermédiaires** d'une séquence non-user, donnant l'illusion d'une carte unique alors que ce sont des éléments séparés.

**Bug visuel :** quand le composable virtuel `unload` un item d'une séquence visuellement contigüe (parce qu'il est passé hors de la zone `unloadBuffer`), le voisin restant change de rôle (l'ancien second devient premier ; ou l'avant-dernier devient dernier). Ses variables CSS (`--content-card-start-item`, `--assistant-card-border-top-width`, `--assistant-card-top-spacing`, etc.) basculent. Sa hauteur change. Le composable doit alors compenser via son anchor-based scroll, ce qui casse la fluidité du défilement.

## Objectif

Regrouper en amont les items en **blocs**, et virtualiser les blocs (un bloc = un `VirtualScrollerItem`). Un bloc est atomique : il est rendu entièrement ou pas du tout. Les règles CSS first/last deviennent **internes au bloc** (s'appliquent sur les enfants directs d'un même wrapper), donc immunes au load/unload.

## Décisions de design

### Définition d'un bloc

À partir de la liste plate `sessionVisualItems[sessionId]` (calculée par `recomputeVisualItems`), on identifie des **runs** : suites contiguës d'items partageant la propriété "est un user_message" ou "n'en est pas un".

- Run "user" : un ou plusieurs `user_message` consécutifs (typiquement 1 dans la pratique).
- Run "non-user" : tout le reste, consécutif (content_items, assistant_message, tool, compact_summary, api_error, custom_title, etc.). En modes `conversation`/`normal`/`simplified`, les `system` sont déjà filtrés par `computeVisualItems` et n'apparaissent pas dans `visualItems`. En mode `debug` ils apparaissent et compteront comme un item normal — c'est acceptable.

### Cap par bloc et découpage en sous-blocs

Cap : **`MAX_BLOCK_SIZE = 100` items par sous-bloc**.

Justification : analyse statistique sur 5 374 sessions de la base locale (médiane 10, p95 99, p99 169, p99.9 313, max 1164 — items affichables, c'est-à-dire en excluant les `system`). À 100, on couvre ~88 % des blocs réels en un seul sous-bloc, et on découpe seulement la longue queue.

Algorithme :
1. Pour chaque run, calculer `numSubBlocks = ceil(run.items.length / MAX_BLOCK_SIZE)`.
2. Découper en parts égales (sauf la dernière, plus petite éventuellement) via `slice`.
3. Le 1er sous-bloc d'un run → `isRealStart = true`.
4. Le dernier sous-bloc d'un run → `isRealEnd = true`.
5. Si un run tient en un seul sous-bloc, ce dernier porte les deux flags.
6. Si un run fait 1 seul item, ce sous-bloc unique porte les deux flags.

### Forme d'un bloc

```js
{
  blockKey: number,        // = lineNum du premier item du sous-bloc (stable, unique)
  isUserBlock: boolean,    // true si run de user_message
  isRealStart: boolean,    // sous-bloc en tête du run
  isRealEnd: boolean,      // sous-bloc en queue du run
  items: VisualItem[],     // visual items à rendre (références stables, héritées du visualItemCache)
}
```

### Choix de la clé `blockKey`

`blockKey = firstLineNum` du sous-bloc.

Pourquoi pas `firstLineNum-lastLineNum` : le `lastLineNum` change quand un nouvel item s'ajoute à la fin (cas fréquent durant l'assistant_turn). Cela invaliderait la clé et démonterait l'instance Vue du `VirtualScrollerItem`, alors que la majorité des items à l'intérieur n'a pas changé.

Pourquoi `firstLineNum` est suffisant et stable :
- Les `lineNum` sont monotones et append-only (jamais d'insertion au milieu).
- Append à la fin d'un sous-bloc → `firstLineNum` inchangé → clé stable.
- Découpage du run par dépassement de cap : le 1er sous-bloc garde son `firstLineNum`, les sous-blocs suivants ont chacun leur propre `firstLineNum` unique.
- Aucun item ne peut être premier de deux sous-blocs distincts → unicité garantie.
- Format compact : juste un nombre, agréable à débugger via `data-block-key`.

### Pas de cache au niveau bloc

Les `visualItem` sont déjà stabilisés par `visualItemCache` dans `recomputeVisualItems` (data.js:1486-1508). À chaque recompute, un visual item dont les propriétés n'ont pas changé conserve sa **référence** JS.

Conséquence : à chaque recompute, on calcule les blocs *à neuf* (objets et arrays neufs), mais `block.items` contient les mêmes références d'items qu'avant si ces items n'ont pas changé. Vue fait alors :
- `:items` du `VirtualScroller` est un nouvel array → `positions` du composable recompute en O(blocs). Coût négligeable (≤ ~100 blocs).
- `VirtualScrollerItem` voit le même `:key="blockKey"` → instance Vue **préservée**.
- Le slot est ré-évalué et son `v-for` interne utilise `:key="item.lineNum"`. Comme chaque `item` est une référence stable, les `SessionItem` enfants reçoivent les mêmes props et **ne re-rendent pas**.

Ajouter un cache au niveau bloc serait de la sur-ingénierie : le coût d'un re-walk de la slot function de Vue est marginal, et la stabilité de référence des items à l'intérieur fait tout le travail utile.

### Cas particulier : streaming via `_onBufferDrain`

`data.js:_onBufferDrain` (ligne ~2234) **mute en place** `sessionVisualItems[sessionId][idx]` à chaque frame du streaming (driven par RAF, ~60 Hz), via :

```js
const newVi = { ...visualItems[idx] }
setParsedContent(newVi, newParsed)
visualItems[idx] = newVi
this.localState.visualItemCache[sessionId].set(targetLineNum, newVi)
```

… **sans** appeler `recomputeVisualItems`. Conséquence : avec le design ci-dessus, `block.items[i]` (capturé à la dernière `recomputeVisualItems`) continuerait à pointer vers l'ancien `visualItem`, et le `SessionItem` rendu via le slot recevrait la stale référence → texte streamé figé jusqu'au prochain recompute (ce qui n'arrive qu'à `streamBlockStop`).

**Solution :** étendre `_onBufferDrain` pour aussi patcher l'index correspondant dans le bloc :

```js
const blockKey = this.localState.sessionLineNumToBlockKey[sessionId]?.get(targetLineNum)
if (blockKey != null) {
    const blocks = this.localState.sessionVisualBlocks[sessionId]
    if (blocks) {
        // Linear scan over blocks (small N, <100). Could be optimized via a Map<blockKey, block>
        // if needed, but unnecessary for current scale.
        for (const block of blocks) {
            if (block.blockKey === blockKey) {
                const itemIdx = block.items.findIndex(it => it.lineNum === targetLineNum)
                if (itemIdx !== -1) block.items[itemIdx] = newVi
                break
            }
        }
    }
}
```

Pinia rend `localState` réactif, donc cette mutation profonde déclenche la réactivité Vue : le slot est ré-évalué, le `v-for :key="item.lineNum"` reuse l'instance `SessionItem`, qui reçoit les nouveaux props (nouveau `item` ref → `getParsedContent(item)` retourne le nouveau parsed text) → re-render correct.

Pourquoi pas appeler `recomputeVisualItems` à la place : c'est un O(N) sur tous les items affichés, déclenché à 60 Hz pendant le streaming. La mutation ciblée ci-dessus est O(blocs) (≤ 100 typiquement) puis O(items_in_block) pour le findIndex (≤ 100), donc négligeable.

### Placement de la logique de groupement

Dans le store (`data.js`), à la fin de `recomputeVisualItems`, après le calcul de `stableItems` :

1. On garde `sessionVisualItems[sessionId]` plat (consommé par `scrollToLineNum`, `commentedToolLineNums`, `blocksWithComments`, `ensureBlockDetailed`, etc. — APIs inchangées).
2. On calcule `sessionVisualBlocks[sessionId]` en walk linéaire O(n) sur les visual items stables.
3. Nouveau getter `getSessionVisualBlocks(sessionId)`.

Pourquoi le store et pas un computed dans `SessionItemsList.vue` :
- Le calcul est trivial (un walk linéaire), aucune raison de le déporter.
- Cohérence : tout ce qui dérive de visualItems est déjà calculé dans le store.
- Une seule source de vérité pour la mapping `lineNum → blockKey` (utilisée par `scrollToLineNum`).

### Index secondaire `lineNumToBlockKey`

Pendant la construction des blocs, on remplit en parallèle une map `lineNumToBlockKey: Map<number, number>` stockée dans `localState.sessionLineNumToBlockKey[sessionId]`. Utilisée par `scrollToLineNum` pour trouver rapidement le bloc qui contient un item.

## Rendu côté composant

### `SessionItemsList.vue`

```vue
const visualBlocks = computed(() => store.getSessionVisualBlocks(props.sessionId))
```

```vue
<VirtualScroller
    ref="scrollerRef"
    :items="visualBlocks"
    :item-key="block => block.blockKey"
    :min-item-height="MIN_ITEM_SIZE"
    ...
>
    <template #default="{ item: block }">
        <template v-for="(item, idx) in block.items" :key="item.lineNum">
            <!-- placeholder, group head, regular item — comme aujourd'hui, MAIS avec :class -->
            <SessionItem
                :class="{
                    'is-real-start': block.isRealStart && idx === 0,
                    'is-real-end': block.isRealEnd && idx === block.items.length - 1,
                }"
                ...
            />
        </template>
    </template>
</VirtualScroller>
```

### Pose des classes `is-real-start` / `is-real-end`

- `is-real-start` posée sur le **1er rendu** du bloc, **uniquement si** `block.isRealStart === true`.
- `is-real-end` posée sur le **dernier rendu** du bloc, **uniquement si** `block.isRealEnd === true`.

"Rendu" = soit un `<SessionItem>`, soit un `<GroupToggle>`, soit un placeholder. Si en position extrême c'est un `GroupToggle`, c'est lui qui reçoit la classe (idem placeholder).

Concrètement, dans le template, chaque branche du `v-if` (placeholder / group head + item / regular item) reçoit le calcul `:class="{ 'is-real-start': ..., 'is-real-end': ... }"`.

### Adaptation du CSS (à la charge de l'utilisateur)

Les sélecteurs cross-`virtual-scroller-item` (`+ .virtual-scroller-item:not(...)`, `:has(+ .virtual-scroller-item .session-item[...])`, etc.) deviennent obsolètes. Les nouvelles règles travaillent à l'intérieur d'un même `.virtual-scroller-item` :

- "premier item du bloc visuel" → `.session-item.is-real-start` (ou `.group-toggle.is-real-start`)
- "dernier item du bloc visuel" → `.session-item.is-real-end` (ou `.group-toggle.is-real-end`)
- "intermédiaire" → ni l'une ni l'autre

L'utilisateur s'occupe de cette adaptation. Le présent design pose la pose des classes ; la traduction CSS vers les bonnes propriétés (`--content-card-start-item`, border-radius, etc.) est faite ensuite par lui.

## Adaptations système

### Lazy loading (`onScrollerUpdate`)

Aujourd'hui (`SessionItemsList.vue:760-781`), `onScrollerUpdate` reçoit `{ startIndex, endIndex, visibleStartIndex, visibleEndIndex }` qui sont des indices dans `visualItems`. Avec les blocs, ces indices sont maintenant des **indices dans `visualBlocks`**.

Adaptation :
1. On itère les blocs dans `[bufferedStart, bufferedEnd]` (avec un buffer en blocs, par ex. 1).
2. Pour chaque bloc, on parcourt `block.items` et on collecte les `lineNum` sans content (`!hasContent(item)`).
3. Le reste (debounce, conversion en ranges, appel à `loadSessionItemsRanges`) est identique.

`LOAD_BUFFER` (50) actuel s'exprime en items. On peut soit :
- Le garder en items mais l'appliquer comme un buffer interne au-delà des blocs visibles (pénible).
- Plus simple : remplacer par `BLOCK_LOAD_BUFFER = 1` (charger les blocs voisins) et collecter tous les items sans content de ces blocs. Vu qu'un bloc fait jusqu'à 100 items, charger 1 bloc voisin = 100 items max de buffer, ce qui est cohérent avec l'esprit de l'ancienne valeur.

Choix : `BLOCK_LOAD_BUFFER = 1`.

### `scrollToLineNum`

Actuellement (`SessionItemsList.vue:1150-1226`) : trouve l'item par `lineNum` dans `visualItems` puis appelle `scroller.scrollToKey(lineNum)`.

Avec les blocs, le scroller indexe par `blockKey`, pas par `lineNum`. **Important :** `composableScrollToKey` avec `align: 'center'` centre **le bloc**, pas l'item à l'intérieur. Pour un sous-bloc qui peut faire jusqu'à 100 items et plusieurs viewports de hauteur, l'item ciblé peut se retrouver complètement hors viewport.

Adaptation :

1. Recherche de l'item dans `visualItems` (inchangé) ; si nécessaire, `ensureBlockDetailed` en mode conversation (inchangé).
2. Pré-chargement du buffer de contenu autour de la cible (inchangé sur le principe ; on cible toujours `lineNum` dans `visualItems` pour le buffer LOAD_BUFFER en items).
3. Récupérer `blockKey = store.localState.sessionLineNumToBlockKey[sessionId].get(lineNum)`.
4. Appeler `await scroller.scrollToKey(blockKey, { align: 'center' })` → scrolle le bloc au centre du viewport.
5. **Toujours** ensuite (peu importe la taille du bloc, sans check préalable) : trouver le DOM element `.session-item[data-line-num="${lineNum}"]` (via `scrollerEl.querySelector(...)`) et faire `el.scrollIntoView({ block: 'center', behavior: 'instant' })`. Pour un item déjà au centre (cas des petits blocs), c'est un no-op naturel ; pour un item dans un long bloc, c'est ce qui ramène l'item dans le viewport. Vu que tout le bloc est rendu d'un coup, l'élément existe en DOM dès que `scrollToKey` a résolu.
6. L'étape 5 historique (`scrollToFirstHighlight`, défile le scroller jusqu'au 1er `mark.search-highlight` *dans* l'item s'il est plus grand que le viewport) reste fonctionnelle telle quelle. Elle s'applique après le scrollIntoView de l'item — l'utilisateur voit en pratique un seul mouvement composé.

### `commentedToolLineNums` / `blocksWithComments` / `groupCommentsCount`

Ces fonctions opèrent sur `visualItems` plat. **Inchangées.**

### `useVirtualScroll`

**Inchangé.** Le composable est agnostique de ce qu'il rend ; il voit "des items" avec des clés. Le fait que ce soient des blocs n'est qu'un changement de granularité.

### `MIN_ITEM_SIZE`

Reste à 50px. C'est le minimum légitime (ex. user_message d'une ligne dans un bloc-de-1). Pour les blocs plus grands, le ResizeObserver remplace l'estimation par la vraie hauteur dès le 1er rendu.

### Buffer/unloadBuffer du scroller (1000 / 1500 px)

Inchangés. Le composable sélectionne les blocs à rendre via `findIndexAtPosition()` qui travaille sur les positions cumulées de blocs. Un bloc dont le rectangle (top..top+height) chevauche la zone load est inclus, peu importe sa hauteur. Pour un bloc de 5000 px de haut, dès qu'on est "dedans" il est rendu en entier ; il est unloadé seulement quand on est totalement hors de la zone unload (1500 px). Comportement correct par construction.

## Constantes

À ajouter dans `frontend/src/constants.js` :

```js
/**
 * Maximum number of visual items per virtual-scroller block (sub-block of a non-user/user run).
 * Beyond this cap, a run is split into multiple sub-blocks. Picked from session-item statistics
 * to keep ~88% of real blocks in a single sub-block while ensuring the DOM stays bounded for
 * exceptionally long sessions.
 */
export const MAX_BLOCK_SIZE = 100
```

`BLOCK_LOAD_BUFFER` reste local à `SessionItemsList.vue`.

## Modifications fichier par fichier

### `frontend/src/utils/visualItems.js`

Ajouter une fonction exportée `groupVisualItemsIntoBlocks(visualItems, maxBlockSize)` :

```js
export function groupVisualItemsIntoBlocks(visualItems, maxBlockSize) {
    const blocks = []
    if (!visualItems.length) return blocks

    // Walk visualItems, accumulate runs by isUser status
    let runItems = []
    let runIsUser = visualItems[0].kind === 'user_message'

    const flushRun = () => {
        if (!runItems.length) return
        const numParts = Math.ceil(runItems.length / maxBlockSize)
        for (let i = 0; i < numParts; i++) {
            const subItems = runItems.slice(i * maxBlockSize, (i + 1) * maxBlockSize)
            blocks.push({
                blockKey: subItems[0].lineNum,
                isUserBlock: runIsUser,
                isRealStart: i === 0,
                isRealEnd: i === numParts - 1,
                items: subItems,
            })
        }
    }

    for (const vi of visualItems) {
        const isUser = vi.kind === 'user_message'
        if (isUser !== runIsUser) {
            flushRun()
            runItems = [vi]
            runIsUser = isUser
        } else {
            runItems.push(vi)
        }
    }
    flushRun()

    return blocks
}
```

### `frontend/src/stores/data.js`

- Ajouter à `localState` : `sessionVisualBlocks: {}`, `sessionLineNumToBlockKey: {}`.
- Ajouter le getter `getSessionVisualBlocks: (state) => (sessionId) => state.localState.sessionVisualBlocks[sessionId] || []`.
- À la fin de `recomputeVisualItems`, après `this.localState.sessionVisualItems[sessionId] = stableItems` :
  ```js
  const blocks = groupVisualItemsIntoBlocks(stableItems, MAX_BLOCK_SIZE)
  this.localState.sessionVisualBlocks[sessionId] = blocks

  const lineNumToBlockKey = new Map()
  for (const block of blocks) {
      for (const item of block.items) {
          lineNumToBlockKey.set(item.lineNum, block.blockKey)
      }
  }
  this.localState.sessionLineNumToBlockKey[sessionId] = lineNumToBlockKey
  ```
- Dans le bloc qui supprime tout pour un sessionId (vers data.js:1267-1273), ajouter le delete de `sessionVisualBlocks` et `sessionLineNumToBlockKey`.
- Étendre `_onBufferDrain` pour patcher aussi `block.items[itemIdx]` après la mutation de `sessionVisualItems[idx]` (voir section "Cas particulier : streaming via `_onBufferDrain`").
- Importer `groupVisualItemsIntoBlocks` et `MAX_BLOCK_SIZE`.

### `frontend/src/components/SessionItemsList.vue`

- Remplacer `const visualItems = computed(...)` par `const visualBlocks = computed(() => store.getSessionVisualBlocks(props.sessionId))`. Garder `visualItems` aussi (toujours utilisé par `scrollToLineNum`, `commentedToolLineNums`, `blocksWithComments`, l'auto-scroll-on-new-items).
- `<VirtualScroller>` : changer `:items="visualItems"` → `:items="visualBlocks"` et `:item-key="item => item.lineNum"` → `:item-key="block => block.blockKey"`.
- Slot par défaut : passer du rendu d'un seul item à l'itération sur `block.items`. Conserver les trois branches (placeholder / group head / regular). Ajouter `:class="{ 'is-real-start': block.isRealStart && idx === 0, 'is-real-end': block.isRealEnd && idx === block.items.length - 1 }"` sur la racine de chaque branche.
- `onScrollerUpdate` : indices reçus = indices de blocs. Adapter :
  ```js
  function onScrollerUpdate({ startIndex, endIndex, visibleStartIndex, visibleEndIndex }) {
      const blocks = visualBlocks.value
      if (!blocks?.length) return
      const bufferedStart = Math.max(0, visibleStartIndex - BLOCK_LOAD_BUFFER)
      const bufferedEnd = Math.min(blocks.length - 1, visibleEndIndex + BLOCK_LOAD_BUFFER)
      const lineNumsToLoad = []
      for (let bIdx = bufferedStart; bIdx <= bufferedEnd; bIdx++) {
          for (const item of blocks[bIdx].items) {
              if (!hasContent(item)) lineNumsToLoad.push(item.lineNum)
          }
      }
      if (lineNumsToLoad.length > 0) {
          pendingLoadRange.value = { lineNums: lineNumsToLoad }
          debouncedLoad()
      }
  }
  ```
  Renommer/remplacer la constante `LOAD_BUFFER` par `BLOCK_LOAD_BUFFER = 1`.
- `scrollToLineNum` : après l'étape 4 actuelle (chargement du buffer de contenu), récupérer le `blockKey` via `store.localState.sessionLineNumToBlockKey[sessionId].get(lineNum)` et passer `blockKey` à `scroller.scrollToKey(...)` au lieu de `lineNum`. Une fois la promise résolue, **systématiquement** (sans check préalable) appeler `scrollerEl.querySelector('.session-item[data-line-num="${lineNum}"]')?.scrollIntoView({ block: 'center', behavior: 'instant' })` pour ramener l'item au centre dans le cas d'un sous-bloc plus grand que le viewport. Pour un item déjà au centre, c'est un no-op naturel — pas besoin de logique conditionnelle. **Ne pas réutiliser** `scrollToFirstHighlight` ici : ce helper cible `mark.search-highlight` *à l'intérieur* d'un item (problème orthogonal), il reste tel quel pour l'étape 5 historique du flow de recherche.
- L'auto-scroll-on-new-items watch (`watch(() => visualItems.value?.length, ...)`) reste sur `visualItems` plat ; pas de lien direct avec les blocs.
- Dans `handleRetry` (`SessionItemsList.vue:485-510`), où le composant fait `delete store.sessionItems[sId]` et `delete store.sessionVisualItems[sId]` avant de relancer un load, ajouter le cleanup symétrique de `store.localState.sessionVisualBlocks[sId]` et `store.localState.sessionLineNumToBlockKey[sId]`. Sans cela, on retomberait sur des données obsolètes le temps que `recomputeVisualItems` les régénère.

### `frontend/src/components/SessionItem.vue`

CSS adapté par l'utilisateur. Pas de modification JS/template par moi.

## Hors-scope

- Adaptation des règles CSS dans `SessionItem.vue`. L'utilisateur s'en charge.
- Pas de tests à écrire (cohérent avec le `CLAUDE.md` du projet : "no tests, no linting").
- Pas de lazy-virtualization à l'intérieur d'un sous-bloc (cap à 100 suffit pour l'écrasante majorité des sessions ; les 3 sessions extrêmes restent fonctionnelles avec un DOM plus chargé).
- Pas de modification du backend.
- Mode `debug` : les `system` se retrouvent dans les blocs et comptent vers le cap. Cas marginal, accepté tel quel.

## Risques et mitigations

- **Régression de la recherche (Ctrl+F + clic dans SearchOverlay).** Tester manuellement : ouvrir une session, lancer une recherche, cliquer sur un résultat → l'item doit s'afficher au centre du viewport, surligné.
- **Régression du scroll-to-bottom auto.** Tester : nouvelle session, l'assistant_turn produit des items en streaming → le scroll doit suivre.
- **Régression du toggle de groupes (mode simplified) et de detail blocks (mode conversation).** Tester : ouvrir/fermer plusieurs groupes successifs, le DOM ne doit pas sauter.
- **Sessions très longues (>1000 items dans un run réel).** Tester sur les 3 sessions identifiées en analyse statistique (si accessibles), sinon sur une session synthétique large.
- **Mode debug avec beaucoup de `system`.** Tester rapidement, pas de garantie de fluidité comparable mais doit rester fonctionnel.
- **Streaming text en assistant_turn (corrigé).** Tester explicitement : démarrer une session, observer le streaming d'une réponse longue → le texte doit progresser dans le DOM en temps réel. C'est le scénario du bug que la mutation `_onBufferDrain` ciblée doit éviter.
- **Lazy loading en mode conversation (avec `BLOCK_LOAD_BUFFER = 1`).** En mode conversation, les blocs sont souvent petits (1 user_message + 1 assistant_message kept). Le buffer de chargement de 1 bloc voisin charge donc moins de contenu spéculativement qu'avec l'ancien `LOAD_BUFFER = 50` items. Vérifier qu'on ne voit pas plus de placeholders pendant le scroll qu'avant. Si gênant, on pourra remonter à 2-3 blocs voisins.

## Ordre d'implémentation suggéré

1. Ajouter `MAX_BLOCK_SIZE` dans `constants.js`.
2. Ajouter `groupVisualItemsIntoBlocks` dans `visualItems.js`.
3. Ajouter `sessionVisualBlocks`, `sessionLineNumToBlockKey`, `getSessionVisualBlocks` au store, et le calcul dans `recomputeVisualItems`. Vérifier que `recomputeVisualItems` est appelé partout où il faut (déjà OK aujourd'hui — pas de nouvelle exigence).
4. Modifier `SessionItemsList.vue` : `:items`, `:item-key`, slot, `onScrollerUpdate`, `scrollToLineNum`. Pose des classes `is-real-start`/`is-real-end`.
5. Vérifier rendu visuel : à ce stade, le CSS de `SessionItem.vue` cible toujours les anciens sélecteurs. L'utilisateur adapte le CSS ensuite.
6. Tests manuels : voir section Risques.

Cet ordre permet d'avancer en plusieurs commits intermédiaires si souhaité (ex : 1+2+3 dans un commit, 4+5 dans un autre).
