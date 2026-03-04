# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.3] - Unreleased

### Added

- Display unnamed projects as a compressed directory tree (radix trie) on the home page, in the sidebar project selector, and in "new session" dropdowns
- Persist "show archived sessions" and "compact view" sidebar toggles in localStorage across page reloads
- Archive/unarchive projects via ellipsis dropdown menu on project cards, with toggle to show archived projects on home page; archived projects hidden by default from home, selectors, and session lists
- Display `custom-title` session items (session title changes) with a dedicated component instead of falling through to the generic "Unhandled event" view
- Improved tool use summaries: richer descriptions for Skill, Task, Grep, and Glob tools
- WebSocket subscribe filter: clients can connect with `?subscribe=type1,type2` to receive only specific message types, reducing bandwidth for lightweight consumers (contributed by David Guerizec)

### Changed

- Hide sessions without any user message from session listings and project session counts (sessions created by launching Claude Code but never sending a message)
- Refactor Session database indexes: replace two separate indexes with a combined `(project, type, -mtime)` index and a conditional index for visible sessions
- Refactor git resolution: cache-free filesystem reads in the live watcher (detects branch switches and new repos immediately), CWD fallback for Bash-only sessions, session git propagation moved from item-level to session-level, post-batch validation of git state (detects branch changes and deleted repos after Bash commands)
- Lazy-render wa-details content: tool use inputs/results, thinking blocks, and unknown entries are now only mounted when expanded (v-if on open state), saving significant CPU/memory on long conversations
- Lazy parsed content caching for session items, eliminating redundant JSON parsing
- Stabilize visual item references across recomputes, so Vue skips re-rendering unchanged items
- Extract ProcessDuration component and SessionListItem sub-component to eliminate per-second global re-renders and redundant store lookups in session list

### Fixed

- Fix stale detection for projects whose Claude folder (`~/.claude/projects/`) was removed but working directory still exists (e.g. after session sublimation)
- Strip inherited `CLAUDE_*` environment variables at startup, in devctl, and in PTY terminals to prevent Claude Code from detecting a false nested SDK session
- Limit project selector and new-session dropdown heights to 50vh with scrolling when too many projects exist
- Disable diff editor compact mode so hidden unchanged regions can always be expanded (the compact widget had no interactivity)
- Add `domReadOnly` to Monaco editors when read-only, preventing the mobile keyboard from appearing on tap
- Ensure enough space for the virtual keyboard on draft session screens on mobile
- Block message sending while attached images are still being processed (encoding, resizing), preventing partial uploads
- Add per-IP rate limiting on the login endpoint (5 attempts / 5 min, 60s lockout) with real client IP resolution from proxy/tunnel headers (CF-Connecting-IP, X-Forwarded-For, etc.)

## [1.0.2] - 2026-02-28

### Added

- Smart permission suggestions: auto-generate actionable suggestions for all tool types (file Read/Edit/Write, WebFetch, WebSearch, MCP tools) when the SDK doesn't provide them
- Wildcard MCP tool suggestions: offer server-wide permission alongside tool-specific ones
- Ungroup multi-rule permission suggestions so users can accept/reject each rule independently
- Destination selector for permission suggestions (user/project/local settings or session)
- File type icons in tool use summaries
- Display relative file paths in tool use summaries (relative to session working directory)

### Fixed

- Improve pending request form layout on mobile (reordered sections, wrapping buttons)
- Work around SDK bug serializing null `ruleContent` in permission responses
- Hide "Approve with changes" button when tool input is empty

## [1.0.1] - 2026-02-28

### Added

- Create new projects from the home page and from session dropdown menus, with directory path validation
- Dedicated component for displaying thinking blocks (instead of generic fallback)
- Show file path in tool use summary for Edit/Write/Read tools
- Stale project handling: hide stale projects from "new session" dropdowns and disable the button

### Fixed

- Detect stale projects based on actual working directory existence, not just the Claude projects folder
- Support HTTP access on LAN (non-secure contexts) by replacing `crypto.randomUUID()` with a fallback
- Avoid creating empty projects for folders with no sessions (defer creation until first session with content)
- Clean up existing empty projects via migration

## [1.0.0] - 2026-02-27

Initial release.
