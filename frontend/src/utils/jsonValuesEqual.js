/** Compare JSON values. Object key order does not affect equality. */
export function jsonValuesEqual(left, right) {
    if (left === right) return true
    if (left === null || right === null || typeof left !== 'object' || typeof right !== 'object') return false
    if (Array.isArray(left) !== Array.isArray(right)) return false
    const keys = Object.keys(left)
    if (keys.length !== Object.keys(right).length) return false
    return keys.every(key => Object.hasOwn(right, key) && jsonValuesEqual(left[key], right[key]))
}
