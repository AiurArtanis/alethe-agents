import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const install = vi.hoisted(() => ({ reset: vi.fn(), status: 'running' }))

vi.mock('../../hooks/useAgentInstall', () => ({
  useAgentInstall: () => ({
    status: install.status,
    log: 'Do you agree to all the source agreements terms? [Y] Yes [N] No:',
    install: vi.fn(),
    reset: install.reset,
  }),
  useAgentOperationBusy: () => 'copilot',
}))

vi.mock('../../lib/tauri', () => ({
  openInBrowser: vi.fn(),
  probeInstallToolchain: vi.fn(async () => ({
    node: null,
    npm: false,
    winget: true,
    scoop: false,
    choco: false,
    bun: false,
    pnpm: false,
  })),
}))

import { AgentInstallModal } from './AgentInstallModal'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('AgentInstallModal', () => {
  // An installer stuck on a prompt must not hold the modal and the app-wide lock forever (#235).
  it('cancels a running install instead of trapping the user in the modal', () => {
    const onClose = vi.fn()
    render(<AgentInstallModal agent="copilot" label="Copilot" open onClose={onClose} />)

    const cancel = screen.getByRole('button', { name: 'Cancel' })
    expect(cancel).toBeEnabled()
    fireEvent.click(cancel)

    expect(install.reset).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })
})
