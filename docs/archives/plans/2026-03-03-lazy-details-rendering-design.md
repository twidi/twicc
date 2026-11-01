# Lazy Rendering for wa-details Content

## Problem

`wa-details` (Web Awesome) always renders its slotted content in the DOM, even when closed. The open/close is purely visual (CSS height animation), not conditional rendering. In conversations with hundreds of tool uses, this means hundreds of `JsonHumanView` and `MarkdownContent` instances are rendered for details that are rarely opened — wasting CPU and memory.

## Solution

Add a `v-if="isOpen"` gate on the content of each `wa-details` usage. Content is destroyed on close and recreated on open.

## Components

### ToolUseContent.vue (major impact)

- Add `ref isOpen = false`, toggled in existing `onToolUseOpen` / `onToolUseClose` handlers
- Wrap content of outer wa-details (input + result wa-details) in `<template v-if="isOpen">`
- Inner result wa-details needs no separate v-if — destroyed/recreated with parent
- On re-open: `JsonHumanView` recreated from props (instant); `resultData` ref survives the v-if cycle so already-fetched results display immediately

### ThinkingContent.vue (notable impact)

- Add `ref isOpen = false`, toggled via `@wa-show` / `@wa-hide`
- `v-if="isOpen"` on `<div class="thinking-body">`
- On re-open: `MarkdownContent` re-renders (slight delay, acceptable given rarity)

### UnknownEntry.vue (minor impact, consistency)

- Same pattern: `ref isOpen = false`, `v-if="isOpen"` on content

## Approach

Destroy on close (`v-if="isOpen"`) rather than render-once-and-keep (`v-if="hasBeenOpened"`). Rationale: tool use details are rarely reopened a second time, so no benefit in keeping rendered content in memory.
