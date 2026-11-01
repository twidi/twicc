# Spec: Custom Virtual Scroller

**STATUS: COMPLETED**

---

## Contexte et Besoin

### Problème actuel
Le projet utilise `vue-virtual-scroller` (DynamicScroller) pour afficher les items de session. Cette librairie pose plusieurs problèmes :
- Calculs de hauteurs parfois incorrects
- Nécessité de hacks (ex: `sortViews()` pour l'ordre DOM, retries pour `scrollToBottom`)
- Comportement imprévisible difficile à débugger

### Objectif
Créer un virtual scroller **sur mesure**, adapté à nos besoins spécifiques, plus simple et plus fiable.

### Besoins spécifiques
1. **Chargement initial partiel** : Seulement les N premiers et N derniers items
2. **Lazy loading** : Charger les items manquants quand on scrolle vers eux
3. **Déchargement** : Ne garder qu'un nombre limité d'items rendus (pas de recyclage complexe)
4. **Hauteurs variables** : Items de taille différente, pouvant changer après rendu (expand/collapse)
5. **Découplage** : Le scroller doit être réutilisable, agnostique des données de session

### Addendums de référence
- **[Addendum 1](./virtual-scroller-addendum-1.md)** : Analyse complète du code existant (vue-virtual-scroller, visualItems, store, API)
- **[Addendum 2](./virtual-scroller-addendum-2.md)** : Recherche des patterns et meilleures pratiques pour virtual scrolling

---

## Décisions d'architecture

### Structure DOM à 3 zones

```
┌─────────────────────────────────────────┐
│ Container (overflow-y: auto)            │
│ ┌─────────────────────────────────────┐ │
│ │ Spacer AVANT (div avec height)      │ │
│ │ hauteur = Σ(items non-rendus avant) │ │
│ └─────────────────────────────────────┘ │
│ ┌─────────────────────────────────────┐ │
│ │ Items RENDUS                        │ │
│ │ (plage contiguë : startIdx→endIdx)  │ │
│ └─────────────────────────────────────┘ │
│ ┌─────────────────────────────────────┐ │
│ │ Spacer APRÈS (div avec height)      │ │
│ │ hauteur = Σ(items non-rendus après) │ │
│ └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

- Plage **toujours contiguë** d'items rendus (pas de "trous")
- Les spacers donnent l'illusion d'une liste complète pour la scrollbar

### Détection par calcul mathématique (pas IntersectionObserver)

**Choix** : Scroll event + calcul direct plutôt que IntersectionObserver.

**Raison** : L'IntersectionObserver dit "visible/pas visible" mais pas "de combien". Si on continue à scroller dans une zone vide, il n'émet plus rien.

**Algorithme** :
1. À chaque scroll event (throttlé via `requestAnimationFrame`)
2. Calculer la zone à couvrir : `[scrollTop - buffer, scrollTop + viewportHeight + buffer]`
3. Binary search pour trouver les indices correspondants
4. Comparer avec les items actuellement rendus
5. Ajuster (rendre plus, ou décharger)

### Buffers asymétriques

| Zone | Distance du viewport | Action |
|------|---------------------|--------|
| Zone de chargement | ± 500px | Commencer à rendre les items |
| Zone de déchargement | ± 1000px | Arrêter de rendre (évite le thrashing) |

```
         ↑ scroll vers le haut
    ─────────────────────────
    │  Zone déchargement    │  -1000px
    │  ┌─────────────────┐  │
    │  │ Zone chargement │  │  -500px
    │  │  ┌───────────┐  │  │
    │  │  │ VIEWPORT  │  │  │  0
    │  │  └───────────┘  │  │
    │  │                 │  │  +500px
    │  └─────────────────┘  │
    │                       │  +1000px
    ─────────────────────────
         ↓ scroll vers le bas
```

### Gestion des hauteurs

- **Hauteur minimum** : 24px (constante existante `MIN_ITEM_SIZE`)
- **Cache des hauteurs** : `Map<itemKey, number>` - hauteur mesurée par ResizeObserver
- Items jamais mesurés → hauteur minimum
- Items mesurés → hauteur réelle du cache

### ResizeObserver unique

Un seul `ResizeObserver` global pour tous les items rendus (meilleure performance).
- Observer quand un item est rendu
- Unobserver quand un item est déchargé
- Callback met à jour le cache des hauteurs

### Correction du scroll position

Quand la hauteur d'un item change (mesure initiale ou expand/collapse) :

```javascript
if (itemPosition < scrollTop && oldHeight !== undefined) {
  const delta = newHeight - oldHeight
  container.scrollTop += delta
}
```

Si l'item est **au-dessus** du viewport, on ajuste le scrollTop pour maintenir la position visuelle.

### Découplage du scroller

Le scroller est **agnostique** :
- Il reçoit un tableau `items` et une fonction `itemKey`
- Il émet un event `@update` avec les indices visibles
- Le **parent** décide quoi faire (fetch API, etc.)
- Le contenu est rendu via un **slot**

---

## Interface du composant

### Props

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `items` | `Array` | required | Tableau des items à afficher |
| `itemKey` | `Function` | required | `(item) => uniqueKey` - extrait la clé unique |
| `minItemHeight` | `Number` | `24` | Hauteur minimum/estimée des items |
| `buffer` | `Number` | `500` | Buffer en pixels pour préchargement |
| `unloadBuffer` | `Number` | `1000` | Buffer en pixels avant déchargement |

### Events

| Event | Payload | Description |
|-------|---------|-------------|
| `@update` | `{ startIndex, endIndex, visibleStartIndex, visibleEndIndex }` | Émis quand la plage visible change |
| `@item-resized` | `{ key, height, oldHeight }` | Émis quand un item change de taille |

### Slots

```vue
<VirtualScroller :items="items" :item-key="item => item.id">
  <!-- Slot par défaut : rendu de chaque item -->
  <template #default="{ item, index }">
    <MyItemComponent :data="item" />
  </template>
</VirtualScroller>
```

### Méthodes exposées (via ref)

| Méthode | Signature | Description |
|---------|-----------|-------------|
| `scrollToIndex` | `(index, { align?: 'start' \| 'center' \| 'end' })` | Scroll vers un index |
| `scrollToTop` | `()` | Scroll tout en haut |
| `scrollToBottom` | `()` | Scroll tout en bas |
| `getScrollState` | `()` | Retourne `{ scrollTop, scrollHeight, clientHeight }` |

### Exemple d'utilisation

```vue
<template>
  <VirtualScroller
    ref="scrollerRef"
    :items="visualItems"
    :item-key="item => item.lineNum"
    :min-item-height="24"
    :buffer="500"
    :unload-buffer="1000"
    @update="onScrollerUpdate"
    @item-resized="onItemResized"
  >
    <template #default="{ item }">
      <SessionItem
        v-if="item.content"
        :content="item.content"
      />
      <div v-else class="placeholder" style="height: 24px" />
    </template>
  </VirtualScroller>
</template>

<script setup>
const scrollerRef = ref(null)

function onScrollerUpdate({ startIndex, endIndex }) {
  // Le parent regarde dans visualItems[startIndex..endIndex]
  // quels items ont content === null
  // et lance les appels API pour les ranges manquants
}
</script>
```

---

## Mapping des indices

**Important** : Les indices du VirtualScroller sont ses indices internes (0 à n-1 dans le tableau `items`).

Le parent (SessionItemsList) fait le mapping :
1. Reçoit `startIndex, endIndex` du scroller
2. Regarde `visualItems[startIndex]` à `visualItems[endIndex]`
3. Pour chaque item sans `content`, note son `lineNum`
4. Convertit en ranges avec `lineNumsToRanges()`
5. Appelle l'API avec ces ranges
6. Le store met à jour → réactivité → scroller re-render

---

## Tâches d'implémentation

### Task 1: Composable `useVirtualScroll`

**Fichier** : `frontend/src/composables/useVirtualScroll.js`

**Responsabilités** :
- Cache des hauteurs (`Map<key, height>`)
- Calcul des positions cumulées (computed)
- Binary search pour trouver les indices visibles
- Gestion du scroll event (RAF-throttled)
- Calcul de la plage à rendre (avec buffers)
- Correction du scrollTop quand les hauteurs changent

**Idée de structure** :

```javascript
export function useVirtualScroll(options) {
  const {
    items,              // Ref<Array>
    itemKey,            // (item) => key
    minItemHeight,      // number
    buffer,             // number (px)
    unloadBuffer,       // number (px)
    containerRef,       // Ref<HTMLElement>
  } = options

  // State interne
  const heightCache = reactive(new Map())  // key → height
  const scrollTop = ref(0)
  const viewportHeight = ref(0)

  // Computed : positions cumulées
  const positions = computed(() => {
    let top = 0
    return items.value.map((item, index) => {
      const key = itemKey(item)
      const height = heightCache.get(key) ?? minItemHeight
      const pos = { index, key, top, height }
      top += height
      return pos
    })
  })

  const totalHeight = computed(() => /* ... */)

  // Binary search
  function findIndexAtPosition(targetTop) { /* ... */ }

  // Computed : plage à rendre
  const renderRange = computed(() => {
    const start = findIndexAtPosition(scrollTop.value - buffer)
    const end = findIndexAtPosition(scrollTop.value + viewportHeight.value + buffer)
    return { start: Math.max(0, start), end: Math.min(items.value.length, end + 1) }
  })

  // Spacers
  const spacerBeforeHeight = computed(() => /* sum of heights before renderRange.start */)
  const spacerAfterHeight = computed(() => /* sum of heights after renderRange.end */)

  // Mesure d'un item
  function updateItemHeight(key, newHeight) {
    const oldHeight = heightCache.get(key)
    if (oldHeight === newHeight) return

    heightCache.set(key, newHeight)

    // Correction du scroll si nécessaire
    const pos = positions.value.find(p => p.key === key)
    if (pos && pos.top < scrollTop.value && oldHeight !== undefined) {
      const delta = newHeight - oldHeight
      containerRef.value.scrollTop += delta
    }
  }

  // Scroll handler (RAF-throttled)
  function handleScroll(e) { /* ... */ }

  // Navigation
  function scrollToIndex(index, options) { /* ... */ }
  function scrollToTop() { /* ... */ }
  function scrollToBottom() { /* ... */ }

  return {
    positions,
    totalHeight,
    renderRange,
    spacerBeforeHeight,
    spacerAfterHeight,
    updateItemHeight,
    handleScroll,
    scrollToIndex,
    scrollToTop,
    scrollToBottom,
  }
}
```

---

### Task 2: Composant `VirtualScroller.vue`

**Fichier** : `frontend/src/components/VirtualScroller.vue`

**Responsabilités** :
- Template avec spacer-before, items rendus, spacer-after
- Utilise le composable `useVirtualScroll`
- ResizeObserver unique pour le container (viewport height)
- Émet les events `@update` et `@item-resized`
- Expose les méthodes de navigation via `defineExpose`

**Idée de structure** :

```vue
<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useVirtualScroll } from '@/composables/useVirtualScroll'

