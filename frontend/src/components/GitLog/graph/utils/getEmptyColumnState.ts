import type { GraphColumnState } from '../GraphMatrixBuilder/types'

export const getEmptyColumnState = ({ columns }: { columns: number }) => {
  return new Array<GraphColumnState>(columns).fill({})
}
