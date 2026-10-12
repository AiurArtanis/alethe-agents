import { Check, ChevronDown } from 'lucide-react'
import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'
import { createPortal } from 'react-dom'

import styles from './RowSelect.module.css'

export type RowSelectOption = {
  value: string
  title: string
  description?: string
  icon?: ReactNode
}

type Props = {
  value: string
  options: RowSelectOption[]
  onChange: (value: string) => void
  ariaLabel: string
  field: string
  icon?: ReactNode
  title: string
  side?: string
  disabled?: boolean
  autoFocus?: boolean
}

export function RowSelect({
  value,
  options,
  onChange,
  ariaLabel,
  field,
  icon,
  title,
  side,
  disabled = false,
  autoFocus = false,
}: Props) {
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [position, setPosition] = useState({ left: 0, top: 0, width: 260, maxHeight: 280 })
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const listboxId = useId()
  const optionsKey = options.map((option) => option.value).join('|')

  const close = (restoreFocus = false) => {
    setOpen(false)
    setActiveIndex(-1)
    if (restoreFocus) window.requestAnimationFrame(() => triggerRef.current?.focus())
  }

  useLayoutEffect(() => {
    if (!open) return
    const updatePosition = () => {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (!rect) return
      const availableWidth = Math.max(0, window.innerWidth - 16)
      const width = Math.min(rect.width, availableWidth)
      const estimated = Math.min(320, options.length * 52 + 10)
      const spaceBelow = window.innerHeight - rect.bottom - 10
      const spaceAbove = rect.top - 10
      const opensBelow = spaceBelow >= Math.min(estimated, 200) || spaceBelow >= spaceAbove
      const maxHeight = Math.min(320, Math.max(40, opensBelow ? spaceBelow : spaceAbove))
      const top = opensBelow ? rect.bottom + 6 : rect.top - Math.min(estimated, maxHeight) - 6
      const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))
      setPosition({ left, top: Math.max(8, top), width, maxHeight })
    }
    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open, options.length])

  useEffect(() => {
    if (!open) return
    const selectedIndex = options.findIndex((option) => option.value === value)
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : options.length > 0 ? 0 : -1)
  }, [open, optionsKey, value])

  useEffect(() => {
    if (!disabled || !open) return
    setOpen(false)
    setActiveIndex(-1)
  }, [disabled, open])

  useEffect(() => {
    if (!open || activeIndex < 0) return
    const activeOption = document.getElementById(`${listboxId}-option-${activeIndex}`)
    activeOption?.scrollIntoView?.({ block: 'nearest' })
  }, [activeIndex, listboxId, open])

  useEffect(() => {
    if (!open) return
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target as Node
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) close()
    }
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      close(true)
    }
    document.addEventListener('pointerdown', closeOnOutsidePointer)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  const handleTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (!open) setOpen(true)
      else if (activeIndex >= 0) {
        onChange(options[activeIndex].value)
        close(true)
      }
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) setOpen(true)
      else {
        const direction = event.key === 'ArrowDown' ? 1 : -1
        setActiveIndex((current) => {
          if (options.length === 0) return -1
          if (current < 0) return direction === 1 ? 0 : options.length - 1
          return (current + direction + options.length) % options.length
        })
      }
    } else if (open && (event.key === 'Home' || event.key === 'End')) {
      event.preventDefault()
      setActiveIndex(event.key === 'Home' ? 0 : options.length - 1)
    } else if (open && event.key === 'Tab') {
      close()
    }
  }

  return (
    <div className={styles.root}>
      <button
        ref={triggerRef}
        type="button"
        className={styles.trigger}
        data-alethe-field={field}
        data-autofocus={autoFocus ? '' : undefined}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={
          open && activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined
        }
        aria-expanded={open}
        disabled={disabled}
        onClick={(event) => {
          event.stopPropagation()
          if (open) close()
          else setOpen(true)
        }}
        onKeyDown={handleTriggerKeyDown}
        onPointerDown={(event) => event.stopPropagation()}
      >
        {icon ? <span className={styles.icon}>{icon}</span> : null}
        <span className={styles.title}>{title}</span>
        {side ? (
          <span className={styles.side} title={side}>
            {side}
          </span>
        ) : null}
        <ChevronDown className={styles.chevron} size={13} aria-hidden="true" />
      </button>
      {open && !disabled
        ? createPortal(
            <div
              ref={menuRef}
              className={styles.menu}
              data-alethe-dropdown-menu=""
              id={listboxId}
              role="listbox"
              aria-label={ariaLabel}
              onPointerDown={(event) => event.stopPropagation()}
              onMouseDown={(event) => event.stopPropagation()}
              onWheel={(event) => event.stopPropagation()}
              style={{
                left: position.left,
                top: position.top,
                width: position.width,
                maxHeight: position.maxHeight,
              }}
            >
              {options.map((option, index) => {
                const selected = option.value === value
                return (
                  <button
                    key={option.value}
                    id={`${listboxId}-option-${index}`}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    tabIndex={-1}
                    data-alethe-option={option.value}
                    className={`${styles.option} ${selected ? styles.optionSelected : ''} ${index === activeIndex ? styles.optionActive : ''}`}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={(event) => {
                      event.stopPropagation()
                      onChange(option.value)
                      close(true)
                    }}
                  >
                    {option.icon ? <span className={styles.icon}>{option.icon}</span> : null}
                    <span className={styles.optionBody}>
                      <span className={styles.optionTitle}>{option.title}</span>
                      {option.description ? (
                        <span className={styles.optionDescription} title={option.description}>
                          {option.description}
                        </span>
                      ) : null}
                    </span>
                    {selected ? <Check className={styles.check} size={13} /> : null}
                  </button>
                )
              })}
            </div>,
            document.body,
          )
        : null}
    </div>
  )
}