const props = defineProps({
  items: { type: Array, required: true },
  itemKey: { type: Function, required: true },
  minItemHeight: { type: Number, default: 24 },
  buffer: { type: Number, default: 500 },
  unloadBuffer: { type: Number, default: 1000 },
})

const emit = defineEmits(['update', 'item-resized'])

const containerRef = ref(null)

const {
  totalHeight,
  renderRange,
  spacerBeforeHeight,
  spacerAfterHeight,
  updateItemHeight,
  handleScroll,
  scrollToIndex,
  scrollToTop,
  scrollToBottom,
} = useVirtualScroll({
  items: toRef(props, 'items'),
  itemKey: props.itemKey,
  minItemHeight: props.minItemHeight,
  buffer: props.buffer,
  unloadBuffer: props.unloadBuffer,
  containerRef,
})

// Items à rendre
const renderedItems = computed(() => {
  const { start, end } = renderRange.value
  return props.items.slice(start, end).map((item, i) => ({
    item,
    index: start + i,
    key: props.itemKey(item),
  }))
})

// Émettre @update quand la plage change
watch(renderRange, (range) => {
  emit('update', {
    startIndex: range.start,
    endIndex: range.end,
    // TODO: calculer visibleStartIndex/visibleEndIndex (sans buffer)
  })
}, { immediate: true })

