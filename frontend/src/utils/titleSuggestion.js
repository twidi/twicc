import { resolveTitleSuggestionModel } from '../constants.js'

export function buildTitleSuggestionRequest({
    sessionId,
    provider,
    systemPrompt,
    prompt = null,
    titleSuggestionModel,
    noFallback = false,
}) {
    const message = {
        type: 'suggest_title',
        sessionId,
        provider,
        systemPrompt,
        titleSuggestionModel: resolveTitleSuggestionModel(titleSuggestionModel),
    }
    if (prompt) message.prompt = prompt
    // Set when the user picked this provider explicitly: the backend must not
    // answer with another one if it fails.
    if (noFallback) message.noFallback = true
    return message
}
