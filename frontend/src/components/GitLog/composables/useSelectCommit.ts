import type { Commit } from '../types'
import { useGitContext } from './useGitContext'

export interface SelectCommitHandler {
  selectCommitHandler: {
    onMouseOver: (commit: Commit) => void
    onMouseOut: () => void
    onClick: (commit: Commit) => void
  }
}

export function useSelectCommit(): SelectCommitHandler {
  const { selectedCommit, previewedCommit, setPreviewedCommit, setSelectedCommit } = useGitContext()

  function handleMouseOver(commit: Commit): void {
    if (commit.hash !== selectedCommit.value?.hash) {
      setPreviewedCommit(commit)
    }
  }

  function handleMouseOut(): void {
    setPreviewedCommit(undefined)
  }

  function handleClickCommit(commit: Commit): void {
    if (commit.hash === previewedCommit.value?.hash) {
      setPreviewedCommit(undefined)
    }

    if (selectedCommit.value?.hash === commit.hash) {
      setSelectedCommit(undefined)
    } else {
      setSelectedCommit(commit)
    }
  }

  return {
    selectCommitHandler: {
      onMouseOver: handleMouseOver,
      onMouseOut: handleMouseOut,
      onClick: handleClickCommit,
    },
  }
}
