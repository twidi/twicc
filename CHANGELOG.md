# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
