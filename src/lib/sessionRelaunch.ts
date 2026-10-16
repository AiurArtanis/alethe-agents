import { useProjectsStore } from '../stores/projectsStore'
import { preparePtyRuntimeLaunch } from './agentRuntimeAdapter'
import { buildAgentLaunch } from './sessionLaunch'
import { agentHooksSettingsPath } from './tauri'
import type { AgentRuntimeProfile, AgentType } from './types'

export type AgentRelaunch = {
  args: string[]
  env: Record<string, string>
  sessionId?: string
}

export async function claudeHooksSettingsPath(ptyId: string): Promise<string | undefined> {
  const { orchestrator } = useProjectsStore.getState().preferences.enabledFeatures
  return agentHooksSettingsPath(ptyId, orchestrator).catch(() => undefined)
}

export async function prepareAgentRelaunch(options: {
  agent: AgentType
  ptyId: string
  baseArgs?: readonly string[]
  runtimeProfile?: AgentRuntimeProfile
  sessionId?: string
}): Promise<AgentRelaunch> {
  const { agent, ptyId } = options
  const prepared = preparePtyRuntimeLaunch(agent, options.runtimeProfile, options.baseArgs ?? [])
  const hooksSettingsPath = agent === 'claude' ? await claudeHooksSettingsPath(ptyId) : undefined
  const launch = buildAgentLaunch(
    agent,
    prepared.args,
    options.sessionId,
    undefined,
    undefined,
    hooksSettingsPath,
  )
  return {
    args: launch.args,
    env: { ...(prepared.env ?? {}), ALETHE_PLANNER: ptyId },
    sessionId: launch.sessionId,
  }
}
