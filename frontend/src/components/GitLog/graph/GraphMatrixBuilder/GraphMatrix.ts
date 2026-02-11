import { isColumnEmpty } from '../utils/isColumnEmpty'
import { GraphMatrixColumns } from './GraphMatrixColumns'

export class GraphMatrix {
  private readonly _matrix = new Map<number, GraphMatrixColumns>
  private readonly _graphColumns: number

  constructor(graphColumns: number) {
    this._graphColumns = graphColumns
  }

  public getColumns(rowIndex: number) {
    return this._matrix.get(rowIndex) ?? this.emptyColumnState()
  }

  public setColumns(rowIndex: number, columns: GraphMatrixColumns) {
    this._matrix.set(rowIndex, columns)
  }

  public hasRowColumns(rowIndex: number) {
    return this._matrix.has(rowIndex)
  }

  public isColumnBelowEmpty(rowIndex: number, columnIndex: number) {
    return this._matrix.has(rowIndex + 1)
      ? isColumnEmpty(this._matrix.get(rowIndex + 1)!.columns[columnIndex])
      : false
  }

  public isColumnAboveEmpty(rowIndex: number, columnIndex: number) {
    return this._matrix.has(rowIndex - 1)
      ? isColumnEmpty(this._matrix.get(rowIndex - 1)!.columns[columnIndex])
      : false
  }

  public isColumnAboveBreakPoint(rowIndex: number, columnIndex: number) {
    return this._matrix.has(rowIndex - 1)
      ? this._matrix.get(rowIndex - 1)!.columns[columnIndex].isBottomBreakPoint
      : false
  }

  public hasCommitNodeAt(rowIndex: number, columnIndex: number) {
    return this._matrix.has(rowIndex)
      ? this._matrix.get(rowIndex)!.columns[columnIndex].isNode
      : false
  }

  public get value() {
    return this._matrix
  }

  private emptyColumnState() {
    return GraphMatrixColumns.empty(this._graphColumns)
  }
}
