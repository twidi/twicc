// Shared fixture logic. This module never renders Markdown or supplies rendered HTML.
const browserClock = { now: () => performance.now(), sleep: delay => new Promise(resolve => setTimeout(resolve, delay)) }
export async function feedMarkdownSnapshots(snapshot, apply, clock = browserClock) {
    const start = clock.now()
    const times = []
    for (let index = 0; index < 100; index++) {
        const deadline = index * 20
        const delay = start + deadline - clock.now()
        if (delay > 0) await clock.sleep(delay)
        const tuple = { ...snapshot(index) }
        times.push({ index, deadline, at: clock.now() - start, absoluteAt: clock.now(), ...tuple })
        apply(tuple)
    }
    return times
}
export async function waitForMarkdownCondition(condition, { timeout = 15000, clock = browserClock } = {}) {
    const start = clock.now()
    while (true) {
        if (await condition()) return
        const remaining = timeout - (clock.now() - start)
        if (remaining <= 0) throw new Error(`Timed out after ${timeout} ms; rendering acceptance is failed/inconclusive`)
        await clock.sleep(Math.min(20, remaining))
    }
}
export function createMarkdownMetrics(now = () => performance.now()) {
    const report = { active: 0, peakActive: 0, starts: [], finishes: [], parsed: [], blocks: [], mermaid: [], commits: [], emits: [] }
    const active = new Set()
    const sink = (kind, value) => {
        const at = now()
        if (kind === 'document-start') {
            const token = { id: report.starts.length + 1, at, ...value }
            active.add(token)
            report.active = active.size
            report.peakActive = Math.max(report.peakActive, report.active)
            report.starts.push({ ...token })
            return token
        }
        if (kind === 'document-finish') {
            if (!active.delete(value)) throw new Error('Unknown document operation finish')
            report.active = active.size
            report.finishes.push({ id: value.id, at })
        } else if (kind === 'parse') report.parsed.push({ at, source: value })
        else {
            const field = { block: 'blocks', mermaid: 'mermaid', commit: 'commits', emit: 'emits' }[kind]
            if (!field) throw new Error(`Unknown metric: ${kind}`)
            report[field].push({ at, ...value })
        }
    }
    return { report, sink }
}
const tuple = (source, slashTag = false) => ({ source, theme: 'default', slashTag })
const marker = index => `Final marker ${index}.`
const compatibility = `# Compatibility\n\n- list one\n- list two\n\n| Name | Value |\n| --- | --- |\n| sample | value |\n\n::: comment on selected text\n> quoted text\ncomment body\n:::\n\n:: note line\n\n<!-- hidden fixture comment -->\n\n<script>window.fixtureUnsafe=true</script>\n\n[File](src/example.py:12)\n\n![shared image](/artifacts/fixture/image.png)\n\n![unavailable image](/artifacts/fixture/private.png)\n\n\`\`\`markdown\n# Nested heading\n\nNested content.\n\`\`\``
export const markdownScenarios = {
    paragraphs: { snapshot: index => tuple(Array.from({ length: index + 1 }, (_, n) => `Paragraph ${n}. Readable content.`).join('\n\n') + `\n\n${marker(index)}`) },
    code: { snapshot: index => tuple(`\`\`\`javascript\n${Array.from({ length: index + 1 }, (_, n) => `const value${n} = ${n}`).join('\n')}\n\`\`\`\n\n${marker(index)}`) },
    mermaid: { snapshot: index => tuple([0, 1, 2].map(n => `\`\`\`mermaid\ngraph TD; A${n}[Start ${index}]-->B${n}[End];\n\`\`\``).join('\n\n') + `\n\n${marker(index)}`) },
    references: { snapshot: index => tuple(`[Reference][target]\n\n${marker(index)}\n\n[target]: https://example.com/${index === 99 ? 'latest' : `step-${index}`} "${index === 99 ? 'latest title' : `title ${index}`}"`) },
    slash: { snapshot: index => tuple('/review Inspect this source.\n\nFinal marker 99.', index % 2 === 1) },
    empty: { snapshot: index => tuple(index === 99 ? '' : `Previous complete source. ${marker(index)}`) },
    compatibility: { snapshot: index => tuple(`${compatibility}\n\n${marker(index)}`) },
}
function requireOutput(condition, message) { if (!condition) throw new Error(message) }
export function inspectMarkdownOutput(root, scenario) {
    requireOutput(root, 'Rendered Markdown root is missing')
    const evidence = { text: root.textContent, html: root.innerHTML,
        links: [...root.querySelectorAll('a')].map(a => ({ text: a.textContent, href: a.getAttribute('href'), title: a.getAttribute('title'), fileCandidates: a.getAttribute('data-file-candidates'), line: a.getAttribute('data-file-line') })),
        diagrams: root.querySelectorAll('.mermaid-diagram svg').length,
        codeBlocks: root.querySelectorAll('pre.shiki').length,
        tools: root.querySelectorAll('.code-tools .block-tools-bar').length }
    if (scenario === 'empty') requireOutput(root.childElementCount === 0 && root.textContent === '', 'Empty final source leaves rendered content')
    else requireOutput(root.textContent.includes('Final marker 99.'), 'Final DOM misses the independent final marker')
    if (scenario === 'code') requireOutput(evidence.codeBlocks === 1 && root.textContent.includes('const value99 = 99') && evidence.tools === 1, 'Final highlighted code or tools are missing')
    if (scenario === 'mermaid') requireOutput(evidence.diagrams === 3, 'Three final Mermaid SVG diagrams are required')
    if (scenario === 'references') {
        const link = evidence.links.find(a => a.text === 'Reference')
        requireOutput(link?.href === 'https://example.com/latest' && link?.title === 'latest title', 'Final reference href/title is stale')
    }
    if (scenario === 'slash') requireOutput(root.querySelector('.slash-command-tag')?.textContent === '/review', 'Final slash-command mode is stale')
    if (scenario === 'compatibility') {
        requireOutput(root.querySelectorAll('li').length === 2 && root.querySelector('table tbody td')?.textContent === 'sample', 'Lists or tables fail')
        requireOutput(root.querySelector('.md-container-comment')?.textContent.includes('comment body') && root.querySelector('.md-line'), 'Colon blocks or comments fail')
        requireOutput(!root.querySelector('script, [onclick], [onerror]'), 'Sanitization leaves executable elements')
        requireOutput(!root.innerHTML.includes('<!-- hidden fixture comment -->'), 'HTML comment remains')
        requireOutput(root.querySelector('a[data-file-line="12"]'), 'File-link annotation fails')
        requireOutput(root.querySelector('img[alt="shared image"]')?.getAttribute('src') === '/share/fixture/media/image.png', 'Share media rewrite fails')
        requireOutput(root.querySelector('img[alt="unavailable image"]')?.hasAttribute('data-media-unavailable'), 'Share unavailable-media compatibility fails')
        requireOutput(root.querySelector('.code-tools [data-block-action="view"]'), 'Nested Markdown view tool is missing')
    }
    return evidence
}