// Callback pour VirtualScrollerItem
function onItemResized(key, height, oldHeight) {
  updateItemHeight(key, height)
  emit('item-resized', { key, height, oldHeight })
}

// ResizeObserver pour le container
let containerObserver = null
onMounted(() => {
  containerObserver = new ResizeObserver(/* update viewportHeight */)
  containerObserver.observe(containerRef.value)
})
onUnmounted(() => {
  containerObserver?.disconnect()
})

defineExpose({
  scrollToIndex,
  scrollToTop,
  scrollToBottom,
  getScrollState: () => ({ /* ... */ }),
})
</script>

<template>
  <div
    ref="containerRef"
    class="virtual-scroller"
    @scroll.passive="handleScroll"
  >
    <!-- Spacer avant -->
    <div :style="{ height: spacerBeforeHeight + 'px' }" />

    <!-- Items rendus -->
    <VirtualScrollerItem
      v-for="{ item, index, key } in renderedItems"
      :key="key"
      :item-key="key"
      @resized="(height, oldHeight) => onItemResized(key, height, oldHeight)"
    >
      <slot :item="item" :index="index" />
    </VirtualScrollerItem>

    <!-- Spacer après -->
    <div :style="{ height: spacerAfterHeight + 'px' }" />
  </div>
</template>

