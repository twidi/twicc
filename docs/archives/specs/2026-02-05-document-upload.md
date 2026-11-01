# Document Upload

**Date:** 2026-02-05
**Status:** DRAFT

## Overview

Allow users to attach files (images, PDFs, text files) to their messages before sending. Files are encoded to base64 immediately upon selection, stored in IndexedDB for persistence, and sent to the backend with the message for the Claude Agent SDK.

## Requirements

### User Stories

1. **As a user**, I want to drag & drop files anywhere in the session view to attach them to my message
2. **As a user**, I want to click an attach button to select files from my file system
3. **As a user**, I want to paste images (Ctrl+V) directly in the textarea
4. **As a user**, I want to see thumbnails of my attachments before sending
5. **As a user**, I want to click on a thumbnail to preview the full image or text content
6. **As a user**, I want to remove an attachment before sending
7. **As a user**, I want my attachments to persist if I accidentally close the tab

### Supported File Types

| Type | MIME Types | Extension |
|------|------------|-----------|
| Images | `image/png`, `image/jpeg`, `image/gif`, `image/webp` | .png, .jpg, .jpeg, .gif, .webp |
| PDF | `application/pdf` | .pdf |
| Text | `text/plain` | .txt |

### Limits (from Claude API documentation)

| Limit | Value | Source |
|-------|-------|--------|
| Max file size | **5 MB** per file | Claude API |
| Max images per request | **100** | Claude API |
| Max request size | **32 MB** total | Claude API |
| Max image dimensions | **8000x8000 px** | Claude API |
| Max dimensions (if >20 images) | **2000x2000 px** | Claude API |

**Note:** The Claude Code Viewer project does not enforce these limits client-side; it relies on the API to reject invalid requests. We will implement client-side validation for better UX.

## Architecture

### Input Methods

1. **Drag & Drop**: Entire session content area is a drop zone
2. **File Input Button**: Attach button (📎) to the left of thumbnails, under the textarea
3. **Clipboard Paste**: Ctrl+V in textarea captures images from clipboard (images only)

### Immediate Base64 Encoding

Files are converted to base64 **immediately** upon selection for two reasons:

1. **Temporary file references**: Screenshot tools and some drag operations provide temporary blob references that become invalid if the source changes (e.g., taking another screenshot)
2. **IndexedDB persistence**: Base64 strings can be stored directly in IndexedDB

### Data Flow

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Drag & Drop    │     │   Ctrl+V Paste   │     │  Bouton Attach   │
│   (File/Blob)    │     │   (images only)  │     │  (File Input)    │
└────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘
         │                        │                        │
         └────────────────────────┼────────────────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │      fileUtils.js             │
                  │  - validateFile(file)         │
                  │    • type supported?          │
                  │    • size < 5MB?              │
                  │  - fileToBase64(file)         │
                  │  - fileToText(file) for .txt  │
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │    DraftMedia Object          │
                  │  {                            │
                  │    id: uuid(),                │
                  │    sessionId,                 │
                  │    name: file.name,           │
                  │    type: 'image'|'txt'|'pdf', │
                  │    mimeType: file.type,       │
                  │    base64: "...",             │
                  │    createdAt: Date.now()      │
                  │  }                            │
                  └───────────────┬───────────────┘
                                  │
           ┌──────────────────────┼──────────────────────┐
           ▼                      ▼                      ▼
   ┌─────────────────┐  ┌─────────────────┐    ┌─────────────────┐
   │   Pinia Store   │  │    IndexedDB    │    │   UI Display    │
   │   (in-memory)   │  │   draftMedias   │    │   thumbnails    │
   │                 │  │                 │    │                 │
   │ attachments:    │  │ + add mediaId   │    │ - miniatures    │
   │  Map<id,Media>  │  │   to draftMsg   │    │ - delete btn    │
   └────────┬────────┘  └─────────────────┘    │ - click=preview │
            │                                   └─────────────────┘
            │
            ▼  ON SEND MESSAGE
   ┌─────────────────────────────────────────────────────────────┐
   │  Build WebSocket payload:                                    │
   │  {                                                           │
   │    type: 'send_message',                                     │
   │    session_id | project_id,                                  │
   │    text: "...",                                              │
   │    images: [                                                 │
   │      { type: 'image', source: { type: 'base64', ... } }     │
   │    ],                                                        │
   │    documents: [                                              │
   │      { type: 'document', source: { type: 'base64'|'text' }} │
   │    ]                                                         │
   │  }                                                           │
   │                                                              │
   │  Then: delete draftMessage + draftMedias from IndexedDB     │
   └─────────────────────────────────────────────────────────────┘
