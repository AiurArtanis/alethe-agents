import { describe, expect, it } from 'vitest'

import { remeasuredFontFamily, TERMINAL_FONT_FAMILY } from './terminalFont'

describe('terminal font', () => {
  it('puts the bundled fonts ahead of any system font', () => {
    expect(
      TERMINAL_FONT_FAMILY.startsWith('"Caskaydia Cove Nerd Font Mono", "Alethe Terminal Symbols"'),
    ).toBe(true)
    expect(TERMINAL_FONT_FAMILY.endsWith('monospace')).toBe(true)
  })

  it('always hands xterm a different value so it re-measures the cell', () => {
    const first = remeasuredFontFamily(TERMINAL_FONT_FAMILY)
    expect(first).not.toBe(TERMINAL_FONT_FAMILY)
    expect(first.trim()).toBe(TERMINAL_FONT_FAMILY)
    expect(remeasuredFontFamily(first)).toBe(TERMINAL_FONT_FAMILY)
  })
})
