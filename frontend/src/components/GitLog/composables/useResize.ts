import { type Ref, onUnmounted, shallowRef } from 'vue'
import { useGitContext } from './useGitContext'

const MIN_WIDTH = 200
const MAX_WIDTH = 800

export interface ResizeState {
  width: Readonly<Ref<number>>
  ref: Ref<HTMLDivElement | null>
  startResizing: () => void
}

export function useResize(): ResizeState {
  const { graphWidth, setGraphWidth } = useGitContext()
  const graphContainerRef = shallowRef<HTMLDivElement | null>(null)

  function handleMouseMove(event: MouseEvent): void {
    const container = graphContainerRef.value
    if (!container) return

    const rect = container.getBoundingClientRect()
    const containerRight = rect.x + rect.width
    const newWidth = rect.width + (event.clientX - containerRight)

    if (newWidth > MIN_WIDTH && newWidth < MAX_WIDTH) {
      setGraphWidth(newWidth)
    }
  }

  function handleMouseUp(): void {
    window.removeEventListener('mousemove', handleMouseMove)
    window.removeEventListener('mouseup', handleMouseUp)
  }

  function startResizing(): void {
    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
  }

  onUnmounted(() => {
    window.removeEventListener('mousemove', handleMouseMove)
    window.removeEventListener('mouseup', handleMouseUp)
  })

  return {
    width: graphWidth,
    ref: graphContainerRef,
    startResizing,
  }
}
