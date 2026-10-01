import { test } from 'node:test'
import assert from 'node:assert/strict'
import { baseName, collapseLeadingSlashes, displayTargetDir, findTreeNode, pathContains } from './paths.js'

test('leading slashes collapse to one (//home/u under the / root)', () => {
    assert.equal(collapseLeadingSlashes('//home/u'), '/home/u')
    assert.equal(collapseLeadingSlashes('///x//y'), '/x//y')
    assert.equal(collapseLeadingSlashes('/a/b'), '/a/b')
})

test('pathContains: equal, inside, sibling prefix, root /', () => {
    assert.equal(pathContains('/p', '/p'), true)
    assert.equal(pathContains('/p', '/p/a/b'), true)
    assert.equal(pathContains('/p', '/pp/a'), false)
    assert.equal(pathContains('/', '/anything/x'), true)
    assert.equal(pathContains('/', 'relative'), false)
})

const tree = {
    name: 'p', type: 'directory', loaded: true,
    children: [
        { name: 'a', type: 'directory', loaded: true, children: [{ name: 'f.txt', type: 'file' }] },
        { name: 'stub', type: 'directory', loaded: false },
    ],
}

test('findTreeNode walks from the root node', () => {
    assert.equal(findTreeNode(tree, '/p', '/p'), tree)
    assert.equal(findTreeNode(tree, '/p', '/p/a'), tree.children[0])
    assert.equal(findTreeNode(tree, '/p', '/p/a/f.txt').name, 'f.txt')
    assert.equal(findTreeNode(tree, '/p', '/p/missing'), null)
    assert.equal(findTreeNode(tree, '/p', '/p/stub/deeper'), null)
    assert.equal(findTreeNode(tree, '/p', '/other'), null)
    assert.equal(findTreeNode(null, '/p', '/p'), null)
})

test('findTreeNode under the / root ignores empty segments', () => {
    const rootTree = { name: '/', type: 'directory', loaded: true, children: [
        { name: 'home', type: 'directory', loaded: true, children: [{ name: 'u', type: 'directory', loaded: true, children: [] }] },
    ] }
    const u = rootTree.children[0].children[0]
    assert.equal(findTreeNode(rootTree, '/', '/home/u'), u)
    assert.equal(findTreeNode(rootTree, '/', '//home/u'), u)
    assert.equal(findTreeNode(rootTree, '/', '/home//u/'), u)
    assert.equal(findTreeNode(rootTree, '/', '/'), rootTree)
})

test('displayTargetDir is relative to the panel root when inside it', () => {
    assert.equal(displayTargetDir('/p/a/b', '/p'), 'a/b')
    assert.equal(displayTargetDir('/p', '/p'), '.')
    assert.equal(displayTargetDir('/q/a', '/p'), '/q/a')
    assert.equal(displayTargetDir('/home/u', '/'), 'home/u')
    assert.equal(displayTargetDir('/q/a', null), '/q/a')
})

test('baseName returns the last segment', () => {
    assert.equal(baseName('/p/a (1).txt'), 'a (1).txt')
    assert.equal(baseName(''), '')
})
