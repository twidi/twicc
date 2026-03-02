# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.3] - Unreleased

### Added

- Display unnamed projects as a compressed directory tree (radix trie) on the home page, in the sidebar project selector, and in "new session" dropdowns
- Persist "show archived sessions" and "compact view" sidebar toggles in localStorage across page reloads

### Changed

- Hide sessions without any user message from session listings and project session counts (sessions created by launching Claude Code but never sending a message)
- Refactor Session database indexes: replace two separate indexes with a combined `(project, type, -mtime)` index and a conditional index for visible sessions

### Fixed

- Fix stale detection for projects whose Claude folder (`~/.claude/projects/`) was removed but working directory still exists (e.g. after session sublimation)
- Strip inherited `CLAUDE_*` environment variables at startup, in devctl, and in PTY terminals to prevent Claude Code from detecting a false nested SDK session
- Limit project selector and new-session dropdown heights to 50vh with scrolling when too many projects exist
- Disable diff editor compact mode so hidden unchanged regions can always be expanded (the compact widget had no interactivity)
- Add `domReadOnly` to Monaco editors when read-only, preventing the mobile keyboard from appearing on tap

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
