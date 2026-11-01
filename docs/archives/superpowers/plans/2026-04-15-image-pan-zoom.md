# Image Pan/Zoom Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add zoom/pan to all image displays (MediaPreviewDialog and FilePane) using @panzoom/panzoom, with gesture-only UX (wheel, pinch, drag, double-click reset).

**Architecture:** A shared `usePanZoom` composable wraps the @panzoom/panzoom library and is consumed by both image-displaying components. The composable watches a template ref, initializes/destroys the panzoom instance lifecycle-safely, and exposes a `reset()` for programmatic zoom reset.

**Tech Stack:** Vue 3 (Composition API, `<script setup>`), @panzoom/panzoom

**Spec:** `docs/superpowers/specs/2026-04-15-image-pan-zoom-design.md`

**Note:** This project uses "no tests, no linting" policy (see CLAUDE.md). Verification is manual via dev servers.

---

### Task 1: Add @panzoom/panzoom dependency

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install the package**

```bash
cd frontend && npm install @panzoom/panzoom
```

- [ ] **Step 2: Verify installation**

```bash
ls frontend/node_modules/@panzoom/panzoom/dist/
```

Expected: `panzoom.js`, `panzoom.min.js` (or similar dist files).

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "feat: add @panzoom/panzoom dependency for image zoom/pan"
```

---

### Task 2: Create `usePanZoom` composable

**Files:**
- Create: `frontend/src/composables/usePanZoom.js`

**Reference:** Check `frontend/src/composables/useDragHover.js` for project composable patterns.

**Library docs:** `frontend/node_modules/@panzoom/panzoom/README.md`

- [ ] **Step 1: Create the composable**

```javascript
// frontend/src/composables/usePanZoom.js
import Panzoom from '@panzoom/panzoom'
import { watch, onBeforeUnmount } from 'vue'

const MAX_SCALE = 10
const MIN_SCALE = 0.5
const ZOOM_STEP = 0.3

export function usePanZoom(elementRef) {
    let instance = null
    let currentEl = null
    let wheelHandler = null
    let dblclickHandler = null

    function init(el) {
        destroy()
        currentEl = el
        instance = Panzoom(el, {
            maxScale: MAX_SCALE,
            minScale: MIN_SCALE,
            step: ZOOM_STEP,
            panOnlyWhenZoomed: true,
            cursor: 'grab',
        })

        wheelHandler = (event) => instance.zoomWithWheel(event)
        el.parentElement.addEventListener('wheel', wheelHandler, { passive: false })

        dblclickHandler = () => instance.reset()
        el.addEventListener('dblclick', dblclickHandler)
    }

    function destroy() {
        if (!instance) return
        if (dblclickHandler && currentEl) {
            currentEl.removeEventListener('dblclick', dblclickHandler)
        }
        if (wheelHandler && currentEl?.parentElement) {
            currentEl.parentElement.removeEventListener('wheel', wheelHandler)
        }
        instance.destroy()
        instance = null
        currentEl = null
        wheelHandler = null
        dblclickHandler = null
    }

    function reset() {
        instance?.reset()
    }

    watch(elementRef, (el, _oldEl, onCleanup) => {
        if (el) {
            init(el)
        } else {
            destroy()
        }
        onCleanup(() => destroy())
    }, { flush: 'post' })

    onBeforeUnmount(() => destroy())

    return { reset }
}
```

**Important implementation notes:**
- The `destroy()` function must safely handle the case where the DOM element has already been removed (Vue may unmount the element before the watcher fires). Use defensive checks on `el` and `el.parentElement`.
- `@panzoom/panzoom` does not expose the target element directly. We need to track it ourselves or use a closure. **Before writing the code, read the library's README** at `frontend/node_modules/@panzoom/panzoom/README.md` to verify the API — specifically: how to get the target element back from the instance, and the exact signature of `destroy()`, `reset()`, `zoomWithWheel()`.
- The code above is a starting point. Adapt based on what the library actually exposes.

- [ ] **Step 2: Verify the file was created correctly**

```bash
head -5 frontend/src/composables/usePanZoom.js
```

Expected: the import line and constants.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/composables/usePanZoom.js
git commit -m "feat: create usePanZoom composable wrapping @panzoom/panzoom"
```

---

### Task 3: Integrate pan/zoom into MediaPreviewDialog.vue

**Files:**
- Modify: `frontend/src/components/MediaPreviewDialog.vue`

**Key context:**
- The `<img>` is at line ~175-180, inside `.preview-content`
- `currentIndex` ref controls which image is shown (line 22)
- Navigation via `prev()`/`next()` changes `currentIndex`
- Dialog opens via `open(index)` method (line 127)

- [ ] **Step 1: Add import and composable call**

