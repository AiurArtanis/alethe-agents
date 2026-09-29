import { beforeEach, describe, expect, it } from 'vitest'

import { type AgentHookPayload, useAgentCanvasStore } from './agentCanvasStore'

const ingest = (payload: AgentHookPayload) => useAgentCanvasStore.getState().ingest(payload)
const backgroundNode = (taskId: string) =>
  useAgentCanvasStore.getState().nodes.find((node) => node.id === `background:${taskId}`)

function startBackgroundShell(taskId: string) {
  ingest({
    hook_event_name: 'PostToolUse',
    plannerId: 'pty-1',
    tool_name: 'Bash',
    tool_input: { command: 'sleep 6 && echo finished', run_in_background: true },
    tool_response: { backgroundTaskId: taskId },
  })
}

// What Claude Code sends as `UserPromptSubmit` when a background task ends (#239).
function taskNotification(taskId: string, status: string, summary: string): string {
  return [
    '<task-notification>',
    `<task-id>${taskId}</task-id>`,
    '<tool-use-id>toolu_011CkiLGevk1hTiUcCPQwXqY</tool-use-id>',
    `<output-file>C:\\Temp\\tasks\\${taskId}.output</output-file>`,
    `<status>${status}</status>`,
    `<summary>${summary}</summary>`,
    '</task-notification>',
  ].join('\n')
}

beforeEach(() => {
  useAgentCanvasStore.getState().clear()
})

describe('background shells', () => {
  it('ends a background shell when Claude reports that its task finished', () => {
    startBackgroundShell('b3f3vq4c2')
    startBackgroundShell('still-going')
    expect(backgroundNode('b3f3vq4c2')?.status).toBe('running')

    const summary = 'Background command "Sleep" completed (exit code 0)'
    ingest({
      hook_event_name: 'UserPromptSubmit',
      plannerId: 'pty-1',
      prompt: taskNotification('b3f3vq4c2', 'completed', summary),
    })

    expect(backgroundNode('b3f3vq4c2')).toMatchObject({ status: 'done', result: summary })
    expect(backgroundNode('b3f3vq4c2')?.endedAt).toEqual(expect.any(Number))
    expect(backgroundNode('still-going')?.status).toBe('running')
  })

  it('ends every task a batched notification reports, whatever its final status', () => {
    startBackgroundShell('first')
    startBackgroundShell('second')

    ingest({
      hook_event_name: 'UserPromptSubmit',
      plannerId: 'pty-1',
      prompt: `${taskNotification('first', 'failed', 'exit code 1')}\n${taskNotification('second', 'killed', 'stopped')}`,
    })

    expect(backgroundNode('first')?.status).toBe('done')
    expect(backgroundNode('second')?.status).toBe('done')
  })

  it('leaves background shells alone on an ordinary prompt', () => {
    startBackgroundShell('b3f3vq4c2')

    ingest({
      hook_event_name: 'UserPromptSubmit',
      plannerId: 'pty-1',
      prompt: 'what does <task-id>b3f3vq4c2</task-id> mean?',
    })

    expect(backgroundNode('b3f3vq4c2')?.status).toBe('running')
  })

  it('only trusts a prompt that is a notification with a final status', () => {
    startBackgroundShell('b3f3vq4c2')
    const prompts = [
      `why did this arrive?\n${taskNotification('b3f3vq4c2', 'completed', 'done')}`,
      taskNotification('b3f3vq4c2', 'running', 'still going'),
      taskNotification('b3f3vq4c2', 'completed', 'done').replace(/<status>.*<\/status>\n/, ''),
    ]

    for (const prompt of prompts) {
      ingest({ hook_event_name: 'UserPromptSubmit', plannerId: 'pty-1', prompt })
      expect(backgroundNode('b3f3vq4c2')?.status).toBe('running')
    }
  })

  it("never ends another planner's background shell", () => {
    startBackgroundShell('b3f3vq4c2')

    ingest({
      hook_event_name: 'UserPromptSubmit',
      plannerId: 'pty-2',
      prompt: taskNotification('b3f3vq4c2', 'completed', 'done'),
    })

    expect(backgroundNode('b3f3vq4c2')?.status).toBe('running')
  })
})
