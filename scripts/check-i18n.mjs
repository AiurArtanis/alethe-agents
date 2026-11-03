#!/usr/bin/env node
/**
 * i18n hard gate for verify-alethe:
 * 1) en.ts vs zh-CN.ts key parity (missing/extra) — fails on divergence
 * 2) heuristic hardcoded English scan — reports baseline; fails only with --fail-on-hardcoded
 *
 * Node-only (no /bin/sh). Works with CRLF and LF.
 */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const EN_PATH = path.join(ROOT, 'src/lib/i18n/messages/en.ts')
const ZH_PATH = path.join(ROOT, 'src/lib/i18n/messages/zh-CN.ts')
const SRC_ROOT = path.join(ROOT, 'src')
const MESSAGES_DIR = path.join(ROOT, 'src/lib/i18n/messages')

const PROP_KEYS = new Set(['title', 'placeholder', 'aria-label', 'label', 'description'])

/** Locked by 监工 2026-11-03: --fail-on-hardcoded fails only above this count. */
export const HARDCODED_BASELINE = 14

/** Strip line and block comments without trying to fully parse TS strings. */
export function stripComments(source) {
  let out = ''
  let i = 0
  let inSingle = false
  let inDouble = false
  let inTemplate = false
  while (i < source.length) {
    const c = source[i]
    const next = source[i + 1]
    if (!inSingle && !inDouble && !inTemplate) {
      if (c === '/' && next === '/') {
        i += 2
        while (i < source.length && source[i] !== '\n') i++
        continue
      }
      if (c === '/' && next === '*') {
        i += 2
        while (i < source.length && !(source[i] === '*' && source[i + 1] === '/')) i++
        i += 2
        continue
      }
    }
    if (!inDouble && !inTemplate && c === "'" && source[i - 1] !== '\\') {
      inSingle = !inSingle
    } else if (!inSingle && !inTemplate && c === '"' && source[i - 1] !== '\\') {
      inDouble = !inDouble
    } else if (!inSingle && !inDouble && c === '`' && source[i - 1] !== '\\') {
      inTemplate = !inTemplate
    }
    out += c
    i++
  }
  return out
}

/**
 * Extract object keys matching `'some.key':` (after comment strip).
 * Ignores keys inside nested non-message objects by requiring a dotted or
 * simple message-style key with at least one letter.
 */
export function parseMessageKeys(source) {
  const cleaned = stripComments(source).replace(/\r\n/g, '\n')
  const keys = new Set()
  const re = /^\s*'((?:\\.|[^'\\])+)'\s*:/gm
  let m
  while ((m = re.exec(cleaned)) !== null) {
    const key = m[1].replace(/\\'/g, "'")
    // Skip TypeScript import/type noise if any slipped through
    if (!key || key.startsWith('http')) continue
    keys.add(key)
  }
  return keys
}

export function diffKeys(enKeys, zhKeys) {
  const missing = [...enKeys].filter((k) => !zhKeys.has(k)).sort()
  const extra = [...zhKeys].filter((k) => !enKeys.has(k)).sort()
  return {
    enCount: enKeys.size,
    zhCount: zhKeys.size,
    missing,
    extra,
    identical: missing.length === 0 && extra.length === 0,
  }
}

function isUnderMessages(filePath) {
  const rel = path.relative(MESSAGES_DIR, filePath)
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel))
}

function walkTsFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist') continue
    const full = path.join(dir, name)
    let st
    try {
      st = statSync(full)
    } catch {
      continue
    }
    if (st.isDirectory()) {
      if (full === MESSAGES_DIR) continue
      walkTsFiles(full, out)
      continue
    }
    if (!/\.(tsx?)$/.test(name)) continue
    if (/\.d\.ts$/.test(name)) continue
    out.push(full)
  }
  return out
}

function looksLikeUrl(text) {
  return /^(https?:|mailto:|data:|\/[\w.-])/i.test(text.trim())
}

