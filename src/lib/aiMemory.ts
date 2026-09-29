import { invoke } from '@tauri-apps/api/core'

export type { AiMemoryStatus } from './tauri/aiMemory'
import type { AiMemoryStatus } from './tauri/aiMemory'

export const AI_MEMORY_DEFAULT_PORT = 49374
export const AI_MEMORY_REPO = 'https://github.com/akitaonrails/ai-memory'

export type AiMemoryCounts = { sessions: number; observations: number; pages: number }

export function offerInstall(status: AiMemoryStatus | null): boolean {
  return Boolean(status && !status.installed && status.supported)
}

export function canStart(status: AiMemoryStatus | null): boolean {
  return Boolean(status && status.installed && !status.running)
}

/** True when the endpoint answers and the server behind it is not the child Alethe started. */
export function portOwnedByOther(status: AiMemoryStatus | null): boolean {
  return Boolean(status?.running) && !status?.ours
}

export function normalizePort(port: number): number {
  return Number.isInteger(port) && port > 0 && port <= 65535 ? port : AI_MEMORY_DEFAULT_PORT
}

export async function aiMemoryInstall(): Promise<string> {
  return invoke<string>('ai_memory_install')
}

export async function aiMemoryStart(port?: number): Promise<void> {
  await invoke('ai_memory_start', { port })
}

export async function aiMemoryStop(): Promise<void> {
  await invoke('ai_memory_stop')
}

export async function aiMemoryCounts(): Promise<AiMemoryCounts> {
  return invoke<AiMemoryCounts>('ai_memory_counts', {})
}
