import { useCallback, useEffect, useState } from 'react'

import {
  AI_MEMORY_DEFAULT_PORT,
  AI_MEMORY_REPO,
  type AiMemoryCounts,
  type AiMemoryStatus,
  aiMemoryCounts,
  aiMemoryInstall,
  aiMemoryStart,
  aiMemoryStop,
  canStart,
  offerInstall,
  portOwnedByOther,
} from '../../../lib/aiMemory'
import { useT } from '../../../lib/i18n'
import { aiMemoryDetect } from '../../../lib/tauri'
import { useUiStore } from '../../../stores/uiStore'
import controls from '../controls.module.css'
import styles from './AiMemoryPanel.module.css'

export function AiMemoryPanel() {
  const t = useT()
  const pushToast = useUiStore((state) => state.pushToast)
  const [status, setStatus] = useState<AiMemoryStatus | null>(null)
  const [counts, setCounts] = useState<AiMemoryCounts | null>(null)
  const [busy, setBusy] = useState<'install' | 'start' | 'stop' | null>(null)

  // `shouldApply` lets the mount effect skip both setters once the panel has unmounted — the detect
  // call can resolve after that (a subprocess spawn plus a loopback connect with up to a 250ms
  // timeout, longer under antivirus scanning). Calls from `run` below omit it: the action just
  // completed on a control the panel is still rendering.
  const refresh = useCallback(async (shouldApply: () => boolean = () => true) => {
    const next = await aiMemoryDetect().catch(() => null)
    if (!shouldApply()) return
    setStatus(next)
    const nextCounts = next?.installed ? await aiMemoryCounts().catch(() => null) : null
    if (!shouldApply()) return
    setCounts(nextCounts)
  }, [])

  useEffect(() => {
    let cancelled = false
    void refresh(() => !cancelled)
    return () => {
      cancelled = true
    }
  }, [refresh])

  const run = async (
    kind: 'install' | 'start' | 'stop',
    action: () => Promise<unknown>,
    errorKey: 'aiMemory.installError' | 'aiMemory.startError',
  ) => {
    setBusy(kind)
    try {
      await action()
      await refresh()
    } catch (cause) {
      pushToast({ title: t(errorKey), body: String(cause) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className={styles.panel}>
      <p className={styles.captures}>{t('aiMemory.panelCaptures')}</p>

      <p className={styles.state}>
        {status === null
          ? t('aiMemory.checking')
          : status.installed
            ? `${t(status.managed ? 'aiMemory.installedManaged' : 'aiMemory.installedExternal')} — ${t('aiMemory.at', { path: status.command })}`
            : status.supported === false
              ? t('aiMemory.unsupported')
              : t('aiMemory.missing')}
      </p>

      {status?.installed ? (
        <p className={styles.state}>
          {status.running
            ? t('aiMemory.running', { endpoint: status.endpoint })
            : t('aiMemory.stopped')}
          {counts
            ? ` · ${t('aiMemory.counts', {
                pages: counts.pages,
                sessions: counts.sessions,
                observations: counts.observations,
              })}`
            : ''}
        </p>
      ) : null}

      {portOwnedByOther(status) ? (
        <p className={styles.warning}>
          {t('aiMemory.portBusy', { endpoint: status?.endpoint ?? '' })}
        </p>
      ) : null}

      <div className={styles.actions}>
        {offerInstall(status) ? (
          <button
            type="button"
            className={`${controls.btn} ${controls.btnPrimary}`}
            disabled={busy !== null}
            onClick={() => void run('install', aiMemoryInstall, 'aiMemory.installError')}
          >
            {busy === 'install' ? t('aiMemory.installing') : t('aiMemory.install')}
          </button>
        ) : null}
        {status?.running && status.ours ? (
          <button
            type="button"
            className={controls.btn}
            disabled={busy !== null}
            onClick={() => void run('stop', aiMemoryStop, 'aiMemory.startError')}
          >
            {t('aiMemory.stop')}
          </button>
        ) : (
          <button
            type="button"
            className={controls.btn}
            disabled={busy !== null || !canStart(status)}
            onClick={() =>
              void run('start', () => aiMemoryStart(AI_MEMORY_DEFAULT_PORT), 'aiMemory.startError')
            }
          >
            {t('aiMemory.start')}
          </button>
        )}
        <a className={styles.link} href={AI_MEMORY_REPO} target="_blank" rel="noreferrer">
          {t('aiMemory.openRepo')}
        </a>
      </div>
    </div>
  )
}
