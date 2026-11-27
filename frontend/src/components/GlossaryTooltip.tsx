import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ArrowRight, HelpCircle } from 'lucide-react'
import { GLOSSARY_TERMS, type GlossaryTermId } from '../config/glossaryTerms'

export interface GlossaryTooltipProps {
  termId: GlossaryTermId
  onNavigateToGuide?: (anchorId: string) => void
  title?: string
  summary?: string
  className?: string
  iconClassName?: string
}

interface PopoverCoords {
  top: number
  left: number
  placement: 'top' | 'bottom'
}

const POPOVER_WIDTH = 288
const VIEWPORT_PADDING = 12
const TRIGGER_GAP = 8

export function GlossaryTooltip({
  termId,
  onNavigateToGuide,
  title,
  summary,
  className = '',
  iconClassName = 'h-3.5 w-3.5',
}: GlossaryTooltipProps) {
  const term = GLOSSARY_TERMS[termId]
  const tooltipId = useId()
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const popoverRef = useRef<HTMLDivElement | null>(null)
  const openTimerRef = useRef<number | null>(null)
  const closeTimerRef = useRef<number | null>(null)

  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [coords, setCoords] = useState<PopoverCoords | null>(null)

  const clearTimers = useCallback(() => {
    if (openTimerRef.current !== null) {
      window.clearTimeout(openTimerRef.current)
      openTimerRef.current = null
    }
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
  }, [])

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const popoverHeight = popoverRef.current?.offsetHeight ?? 130
    const vw = window.innerWidth
    const vh = window.innerHeight

    let left = rect.left + rect.width / 2 - POPOVER_WIDTH / 2
    left = Math.max(VIEWPORT_PADDING, Math.min(left, vw - POPOVER_WIDTH - VIEWPORT_PADDING))

    const spaceBelow = vh - rect.bottom - TRIGGER_GAP
    const spaceAbove = rect.top - TRIGGER_GAP
    const placeTop = spaceBelow < popoverHeight + VIEWPORT_PADDING && spaceAbove > spaceBelow

    const top = placeTop
      ? Math.max(VIEWPORT_PADDING, rect.top - popoverHeight - TRIGGER_GAP)
      : Math.min(vh - popoverHeight - VIEWPORT_PADDING, rect.bottom + TRIGGER_GAP)

    setCoords({
      top,
      left,
      placement: placeTop ? 'top' : 'bottom',
    })
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    updatePosition()
    const raf = window.requestAnimationFrame(updatePosition)
    return () => window.cancelAnimationFrame(raf)
  }, [open, updatePosition])

  useEffect(() => {
    if (!open) return

    const handlePointerDown = (e: MouseEvent) => {
      const target = e.target as Node
      if (triggerRef.current?.contains(target) || popoverRef.current?.contains(target)) {
        return
      }
      setOpen(false)
      setPinned(false)
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        setPinned(false)
      }
    }

    const handleScrollOrResize = () => {
      updatePosition()
    }

    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    window.addEventListener('scroll', handleScrollOrResize, true)
    window.addEventListener('resize', handleScrollOrResize)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('scroll', handleScrollOrResize, true)
      window.removeEventListener('resize', handleScrollOrResize)
    }
  }, [open, updatePosition])

  useEffect(() => clearTimers, [clearTimers])

  const scheduleOpen = () => {
    clearTimers()
    openTimerRef.current = window.setTimeout(() => {
      setOpen(true)
    }, 100)
  }

  const scheduleClose = () => {
    if (pinned) return
    clearTimers()
    closeTimerRef.current = window.setTimeout(() => {
      setOpen(false)
    }, 160)
  }

  const handleToggleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault()
    e.stopPropagation()
    clearTimers()
    if (open && pinned) {
      setOpen(false)
      setPinned(false)
    } else {
      setOpen(true)
      setPinned(true)
    }
  }

  const handleGuideJump = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault()
    e.stopPropagation()
    clearTimers()
    setOpen(false)
    setPinned(false)
    onNavigateToGuide?.(term.anchorId)
  }

  const displayTitle = title ?? term.title
  const displaySummary = summary ?? term.summary

  return (
    <span className={`inline-flex items-center align-middle ${className}`}>
      <button
        ref={triggerRef}
        type="button"
        aria-label={`查看「${displayTitle}」说明`}
        aria-expanded={open}
        aria-describedby={open ? tooltipId : undefined}
        onClick={handleToggleClick}
        onMouseEnter={scheduleOpen}
        onMouseLeave={scheduleClose}
        onFocus={scheduleOpen}
        onBlur={scheduleClose}
        className={`inline-flex items-center justify-center rounded-full p-0.5 transition focus:outline-none focus:ring-1 focus:ring-amber-400/60 ${
          open
            ? 'text-amber-300'
            : 'text-slate-500 hover:text-amber-300'
        }`}
      >
        <HelpCircle className={iconClassName} />
      </button>

      {open &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            ref={popoverRef}
            id={tooltipId}
            role="tooltip"
            onMouseEnter={clearTimers}
            onMouseLeave={scheduleClose}
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'fixed',
              top: coords ? `${coords.top}px` : '-9999px',
              left: coords ? `${coords.left}px` : '-9999px',
              width: `${POPOVER_WIDTH}px`,
            }}
            className="z-[80] rounded-xl border border-slate-700/90 bg-slate-900/95 p-3 text-left shadow-2xl shadow-black/60 backdrop-blur-md"
          >
            <div className="text-xs font-semibold text-amber-300">{displayTitle}</div>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-300">{displaySummary}</p>
            {onNavigateToGuide && (
              <div className="mt-2.5 border-t border-slate-800 pt-2">
                <button
                  type="button"
                  onClick={handleGuideJump}
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-amber-400 transition hover:text-amber-300"
                >
                  查看产品说明
                  <ArrowRight className="h-3 w-3" />
                </button>
              </div>
            )}
          </div>,
          document.body,
        )}
    </span>
  )
}
