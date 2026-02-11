/**
 * Converts a pixel value to a rem string.
 *
 * All layout values are expressed as numbers calibrated for a root
 * font-size of 16px (1rem = 16px). This helper turns the final
 * computed number into a CSS rem value so the component scales
 * proportionally when the root font-size changes.
 *
 * @example
 * pxToRem(16)  // '1rem'
 * pxToRem(20)  // '1.25rem'
 * pxToRem(0)   // '0rem'
 */
export function pxToRem(px: number): string {
  return `${px / 16}rem`
}
