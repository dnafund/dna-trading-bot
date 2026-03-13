/**
 * useConfig — hook for fetching and managing strategy config.
 * Loads current config, defaults, and validation rules from backend.
 * Provides update/reset methods with optimistic local state.
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { flattenToObject } from '../utils/flattenConfig'

const API_BASE = '/api'

export function useConfig(authFetch) {
  const doFetch = authFetch || fetch

  async function fetchJSON(url) {
    const res = await doFetch(url)
    const data = await res.json()
    if (!data.success) {
      throw new Error(data.error || 'API request failed')
    }
    return data.data
  }
  const [config, setConfig] = useState(null)
  const [serverConfig, setServerConfig] = useState(null)
  const [defaults, setDefaults] = useState(null)
  const [rules, setRules] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  // Load all config data on mount
  const loadConfig = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [configData, defaultsData, rulesData] = await Promise.all([
        fetchJSON(`${API_BASE}/config`),
        fetchJSON(`${API_BASE}/config/defaults`),
        fetchJSON(`${API_BASE}/config/rules`),
      ])
      setConfig(configData)
      setServerConfig(configData)
      setDefaults(defaultsData)
      setRules(rulesData)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadConfig()
  }, [loadConfig])

  // Update a single field locally (optimistic)
  // Special: key === '__full__' replaces the entire section (used by LEVERAGE)
  const updateField = useCallback((section, key, value) => {
    setConfig((prev) => {
      if (!prev) return prev

      // Full section replacement (e.g., LEVERAGE)
      if (key === '__full__') {
        return { ...prev, [section]: value }
      }

      const sectionData = prev[section]
      if (!sectionData) return prev

      // Handle nested keys (e.g., "h1.tp1_roi")
      const parts = key.split('.')
      if (parts.length === 1) {
        return {
          ...prev,
          [section]: { ...sectionData, [key]: value },
        }
      }

      // Deep nested update (immutable via structured clone)
      const newSection = JSON.parse(JSON.stringify(sectionData))
      let current = newSection
      for (let i = 0; i < parts.length - 1; i++) {
        current = current[parts[i]]
      }
      current[parts[parts.length - 1]] = value

      return { ...prev, [section]: newSection }
    })
  }, [])

  // Sections that use full-replacement diff (dynamic keys, not flattened)
  const FULL_REPLACE_SECTIONS = new Set(['LEVERAGE'])

  // Build diff: only changed values compared to last loaded server config (memoized)
  const changedValues = useMemo(() => {
    if (!config || !serverConfig) return {}

    const changes = {}

    for (const [section, sectionValues] of Object.entries(config)) {
      const serverSection = serverConfig[section]
      if (!serverSection) continue

      // Full-replacement sections: compare as JSON, send entire object
      if (FULL_REPLACE_SECTIONS.has(section)) {
        if (JSON.stringify(sectionValues) !== JSON.stringify(serverSection)) {
          changes[section] = sectionValues
        }
        continue
      }

      const flatCurrent = flattenToObject(sectionValues)
      const flatServer = flattenToObject(serverSection)

      const sectionChanges = {}
      for (const [key, val] of Object.entries(flatCurrent)) {
        if (flatServer[key] !== undefined) {
          const serverVal = flatServer[key]
          // Deep compare for arrays/objects, strict for primitives
          const isEqual = (Array.isArray(val) || typeof val === 'object')
            ? JSON.stringify(serverVal) === JSON.stringify(val)
            : serverVal === val
          if (!isEqual) {
            sectionChanges[key] = val
          }
        }
      }

      if (Object.keys(sectionChanges).length > 0) {
        changes[section] = sectionChanges
      }
    }

    return changes
  }, [config, serverConfig])

  // Count total changes (memoized, derived from changedValues)
  const changeCount = useMemo(() => {
    let count = 0
    for (const [section, sectionVal] of Object.entries(changedValues)) {
      if (FULL_REPLACE_SECTIONS.has(section)) {
        // For full-replacement sections, count individual key diffs
        const serverSection = serverConfig?.[section] || {}
        const allKeys = new Set([...Object.keys(sectionVal), ...Object.keys(serverSection)])
        for (const k of allKeys) {
          if (sectionVal[k] !== serverSection[k]) count++
        }
      } else {
        count += Object.keys(sectionVal).length
      }
    }
    return count
  }, [changedValues, serverConfig])

  // Save changes to backend
  const saveConfig = useCallback(async () => {
    if (!config || !defaults) return { success: false, error: 'Not loaded' }

    if (Object.keys(changedValues).length === 0) {
      return { success: true, message: 'No changes to save' }
    }

    setSaving(true)
    try {
      const res = await doFetch(`${API_BASE}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changedValues),
      })
      const result = await res.json()

      if (result.success) {
        // Reload config from server to ensure consistency
        await loadConfig()
      }

      return result
    } catch (err) {
      return { success: false, error: err.message }
    } finally {
      setSaving(false)
    }
  }, [config, defaults, changedValues, loadConfig])

  // Reset to defaults (all or specific sections)
  const resetConfig = useCallback(async (sections = null) => {
    setSaving(true)
    try {
      const res = await doFetch(`${API_BASE}/config/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sections }),
      })
      const result = await res.json()

      if (result.success) {
        await loadConfig()
      }

      return result
    } catch (err) {
      return { success: false, error: err.message }
    } finally {
      setSaving(false)
    }
  }, [loadConfig])

  // Discard local changes (reload from server)
  const discardChanges = useCallback(() => {
    loadConfig()
  }, [loadConfig])

  return {
    config,
    defaults,
    rules,
    loading,
    error,
    saving,
    changeCount,
    updateField,
    saveConfig,
    resetConfig,
    discardChanges,
    reload: loadConfig,
  }
}
