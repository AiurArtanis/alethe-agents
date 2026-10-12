---
name: alethe-orchestrate
description: Orchestrate bounded Codex, Claude Code, Agy, Kimi, or custom CLI workers with explicit strategies and skill-only preferences. Use when the user asks to orchestrate, delegate, compare agents, conserve agent tokens or quota, recover a worker, or work without MCP. Do not use for a small mechanical task unless explicitly requested.
---

# Orchestrate CLI workers

Act as planner and integrator. Give each worker one bounded unit, keep detailed transcripts outside
your context, and integrate only results supported by evidence or diffs.

## Choose the backend

Honor an explicit request for the local skill or no MCP. It always wins. Otherwise run
`scripts/orchestrate.py policy show` and use its `backend`: `skill` selects this runner, `mcp`
selects `alethe_*` tools when present, and `auto` prefers those tools when available.

The skill path is self-contained and never requires MCP. Its runner provides strategies, provider
switching preferences, concurrent processes, deadlines, structured outcomes, selective worktree
isolation, and resumable session IDs. It does not add workers to Alethe's board or invent quota
readings that a CLI does not report.

## First run and configuration

Run `scripts/orchestrate.py doctor` when the environment may be unprepared or the user asks what
is set up. It reports the Python version, installed providers, and whether a saved policy exists
and is valid, plus actionable hints.

When `doctor` reports no configured policy, offer a short setup interview instead of reciting
commands: ask which providers to prefer or conserve, whether a cheaper model should handle
bounded work, and whether a default strategy or hard budget should apply. Confirm and persist
with `policy set`. Close by showing the `policy show` summary. Never persist a preference the
user did not confirm.

Translate loose configuration requests into actions:

| The user says | Do |
| --- | --- |
| "save provider X" as an ongoing preference | confirm, then `policy set --conserve-providers X` |
| "use cheap models for the small stuff" | ask which provider and model, confirm, then `policy set --cheap-model provider=model` |
| "at most N workers / T minutes / C dollars" as an ongoing preference | confirm, then `policy set --max-calls N --max-wall-seconds T --max-reported-cost-usd C` |
| any of the above "for this run only" | put it in the plan's `budget` or use `--strategy`; never touch the policy |
| "back to defaults" | confirm, then `policy reset` |

## Orchestrate

1. Delegate only when the requested strategy makes the worker overhead worthwhile. `economy` and
   `rescue` intentionally allow one worker; keep an ordinary small task with the lead.
2. Read [references/routing.md](references/routing.md) to resolve `auto` or apply `economy`,
   `balanced`, `quality`, `research`, `parallel`, or `rescue`. An explicit strategy in the current
   request wins over saved preferences. Treat the word immediately after an explicit skill
   invocation as the strategy when it matches one of those names.
3. Run `scripts/orchestrate.py probe` and use only available providers. Read
   [references/providers.md](references/providers.md) when using Agy, Kimi, resuming a worker, or
   adding a custom CLI.
4. Give writers disjoint ownership. Use an isolated worktree for concurrent writers; a single
   writer may use the main checkout when existing user changes are preserved.
5. Create a version-1 plan JSON outside the repository. `scripts/orchestrate.py scaffold
   <strategy> --workers N` prints a valid skeleton to fill in. Use `strategy`, and use
   `provider: auto` only when saved provider switching preferences may decide the provider. Set
   `budget` when the run needs hard limits. Validate it against
   [schemas/plan.schema.json](schemas/plan.schema.json) or with `scripts/orchestrate.py validate`.
6. Preview with `scripts/orchestrate.py dry-run`. It explains the resolved strategy, the effective
   provider per worker, the concurrency, the estimated prompt tokens, and the budget that would
   apply, without starting anything.
7. Run the plan with `scripts/orchestrate.py run`. The runner never invokes a shell, caps
   concurrency, enforces the plan budget, terminates timed-out process trees, writes state
   atomically, and leaves worktrees for review.
8. Read `state.json`, each `outcome.json`, the concise `result.md`, and writer `diff.patch` files.
   Do not load raw logs unless diagnosing a failure.
9. Integrate reviewed changes, run validation in the combined checkout, then remove only temporary
   data and worktrees whose useful changes were handled.

For plan examples, result semantics, selective dirty-state seeding, and cleanup, read
[references/operations.md](references/operations.md).

## Worker contract

Each task must state its goal, definition of done, owned paths, relevant repository rules, checks,
forbidden changes, and desired handoff. The runner automatically appends these invariants:

- do not delegate, spawn subagents, or launch another agent CLI
- do not commit, push, tag, release, or open a pull request
- return a concise outcome, evidence, changed files, checks, failures, and risks

Do not put secrets in plans, tasks, arguments, or logs. Do not use bypass flags unless the user
explicitly requested that loss of isolation; custom workers require `allowUnsafe: true` for known
unsafe flags.

## Token discipline

- Treat statements such as "low on tokens", "save quota", or "economize" as `economy` for the
  current run. Persist it with `policy set economy` only when the user asks for an ongoing default.
- A plan `budget` turns saving into a hard limit: cap worker calls, wall time, estimated prompt
  tokens, and reported cost. Use `dry-run` to see the estimate before anything runs.
- More workers increase total provider usage. `auto` must choose no orchestration when the task is
  cheaper to finish directly; if orchestration was explicitly requested, it chooses the smallest
  valid strategy.
- Send only the context each unit needs. Point to repository files instead of copying their full
  contents into every task.
- Read concise results and diffs, not full transcripts.
- Reuse a returned `sessionId` for a focused follow-up instead of spawning a worker that must read
  the same area again.
- Use cheap workers for bounded discovery and mechanical work; keep ambiguity and final integration
  with the lead or a capable reviewer.
- A failed worker may be retried once only when the error has a specific correctable cause.

The runner reduces the lead agent's context load. It does not make worker tokens free.

Do not commit, push, tag, release, or open a pull request unless the user explicitly asked for it.