<style scoped>
.virtual-scroller {
  overflow-y: auto;
  overflow-anchor: none; /* On gère manuellement */
}
</style>
```

---

### Task 3: Composant `VirtualScrollerItem.vue`

**Fichier** : `frontend/src/components/VirtualScrollerItem.vue`

**Responsabilités** :
- Wrapper pour chaque item rendu
- ResizeObserver sur l'élément (utilise un observer partagé pour la perf)
- Émet `@resized` quand la taille change

**Idée de structure** :

```vue
<script setup>
import { ref, onMounted, onUnmounted, inject } from 'vue'

const props = defineProps({
  itemKey: { type: [String, Number], required: true },
})

const emit = defineEmits(['resized'])

const itemRef = ref(null)
let currentHeight = null

// On pourrait injecter un observer partagé depuis VirtualScroller
// Pour simplifier, on utilise un observer local au début

let observer = null

onMounted(() => {
  observer = new ResizeObserver((entries) => {
    const newHeight = entries[0].contentRect.height
    if (newHeight !== currentHeight) {
      const oldHeight = currentHeight
      currentHeight = newHeight
      emit('resized', newHeight, oldHeight)
    }
  })
  observer.observe(itemRef.value)
})

onUnmounted(() => {
  observer?.disconnect()
})
</script>

<template>
  <div ref="itemRef" class="virtual-scroller-item">
    <slot />
  </div>
</template>

