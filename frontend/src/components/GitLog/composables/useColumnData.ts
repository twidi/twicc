import { computed, type Ref, type ComputedRef } from 'vue'
import { GraphMatrixBuilder } from '../graph/GraphMatrixBuilder'
import type { RowIndexToColumnStates } from './keys'
import { useGitContext } from './useGitContext'

export interface ColumnData {
  columnData: ComputedRef<RowIndexToColumnStates>
  virtualColumns: ComputedRef<number>
}

export function useColumnData(visibleCommits: Readonly<Ref<number>>): ColumnData {
  const {
    paging,
    headCommit,
    headCommitHash,
    isIndexVisible,
    isServerSidePaginated,
    graphData,
  } = useGitContext()

  const result = computed(() => {
    const { graphColumns, positions, edges, commits } = graphData.value

    const matrixBuilder = new GraphMatrixBuilder({
      commits,
      positions,
      headCommit: headCommit.value,
      graphColumns,
      visibleCommits: visibleCommits.value,
      headCommitHash: headCommitHash.value,
      isIndexVisible: isIndexVisible.value,
    })

    matrixBuilder.drawEdges(edges)
    positions.forEach(position => matrixBuilder.drawNode(position))
    matrixBuilder.checkPostRenderBreakPoints()
    matrixBuilder.drawIndexPseudoCommitEdge()

    if (isServerSidePaginated.value) {
      matrixBuilder.drawOffPageVirtualEdges()
    }

    const pagingValue = paging.value
    matrixBuilder.markFirstRow(pagingValue ? pagingValue.startIndex + 1 : 1)
    matrixBuilder.markLastRow(pagingValue ? pagingValue.endIndex : visibleCommits.value)

    return {
      rowToColumnState: matrixBuilder.matrix.value,
      virtualColumns: matrixBuilder.virtualColumns,
    }
  })

  const columnData = computed(() => result.value.rowToColumnState)
  const virtualColumns = computed(() => result.value.virtualColumns)

  return {
    columnData,
    virtualColumns,
  }
}
