# Plan: Language Detection from Path Sibling

## Goal
When rendering JSON objects in JsonHumanView, detect the programming language from a sibling key that looks like a file path, and apply syntax highlighting to "content-like" keys and diff old/new values.

## Steps

### 1. Create `frontend/src/utils/languages.js`
- Export `getLanguageFromPath(filePath)` → returns shiki language id or `null`
- Static map of common extensions → shiki language ids
- Extract extension from path, look up in map
- Can also be used by FilePane.vue in the future (replace Monaco registry lookup)

### 2. In `JsonHumanView.vue` — add `keyLooksLikeCodeContent(key)`
- Uses `keyMatches(key, ['content'])` → matches `content`, `contents`, `*_content`, `content_*`, etc.

### 3. In `JsonHumanView.vue` — add sibling-based override computation
- New computed `siblingLanguageOverrides` for when `value` is an object:
  - Find first key where `keyLooksLikePath(key)` and value is a string
  - Extract language via `getLanguageFromPath(value[pathKey])`
  - For each key where `keyLooksLikeCodeContent(key)`: generate `{ valueType: 'string-code', language }` (language may be null/undefined if extension unknown → still string-code, just no highlighting)
- Merge logic: explicit parent `overrides[key]` > sibling-detected override > nothing
- Apply in the object rendering loop: `:override="overrides[key] ?? siblingLanguageOverrides[key]"`

### 4. In `JsonHumanView.vue` — apply to diff pairs
- When rendering the old/new values in `<details>`:
  - If a sibling path key exists → use `{ valueType: 'string-code', language }` instead of `{ valueType: 'string-multiline' }`
  - Explicit overrides still win: `overrides[pair.oldKey] ?? siblingLanguageOverrides[pair.oldKey] ?? { valueType: 'string-multiline' }`
  - The diff block itself stays as ` ```diff ` (diff coloring is more useful there)

### 5. In `PendingRequestForm.vue` — enrich tool overrides with language
- For Write: extract language from `toolInput.file_path`, add to `content` override
- For Edit: extract language from `toolInput.file_path`, add to `old_string` and `new_string` overrides
- For NotebookEdit: extract language from `toolInput.notebook_path`, add to `new_source` override
- Make `toolOverrides` a computed that merges static TOOL_OVERRIDES with detected language

### 6. Update `JsonTestView.vue`
- Add test cases exercising sibling path detection (object with `file_path` + `content`)
- Add test case with diff pairs + sibling path
