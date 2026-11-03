import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { describe, expect, it } from 'vitest'

const scriptUrl = pathToFileURL(
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../scripts/check-i18n.mjs'),
).href

const { parseMessageKeys, diffKeys, compareMessageFiles, stripComments, scanHardcoded } =
  await import(scriptUrl)

describe('check-i18n parser', () => {
  it('parses keys and ignores comments', () => {
    const src = `
export const en = {
  // 'skip.me': 'nope',
  'common.back': 'Back',
  /* 'skip.block': 'nope', */
  'projectGrid.deleteDescription':
    'Delete “{name}”?',
  'ok.key': 'Value with // not a comment',
} as const
`
    const keys = parseMessageKeys(src)
    expect(keys.has('common.back')).toBe(true)
    expect(keys.has('projectGrid.deleteDescription')).toBe(true)
    expect(keys.has('ok.key')).toBe(true)
    expect(keys.has('skip.me')).toBe(false)
    expect(keys.has('skip.block')).toBe(false)
    expect(keys.size).toBe(3)
  })

  it('strips block and line comments outside strings', () => {
    const cleaned = stripComments(`a // x\nb /* y */ c\n'd // keep'`)
    expect(cleaned).toContain('a ')
    expect(cleaned).toContain('b ')
    expect(cleaned).toContain('c')
    expect(cleaned).toContain("'d // keep'")
    expect(cleaned).not.toContain('/* y */')
  })

  it('reports missing and extra keys when locales diverge', () => {
    const en = `
export const en = {
  'a.one': 'One',
  'a.two': 'Two',
} as const
`
    const zh = `
export const zhCN = {
  'a.one': '一',
  'a.three': '三',
}
`
    const diff = compareMessageFiles(en, zh)
    expect(diff.identical).toBe(false)
    expect(diff.missing).toEqual(['a.two'])
    expect(diff.extra).toEqual(['a.three'])
    expect(diff.enCount).toBe(2)
    expect(diff.zhCount).toBe(2)
  })

  it('passes when fixture keys match', () => {
    const body = `
  'x.a': 'A',
  'x.b': 'B',
`
    const diff = compareMessageFiles(
      `export const en = {${body}} as const`,
      `export const zh = {${body}}`,
    )
    expect(diff.identical).toBe(true)
    expect(diffKeys(parseMessageKeys(`{${body}}`), parseMessageKeys(`{${body}}`)).identical).toBe(
      true,
    )
  })

  it('real en.ts and zh-CN.ts keys are aligned', async () => {
    const { readFileSync } = await import('node:fs')
    const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
    const en = readFileSync(path.join(root, 'src/lib/i18n/messages/en.ts'), 'utf8')
    const zh = readFileSync(path.join(root, 'src/lib/i18n/messages/zh-CN.ts'), 'utf8')
    const diff = compareMessageFiles(en, zh)
    expect(diff.identical).toBe(true)
    expect(diff.missing).toEqual([])
    expect(diff.extra).toEqual([])
    expect(diff.enCount).toBeGreaterThan(100)
  })

  it('scanHardcoded finds prop and jsx text suspects', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'i18n-scan-'))
    writeFileSync(
      path.join(dir, 'Sample.tsx'),
      `export function Sample() {
  return (
    <button title="Open settings panel" aria-label="Close dialog now">
      Save changes now
    </button>
  )
}

export function Inline() {
  return <span>Discard unsaved edits</span>
}
`,
    )
    writeFileSync(
      path.join(dir, 'Ok.tsx'),
      `export function Ok({ t }: { t: (k: string) => string }) {
  return <button title={t('common.save')}>{t('common.save')}</button>
}
`,
    )
    const { suspects, count } = scanHardcoded(dir)
    expect(count).toBeGreaterThan(0)
    expect(suspects.some((s) => s.kind === 'prop:title')).toBe(true)
    expect(suspects.some((s) => s.kind === 'jsx-text')).toBe(true)
    // Translated call nearby should suppress that line
    expect(suspects.every((s) => !s.file.endsWith('Ok.tsx'))).toBe(true)
  })
})
