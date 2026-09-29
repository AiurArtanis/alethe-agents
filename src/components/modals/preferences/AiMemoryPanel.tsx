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
  // Whether the running server is the child Alethe started. Without this, a server the person runs
  // themselves would be reported as ours and Stop would look like it controls it.
  const [ours, setOurs] = useState(false)

  const refresh = useCallback(async () => {
    const next = await aiMemoryDetect().catch(() => null)
    setStatus(next)
    setCounts(next?.installed ? await aiMemoryCounts().catch(() => null) : null)
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const run = async (
    kind: 'install' | 'start' | 'stop',
    action: () => Promise<unknown>,
    errorKey: 'aiMemory.installError' | 'aiMemory.startError',
  ) => {
    setBusy(kind)
    try {
      await action()
      if (kind === 'start') setOurs(true)
      if (kind === 'stop') setOurs(false)
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
        {status?.installed
          ? `${t(status.managed ? 'aiMemory.installedManaged' : 'aiMemory.installedExternal')} — ${t('aiMemory.at', { path: status.command })}`
          : status?.supported === false
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

      {portOwnedByOther(status, ours) ? (
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
        {status?.running && ours ? (
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