<style scoped>
.virtual-scroller-item {
  /* Permettre au contenu de définir sa hauteur */
}
</style>
```

**Optimisation potentielle** : Utiliser un ResizeObserver partagé (injecté via `provide/inject`) au lieu d'un observer par item. À évaluer selon les performances.

---

### Task 4: Intégration dans `SessionItemsList.vue`

**Fichier** : `frontend/src/components/SessionItemsList.vue`

**Modifications** :
1. Remplacer `<DynamicScroller>` par `<VirtualScroller>`
2. Supprimer les imports de `vue-virtual-scroller`
3. Adapter `onScrollerUpdate` pour la nouvelle signature
4. Supprimer le hack `sortScrollerViews()` (plus nécessaire)
5. Adapter les appels `scrollToBottom()` / `scrollToIndex()`

**Points d'attention** :
- Les `size-dependencies` de DynamicScrollerItem disparaissent → le ResizeObserver les détecte automatiquement
- Le `key-field` devient la prop `itemKey`
- La logique de lazy loading reste identique (dans le parent)

---

## Task Tracking

- [x] **Task 1**: Composable `useVirtualScroll`
- [x] **Task 2**: Composant `VirtualScroller.vue`
- [x] **Task 3**: Composant `VirtualScrollerItem.vue`
- [x] **Task 4**: Intégration dans `SessionItemsList.vue`
- [x] **Task 5**: Fix Issue 1 - Multiple tabs on same session cause rendering problems

---

## Decisions made during implementation

### Task 1: useVirtualScroll composable

1. **Height cache uses reactive Map**: Using `reactive(new Map())` allows Vue to track mutations to the Map while keeping the Map API intact.

2. **Binary search returns first item whose bottom edge is at or past target**: This ensures we always include items that are partially visible at the top of the viewport.

3. **Programmatic scroll flag**: Added `isProgrammaticScroll` flag to prevent scroll correction during programmatic navigation (scrollToIndex, scrollToTop, scrollToBottom). This avoids unwanted adjustments when the user explicitly navigates.

4. **Smooth scroll timeout**: When using `behavior: 'smooth'`, we use a 500ms timeout to reset the programmatic scroll flag. This is an approximation; a more precise solution would listen for scroll end events.

5. **Automatic height cache cleanup**: Added a watcher on the items array that cleans up height cache entries for removed items while preserving heights for items that still exist. This prevents memory leaks for long-running sessions.

6. **Exposed visibleRange separately from renderRange**: The `visibleRange` computed provides indices of items actually visible (without buffer), useful for the `@update` event payload. The `renderRange` includes the buffer for preloading.

7. **Additional utility methods**: Added `isAtBottom()`, `isAtTop()`, `syncScrollPosition()`, and `getItemHeight()` methods that weren't in the original spec but are useful for the component implementation and edge cases.

8. **Default values as module constants**: Defined `DEFAULT_MIN_ITEM_HEIGHT`, `DEFAULT_BUFFER`, and `DEFAULT_UNLOAD_BUFFER` as module-level constants for clarity and easy reference.

9. **Hysteresis for asymmetric buffers (unloadBuffer implementation)**: The `renderRange` implements true hysteresis using both `buffer` (500px) and `unloadBuffer` (1000px). The algorithm tracks the previous render range and applies different thresholds:
   - **To ENTER the range**: Items must be within `buffer` (500px) of the viewport
   - **To LEAVE the range**: Items must be outside `unloadBuffer` (1000px) of the viewport

   This prevents thrashing (constant load/unload cycles) when scrolling near buffer boundaries. For example, an item at 600px from the viewport:
   - Won't be loaded initially (outside 500px load buffer)
   - Won't be unloaded if already loaded (inside 1000px unload buffer)

   **Implementation pattern**: The `renderRange` is a ref updated by `watchEffect` rather than a computed property. This is the proper Vue pattern because the hysteresis logic requires tracking state (`previousRange`) between computations. Using `watchEffect`:
   - Automatically tracks all reactive dependencies (scrollTop, viewportHeight, positions)
   - Makes the side effect (updating previousRange) explicit and expected
   - Keeps the ref reactive for consumers
   - Avoids the anti-pattern of mutating state inside computed properties

   For the start index: if the previous range started higher (smaller index), we keep items that are still within the unload buffer; otherwise we use the load buffer. The same logic applies symmetrically for the end index.

### Task 2: VirtualScroller.vue component

1. **Minimal placeholder VirtualScrollerItem created**: Since VirtualScroller depends on VirtualScrollerItem for rendering, a basic working implementation was created. This placeholder uses individual ResizeObservers per item (Task 3 will optimize this with shared observers).

2. **Watch both renderRange and visibleRange for @update emission**: The `@update` event is emitted whenever either the render range or visible range changes, ensuring the parent always has accurate indices. The payload includes both buffer-extended indices (`startIndex`, `endIndex`) and actual visible indices (`visibleStartIndex`, `visibleEndIndex`).

3. **Container ResizeObserver uses contentBoxSize with fallback**: For viewport height tracking, we prefer `contentBoxSize[0].blockSize` for accuracy but fall back to `contentRect.height` for older browser compatibility.

4. **Initial syncScrollPosition on mount**: After mounting, we call `syncScrollPosition()` to ensure the composable has accurate initial scroll position and viewport height before any rendering decisions.

5. **Additional utility methods exposed**: Beyond the spec requirements (`scrollToIndex`, `scrollToTop`, `scrollToBottom`, `getScrollState`), we also expose `isAtBottom` and `isAtTop` from the composable as they may be useful for parent components.

6. **Passive scroll listener**: The scroll handler is attached with `.passive` modifier for better scroll performance, as we don't need to prevent default behavior.

7. **overflow-anchor: none in CSS**: Explicitly disabled browser's native scroll anchoring since we manage scroll position manually when item heights change above the viewport.

8. **VirtualScrollerItem uses borderBoxSize for height measurement**: Using `borderBoxSize` instead of `contentRect` ensures padding and borders are included in the height measurement, providing more accurate sizing for the height cache.

9. **VirtualScrollerItem uses display: flow-root**: This creates a Block Formatting Context so that child margins don't collapse through, ensuring accurate height measurement (same pattern used in existing SessionItemsList item-wrapper).

10. **Non-reactive props documented**: The props `itemKey`, `minItemHeight`, `buffer`, and `unloadBuffer` are passed to the composable as plain values (not refs) because the composable reads them once during initialization. Rather than refactoring the composable to accept refs, we document clearly that these props are "captured at mount time" and changes after mount are not observed. This is a pragmatic choice since these values typically don't change during component lifetime.

11. **Container requires explicit parent height**: The container uses `height: 100%` which requires the parent element to have a defined height. Added a comment in the component's JSDoc header and in the CSS explaining this requirement.

12. **Deferred initial @update emission**: The watch with `immediate: true` was causing incorrect initial ranges to be emitted (with viewportHeight=0). Fixed by introducing a `hasMounted` flag and deferring the first emission until `nextTick` after mount, when the container has been measured. The initial emission is then triggered manually to ensure accurate ranges.

13. **Container uses flex layout for spacers**: Changed the container to use `display: flex; flex-direction: column;` so that `flex-shrink: 0` on spacers has actual effect. This also provides proper layout behavior for the virtual scroll structure.

14. **Added CSS `contain: layout style`**: Added the `contain` CSS property to the container for rendering performance optimization. We use `layout style` (not `size`) because the container needs to respond to parent sizing.

15. **Defensive null check on items**: Added `if (!props.items?.length) return []` in the `renderedItems` computed to handle cases where items may be momentarily null/undefined during data loading.

16. **ResizeObserver uses entries[0] directly**: Since we observe a single element (the container), the ResizeObserver callback now uses `const entry = entries[0]` directly instead of looping through entries, which is cleaner and more accurate to the actual use case.

17. **Container has position: relative**: Added `position: relative` to the container CSS to establish a positioning context for any absolute positioning needs of children.

18. **Client-only component documented**: Added a note in the component JSDoc header that SSR is not supported due to ResizeObserver and DOM measurement requirements. The `containerObserver` being a plain `let` variable (not a ref) is fine since this is client-only code.

### Task 3: VirtualScrollerItem.vue component

1. **Shared ResizeObserver via provide/inject**: Instead of creating a ResizeObserver per item (which would be inefficient with many items), VirtualScroller now provides a shared observer through Vue's provide/inject mechanism. VirtualScrollerItem exports a `RESIZE_OBSERVER_KEY` Symbol that VirtualScroller imports and uses with `provide()`. This pattern is dramatically more performant - one callback per frame vs N callbacks per frame. See: https://github.com/WICG/resize-observer/issues/59

2. **Lazy observer creation**: The shared ResizeObserver is created lazily via `getItemObserver()` function, only when the first item registers. This ensures the observer is never created during SSR (where ResizeObserver doesn't exist).

3. **Defensive ResizeObserver check**: Added `if (typeof ResizeObserver === 'undefined') return null` check in the observer creation function to handle environments without ResizeObserver support gracefully.

4. **box: 'border-box' option**: The shared observer uses `observer.observe(element, { box: 'border-box' })` for explicit behavior when measuring item heights.

5. **currentHeight initial value is undefined**: Changed from `null` to `undefined` per spec, indicating "no measurement yet" rather than "null height".

6. **flex-shrink: 0 on item wrapper**: Since the parent VirtualScroller uses flex layout, items need `flex-shrink: 0` to prevent them from being compressed.

7. **itemKey used as data-attribute**: The `itemKey` prop is now used as a `data-item-key` attribute on the wrapper div for debugging purposes (inspecting which item is rendered).

8. **Warning when used outside VirtualScroller**: If `inject(RESIZE_OBSERVER_KEY)` returns null (component used outside a VirtualScroller), a console warning is emitted to help developers identify the misconfiguration.

9. **SSR note in documentation**: Added the same SSR note as the parent component to clarify that this component requires browser APIs.

10. **Cleanup in VirtualScroller onUnmounted**: VirtualScroller now calls `itemObserver.disconnect()` and `itemCallbacks.clear()` in its `onUnmounted` hook to properly clean up the shared observer resources.

### Task 4: Integration into SessionItemsList.vue

1. **Removed vue-virtual-scroller entirely**: Removed imports for `DynamicScroller`, `DynamicScrollerItem`, and the CSS file. The `vue-virtual-scroller` package can now be removed from `package.json` if no longer needed elsewhere.

2. **Removed sortScrollerViews hack**: The `sortScrollerViews()` function, its throttled version, and the scroll event listener setup/cleanup in `onMounted`/`onBeforeUnmount` were all removed. Our scroller doesn't recycle DOM nodes, so the DOM order always matches the visual order naturally.

3. **Removed useThrottleFn import**: Since the throttled sort function is no longer needed, the `useThrottleFn` import from `@vueuse/core` was removed. Only `useDebounceFn` is still used for the lazy loading debounce.

4. **Removed onMounted/onBeforeUnmount**: These lifecycle hooks were only used for the scroll listener setup (for DOM sorting). Since that's no longer needed, the lifecycle hook imports and handlers were removed.

5. **Adapted onScrollerUpdate signature**: Changed from four positional parameters `(startIndex, endIndex, visibleStartIndex, visibleEndIndex)` to a single destructured object parameter `({ startIndex, endIndex, visibleStartIndex, visibleEndIndex })` to match our scroller's `@update` event payload format.

6. **Adapted scrollToBottomUntilStable**: Now uses `scroller.scrollToBottom({ behavior: 'auto' })` instead of `scroller.scrollToBottom()` and `scroller.getScrollState()` instead of directly accessing the `$el` DOM element. This uses our scroller's exposed API.

7. **Adapted executePendingLoad wasAtBottom check**: Now uses `scroller?.isAtBottom?.()` method instead of manual scroll position calculation. Falls back to `false` if scroller or method is not available.

8. **Removed .item-wrapper CSS class**: The `display: flow-root` styling is now handled by `VirtualScrollerItem.vue`'s CSS, so the `.item-wrapper` class in SessionItemsList was removed.

9. **Simplified template**: Removed the `<DynamicScrollerItem>` wrapper since our `VirtualScroller` handles item wrapping internally via `VirtualScrollerItem`. The slot template now only receives `{ item, index }` (removed `active` which was DynamicScroller-specific).

10. **Added unload-buffer prop**: Added `:unload-buffer="1000"` to configure the hysteresis buffer that prevents thrashing when scrolling near buffer boundaries.

11. **Changed key-field to item-key**: Replaced `key-field="lineNum"` (string-based field name) with `:item-key="item => item.lineNum"` (function-based key extraction) to match our scroller's API.

12. **Retained scrollToBottomUntilStable retries**: Kept the retry mechanism (10 attempts, 50ms delay) as it may still be useful for edge cases where item heights change after initial render (e.g., images loading, content expansion). Our scroller handles scroll position correction for height changes above the viewport, but this provides additional robustness for the scroll-to-bottom use case.

### Task 5: Fix Issue 1 - Multi-tab rendering

1. **Root cause identified: ResizeObserver + display:none interaction**: The issue was not about shared state between VirtualScroller instances. Each instance has properly isolated state. The problem occurs because `wa-tab-panel` uses `display: none` for inactive tabs, causing all child elements to report 0 height to ResizeObserver. These 0-height values were being cached, corrupting the height cache.

2. **Three-layer defense against zero-height corruption**:
   - **Layer 1 (VirtualScrollerItem.vue)**: Ignore ResizeObserver callbacks reporting 0 height. These measurements are invalid (element is hidden) and should not be emitted to the parent.
   - **Layer 2 (useVirtualScroll.js)**: Added `updateItemHeight()` safeguard to reject 0 height values. Also added `invalidateZeroHeights()` function to clear corrupted cache entries.
   - **Layer 3 (VirtualScroller.vue)**: Track viewport height transitions. When container goes from 0 (hidden) to positive (visible), call `invalidateZeroHeights()` to clear any corrupted entries and allow re-measurement.

3. **lastKnownViewportHeight tracking**: Added a component-level variable to track the previous viewport height. This enables detection of "visibility recovery" events (0 -> positive height transition) which indicate the scroller has become visible after being hidden.

4. **No changes to instance isolation**: The original investigation areas (provide/inject, module-level variables, keep-alive) were all confirmed to be correctly implemented. Each VirtualScroller instance has its own composable state, its own itemObserver, and its own itemCallbacks Map.

### Build fixes

1. **Relative imports instead of @ alias**: Changed `@/composables/useVirtualScroll` to `../composables/useVirtualScroll` in VirtualScroller.vue. This project uses relative imports throughout, not the `@` alias pattern.

2. **Moved RESIZE_OBSERVER_KEY to separate file**: The `RESIZE_OBSERVER_KEY` Symbol was initially defined and exported directly in `VirtualScrollerItem.vue`'s `<script setup>`. However, ES module exports are not allowed in `<script setup>` blocks. Fixed by moving the Symbol to a dedicated file `frontend/src/components/virtualScrollerKeys.js` and importing it in both VirtualScroller.vue and VirtualScrollerItem.vue.

---

## Build validation

- Build passes successfully with `npm run build`
- No errors
- One expected warning about chunk sizes (Shiki syntax highlighting library) - this is pre-existing and unrelated to the virtual scroller implementation

---

## Notes additionnelles

### Ce qu'on ne fait PAS
- Recyclage des composants (trop complexe, gains marginaux pour notre cas)
- Support du scroll horizontal
- Virtualisation imbriquée (scrollers dans scrollers)

### Métriques de succès
- Scroll fluide avec 1000+ items
- Pas de "sauts" de scroll lors des expand/collapse
- Lazy loading fonctionne correctement
- Code plus simple et maintenable que vue-virtual-scroller

---

## Known Issues

### Issue 1: Multiple tabs on same session cause rendering problems

**Status:** FIXED

**Description:**
When viewing the same session in multiple tabs (main session view + one or more subagent tabs), the virtual scrollers interfere with each other causing rendering issues. Each tab should have its own completely independent VirtualScroller instance with isolated state (scroll position, height cache, render range, etc.).

**Symptoms:**
- Rendering becomes inconsistent when switching between tabs of the same session
- Virtual scroller state appears to be shared or corrupted between tabs

**Expected behavior:**
Each tab's VirtualScroller should be fully independent - scrolling in one tab should not affect another tab's scroll position, rendered items, or height cache.

**Root cause:**
The issue was NOT about shared state between instances. Each VirtualScroller instance correctly has its own isolated state. The problem was with **ResizeObserver behavior when elements have `display: none`**.

When a `wa-tab-panel` becomes inactive, it gets `display: none`. This causes:
1. All child elements report 0 computed dimensions
2. ResizeObserver on items reports 0 height, which gets cached
3. ResizeObserver on container reports 0 viewport height
4. With `viewportHeight = 0`, the render range calculation produces minimal ranges
5. When the tab becomes active again, the items have incorrect 0px heights cached

**Fix implemented:**
Three-layer defense against zero-height corruption:

1. **VirtualScrollerItem.vue**: Ignore ResizeObserver callbacks that report 0 height - these occur when the item is in a hidden parent and should not be cached.

2. **useVirtualScroll.js**: Added `invalidateZeroHeights()` function that removes all 0-height entries from the cache. Also added a safeguard in `updateItemHeight()` to reject 0 height values.

3. **VirtualScroller.vue**: Track viewport height transitions. When container goes from 0 (hidden) to positive (visible), call `invalidateZeroHeights()` to clear any corrupted cache entries, forcing items to be re-measured with valid heights.
