import test from 'node:test'
import assert from 'node:assert/strict'
import { scanKotlinSource, verifyFingerprints } from './check-android-i18n-source.mjs'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'

test('rawKotlinControlTextFails', () => {
    for (const source of [
        'button.setText("Save")', 'button.text = "Save"', 'contentDescription = "Open menu"',
        'dialog.setTitle("Title")', 'dialog.setMessage("Try again")',
        'dialog.setPositiveButton("Save", listener)', 'dialog.setNegativeButton("Cancel", null)',
        'dialog.setNeutralButton("Later", null)', 'menu.add(0, 1, 0, "Remove")',
        'Toast.makeText(context, "Failed", Toast.LENGTH_LONG)',
        'NotificationChannel("channel", "Speech", 1)',
        'Notification.Action.Builder(icon, "Stop", intent)',
        'TtsActionError("failure", "Try again")', 'mapOf("message" to "Failed")',
        'rejected("failure", "Try again")',
        'response.put("error", "Try again")', 'input.hint = "Name"',
        'button.text = """Save\nnow"""', 'dialog.setTitle(/* comment */ "Title")',
    ]) assert.ok(scanKotlinSource(source).length, source)
})

test('diagnosticAndUserContentDoNotFail', () => {
    const source = `
        // button.text = "comment"
        /* dialog.setTitle("comment") */
        Log.w("Tag", "Diagnostic")
        error("Internal invariant")
        button.text = instance.name
        dialog.setTitle(prompt.title)
        button.text = NativeMessages.text(context, UiMessage("common.actions.save"))
        TtsActionError("failure", "Try again", uiMessage = ttsUiMessage("android.error"))
        mapOf("message" to "Failed", "ui_message" to ttsUiMessage("android.error"))
        reply.put("error", "Failed").put("ui_message", UiMessageJson.encode(UiMessage("android.error")))
    `
    assert.deepEqual(scanKotlinSource(source), [])
    // An exclusion for an internal diagnostic must never exempt the same UI text.
    assert.ok(scanKotlinSource('button.text = "Diagnostic"').length)
})

test('generatedOutputEditsFailAndAllOutputsHaveFingerprints', async () => {
    const contents = await readFile(new URL('../../android/app/src/main/assets/i18n/sources.sha256', import.meta.url), 'utf8')
    for (const path of [
        'android/app/src/main/assets/i18n/messages.json', 'android/app/src/main/res/values/strings.xml',
        'android/app/src/test/resources/i18n/render-cases.json',
        'android/app/src/androidTest/generated/androidAppClient.js', 'android/app/src/androidTest/generated/nativeTtsClient.js',
    ]) {
        assert.ok(contents.includes(`  ${path}\n`), path)
        const failures = await verifyFingerprints(contents, input => input === path ? Buffer.from('changed')
            : readFile(new URL(`../../${input}`, import.meta.url)))
        assert.deepEqual(failures, [`Stale fingerprint: ${path}`])
    }
    assert.ok(!contents.includes('  android/app/src/main/assets/i18n/sources.sha256'))
})

test('generatedBrowserFixturesLoadTheirRealDependencies', async () => {
    for (const name of ['androidAppClient', 'nativeTtsClient']) {
        const source = await readFile(new URL(`../../android/app/src/androidTest/generated/${name}.js`, import.meta.url), 'utf8')
        const module = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
        assert.equal(typeof module[name === 'androidAppClient' ? 'createAndroidAppClient' : 'createNativeTtsClient'], 'function')
        if (name === 'androidAppClient') {
            const client = module.createAndroidAppClient({ addEventListener() {}, removeEventListener() {} })
            client.close()
            await assert.rejects(client.request('locale.get'), error =>
                error.uiMessage?.key === 'common.android.errors.closed' && error.message !== error.uiMessage.key)
        }
    }
})

test('changedCatalogFailsFingerprintCheck', async () => {
    const original = Buffer.from('{"message":"Save"}')
    const digest = createHash('sha256').update(original).digest('hex')
    const fingerprint = `${digest}  frontend/src/i18n/locales/en.json\n`
    const read = async () => original
    assert.deepEqual(await verifyFingerprints(fingerprint, read), [])
    assert.match((await verifyFingerprints(fingerprint, async () => Buffer.from('changed')))[0], /en.json/)
    assert.match((await verifyFingerprints(fingerprint, async () => { throw Error('missing') }))[0], /en.json/)
    assert.ok((await verifyFingerprints('', read)).length)
    assert.ok((await verifyFingerprints(`${digest}  ../escape\n`, read)).length)
})

test('notificationChannelDescriptionPropertyAndSetterFail', () => {
    for (const source of ['channel.description = "Owned description"', 'channel.setDescription("Owned description")']) {
        assert.equal(scanKotlinSource(source).length, 1, source)
    }
    assert.deepEqual(scanKotlinSource('channel.description = NativeMessages.text(context, UiMessage("android.channel.description"))'), [])
})
