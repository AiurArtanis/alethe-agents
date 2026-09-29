import { describe, expect, it } from 'vitest'

import {
  type AiMemoryStatus,
  canStart,
  normalizePort,
  offerInstall,
  portOwnedByOther,
} from './aiMemory'

function status(patch: Partial<AiMemoryStatus> = {}): AiMemoryStatus {
  return {
    installed: false,
    running: false,
    command: 'ai-memory',
    endpoint: '127.0.0.1:49374',
    version: null,
    managed: false,
    supported: true,
    ...patch,
  }
}

describe('whether the panel offers to install', () => {
  it('offers when nothing is installed and upstream builds for this machine', () => {
    expect(offerInstall(status())).toBe(true)
  })

  it('does not offer on a machine upstream publishes no build for', () => {
    // Windows on ARM. Saying so beats a button that downloads a 404.
    expect(offerInstall(status({ supported: false }))).toBe(false)
  })

  it('does not offer when a binary is already there', () => {
    expect(offerInstall(status({ installed: true }))).toBe(false)
  })

  it('offers nothing while the status is unknown', () => {
    expect(offerInstall(null)).toBe(false)
  })
})

describe('whether starting is offered', () => {
  it('needs a binary', () => {
    expect(canStart(status())).toBe(false)
    expect(canStart(status({ installed: true }))).toBe(true)
  })

  it('is not offered while something already answers on the endpoint', () => {
    expect(canStart(status({ installed: true, running: true }))).toBe(false)
  })
})

describe('who owns the endpoint', () => {
  it('names a server Alethe did not start', () => {
    // Most likely the person's own instance: a reason to leave it alone, not to fight for the bind.
    expect(portOwnedByOther(status({ installed: true, running: true }), false)).toBe(true)
  })

  it('says nothing when the running server is ours', () => {
    expect(portOwnedByOther(status({ installed: true, running: true }), true)).toBe(false)
  })

  it('says nothing when nothing is running', () => {
    expect(portOwnedByOther(status({ installed: true }), false)).toBe(false)
  })
})

describe('the port a person can type', () => {
  it('keeps a usable port and falls back to the default otherwise', () => {
    expect(normalizePort(50000)).toBe(50000)
    expect(normalizePort(0)).toBe(49374)
    expect(normalizePort(70000)).toBe(49374)
    expect(normalizePort(Number.NaN)).toBe(49374)
  })
})
