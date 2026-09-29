import { invoke } from '@tauri-apps/api/core'

export type AiMemoryStatus = {
  installed: boolean
  /** Something answers on the loopback endpoint — not necessarily a server Alethe started. */
  running: boolean
  command: string
  endpoint: string
  version: string | null
  /** The binary is the copy Alethe installed, not one found on PATH. */
  managed: boolean
  /** Upstream publishes a build for this machine. False on Windows ARM64. */
  supported: boolean
  /** The server behind `endpoint`, if any, is the child process Alethe itself started. */
  ours: boolean
}

export async function aiMemoryDetect(command?: string): Promise<AiMemoryStatus> {
  return invoke<AiMemoryStatus>('ai_memory_detect', { command })
}

export async function aiMemoryMcpConfigPath(repo: string, command?: string): Promise<string> {
  return invoke<string>('ai_memory_mcp_config_path', { repo, command })
}

                                                                                                                                  
export async function aiMemoryOpenCodeConfigWrite(repo: string, command?: string): Promise<void> {
  await invoke('ai_memory_opencode_config_write', { repo, command })
}

                                                                                                                                                               
export async function aiMemoryCodexConfigWrite(repo: string, command?: string): Promise<void> {
  await invoke('ai_memory_codex_config_write', { repo, command })
}