```

### UI Layout

```
┌─────────────────────────────────────────────────────────────────┐
│                    SessionContent.vue                            │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              DROP ZONE (entire session area)               │  │
│  │                                                            │  │
│  │   ┌─────────────────────────────────────────────────────┐ │  │
│  │   │              SessionItemsList                        │ │  │
│  │   │              (existing messages)                     │ │  │
│  │   └─────────────────────────────────────────────────────┘ │  │
│  │                                                            │  │
│  │   ┌─────────────────────────────────────────────────────┐ │  │
│  │   │              MessageInputView.vue                    │ │  │
│  │   │  ┌───────────────────────────────────────────────┐  │ │  │
│  │   │  │         Textarea (Ctrl+V for images)          │  │ │  │
│  │   │  └───────────────────────────────────────────────┘  │ │  │
│  │   │                                                      │ │  │
│  │   │  ┌────────────────────────────────────────────────┐ │ │  │
│  │   │  │ [📎] [img] [img] [txt] [pdf]       [Send/Stop] │ │ │  │
│  │   │  │  ↑    └────────┬────────┘                      │ │ │  │
│  │   │  │  │        Thumbnails                           │ │ │  │
│  │   │  │  │        (click = preview dialog)             │ │ │  │
│  │   │  │  Attach                                        │ │ │  │
│  │   │  │  Button                                        │ │ │  │
│  │   │  └────────────────────────────────────────────────┘ │ │  │
│  │   └─────────────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Preview Dialog

```
┌─────────────────────────────────────────────────────────────────┐
│                    Preview Dialog (sl-dialog)                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                                                            │  │
│  │              IMAGE (full size, native scroll)              │  │
│  │                           or                               │  │
│  │              TEXT CONTENT (native scroll)                  │  │
│  │                                                            │  │
│  └───────────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  [img] [img] [txt] [pdf]  ← thumbnails to navigate        │  │
│  │    ↑ selected                                              │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

- **Images**: Displayed at full size with native browser scroll
- **Text files**: Content displayed in a `<pre>` block with native scroll
- **PDFs**: NOT previewable in V1 (just show icon, cannot open in dialog)

## IndexedDB Schema

### Current Schema (v2)

```javascript
// Object Stores:
// - draftMessages: sessionId → { message?: string, title?: string }
// - draftSessions: sessionId → { projectId, title? }
```

### New Schema (v3)

```javascript
// Object Stores:
// - draftMessages: sessionId → { message?: string, title?: string, mediaIds?: string[] }
// - draftSessions: sessionId → { projectId, title? }
// - draftMedias: id → { id, sessionId, name, type, mimeType, base64, createdAt }
//   Index: sessionId (to retrieve all medias for a draft)
```

### DraftMedia Object

```typescript
interface DraftMedia {
  id: string           // UUID
  sessionId: string    // Session this media belongs to
  name: string         // Original filename
  type: 'image' | 'txt' | 'pdf'
  mimeType: string     // 'image/png', 'text/plain', 'application/pdf'
  base64: string       // Encoded data (base64 for images/pdf, plain text for txt)
  createdAt: number    // Timestamp for potential cleanup
}
```

### Why Separate Table?

Draft messages are saved frequently (every keystroke, debounced). Storing large base64 blobs in the same record would:
1. Cause unnecessary writes of megabytes of data on each save
2. Slow down the save operation
3. Risk data corruption if a large write fails

By separating medias:
- Medias are written once when added
- Draft message only stores array of IDs (a few bytes)
- Deleting a media is a simple delete operation

## Claude Agent SDK Format

### Message Structure

```javascript
// For new session:
{
  type: 'send_message',
  project_id: 'proj-xyz',
  session_id: 'uuid-of-new-session',  // Client-generated for draft
  text: 'User message',
  images: [
    {
      type: 'image',
      source: {
        type: 'base64',
        media_type: 'image/png',  // or image/jpeg, image/gif, image/webp
        data: 'iVBORw0KGgo...'    // base64 encoded
      }
    }
  ],
  documents: [
    // For PDF:
    {
      type: 'document',
      source: {
        type: 'base64',
        media_type: 'application/pdf',
        data: 'JVBERi0xLjQ...'    // base64 encoded
      }
    },
    // For TXT:
    {
      type: 'document',
      source: {
        type: 'text',
        media_type: 'text/plain',
        data: 'Plain text content of the file...'  // NOT base64
      }
    }
  ]
}
```

**Important**: Text files use `type: 'text'` (not base64) with raw content.

## Implementation Details

### Files to Create

| File | Purpose |
|------|---------|
| `frontend/src/utils/fileUtils.js` | File validation, base64 encoding, type detection |
| `frontend/src/components/AttachmentThumbnails.vue` | Thumbnail display component |
| `frontend/src/components/AttachmentPreviewDialog.vue` | Full preview dialog |

### Files to Modify

| File | Changes |
|------|---------|
| `frontend/src/utils/draftStorage.js` | Add draftMedias store, bump DB version to 3 |
| `frontend/src/stores/data.js` | Add attachments state and actions |
| `frontend/src/components/SessionContent.vue` | Add drop zone handler |
| `frontend/src/components/MessageInput.vue` | Add attach button, thumbnails, paste handler |
| `frontend/src/composables/useWebSocket.js` | Include images/documents in send_message |
| `backend/src/twicc/asgi.py` | Handle images/documents in send_message |
| `backend/src/twicc/agent/manager.py` | Pass images/documents to process |
| `backend/src/twicc/agent/process.py` | Include attachments in SDK call |

### fileUtils.js

```javascript
// Constants
export const SUPPORTED_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
export const SUPPORTED_DOCUMENT_TYPES = ['application/pdf']
export const SUPPORTED_TEXT_TYPES = ['text/plain']
export const SUPPORTED_MIME_TYPES = [...SUPPORTED_IMAGE_TYPES, ...SUPPORTED_DOCUMENT_TYPES, ...SUPPORTED_TEXT_TYPES]
export const MAX_FILE_SIZE = 5 * 1024 * 1024  // 5 MB

