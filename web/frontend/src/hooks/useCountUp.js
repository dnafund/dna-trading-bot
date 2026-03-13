import { useState, useEffect, useRef } from 'react'

export function useCountUp(target, duration = 800) {
  const [value, setValue] = useState(0)
  const prevTarget = useRef(0)

  useEffect(() => {
    const start = prevTarget.current
    prevTarget.current = target

    if (typeof target !== 'number' || isNaN(target)) {
      setValue(target)
      return
    }

    const startTime = performance.now()

    const animate = (now) => {
      const progress = Math.min((now - startTime) / duration, 1)
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3)
      setValue(start + (target - start) * eased)
      if (progress < 1) requestAnimationFrame(animate)
    }

    requestAnimationFrame(animate)
  }, [target, duration])

  return value
}
