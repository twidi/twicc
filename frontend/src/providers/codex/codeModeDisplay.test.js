// Run with: node --test src/providers/codex/codeModeDisplay.test.js (from the frontend dir)
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
    IMAGE_GEN_TOOL_NAME,
    describeWebRun,
    extractInputImageUrls,
    resolveCodeModeCall,
    summarizeCodeModeCalls,
} from './codeModeDisplay.js'

test('resolves a code-mode update_plan call for Todo rendering', () => {
    const input = {
        input: 'const r = await tools.update_plan({plan:[{step:"Inspect",status:"in_progress"}],explanation:"Live check"}); text(JSON.stringify(r));',
    }
    const call = resolveCodeModeCall(input)

    assert.equal(call.name, 'update_plan')
    assert.deepEqual(call.arg, {
        plan: [{ step: 'Inspect', status: 'in_progress' }],
        explanation: 'Live check',
    })
})

test('describes search_query and image_query calls as Web search', () => {
    assert.deepEqual(describeWebRun({
        search_query: [{ q: 'Codex code mode' }, { q: 'Codex code mode' }],
        image_query: [{ q: 'TwiCC interface' }],
        response_length: 'short',
    }), {
        kind: 'search',
        summaryItems: ['Codex code mode', 'TwiCC interface'],
        operationKeys: ['search_query', 'image_query'],
    })
})

test('describes navigation calls as Web fetch with useful targets', () => {
    assert.deepEqual(describeWebRun({
        open: [{ ref_id: 'https://example.com' }, { ref_id: 'turn0search0' }],
        find: [{ ref_id: 'turn0search0', pattern: 'release notes' }],
        screenshot: [{ ref_id: 'turn0view0', pageno: 2 }],
    }), {
        kind: 'fetch',
        summaryItems: [
            'https://example.com',
            'turn0search0',
            'turn0search0 · release notes',
            'turn0view0 · page 3',
        ],
        operationKeys: ['open', 'find', 'screenshot'],
    })
})

test('uses a neutral Web description for compound or hosted-data calls', () => {
    assert.deepEqual(describeWebRun({
        search_query: [{ q: 'weather source' }],
        weather: [{ location: 'Paris' }],
    }), {
        kind: 'web',
        summaryItems: ['Search', 'Weather'],
        operationKeys: ['search_query', 'weather'],
    })
})

test('resolves supported web__run wrappers and rejects empty ones', () => {
    const resolved = resolveCodeModeCall({
        input: 'const r = await tools.web__run({search_query:[{q:"TwiCC"}],response_length:"short"}); text(JSON.stringify(r));',
    })
    assert.equal(resolved.name, 'web__run')
    assert.equal(describeWebRun(resolved.arg).kind, 'search')

    assert.equal(resolveCodeModeCall({
        input: 'await tools.web__run({response_length:"short"});',
    }), null)
})

test('keeps existing shell, patch, image, and MCP resolution intact', () => {
    assert.equal(resolveCodeModeCall('await tools.exec_command({cmd:"pwd"});').name, 'exec_command')
    assert.equal(resolveCodeModeCall('await tools.apply_patch("*** Begin Patch\\n*** End Patch");').name, 'apply_patch')
    assert.equal(resolveCodeModeCall('await tools.view_image({path:"/tmp/a.png"});').name, 'view_image')
    assert.equal(resolveCodeModeCall('await tools.mcp__demo__read({id:1});').name, 'mcp__demo__read')
})

test('resolves a code-mode image generation call and rejects a prompt-less one', () => {
    const call = resolveCodeModeCall('const r = await tools.image_gen__imagegen({prompt:"a blue square", size:"1024x1024"});')
    assert.equal(call.name, IMAGE_GEN_TOOL_NAME)
    assert.equal(call.arg.prompt, 'a blue square')
    assert.equal(resolveCodeModeCall('await tools.image_gen__imagegen({size:"1024x1024"});'), null)
    assert.equal(resolveCodeModeCall('await tools.image_gen__imagegen({prompt:"  "});'), null)
})

test('extracts input_image URLs from a single row or a chained row array', () => {
    const image = { type: 'input_image', image_url: 'data:image/png;base64,AAA' }
    const single = { type: 'function_call_output', output: [image, { type: 'input_text', text: 'saved to /x.png' }] }
    assert.deepEqual(extractInputImageUrls(single), ['data:image/png;base64,AAA'])

    // A wrapped imagegen that outlived yield_time_ms: three text-only chunks, then the image.
    const chained = [
        { type: 'custom_tool_call_output', output: 'Script running with cell ID 2' },
        null,
        { type: 'function_call_output', output: [{ type: 'input_text', text: 'Script running with cell ID 2' }] },
        { type: 'function_call_output', output: [
            { type: 'input_text', text: 'Script completed' },
            image,
            { type: 'input_image', image_url: 'data:image/png;base64,BBB' },
        ] },
    ]
    assert.deepEqual(extractInputImageUrls(chained), ['data:image/png;base64,AAA', 'data:image/png;base64,BBB'])

    assert.deepEqual(extractInputImageUrls({ output: 'plain text' }), [])
    assert.deepEqual(extractInputImageUrls(undefined), [])
    assert.deepEqual(extractInputImageUrls([{ output: [{ type: 'input_image', image_url: '' }] }]), [])
})

test('does not resolve multi-call or dynamic wrappers', () => {
    assert.equal(resolveCodeModeCall('await tools.update_plan(makePlan());'), null)
    assert.equal(resolveCodeModeCall('await tools.update_plan({plan:[]}); await tools.web__run({open:[{ref_id:"https://example.com"}]});'), null)
})

test('formats multi-call summaries through forced, MCP, and general tool-name rules', () => {
    const input = [
        'await tools.exec_command({cmd:"pwd"});',
        'await tools.exec_command({cmd:"ls"});',
        'await tools.apply_patch("*** Begin Patch\\n*** End Patch");',
        'await tools.mcp__chrome_devtools__list_pages({});',
        'await tools.request_user_input({questions:[]});',
    ].join(' ')

    assert.equal(summarizeCodeModeCalls(input, (name) => ({
        exec_command: 'Shell',
        apply_patch: 'Edit',
    })[name] ?? null), 'Shell ×2, Edit, MCP : Chrome devtools : List pages, Request user input')
})
