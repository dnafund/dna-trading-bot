/**
 * useBacktestConfig — local-only config state for backtest page.
 * Loads live config as starting values, tracks local overrides,
 * and returns a diff (overrides only) for the backtest API call.
 * Never saves to the server.
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { flattenToObject } from '../utils/flattenConfig'

const API_BASE = '/api'

const FULL_REPLACE_SECTIONS = new Set(['LEVERAGE'])

export function useBacktestConfig(authFetch) {
  const doFetch = authFetch || fetch

  const [config, setConfig] = useState(null)
  const [liveConfig, setLiveConfig] = useState(null)
  const [defaults, setDefaults] = useState(null)
  const [rules, setRules] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  async function fetchJSON(url) {
    const res = await doFetch(url)
    const data = await res.json()
    if (!data.success) throw new Error(data.error || 'API request failed')
    return data.data
  }

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
      setLiveConfig(configData)
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

  // Update a single field locally (same pattern as useConfig)
  const updateField = useCallback((section, key, value) => {
    setConfig((prev) => {
      if (!prev) return prev

      if (key === '__full__') {
        return { ...prev, [section]: value }
      }

      const sectionData = prev[section]
      if (!sectionData) return prev

      const parts = key.split('.')
      if (parts.length === 1) {
        return {
          ...prev,
          [section]: { ...sectionData, [key]: value },
        }
      }

      const newSection = JSON.parse(JSON.stringify(sectionData))
      let current = newSection
      for (let i = 0; i < parts.length - 1; i++) {
        current = current[parts[i]]
      }
      current[parts[parts.length - 1]] = value

      return { ...prev, [section]: newSection }
    })
  }, [])

  // Diff: only fields different from live config
  const changedValues = useMemo(() => {
    if (!config || !liveConfig) return {}

    const changes = {}

    for (const [section, sectionValues] of Object.entries(config)) {
      const liveSection = liveConfig[section]
      if (!liveSection) continue

      if (FULL_REPLACE_SECTIONS.has(section)) {
        if (JSON.stringify(sectionValues) !== JSON.stringify(liveSection)) {
          changes[section] = sectionValues
        }
        continue
      }

      const flatCurrent = flattenToObject(sectionValues)
      const flatLive = flattenToObject(liveSection)

      const sectionChanges = {}
      for (const [key, val] of Object.entries(flatCurrent)) {
        if (flatLive[key] !== undefined) {
          const liveVal = flatLive[key]
          const isEqual = (Array.isArray(val) || typeof val === 'object')
            ? JSON.stringify(liveVal) === JSON.stringify(val)
            : liveVal === val
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
  }, [config, liveConfig])

  const changeCount = useMemo(() => {
    let count = 0
    for (const [section, sectionVal] of Object.entries(changedValues)) {
      if (FULL_REPLACE_SECTIONS.has(section)) {
        const liveSection = liveConfig?.[section] || {}
        const allKeys = new Set([...Object.keys(sectionVal), ...Object.keys(liveSection)])
        for (const k of allKeys) {
          if (sectionVal[k] !== liveSection[k]) count++
        }
      } else {
        count += Object.keys(sectionVal).length
      }
    }
    return count
  }, [changedValues, liveConfig])

  // Return overrides for API call (null if no changes)
  const getOverrides = useCallback(() => {
    if (Object.keys(changedValues).length === 0) return null

    // Unflatten dotted keys back to nested objects for the backend
    const overrides = {}
    for (const [section, sectionVal] of Object.entries(changedValues)) {
      if (FULL_REPLACE_SECTIONS.has(section)) {
        overrides[section] = sectionVal
        continue
      }

      const nested = {}
      for (const [key, val] of Object.entries(sectionVal)) {
        const parts = key.split('.')
        if (parts.length === 1) {
          nested[key] = val
        } else {
          if (!nested[parts[0]]) nested[parts[0]] = {}
          nested[parts[0]][parts[1]] = val
        }
      }
      overrides[section] = nested
    }

    return overrides
  }, [changedValues])

  // Reset to live config (re-fetch from server)
  const resetToLive = useCallback(async () => {
    await loadConfig()
  }, [loadConfig])

  // Discard local changes without re-fetching
  const discardChanges = useCallback(() => {
    if (liveConfig) {
      setConfig(JSON.parse(JSON.stringify(liveConfig)))
    }
  }, [liveConfig])

  return {
    config,
    liveConfig,
    defaults,
    rules,
    loading,
    error,
    changeCount,
    updateField,
    getOverrides,
    resetToLive,
    discardChanges,
  }
}
