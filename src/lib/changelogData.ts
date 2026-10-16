import { version as appVersion } from '../../package.json'
import type { MessageKey } from './i18n'

export type ChangelogRelease = {
  version: string
  date: string
  noteKeys: MessageKey[]
}

/** Curated by hand, newest first. `npm run release` refuses to stamp a version that has no entry
 * here, so the notes for a release are written before it, not after. */
export const CHANGELOG_RELEASES: ChangelogRelease[] = [
  {
    version: '1.7.0',
    date: '2026-09-20',
    noteKeys: [
      'whatsNew.v170.note1',
      'whatsNew.v170.note2',
      'whatsNew.v170.note3',
      'whatsNew.v170.note4',
      'whatsNew.v170.note5',
      'whatsNew.v170.note6',
      'whatsNew.v170.note7',
      'whatsNew.v170.note8',
    ],
  },
  {
    version: '1.6.0',
    date: '2026-08-17',
    noteKeys: [
      'whatsNew.v160.note1',
      'whatsNew.v160.note2',
      'whatsNew.v160.note3',
      'whatsNew.v160.note4',
      'whatsNew.v160.note5',
      'whatsNew.v160.note6',
      'whatsNew.v160.note7',
      'whatsNew.v160.note8',
    ],
  },
  {
    version: '1.5.0',
    date: '2026-08-09',
    noteKeys: [
      'whatsNew.v150.note1',
      'whatsNew.v150.note2',
      'whatsNew.v150.note3',
      'whatsNew.v150.note4',
      'whatsNew.v150.note5',
      'whatsNew.v150.note6',
    ],
  },
  {
    version: '1.4.1',
    date: '2026-08-07',
    noteKeys: ['whatsNew.v141.note1'],
  },
  {
    version: '1.4.0',
    date: '2026-08-07',
    noteKeys: [
      'whatsNew.v140.note1',
      'whatsNew.v140.note2',
      'whatsNew.v140.note3',
      'whatsNew.v140.note4',
      'whatsNew.v140.note5',
      'whatsNew.v140.note6',
      'whatsNew.v140.note7',
      'whatsNew.v140.note8',
      'whatsNew.v140.note9',
      'whatsNew.v140.note10',
      'whatsNew.v140.note11',
      'whatsNew.v140.note12',
      'whatsNew.v140.note13',
    ],
  },
  {
    version: '1.3.0',
    date: '2026-07-27',
    noteKeys: [
      'whatsNew.v130.note1',
      'whatsNew.v130.note2',
      'whatsNew.v130.note3',
      'whatsNew.v130.note4',
      'whatsNew.v130.note5',
      'whatsNew.v130.note6',
      'whatsNew.v130.note7',
      'whatsNew.v130.note8',
    ],
  },
]

/** The version of the running build. Read from `package.json`, never from the list above: the
 * list can lag or lead the build, and the panel title must not follow it. */
export const CURRENT_VERSION = appVersion

/** Release dates are calendar days (the changelog uses UTC). `new Date('YYYY-MM-DD')` is UTC midnight,
 * so formatting it in the viewer's time zone shows the previous day anywhere west of UTC. */
export function formatReleaseDate(date: string, locale: string): string {
  return new Date(date).toLocaleDateString(locale, { timeZone: 'UTC' })
}
