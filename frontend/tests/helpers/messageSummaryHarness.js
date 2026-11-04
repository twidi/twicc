import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import * as summaries from '../../src/stores/summaries.js'
import * as speech from '../../src/stores/textToSpeech.js'
import * as ttsConfig from '../../src/utils/ttsConfig.js'
import { PROVIDER } from '../../src/constants.js'

// Keep the real view controller and stores; replace only browser-owned settings initialization.
export function messageSummaryModule(settings = Vue.reactive({ summaryModel: 'auto', enabledProviders: [PROVIDER.CLAUDE_CODE] })) {
    const modules = {
        vue: Vue, '../stores/summaries.js': summaries,
        '../stores/settings.js': { useSettingsStore: () => settings },
        '../stores/textToSpeech.js': speech, '../utils/ttsConfig.js': ttsConfig,
    }
    let source = readFileSync(new URL('../../src/composables/useMessageSummary.js', import.meta.url), 'utf8')
    source = source.replace(/import\s+\{([\s\S]*?)\}\s+from\s+['"]([^'"]+)['"];?/g,
        (_, names, path) => `const {${names.replace(/\bas\b/g, ':')}} = modules[${JSON.stringify(path)}];`)
        .replace(/export (const|function) /g, '$1 ')
    return new Function('modules', `${source}\nreturn { MESSAGE_SUMMARY_CONTEXT, MESSAGE_SUMMARY_VISIT, createMessageSummaryVisit, useMessageSummary };`)(modules)
}
