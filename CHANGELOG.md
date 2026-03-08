# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Git root selector in the Git tab (in sync with the one in the Files tab)

### Fixed

- Bash tool results no longer incorrectly rendered as Markdown

## [1.1.1] - 2026-03-08

### Added

- Auto-reload frontend when backend version changes
- Notify users when a new version is available on PyPI
- Monitor Claude Code status via status.claude.com and show toast notifications on outages

## [1.1.0] - 2026-03-08

### Added

- Effort level and thinking settings for Claude sessions
- Live tracking of Bash commands, agents and other possibly long-running tools
- Syntax-highlighted code display for Read tool results
- Show URL/query in WebFetch, WebSearch, and ToolSearch tool summaries

### Changed

- Upgrade Web Awesome 3.2 → 3.3.1 (removes many workarounds)
- Update claude-agent-sdk 0.1.45 → 0.1.48 (Claude Code CLI 2.1.63 → 2.1.71)
- Replace selects for model, permission, etc... in message input by simple button + popopver

### Fixed

- Classify `/clear` command items as system instead of user message, rewrite titles of sessions saved with "/clear" title
- "starting" state of process wasn't visible
- Fix custom session title not persisting on some circumstances
- Fix mobile layout issues
- Handle invalid TodoWrite
- Missed file attachments in optimistic user messages
- Improve backend resilience (watcher crash prevention, empty session handling, WebSocket error isolation)

## [1.0.3] - 2026-03-04

### Added

- Display unnamed projects as a directory tree
- Persist "show archived sessions" and "compact view" sidebar toggles
- Project archiving
- Improved session item rendering: tool use summaries and title changes
- Filtering of WebSocket message (for twicc external tooling) (contributed by @dguerizec, closes #3)
- Rate limiting on the login endpoint (contributed by @dguerizec)

### Changed

- Hide sessions without any user message
- More reliable git directory and branch detection (Closes #2)
- Performance improvements on the session chat
- Update claude-agent-sdk 0.1.44 → 0.1.45 (Claude Code CLI 2.1.59 → 2.1.63)

### Fixed

- Fix stale project detection
- Strip inherited `CLAUDE_*` environment variables at startup to prevent false nested SDK session detection
- Limit project selector height
- Disable diff editor compact mode
- Fix virtual keyboard behavior on mobile (read-only editors, draft screens)
- Block message sending while attached images are still being processed, preventing partial uploads

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
