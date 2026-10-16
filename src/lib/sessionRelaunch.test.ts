import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useProjectsStore } from '../stores/projectsStore'
import { prepareAgentRelaunch } from './sessionRelaunch'
import * as tauri from './tauri'
import { EMPTY_PROJECTS_FILE } from './types'

vi.mock('./tauri', async (importOriginal) => ({
  ...(await importOriginal<typeof tauri>()),
  agentHooksSettingsPath: vi.fn(async () => 'hooks.json'),
}))

beforeEach(() => {
  vi.clearAllMocks()
  useProjectsStore.setState({ ...structuredClone(EMPTY_PROJECTS_FILE), hydrated: false })
})

describe('prepareAgentRelaunch', () => {
  it('keeps the Claude session hooks and the pane identity across a restart', async () => {
    const launch = await prepareAgentRelaunch({
      agent: 'claude',
      ptyId: 'pty-0',
      baseArgs: ['--verbose'],
      sessionId: 'chat-0',
    })

    expect(launch.args).toEqual(['--resume', 'chat-0', '--settings', 'hooks.json', '--verbose'])
    expect(launch.env.ALETHE_PLANNER).toBe('pty-0')
    expect(launch.sessionId).toBe('chat-0')
    expect(tauri.agentHooksSettingsPath).toHaveBeenCalledWith(
      'pty-0',
      useProjectsStore.getState().preferences.enabledFeatures.orchestrator,
    )
  })

  it('restarts Claude without hooks instead of failing when the hook file cannot be written', async () => {
    vi.mocked(tauri.agentHooksSettingsPath).mockRejectedValueOnce(new Error('no listener'))

    const launch = await prepareAgentRelaunch({ agent: 'claude', ptyId: 'pty-0', sessionId: 'c' })

    expect(launch.args).toEqual(['--resume', 'c'])
  })

  it('resumes Codex by id and asks for no hook file', async () => {
    const launch = await prepareAgentRelaunch({ agent: 'codex', ptyId: 'pty-1', sessionId: 'r' })

    expect(launch.args).toEqual(['resume', 'r'])
    expect(launch.env.ALETHE_PLANNER).toBe('pty-1')
    expect(tauri.agentHooksSettingsPath).not.toHaveBeenCalled()
  })

  it('starts a Codex pane with no known conversation fresh', async () => {
    const launch = await prepareAgentRelaunch({ agent: 'codex', ptyId: 'pty-1' })

    expect(launch.args).toEqual([])
    expect(launch.sessionId).toBeUndefined()
  })
})
