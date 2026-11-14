import { i18n } from '../i18n/index.js'

// Stable frontmatter ids select UI metadata only. Markdown bodies remain external content.
const CONTENT_METADATA = {
    "help": {
        "browser-tab": {
            "titleKey": "help.entries.browserTab.title"
        },
        "external-mcp": {
            "titleKey": "help.entries.externalMcp.title"
        },
        "external-url": {
            "titleKey": "help.entries.externalUrl.title"
        },
        "layout-docks": {
            "titleKey": "help.entries.layoutDocks.title"
        },
        "model-effort-score": {
            "titleKey": "help.entries.modelEffortScore.title"
        },
        "openrouter": {
            "titleKey": "help.entries.openrouter.title"
        },
        "peers": {
            "titleKey": "help.entries.peers.title"
        },
        "projects": {
            "titleKey": "help.entries.projects.title"
        },
        "sharing": {
            "titleKey": "help.entries.sharing.title"
        },
        "text-to-speech": {
            "titleKey": "help.entries.textToSpeech.title"
        },
        "timeline-speech": {
            "titleKey": "help.entries.timelineSpeech.title"
        },
        "what-are-artifacts": {
            "titleKey": "help.entries.whatAreArtifacts.title"
        },
        "workspaces": {
            "titleKey": "help.entries.workspaces.title"
        },
        "worktrees": {
            "titleKey": "help.entries.worktrees.title"
        }
    },
    "tips": {
        "active-sessions-across-projects": {
            "titleKey": "tips.entries.activeSessionsAcrossProjects.title"
        },
        "agent-settings-presets": {
            "titleKey": "tips.entries.agentSettingsPresets.title"
        },
        "apply-settings": {
            "titleKey": "tips.entries.applySettings.title"
        },
        "archive-sessions": {
            "titleKey": "tips.entries.archiveSessions.title"
        },
        "artifacts": {
            "titleKey": "tips.entries.artifacts.title"
        },
        "attach-files": {
            "titleKey": "tips.entries.attachFiles.title"
        },
        "choose-provider": {
            "titleKey": "tips.entries.chooseProvider.title"
        },
        "code-comments": {
            "titleKey": "tips.entries.codeComments.title"
        },
        "command-palette-mac": {
            "titleKey": "tips.entries.commandPaletteMac.title"
        },
        "command-palette-non-mac": {
            "titleKey": "tips.entries.commandPaletteNonMac.title"
        },
        "default-worktree-directory": {
            "titleKey": "tips.entries.defaultWorktreeDirectory.title"
        },
        "detach-session": {
            "titleKey": "tips.entries.detachSession.title"
        },
        "display-modes": {
            "titleKey": "tips.entries.displayModes.title"
        },
        "dockable-layouts": {
            "titleKey": "tips.entries.dockableLayouts.title"
        },
        "download-folder": {
            "titleKey": "tips.entries.downloadFolder.title"
        },
        "draft-sessions": {
            "titleKey": "tips.entries.draftSessions.title"
        },
        "element-comments": {
            "titleKey": "tips.entries.elementComments.title"
        },
        "emergency-stop": {
            "titleKey": "tips.entries.emergencyStop.title"
        },
        "files-options": {
            "titleKey": "tips.entries.filesOptions.title"
        },
        "files-preview": {
            "titleKey": "tips.entries.filesPreview.title"
        },
        "full-text-search-mac": {
            "titleKey": "tips.entries.fullTextSearchMac.title"
        },
        "full-text-search-non-mac": {
            "titleKey": "tips.entries.fullTextSearchNonMac.title"
        },
        "git-options": {
            "titleKey": "tips.entries.gitOptions.title"
        },
        "in-session-search-mac": {
            "titleKey": "tips.entries.inSessionSearchMac.title"
        },
        "in-session-search-non-mac": {
            "titleKey": "tips.entries.inSessionSearchNonMac.title"
        },
        "keyboard-shortcuts-list": {
            "titleKey": "tips.entries.keyboardShortcutsList.title"
        },
        "mark-unread": {
            "titleKey": "tips.entries.markUnread.title"
        },
        "message-inserts": {
            "titleKey": "tips.entries.messageInserts.title"
        },
        "message-snippets": {
            "titleKey": "tips.entries.messageSnippets.title"
        },
        "message-summaries": {
            "titleKey": "tips.entries.messageSummaries.title"
        },
        "mobile-session-header": {
            "titleKey": "tips.entries.mobileSessionHeader.title"
        },
        "new-session-anywhere": {
            "titleKey": "tips.entries.newSessionAnywhere.title"
        },
        "notifications": {
            "titleKey": "tips.entries.notifications.title"
        },
        "orchestration": {
            "titleKey": "tips.entries.orchestration.title"
        },
        "project-agent-defaults": {
            "titleKey": "tips.entries.projectAgentDefaults.title"
        },
        "project-page": {
            "titleKey": "tips.entries.projectPage.title"
        },
        "project-selector-menu": {
            "titleKey": "tips.entries.projectSelectorMenu.title"
        },
        "project-trust": {
            "titleKey": "tips.entries.projectTrust.title"
        },
        "quota-wakeup": {
            "titleKey": "tips.entries.quotaWakeup.title"
        },
        "rename-sessions": {
            "titleKey": "tips.entries.renameSessions.title"
        },
        "selection-comments": {
            "titleKey": "tips.entries.selectionComments.title"
        },
        "send-snippet-long-press": {
            "titleKey": "tips.entries.sendSnippetLongPress.title"
        },
        "session-compact-view": {
            "titleKey": "tips.entries.sessionCompactView.title"
        },
        "session-links": {
            "titleKey": "tips.entries.sessionLinks.title"
        },
        "session-multi-select": {
            "titleKey": "tips.entries.sessionMultiSelect.title"
        },
        "session-pins": {
            "titleKey": "tips.entries.sessionPins.title"
        },
        "session-switcher": {
            "titleKey": "tips.entries.sessionSwitcher.title"
        },
        "sharing": {
            "titleKey": "tips.entries.sharing.title"
        },
        "start-session-from-selection": {
            "titleKey": "tips.entries.startSessionFromSelection.title"
        },
        "steer-running-agent": {
            "titleKey": "tips.entries.steerRunningAgent.title"
        },
        "terminal-copy-on-select": {
            "titleKey": "tips.entries.terminalCopyOnSelect.title"
        },
        "terminal-option-as-meta": {
            "titleKey": "tips.entries.terminalOptionAsMeta.title"
        },
        "terminal-snippets": {
            "titleKey": "tips.entries.terminalSnippets.title"
        },
        "terminal-tabs": {
            "titleKey": "tips.entries.terminalTabs.title"
        },
        "text-to-speech": {
            "titleKey": "tips.entries.textToSpeech.title"
        },
        "timeline": {
            "titleKey": "tips.entries.timeline.title"
        },
        "timeline-speech": {
            "titleKey": "tips.entries.timelineSpeech.title"
        },
        "tips-list": {
            "titleKey": "tips.entries.tipsList.title"
        },
        "title-suggestions": {
            "titleKey": "tips.entries.titleSuggestions.title"
        },
        "twicc-skills": {
            "titleKey": "tips.entries.twiccSkills.title"
        },
        "upload-files": {
            "titleKey": "tips.entries.uploadFiles.title"
        },
        "usage-quotas": {
            "titleKey": "tips.entries.usageQuotas.title"
        },
        "workspaces": {
            "titleKey": "tips.entries.workspaces.title"
        },
        "worktrees": {
            "titleKey": "tips.entries.worktrees.title"
        }
    }
}

export function localizedContentManifest(kind, manifest) {
    return Object.fromEntries(Object.entries(manifest).map(([id, metadata]) => {
        const titleKey = CONTENT_METADATA[kind]?.[id]?.titleKey
        return [id, { ...metadata, title: titleKey ? i18n.global.t(titleKey) : metadata.title }]
    }))
}