// Validation
export function validateFile(file) {
  if (!SUPPORTED_MIME_TYPES.includes(file.type)) {
    return { valid: false, error: `Unsupported file type: ${file.type}` }
  }
  if (file.size > MAX_FILE_SIZE) {
    return { valid: false, error: `File too large: ${(file.size / 1024 / 1024).toFixed(1)} MB (max 5 MB)` }
  }
  return { valid: true }
}

// Type detection
export function getFileType(mimeType) {
  if (mimeType.startsWith('image/')) return 'image'
  if (mimeType === 'application/pdf') return 'pdf'
  return 'txt'
}

// Base64 encoding
export function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      // Remove data URL prefix (e.g., "data:image/png;base64,")
      const base64 = result.split(',')[1]
      resolve(base64)
    }
    reader.onerror = () => reject(new Error('Failed to read file'))
    reader.readAsDataURL(file)
  })
}

// Text reading (for .txt files)
export function fileToText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(new Error('Failed to read file'))
    reader.readAsText(file)
  })
}

// Process file and return DraftMedia object
export async function processFile(file, sessionId) {
  const validation = validateFile(file)
  if (!validation.valid) {
    throw new Error(validation.error)
  }

  const type = getFileType(file.type)
  let base64

  if (type === 'txt') {
    // For text files, store as plain text (will be sent as type: 'text')
    base64 = await fileToText(file)
  } else {
    // For images and PDFs, store as base64
    base64 = await fileToBase64(file)
  }

  return {
    id: crypto.randomUUID(),
    sessionId,
    name: file.name || `file.${type}`,
    type,
    mimeType: file.type,
    base64,
    createdAt: Date.now()
  }
}
```

### draftStorage.js Updates

```javascript
const DB_VERSION = 3  // Bump from 2
const DRAFT_MEDIAS_STORE = 'draftMedias'

// In onupgradeneeded:
if (!db.objectStoreNames.contains(DRAFT_MEDIAS_STORE)) {
  const store = db.createObjectStore(DRAFT_MEDIAS_STORE, { keyPath: 'id' })
  store.createIndex('sessionId', 'sessionId', { unique: false })
}

