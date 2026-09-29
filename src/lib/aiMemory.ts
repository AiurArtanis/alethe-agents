import { invoke } from '@tauri-apps/api/core'

export type { AiMemoryStatus } from './tauri/aiMemory'
import type { MessageKey, TFunction } from './i18n'
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

/**
 * `null` means ai-memory could not be asked — the server is unreachable, `status` exited non-zero —
 * which is not the same thing as a store that has nothing in it yet. Callers must tell the two apart
 * rather than falling back to zeros for both.
 */
export async function aiMemoryCounts(): Promise<AiMemoryCounts | null> {
  return invoke<AiMemoryCounts | null>('ai_memory_counts', {})
}

/** Rust error codes this panel can name, mapped to a real sentence for each locale. */
const AI_MEMORY_ERROR_MESSAGE_KEYS: Record<string, MessageKey> = {
  ai_memory_port_in_use: 'aiMemory.error.portInUse',
  ai_memory_unsupported_platform: 'aiMemory.error.unsupportedPlatform',
  ai_memory_binary_missing: 'aiMemory.error.binaryMissing',
}

/**
 * A sentence for a raw error code a `ai_memory_*` command rejected with, or the raw code itself when
 * it names nothing recognised — never swallowed, just not translated.
 */
export function aiMemoryErrorMessage(cause: unknown, t: TFunction): string {
  const raw = String(cause)
  for (const [code, key] of Object.entries(AI_MEMORY_ERROR_MESSAGE_KEYS)) {
    if (raw.includes(code)) return t(key)
  }
  return raw
}
