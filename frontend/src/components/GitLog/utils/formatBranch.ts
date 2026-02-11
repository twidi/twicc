export interface FormattedBranch {
  /** Display name (e.g. "main", "feature/foo") */
  name: string
  /** Whether this ref is a remote-tracking branch */
  isRemote: boolean
  /** Remote name if remote (e.g. "origin") */
  remote?: string
}

/**
 * Parse a full git ref into a display name, detecting remote branches.
 *
 * Examples:
 *   "refs/heads/main"                → { name: "main",        isRemote: false }
 *   "refs/remotes/origin/main"       → { name: "main",        isRemote: true, remote: "origin" }
 *   "refs/remotes/upstream/develop"  → { name: "develop",     isRemote: true, remote: "upstream" }
 *   "refs/tags/v1.0"                 → { name: "v1.0",        isRemote: false }
 */
export const parseBranch = (branchName: string): FormattedBranch => {
  // Remote branch: refs/remotes/<remote>/<branch-name>
  const remoteMatch = branchName.match(/^refs\/remotes\/([^/]+)\/(.+)$/)
  if (remoteMatch) {
    return { name: remoteMatch[2], isRemote: true, remote: remoteMatch[1] }
  }

  // Local branch or tag
  const name = branchName
    .replace('refs/heads/', '')
    .replace('refs/tags/', '')

  return { name, isRemote: false }
}

/**
 * Legacy helper — returns just the display name string.
 * Used by components that only need the short name.
 */
export const formatBranch = (branchName: string): string => {
  return parseBranch(branchName).name
}
