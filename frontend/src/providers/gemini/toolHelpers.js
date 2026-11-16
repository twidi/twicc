import { i18n } from '../../i18n/index.js'
/**
 * Tool-rendering helpers for Gemini sessions.
 *
 * Gemini tool calls are embedded, finished, in their assistant record
 * (``toolCalls[]``, with ``args`` and ``result``) and rendered by
 * ``components/session/detail/items/gemini/ToolCall.vue``. These helpers
 * provide the parts the generic surfaces share: the "{provider} is …ing"
 * verb (``WorkingAssistantMessage``), the one-line target summary and the
 * subagent capability of ``invoke_agent``.
 */

import { PROVIDER } from '../../constants'
import { BaseToolHelpers } from '../baseHelpers'
import { formatRelativePath } from '../utils/path'
import { invokedAgentName, isAgentToolName } from './records'

// Gemini CLI tool name → verb key under ``session.tools.verbs``.
const VERB_KEYS = {
    read_file: 'reading',
    read_many_files: 'reading',
    write_file: 'writing',
    replace: 'editing',
    run_shell_command: 'running',
    glob: 'searching',
    grep_search: 'searching',
    search_file_content: 'searching',
    list_directory: 'exploring',
    web_fetch: 'fetching',
    google_web_search: 'searching',
    write_todos: 'planning',
    invoke_agent: 'agent',
}

// Argument names holding a path, in lookup order.
const PATH_ARGS = ['file_path', 'absolute_path', 'path', 'dir_path', 'directory']

export class GeminiToolHelpers extends BaseToolHelpers {
    static provider = PROVIDER.GEMINI

    computeToolSummary(name, input, baseDir) {
        return { displayName: this.getDisplayName(name, input), inline: this.getInlineSummary(name, input, baseDir) }
    }

    /**
     * Header label of an ``invoke_agent`` card: the invoked agent's name
     * (``codebase_investigator``), else the generic "Agent". Null for any
     * other tool (the card keeps the call's display name).
     */
    getDisplayName(name, input) {
        if (!isAgentToolName(name)) return null
        return { name: invokedAgentName(input) || i18n.global.t('session.tools.headers.agent'), namespace: null }
    }

    /**
     * Short target of a call, from its arguments: the file or directory,
     * the shell command, the search pattern or query, the fetched URL.
     * Null when no argument is worth a summary.
     */
    getInlineSummary(name, input, baseDir) {
        const args = input && typeof input === 'object' ? input : {}
        if (name === 'run_shell_command' && typeof args.command === 'string') return args.command
        const pattern = firstString(args, ['pattern', 'query'])
        const path = firstString(args, PATH_ARGS)
        if (pattern) return path ? `${pattern} (${formatRelativePath(path, baseDir)})` : pattern
        if (path) return formatRelativePath(path, baseDir)
        if (Array.isArray(args.paths) && args.paths.length) return args.paths.join(', ')
        if (Array.isArray(args.include) && args.include.length) return args.include.join(', ')
        return firstString(args, ['url', 'prompt', 'fact', 'title', 'description'])
    }

    getVerb(name) {
        if (!name) return null
        const key = VERB_KEYS[name]
        return key
            ? i18n.global.t(`session.tools.verbs.${key}`)
            : i18n.global.t('session.tools.verbs.using', { tool: name })
    }

    isFileChangeTool(name) {
        return name === 'write_file' || name === 'replace'
    }

    isAgentTool(name) {
        // ``invoke_agent`` runs a subagent session, linked to the call by an
        // AgentLink: the tool card shows the View Agent button.
        return isAgentToolName(name)
    }
}

function firstString(args, keys) {
    for (const key of keys) {
        const value = args[key]
        if (typeof value === 'string' && value.trim()) return value
    }
    return null
}

export const geminiToolHelpers = new GeminiToolHelpers()
