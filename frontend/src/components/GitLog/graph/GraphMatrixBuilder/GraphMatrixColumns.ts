import type { GraphColumnState } from './types'
import { getEmptyColumnState } from '../utils/getEmptyColumnState'

export class GraphMatrixColumns {
  private readonly _columns: GraphColumnState[]

  constructor(columns: GraphColumnState[]) {
    this._columns = columns
  }

  public static empty(columnQuantity: number) {
    return new GraphMatrixColumns(getEmptyColumnState({
      columns: columnQuantity
    }))
  }

  public update(columnIndex: number, state: GraphColumnState) {
    this._columns[columnIndex] = {
      ...this._columns[columnIndex],
      ...state
    }
  }

  public hasCommitNode(index: number) {
    return this._columns[index].isNode
  }

  public get columns() {
    return this._columns
  }

  public get length() {
    return this._columns.length
  }
}
