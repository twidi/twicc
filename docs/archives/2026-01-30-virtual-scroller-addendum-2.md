# Virtual Scroller - Addendum 2: Recherche des patterns et meilleures pratiques

Ce document contient la recherche complète sur les patterns de virtual scrolling réalisée par un agent de recherche.

---

# Virtual Scrolling en Vue 3 : Patterns et Meilleures Pratiques

Voici une synthese structuree des patterns et techniques pour implementer un virtual scroller custom en Vue 3.

---

## 1. Patterns pour Items de Hauteur Variable

### Calcul de la Position Totale

La strategie recommandee utilise un **cache de hauteurs** avec calcul incrementiel :

```javascript
// Chaque item stocke sa position = position precedente + hauteur precedente
function calculatePositions(items, heightCache) {
  let position = 0;
  return items.map(item => {
    const itemPosition = position;
    position += heightCache.get(item.id) ?? estimatedHeight;
    return { ...item, top: itemPosition };
  });
}

// Hauteur totale = position du dernier + sa hauteur
const totalHeight = positions[positions.length - 1].top + positions[positions.length - 1].height;
```

### Gestion des Hauteurs Inconnues

Deux approches principales selon [DEV Community](https://dev.to/georgii/virtual-scrolling-of-content-with-variable-height-with-angular-3a52) :

**A. Estimation basee sur le contenu (recommande)**

```javascript
function predictHeight(item) {
    const baseHeight = 48; // padding, metadata
    const textRows = Math.ceil(item.content.length / CHARS_PER_ROW);
    return baseHeight + (textRows * DEFAULT_ROW_HEIGHT);
}
```

**B. Mesure apres rendu (two-phase rendering)**
1. Rendu initial avec hauteurs estimees
2. Mesure du DOM reel
3. Mise a jour du cache et correction des positions

### Eviter les "Sauts" de Scroll

Selon [MDN - CSS Scroll Anchoring](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Scroll_anchoring), le navigateur a une feature native `overflow-anchor` :

```css
/* Le navigateur ajuste automatiquement le scroll quand le contenu change */
.virtual-scroller {
  overflow-anchor: auto; /* defaut, active l'ancrage */
}

/* Pour desactiver si vous gerez manuellement */
.virtual-scroller {
  overflow-anchor: none;
}
```

Correction manuelle quand les hauteurs changent :
```javascript
function onHeightChange(itemId, oldHeight, newHeight) {
  const item = items.find(i => i.id === itemId);
  const delta = newHeight - oldHeight;

  // Si l'item est au-dessus du viewport, ajuster le scroll
  if (item.top < scrollTop) {
    scrollContainer.scrollTop += delta;
  }

  // Recalculer les positions des items suivants
  recalculatePositionsFrom(itemId);
}
```

---

## 2. IntersectionObserver pour Virtual Scrolling

### Est-ce la Bonne Approche ?

**Pour le chargement infini : OUI**. Selon [ITNEXT](https://itnext.io/1v1-scroll-listener-vs-intersection-observers-469a26ab9eb6), IntersectionObserver utilise **37.6%** du temps de scripting vs **63%** pour un scroll listener optimise (sur CPU throttle 6x).

**Pour determiner les items visibles : NON recommande**. Le calcul mathematique direct est plus efficace :

```javascript
// Approche recommandee : calcul direct
function getVisibleRange(scrollTop, viewportHeight, positions) {
  const startIndex = binarySearch(positions, scrollTop);
  let endIndex = startIndex;

  while (endIndex < positions.length &&
         positions[endIndex].top < scrollTop + viewportHeight) {
    endIndex++;
  }

  return { startIndex, endIndex };
}
```

### Pattern Sentinel pour le Chargement

```javascript
// Vue 3 Composition API
function useInfiniteScroll(loadMore) {
  const sentinelRef = ref(null);

  onMounted(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          loadMore();
        }
      },
      {
        rootMargin: '100px', // Pre-charge 100px avant
        threshold: 0
      }
    );

    if (sentinelRef.value) {
      observer.observe(sentinelRef.value);
    }

    onUnmounted(() => observer.disconnect());
  });

  return { sentinelRef };
}
```

---

## 3. ResizeObserver sur de Nombreux Elements

### Performance : Un Seul Observer

Selon [ce GitHub issue](https://github.com/WICG/resize-observer/issues/59), utiliser **un seul ResizeObserver** est dramatiquement plus performant :

| Pattern | Callbacks/frame | Performance |
|---------|-----------------|-------------|
| 1 observer, N elements | 1 callback avec N notifications | Excellente |
| N observers | N callbacks avec 1 notification | 25% du temps d'animation |

### Pattern Recommande

```javascript
// composables/useResizeObserver.js
const observer = shallowRef(null);
const callbacks = new Map();

function createObserver() {
  if (observer.value) return;

  observer.value = new ResizeObserver((entries) => {
    // Les entries sont deja batchees par le navigateur
    for (const entry of entries) {
      const callback = callbacks.get(entry.target);
      if (callback) {
        callback(entry.contentRect);
      }
    }
  });
}

export function useResizeObserver() {
  createObserver();

  function observe(element, callback) {
    callbacks.set(element, callback);
    observer.value.observe(element);
  }

  function unobserve(element) {
    callbacks.delete(element);
    observer.value.unobserve(element);
  }

  return { observe, unobserve };
}
```

### Quand Detacher/Rattacher

Selon [MDN](https://developer.mozilla.org/en-US/docs/Web/API/ResizeObserver) :

- **Detacher** (`unobserve`) : quand l'element sort du viewport (virtual scroll)
- **Ne PAS utiliser** `disconnect()` pour un seul element (ca deconnecte tout)
- Le navigateur batch deja les notifications, pas besoin de throttle/debounce

**Attention** : Ne faites pas disconnect/reconnect autour de vos propres mutations DOM, ca cause des boucles infinies.

---

## 4. Scroll Bidirectionnel (Prepend)

### Maintenir la Position Stable

Le pattern cle selon les discussions sur [GitHub](https://github.com/petyosi/react-virtuoso/discussions/1079) :

```javascript
function prependItems(newItems) {
  // 1. Enregistrer la position AVANT
  const scrollContainer = containerRef.value;
  const previousScrollHeight = scrollContainer.scrollHeight;
  const previousScrollTop = scrollContainer.scrollTop;

  // 2. Ajouter les items
  items.value = [...newItems, ...items.value];

  // 3. Attendre le rendu
  nextTick(() => {
    // 4. Restaurer la position relative
    const newScrollHeight = scrollContainer.scrollHeight;
    const heightDelta = newScrollHeight - previousScrollHeight;
    scrollContainer.scrollTop = previousScrollTop + heightDelta;
  });
}
```

### Pattern firstItemIndex (comme Virtuoso)

```javascript
// Pour une liste "infinie" dans les deux directions
const firstItemIndex = ref(10000); // Commencer au "milieu"

function prependItems(newItems) {
  firstItemIndex.value -= newItems.length;
  items.value = [...newItems, ...items.value];
}

// Le calcul d'offset devient :
const itemOffset = computed(() => {
  return (index - firstItemIndex.value) * estimatedHeight;
});
```

---

## 5. Exemple de Composable Vue 3

### Composable Complet pour Virtual Scrolling

```javascript
// composables/useVirtualScroll.js
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue';

export function useVirtualScroll(options) {
  const {
    items,
    estimateHeight = () => 50,
    overscan = 3,
    containerRef
  } = options;

  // State
  const scrollTop = ref(0);
  const viewportHeight = ref(0);
  const heightCache = reactive(new Map());

  // Position cache avec binary search
  const positions = computed(() => {
    let top = 0;
    return items.value.map((item, index) => {
      const height = heightCache.get(item.id) ?? estimateHeight(item);
      const position = { index, top, height };
      top += height;
      return position;
    });
  });

  const totalHeight = computed(() => {
    const last = positions.value[positions.value.length - 1];
    return last ? last.top + last.height : 0;
  });

  // Binary search pour trouver le premier item visible
  function findStartIndex(scrollTop) {
    let low = 0;
    let high = positions.value.length - 1;

    while (low < high) {
      const mid = Math.floor((low + high) / 2);
      if (positions.value[mid].top + positions.value[mid].height < scrollTop) {
        low = mid + 1;
      } else {
        high = mid;
      }
    }

    return Math.max(0, low - overscan);
  }

  // Items visibles
  const visibleRange = computed(() => {
    const start = findStartIndex(scrollTop.value);
    let end = start;

    const bottomEdge = scrollTop.value + viewportHeight.value;
    while (end < positions.value.length &&
           positions.value[end].top < bottomEdge) {
      end++;
    }

    return {
      start,
      end: Math.min(positions.value.length, end + overscan)
    };
  });

  const visibleItems = computed(() => {
    const { start, end } = visibleRange.value;
    return positions.value.slice(start, end).map(pos => ({
      ...items.value[pos.index],
      style: {
        position: 'absolute',
        top: `${pos.top}px`,
        width: '100%'
      }
    }));
  });

  // Offset pour transform (alternative au positionnement absolu)
  const offsetY = computed(() => {
    const { start } = visibleRange.value;
    return positions.value[start]?.top ?? 0;
  });

  // Gestionnaire de scroll avec RAF
  let rafId = null;
  function handleScroll(e) {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(() => {
      scrollTop.value = e.target.scrollTop;
    });
  }

  // Mesure d'un item (appeler depuis le composant enfant)
  function measureItem(id, height) {
    const oldHeight = heightCache.get(id);
    if (oldHeight !== height) {
      heightCache.set(id, height);

      // Correction du scroll si l'item est au-dessus
      const item = items.value.find(i => i.id === id);
      const pos = positions.value.find(p => p.index === items.value.indexOf(item));

      if (pos && pos.top < scrollTop.value && oldHeight !== undefined) {
        const delta = height - oldHeight;
        containerRef.value.scrollTop += delta;
      }
    }
  }

  // Setup
  onMounted(() => {
    const container = containerRef.value;
    if (container) {
      viewportHeight.value = container.clientHeight;
      container.addEventListener('scroll', handleScroll, { passive: true });
    }
  });

  onUnmounted(() => {
    if (rafId) cancelAnimationFrame(rafId);
    containerRef.value?.removeEventListener('scroll', handleScroll);
  });

  return {
    visibleItems,
    totalHeight,
    offsetY,
    measureItem,
    scrollTo: (offset) => {
      if (containerRef.value) {
        containerRef.value.scrollTop = offset;
      }
    }
  };
}
```

### Utilisation dans un Composant

```vue
<script setup>
import { ref } from 'vue';
import { useVirtualScroll } from '@/composables/useVirtualScroll';

const props = defineProps(['items']);
const containerRef = ref(null);

const { visibleItems, totalHeight, measureItem } = useVirtualScroll({
  items: toRef(props, 'items'),
  containerRef,
  estimateHeight: (item) => 50 + (item.content?.length ?? 0) / 50 * 20
});
</script>

<template>
  <div
    ref="containerRef"
    class="virtual-container"
    style="height: 400px; overflow-y: auto; position: relative;"
  >
    <div :style="{ height: totalHeight + 'px' }">
      <VirtualItem
        v-for="item in visibleItems"
        :key="item.id"
        :item="item"
        :style="item.style"
        @measured="(h) => measureItem(item.id, h)"
      />
    </div>
  </div>
</template>
```

### Composant Item avec ResizeObserver

```vue
<script setup>
import { ref, onMounted, onUnmounted } from 'vue';

const props = defineProps(['item']);
const emit = defineEmits(['measured']);

const itemRef = ref(null);
let observer = null;

onMounted(() => {
  observer = new ResizeObserver((entries) => {
    const height = entries[0].contentRect.height;
    emit('measured', height);
  });
  observer.observe(itemRef.value);
});

onUnmounted(() => {
  observer?.disconnect();
});
</script>

<template>
  <div ref="itemRef" class="virtual-item">
    <slot :item="item" />
  </div>
</template>
```

---

## Resume des Points Cles

| Aspect | Recommandation |
|--------|----------------|
| Calcul de position | Binary search O(log n) sur positions triees |
| Hauteurs inconnues | Estimation initiale + mesure + cache |
| Sauts de scroll | `overflow-anchor: auto` OU correction manuelle du scrollTop |
| IntersectionObserver | Pour infinite load uniquement, pas pour visibility |
| ResizeObserver | **Un seul** observer global, `unobserve` quand hors viewport |
| Prepend items | Sauvegarder scrollHeight avant, restaurer delta apres |
| Positionnement | `transform: translateY()` (GPU) > `position: absolute` |
| Scroll handler | `requestAnimationFrame` pour limiter a 60fps |

---

## Sources

- [Build your own Virtual Scroll - Part I](https://dev.to/adamklein/build-your-own-virtual-scroll-part-i-11ib)
- [Build your own Virtual Scroll - Part II](https://dev.to/adamklein/build-your-own-virtual-scroll-part-ii-3j86)
- [Virtual scrolling with variable height - DEV Community](https://dev.to/georgii/virtual-scrolling-of-content-with-variable-height-with-angular-3a52)
- [IntersectionObserver vs Scroll Events Performance](https://itnext.io/1v1-scroll-listener-vs-intersection-observers-469a26ab9eb6)
- [ResizeObserver Performance - GitHub Issue](https://github.com/WICG/resize-observer/issues/59)
- [MDN - CSS Scroll Anchoring](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Scroll_anchoring)
- [MDN - ResizeObserver](https://developer.mozilla.org/en-US/docs/Web/API/ResizeObserver)
- [catamphetamine/virtual-scroller](https://github.com/catamphetamine/virtual-scroller)
- [React Virtuoso Discussions](https://github.com/petyosi/react-virtuoso/discussions/1079)
- [TanStack Virtual Docs](https://tanstack.com/virtual/latest/docs/api/virtualizer)
- [Vue 3 Virtual Scrolling Performance](https://borstch.com/snippet/vue-3-performance-optimization-with-virtual-scrolling)
