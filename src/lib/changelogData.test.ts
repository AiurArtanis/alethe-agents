import { describe, expect, it } from 'vitest'

import packageJson from '../../package.json'
import { CHANGELOG_RELEASES, CURRENT_VERSION, formatReleaseDate } from './changelogData'

describe("What's New releases", () => {
  it('cover the version being shipped', () => {
    // The list is kept by hand; this fails a build whose version has no notes (#69, #213).
    expect(CHANGELOG_RELEASES.map((release) => release.version)).toContain(packageJson.version)
  })

  it('take the panel version from the build, not from the list', () => {
    expect(CURRENT_VERSION).toBe(packageJson.version)
  })

  it('are listed newest first, once each', () => {
    const versions = CHANGELOG_RELEASES.map((release) => release.version)
    const newestFirst = [...versions].sort((a, b) =>
      b.localeCompare(a, undefined, { numeric: true }),
    )
    expect(versions).toEqual(newestFirst)
    expect(new Set(versions).size).toBe(versions.length)
  })

  it('show the release day for viewers west of UTC', () => {
    const previous = process.env.TZ
    process.env.TZ = 'America/Sao_Paulo'
    try {
      expect(formatReleaseDate('2026-09-20', 'pt-BR')).toBe('20/09/2026')
      expect(formatReleaseDate('2026-09-20', 'en-US')).toBe('9/20/2026')
    } finally {
      if (previous === undefined) delete process.env.TZ
      else process.env.TZ = previous
    }
  })
})
