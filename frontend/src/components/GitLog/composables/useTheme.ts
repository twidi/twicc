import { computed } from 'vue'
import type { Commit, GetCommitNodeColoursArgs, ThemeFunctions } from '../types'
import { useThemeContext } from './useThemeContext'
import { useGitContext } from './useGitContext'

export function useTheme(): ThemeFunctions {
  const { theme: themeRef, colours: coloursRef } = useThemeContext()
  const { graphData } = useGitContext()

  const theme = computed(() => themeRef.value)

  const hoverColour = computed(() => {
    if (themeRef.value === 'dark') {
      return 'rgba(70,70,70,0.8)'
    }
    return 'rgba(231, 231, 231, 0.5)'
  })

  const textColour = computed(() => {
    if (themeRef.value === 'dark') {
      return 'rgb(255, 255, 255)'
    }
    return 'rgb(0, 0, 0)'
  })

  function shiftAlphaChannel(rgb: string, opacity: number): string {
    const matches = rgb?.match(/\d+/g)

    if (rgb && matches != null) {
      const [r_fg, g_fg, b_fg] = matches.map(Number)
      const backgroundRgb = themeRef.value === 'dark' ? 'rgb(0, 0, 0)' : 'rgb(255, 255, 255)'
      const [r_bg, g_bg, b_bg] = backgroundRgb.match(/\d+/g)!.map(Number)

      const r_new = Math.round(r_fg * opacity + r_bg * (1 - opacity))
      const g_new = Math.round(g_fg * opacity + g_bg * (1 - opacity))
      const b_new = Math.round(b_fg * opacity + b_bg * (1 - opacity))

      return `rgb(${r_new}, ${g_new}, ${b_new})`
    }

    return rgb
  }

  function reduceOpacity(rgb: string, opacity: number): string {
    return `rgba(${rgb?.replace('rgb(', '').replace(')', '')}, ${opacity})`
  }

  function getGraphColumnColour(columnIndex: number): string {
    const colours = coloursRef.value
    const colour = colours[columnIndex]

    if (!colour) {
      return colours[columnIndex % colours.length]
    }

    return colour
  }

  function getCommitColour(commit: Commit): string {
    if (commit.hash === 'index') {
      return getGraphColumnColour(0)
    }

    const position = graphData.value.positions.get(commit.hash)

    if (position) {
      const columnIndex = position[1]
      return getGraphColumnColour(columnIndex)
    }

    console.warn(`Commit ${commit.hash} did not have a mapped graph position`)
    return 'rgb(0, 0, 0)'
  }

  function getCommitNodeColours(args: GetCommitNodeColoursArgs) {
    return {
      backgroundColour: shiftAlphaChannel(args.columnColour, 0.15),
      borderColour: args.columnColour,
    }
  }

  function getGraphColumnSelectedBackgroundColour(columnIndex: number): string {
    return reduceOpacity(getGraphColumnColour(columnIndex), 0.15)
  }

  function getTooltipBackground(commit: Commit): string {
    if (themeRef.value === 'dark') {
      return shiftAlphaChannel(getCommitColour(commit), 0.2)
    }
    return shiftAlphaChannel(getCommitColour(commit), 0.1)
  }

  return {
    theme,
    hoverColour,
    textColour,
    hoverTransitionDuration: 0.3,
    shiftAlphaChannel,
    reduceOpacity,
    getGraphColumnColour,
    getCommitColour,
    getCommitNodeColours,
    getGraphColumnSelectedBackgroundColour,
    getTooltipBackground,
  }
}