// New functions:
export async function saveDraftMedia(media) { ... }
export async function getDraftMedia(mediaId) { ... }
export async function deleteDraftMedia(mediaId) { ... }
export async function getDraftMediasBySession(sessionId) { ... }
export async function deleteAllDraftMediasForSession(sessionId) { ... }
```

### Store Actions

```javascript
// In data.js store:
state: {
  // ... existing state
  attachments: {},  // sessionId → Map<mediaId, DraftMedia>
}

actions: {
  async addAttachment(sessionId, file) {
    const media = await processFile(file, sessionId)
    await saveDraftMedia(media)

    // Update in-memory state
    if (!this.attachments[sessionId]) {
      this.attachments[sessionId] = new Map()
    }
    this.attachments[sessionId].set(media.id, media)

    // Update draft message with media ID
    const draft = await getDraftMessage(sessionId) || {}
    draft.mediaIds = draft.mediaIds || []
    draft.mediaIds.push(media.id)
    await saveDraftMessage(sessionId, draft)

    return media
  },

  async removeAttachment(sessionId, mediaId) {
    await deleteDraftMedia(mediaId)
    this.attachments[sessionId]?.delete(mediaId)

    // Update draft message
    const draft = await getDraftMessage(sessionId)
    if (draft?.mediaIds) {
      draft.mediaIds = draft.mediaIds.filter(id => id !== mediaId)
      await saveDraftMessage(sessionId, draft)
    }
  },

  async loadAttachmentsForSession(sessionId) {
    const medias = await getDraftMediasBySession(sessionId)
    this.attachments[sessionId] = new Map(medias.map(m => [m.id, m]))
  },

  async clearAttachmentsForSession(sessionId) {
    await deleteAllDraftMediasForSession(sessionId)
    delete this.attachments[sessionId]
  },

  getAttachmentsForSdk(sessionId) {
    const medias = Array.from(this.attachments[sessionId]?.values() || [])

    const images = medias
      .filter(m => m.type === 'image')
      .map(m => ({
        type: 'image',
        source: {
          type: 'base64',
          media_type: m.mimeType,
          data: m.base64
        }
      }))

    const documents = medias
      .filter(m => m.type === 'pdf' || m.type === 'txt')
      .map(m => ({
        type: 'document',
        source: m.type === 'txt'
          ? { type: 'text', media_type: 'text/plain', data: m.base64 }
          : { type: 'base64', media_type: 'application/pdf', data: m.base64 }
      }))

    return { images, documents }
  }
}
```

## Scope V1 vs Future

| Feature | V1 | Future |
|---------|:--:|:------:|
| Drag & Drop on session area | ✅ | |
| Attach button | ✅ | |
| Ctrl+V paste (images) | ✅ | |
| File type validation | ✅ | |
| File size validation (5 MB) | ✅ | |
| Immediate base64 encoding | ✅ | |
| IndexedDB persistence (separate table) | ✅ | |
| Thumbnails under textarea | ✅ | |
| Delete attachment | ✅ | |
| Preview dialog (images + txt) | ✅ | |
| Send to backend | ✅ | |
| Drag to reorder attachments | ❌ | ✅ |
| Preview PDF in dialog | ❌ | ✅ |
| Zoom in preview | ❌ | ✅ |
| Image compression/resize | ❌ | ✅ |

## Open Questions

1. **Error handling UX**: How to display errors when a file is rejected (too large, wrong type)?
   - Suggestion: Toast notification with error message

2. **Thumbnail size**: What dimensions for thumbnails?
   - Suggestion: ~48x48px for inline, CSS `object-fit: cover`

3. **Max attachments**: Should we limit the number of attachments?
   - API allows 100 images, but UX might suffer. Consider 20 as soft limit?

4. **Drop zone visual feedback**: What visual indicator when dragging over session?
   - Suggestion: Border highlight + overlay text "Drop files here"

## References

- [Claude Vision Documentation](https://docs.anthropic.com/en/docs/build-with-claude/vision)
- Claude Code Viewer implementation: `/home/twidi/dev/claude-code-viewer/src/app/projects/[projectId]/components/chatForm/`
- Existing draft storage: `frontend/src/utils/draftStorage.js`
