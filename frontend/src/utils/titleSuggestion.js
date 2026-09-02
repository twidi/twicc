import { resolveTitleSuggestionModel } from '../constants.js'

export function buildTitleSuggestionRequest({
    sessionId,
    provider,
    systemPrompt,
    prompt = null,
    titleSuggestionModel,
}) {
    const message = {
        type: 'suggest_title',
        sessionId,
        provider,
        systemPrompt,
        titleSuggestionModel: resolveTitleSuggestionModel(titleSuggestionModel),
    }
    if (prompt) message.prompt = prompt
    return message
}