function looksLikeClassOrToken(text) {
  const t = text.trim()
  if (t.length < 3) return true
  // Pure identifiers / CSS tokens / paths without spaces
  if (!/\s/.test(t) && /^[A-Za-z0-9_./:#%-]+$/.test(t) && t.length < 40) return true
  if (/^[\d\s.,:;!?+\-*/=<>()[\]{}|\\]+$/.test(t)) return true
  return false
}

function hasI18nCallNearby(snippet) {
  return /\b(?:t|translate|translateDynamic)\s*\(/.test(snippet)
}

/**
 * Heuristic hardcoded English scan.
 * Returns { suspects: [{file,line,kind,text}], count }.
 */
export function scanHardcoded(rootDir = SRC_ROOT) {
  const files = walkTsFiles(rootDir)
  const suspects = []

  for (const file of files) {
    if (isUnderMessages(file)) continue
    // Skip unit/spec fixtures — gate targets product UI copy.
    if (/\.(test|spec)\.(tsx?)$/.test(file)) continue
    const raw = readFileSync(file, 'utf8').replace(/\r\n/g, '\n')
    const lines = raw.split('\n')
    const rel = path.relative(ROOT, file).replace(/\\/g, '/')

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i]
      const lineNo = i + 1

      // Skip import/export/type lines and pure comments
      if (
        /^\s*(?:import|export)\b/.test(line) ||
        /^\s*type\s+[A-Za-z]/.test(line) ||
        /^\s*\/\//.test(line) ||
        /^\s*\*/.test(line)
      ) {
        continue
      }
      if (hasI18nCallNearby(line)) continue

      // Prop string literals: title="...", placeholder='...'
      const propRe =
        /\b(title|placeholder|aria-label|label|description)\s*=\s*(?:\{\s*)?(["'])([^"'\\\n]*(?:\\.[^"'\\\n]*)*)\2/g
      let pm
      while ((pm = propRe.exec(line)) !== null) {
        const prop = pm[1]
        const text = pm[3]
        if (!PROP_KEYS.has(prop)) continue
        if (!/[A-Za-z]/.test(text)) continue
        if (looksLikeUrl(text) || looksLikeClassOrToken(text)) continue
        // Prefer phrases that look user-facing (space or sentence case)
        if (!/\s/.test(text) && text.length < 12 && !/^[A-Z][a-z]+/.test(text)) continue
        suspects.push({ file: rel, line: lineNo, kind: `prop:${prop}`, text })
      }

      // JSX text nodes on the same line: >English words here<
      const jsxRe = />([^<>{}\n][^<>{}\n]*)<\/[A-Za-z]/g
      let jm
      while ((jm = jsxRe.exec(line)) !== null) {
        const text = jm[1].trim()
        if (!text || !/[A-Za-z]/.test(text)) continue
        if (looksLikeUrl(text) || looksLikeClassOrToken(text)) continue
        // Require at least one space or a capitalized word phrase
        if (!/\s/.test(text) && !/^[A-Z][a-z]{2,}/.test(text)) continue
        if (/^\{/.test(text)) continue
        suspects.push({ file: rel, line: lineNo, kind: 'jsx-text', text })
      }

      // Multiline JSX children: indented user-facing prose (2+ words, no code junk)
      if (/\.tsx$/.test(file)) {
        const child = line.match(/^\s{2,}([A-Za-z][A-Za-z0-9 ,.'\u2019"!?%\-]*[A-Za-z.!?])\s*$/)
        if (child) {
          const text = child[1].trim()
          const words = text.split(/\s+/).filter(Boolean)
          if (
            words.length >= 2 &&
            !/,$/.test(text) &&
            !/^type\s/i.test(text) &&
            !looksLikeUrl(text) &&
            !looksLikeClassOrToken(text) &&
            !/\b(return|const|let|var|function|import|export|await|throw|new|typeof|instanceof|void|else|delete|from)\b/.test(
              text,
            )
          ) {
            suspects.push({ file: rel, line: lineNo, kind: 'jsx-text', text })
          }
        }
      }
    }
  }

  return { suspects, count: suspects.length }
}

export function compareMessageFiles(enSource, zhSource) {
  return diffKeys(parseMessageKeys(enSource), parseMessageKeys(zhSource))
}

function gitSha() {
  try {
    return execFileSync('git', ['rev-parse', 'HEAD'], {
      cwd: ROOT,
      encoding: 'utf8',
    }).trim()
  } catch {
    return 'unknown'
  }
}

function parseArgs(argv) {
  const opts = {
    failOnHardcoded: false,
    hardcodedBaseline: HARDCODED_BASELINE,
    outDir: null,
    en: EN_PATH,
    zh: ZH_PATH,
    scanRoot: SRC_ROOT,
  }
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (arg === '--fail-on-hardcoded') opts.failOnHardcoded = true
    else if (arg === '--hardcoded-baseline') opts.hardcodedBaseline = Number(argv[++i])
    else if (arg === '--out-dir') opts.outDir = argv[++i]
    else if (arg === '--en') opts.en = path.resolve(argv[++i])
    else if (arg === '--zh') opts.zh = path.resolve(argv[++i])
    else if (arg === '--scan-root') opts.scanRoot = path.resolve(argv[++i])
    else if (arg === '--help' || arg === '-h') {
      console.log(`Usage: node scripts/check-i18n.mjs [--fail-on-hardcoded] [--out-dir DIR]
  --en PATH          override en.ts
  --zh PATH          override zh-CN.ts
  --scan-root PATH   override src root for hardcoded scan
  --out-dir DIR      write summary artifacts
  --fail-on-hardcoded  exit nonzero when hardcoded suspects > baseline (14)
  --hardcoded-baseline N  override baseline (default 14; used with --fail-on-hardcoded)`)
      process.exit(0)
    } else {
      console.error(`Unknown argument: ${arg}`)
      process.exit(2)
    }
  }
  return opts
}

function formatSummary(diff, hardcoded, sha) {
  const lines = []
  lines.push(`# i18n check summary`)
  lines.push(`sha: ${sha}`)
  lines.push(`en keys: ${diff.enCount}`)
  lines.push(`zh-CN keys: ${diff.zhCount}`)
  lines.push(`missing (in en, not zh-CN): ${diff.missing.length}`)
  lines.push(`extra (in zh-CN, not en): ${diff.extra.length}`)
  lines.push(`keys identical: ${diff.identical ? 'yes' : 'no'}`)
  lines.push(`hardcoded suspects: ${hardcoded.count} (fail threshold ${HARDCODED_BASELINE})`)
  lines.push('')
  if (diff.missing.length) {
    lines.push('## missing')
    for (const k of diff.missing) lines.push(`- ${k}`)
    lines.push('')
  }
  if (diff.extra.length) {
    lines.push('## extra')
    for (const k of diff.extra) lines.push(`- ${k}`)
    lines.push('')
  }
  lines.push('## hardcoded suspects (first 200)')
  for (const s of hardcoded.suspects.slice(0, 200)) {
    lines.push(`- ${s.file}:${s.line} [${s.kind}] ${JSON.stringify(s.text)}`)
  }
  if (hardcoded.suspects.length > 200) {
    lines.push(`... and ${hardcoded.suspects.length - 200} more`)
  }
  lines.push('')
  return lines.join('\n')
}

function main(argv = process.argv.slice(2)) {
  const opts = parseArgs(argv)
  if (!existsSync(opts.en) || !existsSync(opts.zh)) {
    console.error('Missing message files:', opts.en, opts.zh)
    process.exit(2)
  }

  const enSource = readFileSync(opts.en, 'utf8')
  const zhSource = readFileSync(opts.zh, 'utf8')
  const diff = compareMessageFiles(enSource, zhSource)
  const hardcoded = scanHardcoded(opts.scanRoot)
  const sha = gitSha()
  const sha7 = sha.slice(0, 7)

  const outDir = opts.outDir || path.join('/workspace/verify-alethe', `i18n-check-${sha7}`)
  mkdirSync(outDir, { recursive: true })

  const keyDiffPath = path.join(outDir, 'key-diff.json')
  const summaryPath = path.join(outDir, 'summary.txt')
  const hardcodedPath = path.join(outDir, 'hardcoded.json')
  const shaPath = path.join(outDir, 'sha.txt')

  writeFileSync(
    keyDiffPath,
    JSON.stringify(
      {
        sha,
        enCount: diff.enCount,
        zhCount: diff.zhCount,
        missing: diff.missing,
        extra: diff.extra,
        identical: diff.identical,
      },
      null,
      2,
    ) + '\n',
  )
  writeFileSync(
    hardcodedPath,
    JSON.stringify({ sha, count: hardcoded.count, failThreshold: opts.hardcodedBaseline, lockedBaseline: HARDCODED_BASELINE, suspects: hardcoded.suspects }, null, 2) +
      '\n',
  )
  writeFileSync(summaryPath, formatSummary(diff, hardcoded, sha))
  writeFileSync(shaPath, sha + '\n')

  console.log(`en keys: ${diff.enCount}`)
  console.log(`zh-CN keys: ${diff.zhCount}`)
  console.log(`missing: ${diff.missing.length}`)
  console.log(`extra: ${diff.extra.length}`)
  console.log(`hardcoded suspects: ${hardcoded.count} (fail threshold ${opts.hardcodedBaseline})`)
  console.log(`artifacts: ${outDir}`)
  if (!diff.identical) {
    console.error('FAIL: en / zh-CN keys diverge')
    if (diff.missing.length) console.error('missing sample:', diff.missing.slice(0, 20))
    if (diff.extra.length) console.error('extra sample:', diff.extra.slice(0, 20))
  } else {
    console.log('PASS: en / zh-CN keys identical')
  }

  if (opts.failOnHardcoded && hardcoded.count > opts.hardcodedBaseline) {
    console.error(
      `FAIL: hardcoded suspects=${hardcoded.count} > baseline ${opts.hardcodedBaseline} (--fail-on-hardcoded)`,
    )
    process.exit(1)
  }

  process.exit(diff.identical ? 0 : 1)
}

const isMain =
  process.argv[1] && path.resolve(fileURLToPath(import.meta.url)) === path.resolve(process.argv[1])

if (isMain) {
  main()
}
