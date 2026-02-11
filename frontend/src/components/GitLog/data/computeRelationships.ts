import type { Commit, GitLogEntry } from '../types'

export const computeRelationships = <T>(
  entries: GitLogEntry<T>[],
  headCommitHash?: string
) => {
  const children = new Map<string, string[]>()
  const parents = new Map<string, string[]>()
  const hashToEntry = new Map<string, GitLogEntry<T>>()

  entries.forEach(entry => {
    children.set(entry.hash, [])
    hashToEntry.set(entry.hash, entry)
  })

  entries.forEach(entry => {
    const hash = entry.hash
    const parentHashes = entry.parents
    parents.set(hash, parentHashes)

    parentHashes.forEach(parentHash => {
      const currentChildren = children.get(parentHash)
      if (currentChildren) {
        currentChildren.push(hash)
      }
    })
  })

  // Track the first (most recent) commit seen for each branch name.
  // This ensures every branch has its name displayed in the Tags column,
  // even when the branch tip has children (e.g. another branch forked from it).
  const branchesSeen = new Set<string>()
  const branchTipHashes = new Set<string>()
  for (const entry of entries) {
    if (entry.branch && !branchesSeen.has(entry.branch)) {
      branchesSeen.add(entry.branch)
      branchTipHashes.add(entry.hash)
    }
  }

  const hashToCommit = new Map<string, Commit<T>>()

  for (const [hash, entry] of hashToEntry) {
    const isLeaf = (children.get(hash)?.length ?? 0) === 0
    const isFirstOfBranch = branchTipHashes.has(hash)

    hashToCommit.set(hash, {
      ...entry,
      children: children.get(hash) ?? [],
      isBranchTip: headCommitHash
        ? hash === headCommitHash || isFirstOfBranch
        : isLeaf || isFirstOfBranch
    } as Commit<T>)
  }

  return { parents, children, hashToCommit }
}
