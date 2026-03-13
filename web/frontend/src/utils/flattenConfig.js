/**
 * Flatten a nested config object into a flat key-value structure.
 * e.g., { h1: { tp1_roi: 40 } } → { "h1.tp1_roi": 40 }
 */
export function flattenToObject(obj, prefix = '') {
  const result = {}
  for (const [key, val] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}.${key}` : key
    if (val !== null && typeof val === 'object' && !Array.isArray(val)) {
      Object.assign(result, flattenToObject(val, fullKey))
    } else {
      result[fullKey] = val
    }
  }
  return result
}

/**
 * Flatten a nested config object into an array of [key, value] entries.
 */
export function flattenToEntries(obj, prefix = '') {
  return Object.entries(flattenToObject(obj, prefix))
}
