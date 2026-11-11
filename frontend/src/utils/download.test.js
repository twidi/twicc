import test from 'node:test'
import assert from 'node:assert/strict'
import * as downloads from './download.js'

test('directory download URLs preserve scope and encode paths', () => {
    assert.equal(typeof downloads.buildDirectoryDownloadUrl, 'function')
    const directoryPath = '/repo/été #?%&'
    for (const apiPrefix of ['/api/projects/p', '/api/projects/p/sessions/s']) {
        assert.equal(downloads.buildDirectoryDownloadUrl({ directoryPath, projectId: 'p', apiPrefix, root: '/ignored' }),
            `${apiPrefix}/directory-download/?path=%2Frepo%2F%C3%A9t%C3%A9+%23%3F%25%26`)
    }
    assert.equal(downloads.buildDirectoryDownloadUrl({ directoryPath, projectId: null, apiPrefix: '/ignored', root: '/racine/雪 #?%&' }),
        '/api/directory-download/?path=%2Frepo%2F%C3%A9t%C3%A9+%23%3F%25%26&root=%2Fracine%2F%E9%9B%AA+%23%3F%25%26')
    assert.equal(downloads.buildDirectoryDownloadUrl({ directoryPath: '/', root: '' }),
        '/api/directory-download/?path=%2F&root=')
    for (const directoryPath of ['', null, undefined]) {
        assert.equal(downloads.buildDirectoryDownloadUrl({ directoryPath }), null)
    }
})

test('ordinary file download URLs retain their raw endpoints', () => {
    assert.equal(downloads.buildFileDownloadUrl({ filePath: '/repo/a #?.txt', projectId: 'p', apiPrefix: '/api/projects/p' }),
        '/api/projects/p/file-raw/repo/a%20%23%3F.txt?download=1')
    assert.equal(downloads.buildFileDownloadUrl({ filePath: '/repo/a.txt', root: '/repo' }),
        '/api/file-raw/L3JlcG8/repo/a.txt?download=1')
    assert.equal(downloads.buildFileDownloadUrl({ filePath: '' }), null)
})