In `<script setup>`, add:
```javascript
import { usePanZoom } from '../composables/usePanZoom'

const imageRef = ref(null)
const { reset: resetZoom } = usePanZoom(imageRef)
```

- [ ] **Step 2: Add watcher to reset zoom on navigation**

```javascript
watch(currentIndex, () => {
    resetZoom()
})
```

- [ ] **Step 3: Add template ref to the img element**

Change the `<img>` tag (around line 175-179):
```html
<img
    v-if="currentItem?.type === 'image'"
    ref="imageRef"
    :src="currentItem.src"
    :alt="currentItem.name || 'Image'"
    class="preview-image"
/>
```

- [ ] **Step 4: Update CSS**

Add `overflow: hidden` to `.preview-content`:
```css
.preview-content {
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    overflow: hidden;  /* clip zoomed image */
}
```

Add `touch-action: none` to `.preview-image`:
```css
.preview-image {
    display: block;
    max-width: 100%;
    max-height: calc(90dvh - 100px);
    object-fit: contain;
    touch-action: none;  /* prevent browser gesture interception */
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MediaPreviewDialog.vue
git commit -m "feat: add pan/zoom to MediaPreviewDialog image preview"
```

---

### Task 4: Integrate pan/zoom into FilePane.vue

**Files:**
- Modify: `frontend/src/components/FilePane.vue`

**Key context:**
- The `<img>` is at line ~680-684, inside `.image-preview-container`
- `imageSrc` (ref, line 127) holds binary image data URI
- `svgPreviewUrl` (computed, line 149) holds SVG blob URL
- `showImagePreview` (computed, line 244) controls visibility
- `filePath` prop triggers file loading (watcher at line 330)
- There is a single `<img>` tag that uses `:src="imageSrc || svgPreviewUrl"`

- [ ] **Step 1: Add import and composable call**

In `<script setup>`, add:
```javascript
import { usePanZoom } from '../composables/usePanZoom'

const imageRef = ref(null)
const { reset: resetZoom } = usePanZoom(imageRef)
```

- [ ] **Step 2: Reset zoom on file change**

The existing `filePath` watcher (line 330) already handles file switches. Add `resetZoom()` at the beginning of the watcher callback, right after the early return for no path:

```javascript
watch(() => props.filePath, async (newPath) => {
    if (!newPath) {
        // ... existing reset logic ...
        return
    }

    resetZoom()  // ← add this line

    // ... rest of existing watcher ...
}, { immediate: true })
```

- [ ] **Step 3: Add template ref to the img element**

Change the `<img>` tag (around line 680-684):
```html
<img
    ref="imageRef"
    :src="imageSrc || svgPreviewUrl"
    :alt="fileName"
    class="image-preview"
/>
```

- [ ] **Step 4: Update CSS**

Change `.image-preview-container` from `overflow: auto` to `overflow: hidden`:
```css
.image-preview-container {
    position: absolute;
    inset: 0;
    overflow: hidden;  /* was: auto — now clips zoomed image */
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--wa-space-m);
}
```

Add `touch-action: none` to `.image-preview`:
```css
.image-preview {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    touch-action: none;  /* prevent browser gesture interception */
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FilePane.vue
git commit -m "feat: add pan/zoom to FilePane image preview"
```

---

### Task 5: Manual verification

**Prerequisites:** Dev servers running (`uv run ./devctl.py status`). If not running, remind the user to start them.

- [ ] **Step 1: Test MediaPreviewDialog with binary images**

1. Open a session that has image attachments (or send a message with an image)
2. Click a thumbnail to open MediaPreviewDialog
3. Verify: mouse wheel zooms in/out
4. Verify: click-and-drag pans when zoomed in
5. Verify: double-click resets zoom
6. Verify: at 1x zoom, drag does NOT pan (panOnlyWhenZoomed)
7. Verify: navigating prev/next resets zoom
8. Verify: nav buttons remain clickable and not affected by zoom

- [ ] **Step 2: Test FilePane with binary images**

1. Open a session's Files tab
2. Navigate to a binary image file (PNG, JPG, etc.)
3. Verify: mouse wheel zooms in/out
4. Verify: click-and-drag pans when zoomed in
5. Verify: double-click resets zoom
6. Verify: switching to a different file resets zoom

- [ ] **Step 3: Test FilePane with SVG preview**

1. Open a `.svg` file in the Files tab
2. Click the eye toggle to enable SVG preview
3. Verify: zoom/pan works identically to binary images
4. Verify: toggling SVG preview off and back on works correctly

- [ ] **Step 4: Test cursor appearance**

1. Verify: cursor shows `grab` hand when hovering over an image
2. Verify: cursor changes to `grabbing` while dragging

- [ ] **Step 5: Commit (if any fixes were needed)**

```bash
git add -p  # stage only relevant changes
git commit -m "fix: address issues found during pan/zoom testing"
```
