# Batch operations

Read this reference when creating a plan, inspecting outcomes, carrying dirty state into a
worktree, or cleaning up.

## Run the helper

Use Python 3.9 or newer. On Windows, prefer `py -3` when `python` is not on `PATH`.

```text
python scripts/orchestrate.py probe
python scripts/orchestrate.py doctor
python scripts/orchestrate.py strategies
python scripts/orchestrate.py policy show
python scripts/orchestrate.py scaffold research --workers 3 --cwd .
python scripts/orchestrate.py validate <plan.json>
python scripts/orchestrate.py dry-run <plan.json>
python scripts/orchestrate.py run <plan.json>
python scripts/orchestrate.py run <plan.json> --strategy economy
```

`doctor` checks the environment before you promise anything: Python version, installed provider
executables and versions, and whether the saved policy exists, parses, and carries a budget. Its
`hints` list says what is missing in plain language.

`scaffold` prints a starter plan for a strategy so a plan never has to be written from memory.
Worker tasks come back as TODO placeholders to replace, `parallel` workers already carry
`isolate: true`, and `rescue` requires `--provider` because a resumed session belongs to one
CLI. Pass `--write <path>` to save it instead of printing.

`dry-run` validates the plan and explains what a run would do — resolved strategy and why,
effective provider per worker, concurrency, estimated prompt tokens, and the effective budget —
without starting a worker or creating a worktree. Run it before any strategy you did not pick
yourself.

Pass `--run-dir <empty-directory>` when a stable output location is useful. Otherwise the runner
creates a unique directory under the operating system's temporary directory.

If Python is unavailable, do not install it without permission. Use the command shapes in
`references/providers.md` with the host's native background-process facility and reproduce the
same deadlines and result checks.

## Minimal plan

```json
{
  "version": 1,
  "strategy": "balanced",
  "workers": [
    {
      "id": "api",
      "provider": "auto",
      "mode": "write",
      "cwd": "C:\\work\\project",
      "isolate": true,
      "task": "Implement the API unit. Own src/api and tests/api only."
    },
    {
      "id": "ui-review",
      "provider": "agy",
      "mode": "read-only",
      "cwd": "C:\\work\\project",
      "task": "Review the UI state flow. Return findings with file and line evidence."
    }
  ]
}
```

Use `taskFile` instead of `task` for a long instruction. Relative `cwd` and `taskFile` values are
resolved from the plan file's directory.

A worker may declare `tier: cheap` or `tier: strong` to use the policy-mapped model for its
provider instead of pinning `model` or `effort` (see `references/routing.md`). Tiers compose
with `provider: auto`; explicit models do not.

`provider: auto` uses the saved provider order and conservation preferences. Name a provider when
the user requested it, a model or effort belongs to it, or a session is being resumed. The legacy
`preset` field is still accepted, but new plans use `strategy`.

`--strategy` is a one-run override. Its precedence is command line, plan, saved policy, then
`auto`; it never changes the saved default.

## Execution budget

A plan may carry an optional `budget` object; every field is optional and absent means unlimited:

```json
{
  "version": 1,
  "budget": {
    "maxCalls": 4,
    "maxWallSeconds": 1800,
    "maxEstimatedPromptTokens": 60000,
    "maxReportedCostUsd": 2.5
  },
  "workers": []
}
```

- `maxCalls` caps how many worker processes the run may start. A worker refused by a budget gate
  finishes with status `skipped` and counts as unsuccessful.
- `maxWallSeconds` caps the whole batch clock; each worker's effective timeout is the smaller of
  its own `timeoutSeconds` and the remaining wall time.
- `maxEstimatedPromptTokens` is checked at validation time against the summed prompt-only
  estimate (chars/4), so `validate`, `dry-run`, and `run` all reject an over-budget plan before
  anything starts. Provider-side context such as repository reads is additional and not included.
- `maxReportedCostUsd` gates later workers on the cost earlier workers actually reported. Only
  providers that emit usage report cost at all, so this limit can never fire for `kimi` or
  `custom` workers.

Budget gates are checked when a worker is submitted. With `maxConcurrent` above one, workers
already running are not retroactively cancelled — the gates stop new work, they do not recall
in-flight work.

The saved policy can hold the same fields as defaults (`policy set --max-calls 4`); a plan
overrides them field by field. Alethe's preferences bridge rewrites the policy file from app
settings, so policy budgets are a runner-only convenience and plan budgets are the durable form.

`state.json` records the effective `budget` and a `budgetUsage` block with `calls`,
`wallSeconds`, `reportedCostUsd`, and `reportedTokens`; the `reported*` fields are `null` when no
worker reported the corresponding metric.

## Output layout

```text
<run>/
|-- state.json
`-- workers/
    `-- <id>/
        |-- prompt.txt
        |-- stdout.log
        |-- stderr.log
        |-- result.md
        |-- outcome.json
        |-- status.txt
        `-- diff.patch
```

`state.json` is the compact batch view. A worker succeeds only when it exits before its deadline,
its declared structured output parses, and a non-empty final result can be extracted. Raw stdout
and stderr are diagnostic artifacts, not final answers.

The state records both `strategy` and `requestedStrategy`; they differ when `auto` resolves to a
concrete strategy. Validation also reports `requestedProvider` next to the effective provider when
automatic switching occurred.

An overall status is:

- `done`: every worker succeeded
- `partial`: at least one succeeded and at least one failed or timed out
- `failed`: none succeeded

## Worktrees and dirty state

`isolate: true` creates a detached worktree under `.alethe/worktrees/`. It is allowed only for a
writer and is never removed automatically.

Worktrees start from `HEAD`. To carry necessary uncommitted state, list only repository-relative
paths in `seedPaths`. The runner applies tracked diffs and copies explicitly listed untracked files.
Symlinked seed paths are rejected so an isolated worker cannot reach back into the main checkout.
The runner never stashes or commits the main checkout and never copies the whole dirty repository.

After completion, inspect `status.txt` and `diff.patch`. Apply or reproduce reviewed changes in the
main checkout, run combined validation there, and remove the worktree only after every useful
change has been handled.

## Failure and retry

Treat non-zero exit, timeout, invalid structured output, an error result, or an empty final answer
as failure. Read `error`, then the tail of `stderr.log`. Retry once only when a narrower task or
corrected invocation addresses a specific cause. A partial writer diff is untrusted until reviewed.

Do not erase main-checkout changes after a failed non-isolated writer; inspect ownership first.
