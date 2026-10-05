import { runBoundedScrollerAction } from './scrollerConversationHarness.js'

export function reasoningCompletion(messageId, summaryTexts) {
    return { type: 'response_item', payload: { type: 'reasoning', id: messageId,
        summary: summaryTexts.map(text => ({ type: 'summary_text', text })) } }
}

function check(value, message) { if (!value) throw new Error(message) }
function acceptance(observed, scenario, options) {
    check(observed?.domObserved, 'Missing mounted conversation DOM observation')
    check(!observed.visualRows.includes(scenario.streamingLine), 'Synthetic visual row remains')
    check(!observed.domRows.includes(scenario.streamingLine), 'Synthetic DOM row remains')
    check(observed.visualRows.includes(scenario.realLine), 'Persisted visual row is missing')
    check(observed.domRows.includes(scenario.realLine), 'Persisted DOM row is missing')
    check(observed.persistedText === scenario.text, 'Persisted text differs')
    for (const text of scenario.summaryTexts ?? [scenario.text]) {
        check(observed.visibleText.includes(text), 'Complete visible text is missing')
    }
    check(observed.errors.length === 0, `Fixture errors: ${observed.errors.join('; ')}`)
    if (options.blockType === 'thinking') {
        check(observed.detailOpen && observed.domDetailOpen, 'Thinking details close during replacement')
        if (options.order !== 'item-before-start') check(observed.groupTransferred, 'Expanded group state does not transfer')
    }
    if (options.order !== 'item-before-start') {
        const matches = pair => pair.streamingLineNum === scenario.streamingLine && pair.realLineNum === scenario.realLine
        check(observed.retiredPairs.some(matches), 'Retired line pair is missing')
        check(observed.hookPairs.some(matches), 'Mounted scroll hook does not observe retirement')
    }
    check(observed.geometry, 'Scroll geometry is missing')
    if (options.position === 'bottom') check(observed.geometry.bottomGap <= 150, 'Bottom gap exceeds 150px')
    else {
        check(scenario.before?.anchor != null, 'Reading anchor is missing')
        if (options.order !== 'item-before-start') check(observed.scrollHookReads?.includes(scenario.streamingLine),
            'Mounted scroll retirement hook does not read the synthetic height')
        check(observed.geometry.anchor === scenario.before.anchor &&
            Math.abs(observed.geometry.anchorOffset - scenario.before.anchorOffset) <= 2, 'Reading anchor moves more than 2px')
    }
}

export async function runStreamRetirementScenario(adapter, input = {}) {
    const options = { blockType: 'text', order: 'end-before-item', position: 'bottom', loading: 'live', timeoutMs: 20000, ...input }
    const observations = [], pendingObservations = []
    let active = true, acceptanceError = null
    const invoke = async (name, ...args) => { check(active, 'Scenario deadline has expired'); return adapter[name](...args) }
    const outcome = await runBoundedScrollerAction(async () => {
        check(['text', 'thinking'].includes(options.blockType), 'Invalid block type')
        check(['item-before-end', 'end-before-item', 'item-before-start'].includes(options.order), 'Invalid event order')
        check(['bottom', 'reading-above'].includes(options.position), 'Invalid position')
        check(['live', 'slow-rest', 'failed-rest-retry'].includes(options.loading), 'Invalid loading mode')
        check(options.loading === 'live' || options.order === 'end-before-item', 'REST requires end-before-item')
        const scenario = await invoke('prepare', options)
        if (options.summaryParts > 1) check(scenario.summaryTexts?.length === options.summaryParts, 'Multipart summary is missing')
        const retain = async (stage = 'pending') => {
            const observed = await invoke('pending', scenario, { ...options, stage })
            pendingObservations.push(observed)
            check(observed.metadataRequests > 0 && observed.contentRequests > 0, 'Both REST routes must be observed')
            check(observed.retainedText === scenario.text, 'Deferred REST loses streaming text')
            if (options.blockType === 'thinking') check(observed.detailOpen && observed.groupOpen, 'Deferred REST loses detail/group state')
        }
        if (options.order === 'item-before-start') {
            await invoke('insert', scenario); await invoke('start', scenario); await invoke('end', scenario)
            scenario.before = await invoke('position', scenario, options)
        } else {
            await invoke('start', scenario); await invoke('feed', scenario); await invoke('open', scenario, options)
            scenario.before = await invoke('position', scenario, options)
            if (options.order === 'item-before-end') { await invoke('insert', scenario); await invoke('end', scenario) }
            else {
                await invoke('end', scenario)
                if (options.loading === 'live') await invoke('insert', scenario)
                else {
                    await invoke('beginRest', scenario, options); await retain()
                    if (options.loading === 'failed-rest-retry') {
                        await invoke('fail', scenario); await retain('failed'); await invoke('retry', scenario); await retain()
                    }
                    await invoke('resolve', scenario)
                }
            }
        }
        async function accept() {
            let lastError
            while (active) {
                const observed = await invoke('observe', scenario, options)
                observations.push(observed)
                try { acceptance(observed, scenario, options); return observed }
                catch (error) { lastError = error; acceptanceError = String(error) }
                await invoke('settle', 50)
                await new Promise(resolve => setTimeout(resolve, 10))
            }
            throw lastError ?? new Error('No observation before deadline')
        }
        const replacement = await accept()
        if (options.lateNewer) {
            await invoke('startNewer', scenario)
            const newerBefore = await invoke('observeNewer', scenario)
            const validateNewer = observed => {
                check(observed?.messageId && observed.messageId !== scenario.messageId, 'Current B identity is missing')
                check(observed.text && observed.domText?.includes(observed.text), 'Current B DOM text is missing')
                check(observed.bufferActive && observed.sameBlock && observed.samePublication, 'Current B buffer continuity is missing')
            }
            validateNewer(newerBefore)
            await invoke('lateStart', scenario)
            const newerAfter = await invoke('observeNewer', scenario)
            validateNewer(newerAfter)
            check(newerAfter.messageId === newerBefore.messageId && newerAfter.text === newerBefore.text,
                'Late A start changes current B identity or text')
            const expectedContinuedText = await invoke('continueNewer', scenario)
            const newerContinued = await invoke('observeNewer', scenario)
            validateNewer(newerContinued)
            check(newerContinued.messageId === newerBefore.messageId && newerContinued.text === expectedContinuedText && expectedContinuedText.startsWith(newerBefore.text),
                'Current B buffer loses text after late A start')
            delete scenario.newer
            return { scenario, replacement, newerBefore, newerAfter, newerContinued }
        }
        await invoke('lateStart', scenario)
        const lateStart = await accept()
        return { scenario, replacement, lateStart }
    }, { timeoutMs: options.timeoutMs })
    active = false
    return { ...outcome, options, observations, pendingObservations, acceptanceError }
}
