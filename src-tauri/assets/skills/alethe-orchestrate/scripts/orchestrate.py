#!/usr/bin/env python3
"""Run bounded CLI-agent batches without a shell or third-party packages."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


PLAN_VERSION = 1
STATE_VERSION = 1
POLICY_VERSION = 1
MAX_WORKERS = 16
DEFAULT_TIMEOUT_SECONDS = 1200
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
UNSAFE_FLAGS = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-skip-permissions",
    "--auto",
}
PROVIDER_ALIASES = {"antigravity": "agy"}
BUILTIN_PROVIDERS = ("codex", "claude", "agy", "kimi")
PROVIDER_CAPABILITIES: Dict[str, Dict[str, bool]] = {
    "codex": {
        "structuredOutput": True,
        "readOnly": True,
        "write": True,
        "resume": True,
        "tokenUsage": True,
    },
    "claude": {
        "structuredOutput": True,
        "readOnly": True,
        "write": True,
        "resume": True,
        "tokenUsage": True,
    },
    "agy": {
        "structuredOutput": True,
        "readOnly": True,
        "write": True,
        "resume": True,
        "tokenUsage": True,
    },
    "kimi": {
        "structuredOutput": True,
        "readOnly": True,
        "write": True,
        "resume": True,
        "tokenUsage": False,
    },
    "custom": {
        "structuredOutput": False,
        "readOnly": False,
        "write": True,
        "resume": False,
        "tokenUsage": False,
    },
}
STRATEGY_ALIASES = {"performance": "parallel"}
STRATEGIES: Dict[str, Dict[str, Any]] = {
    "auto": {
        "maxWorkers": MAX_WORKERS,
        "maxConcurrent": 4,
        "description": "Choose the smallest strategy supported by the plan.",
    },
    "economy": {
        "maxWorkers": 1,
        "maxConcurrent": 1,
        "description": "Minimize agent usage with one concise worker and no speculative fan-out.",
    },
    "balanced": {
        "maxWorkers": 4,
        "maxConcurrent": 2,
        "description": "Use a small general-purpose batch for independent work.",
    },
    "quality": {
        "maxWorkers": 1,
        "maxConcurrent": 1,
        "description": "Run writer, reviewer, and repair work as separate sequential waves.",
    },
    "research": {
        "maxWorkers": 8,
        "maxConcurrent": 4,
        "description": "Compare independent read-only investigations with compact evidence.",
    },
    "parallel": {
        "maxWorkers": 8,
        "maxConcurrent": 4,
        "description": "Implement disjoint units concurrently; every writer uses a worktree.",
    },
    "rescue": {
        "maxWorkers": 1,
        "maxConcurrent": 1,
        "description": "Resume one interrupted or failed provider session without restarting the work.",
    },
}
DEFAULT_POLICY: Dict[str, Any] = {
    "version": POLICY_VERSION,
    "backend": "auto",
    "defaultStrategy": "auto",
    "providerSwitch": "ask",
    "providerOrder": list(BUILTIN_PROVIDERS),
    "conserveProviders": [],
    "maxWorkers": MAX_WORKERS,
    "maxConcurrent": 4,
    "budget": {},
    "modelTiers": {},
}
BUDGET_FIELDS = ("maxCalls", "maxWallSeconds", "maxEstimatedPromptTokens", "maxReportedCostUsd")
MAX_BUDGET_CALLS = 64
MODEL_TIERS = ("cheap", "standard", "strong")
MODEL_TIER_KEYS = ("cheap", "strong")
WORKER_CONTRACT = """

Runtime contract:
- Work only on this assigned unit and only inside the stated scope.
- Do not delegate, spawn subagents, or launch another agent CLI.
- Do not commit, push, tag, release, or open a pull request.
- End with a concise handoff: outcome, evidence, files changed, checks run, failures, and risks.
""".strip()


class PlanError(ValueError):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PlanError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise PlanError(f"invalid JSON in {path}: {error}") from error


def default_policy_path() -> Path:
    configured = os.environ.get("ALETHE_ORCHESTRATE_POLICY")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Alethe" / "alethe-orchestrate.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "alethe" / "orchestrate.json"


def normalized_strategy(value: Any, label: str) -> str:
    strategy = require_string(value, label).lower()
    strategy = STRATEGY_ALIASES.get(strategy, strategy)
    if strategy not in STRATEGIES:
        raise PlanError(f"{label} must be one of {', '.join(STRATEGIES)}")
    return strategy


def validate_provider_list(value: Any, label: str, *, allow_empty: bool) -> List[str]:
    if not isinstance(value, list):
        raise PlanError(f"{label} must be an array")
    providers: List[str] = []
    for raw in value:
        provider = require_string(raw, label).lower()
        provider = PROVIDER_ALIASES.get(provider, provider)
        if provider not in BUILTIN_PROVIDERS:
            raise PlanError(f"{label} contains unsupported provider {provider}")
        if provider not in providers:
            providers.append(provider)
    if not allow_empty and not providers:
        raise PlanError(f"{label} must contain at least one provider")
    return providers


def validate_budget(raw: Any, label: str) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PlanError(f"{label} must be an object")
    unknown = set(raw) - set(BUDGET_FIELDS)
    if unknown:
        raise PlanError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    budget: Dict[str, Any] = {}
    max_calls = raw.get("maxCalls")
    if max_calls is not None:
        if not isinstance(max_calls, int) or isinstance(max_calls, bool):
            raise PlanError(f"{label}.maxCalls must be an integer")
        if not 1 <= max_calls <= MAX_BUDGET_CALLS:
            raise PlanError(f"{label}.maxCalls must be between 1 and {MAX_BUDGET_CALLS}")
        budget["maxCalls"] = max_calls
    max_wall = raw.get("maxWallSeconds")
    if max_wall is not None:
        if not isinstance(max_wall, int) or isinstance(max_wall, bool):
            raise PlanError(f"{label}.maxWallSeconds must be an integer")
        if not 1 <= max_wall <= 86400:
            raise PlanError(f"{label}.maxWallSeconds must be between 1 and 86400")
        budget["maxWallSeconds"] = max_wall
    max_tokens = raw.get("maxEstimatedPromptTokens")
    if max_tokens is not None:
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
            raise PlanError(f"{label}.maxEstimatedPromptTokens must be a positive integer")
        budget["maxEstimatedPromptTokens"] = max_tokens
    max_cost = raw.get("maxReportedCostUsd")
    if max_cost is not None:
        if not isinstance(max_cost, (int, float)) or isinstance(max_cost, bool) or max_cost <= 0:
            raise PlanError(f"{label}.maxReportedCostUsd must be a positive number")
        budget["maxReportedCostUsd"] = max_cost
    return budget


def estimated_prompt_tokens(text: str) -> int:
    """Prompt-only chars/4 heuristic; provider-side context is additional and not estimable."""
    return max(1, -(-len(text) // 4))


def reported_total_tokens(usage: Any) -> Optional[int]:
    if not isinstance(usage, dict):
        return None
    for key in ("total_tokens", "totalTokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    # No explicit total: sum the parts. Codex's input_tokens already includes its
    # cached_input_tokens, so the cached field is deliberately not added.
    parts = [
        usage.get(key)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    ]
    numeric = [int(value) for value in parts if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return sum(numeric) if numeric else None


def validate_model_tiers(raw: Any, label: str) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PlanError(f"{label} must be an object")
    unknown = set(raw) - set(MODEL_TIER_KEYS)
    if unknown:
        raise PlanError(f"{label} has unknown tiers: {', '.join(sorted(unknown))}")
    tiers: Dict[str, Any] = {}
    for tier, mapping in raw.items():
        if not isinstance(mapping, dict):
            raise PlanError(f"{label}.{tier} must be an object")
        entries: Dict[str, Any] = {}
        for raw_provider, spec in mapping.items():
            provider = PROVIDER_ALIASES.get(str(raw_provider).lower(), str(raw_provider).lower())
            if provider not in BUILTIN_PROVIDERS:
                raise PlanError(f"{label}.{tier} contains unsupported provider {provider}")
            if not isinstance(spec, dict):
                raise PlanError(f"{label}.{tier}.{provider} must be an object")
            unknown_spec = set(spec) - {"model", "effort"}
            if unknown_spec:
                raise PlanError(
                    f"{label}.{tier}.{provider} has unknown fields: {', '.join(sorted(unknown_spec))}"
                )
            model = optional_name(spec.get("model"), f"{label}.{tier}.{provider}.model")
            effort = optional_name(spec.get("effort"), f"{label}.{tier}.{provider}.effort")
            if not model and not effort:
                raise PlanError(f"{label}.{tier}.{provider} must set model or effort")
            entries[provider] = {
                key: value for key, value in (("model", model), ("effort", effort)) if value
            }
        if entries:
            tiers[tier] = entries
    return tiers


def resolve_model_tier(worker: Dict[str, Any], policy: Dict[str, Any]) -> None:
    tier = worker["tier"]
    if tier == "standard":
        worker["modelNote"] = None
        return
    spec = policy["modelTiers"].get(tier, {}).get(worker["provider"], {})
    if spec:
        worker["model"] = spec.get("model")
        worker["effort"] = spec.get("effort")
        parts = ", ".join(f"{key} {value}" for key, value in spec.items())
        worker["modelNote"] = f"tier {tier} on {worker['provider']}: {parts}"
    else:
        worker["model"] = None
        worker["effort"] = None
        worker["modelNote"] = f"tier {tier}: no model configured for {worker['provider']}"


def validate_policy(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise PlanError("policy must be a JSON object")
    unknown = set(raw) - set(DEFAULT_POLICY)
    if unknown:
        raise PlanError(f"unknown policy fields: {', '.join(sorted(unknown))}")
    if raw.get("version", POLICY_VERSION) != POLICY_VERSION:
        raise PlanError(f"policy.version must be {POLICY_VERSION}")
    backend = raw.get("backend", DEFAULT_POLICY["backend"])
    if backend not in ("auto", "skill", "mcp"):
        raise PlanError("policy.backend must be auto, skill or mcp")
    provider_switch = raw.get("providerSwitch", DEFAULT_POLICY["providerSwitch"])
    if provider_switch not in ("ask", "automatic", "never"):
        raise PlanError("policy.providerSwitch must be ask, automatic or never")
    max_workers = raw.get("maxWorkers", DEFAULT_POLICY["maxWorkers"])
    max_concurrent = raw.get("maxConcurrent", DEFAULT_POLICY["maxConcurrent"])
    for label, value in (("maxWorkers", max_workers), ("maxConcurrent", max_concurrent)):
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_WORKERS:
            raise PlanError(f"policy.{label} must be between 1 and {MAX_WORKERS}")
    return {
        "version": POLICY_VERSION,
        "backend": backend,
        "defaultStrategy": normalized_strategy(
            raw.get("defaultStrategy", DEFAULT_POLICY["defaultStrategy"]),
            "policy.defaultStrategy",
        ),
        "providerSwitch": provider_switch,
        "providerOrder": validate_provider_list(
            raw.get("providerOrder", DEFAULT_POLICY["providerOrder"]),
            "policy.providerOrder",
            allow_empty=False,
        ),
        "conserveProviders": validate_provider_list(
            raw.get("conserveProviders", DEFAULT_POLICY["conserveProviders"]),
            "policy.conserveProviders",
            allow_empty=True,
        ),
        "maxWorkers": max_workers,
        "maxConcurrent": max_concurrent,
        "budget": validate_budget(raw.get("budget"), "policy.budget"),
        "modelTiers": validate_model_tiers(raw.get("modelTiers"), "policy.modelTiers"),
    }


def load_policy(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return dict(DEFAULT_POLICY)
    return validate_policy(read_json(path))


def save_policy(path: Path, policy: Dict[str, Any]) -> None:
    atomic_write_json(path, validate_policy(policy))


def available_builtin_providers() -> List[str]:
    commands = {"agy": "agy"}
    return [
        provider
        for provider in BUILTIN_PROVIDERS
        if shutil.which(commands.get(provider, provider)) is not None
    ]


def choose_provider(requested: str, worker: Dict[str, Any], policy: Dict[str, Any]) -> str:
    if requested == "custom":
        return requested
    available = available_builtin_providers()
    conserved = set(policy["conserveProviders"])
    candidates = [
        provider
        for provider in policy["providerOrder"]
        if provider in available and provider not in conserved
    ]
    if worker.get("resume") and requested == "auto":
        raise PlanError(
            f"worker {worker['id']} must name the provider that owns its resumed session"
        )
    if requested == "auto":
        if worker.get("model") or worker.get("effort"):
            raise PlanError(f"worker {worker['id']} cannot combine provider auto with model or effort")
        if not candidates:
            raise PlanError("no unconserved built-in provider is available")
        return candidates[0]
    if requested not in conserved:
        return requested
    switch = policy["providerSwitch"]
    if switch == "never":
        return requested
    if switch == "ask":
        raise PlanError(
            f"provider {requested} is marked for conservation; choose another provider or enable automatic switching"
        )
    alternatives = [provider for provider in candidates if provider != requested]
    if not alternatives:
        raise PlanError(f"no available provider can replace conserved provider {requested}")
    if worker.get("model") or worker.get("effort") or worker.get("resume"):
        raise PlanError(
            f"worker {worker['id']} pins provider-specific state and cannot switch automatically"
        )
    return alternatives[0]


def infer_strategy(workers: Sequence[Dict[str, Any]]) -> Tuple[str, str]:
    if len(workers) == 1:
        if workers[0].get("resume"):
            return "rescue", "one resumed worker session"
        return "economy", "one new worker"
    if all(worker["mode"] == "read-only" for worker in workers):
        return "research", f"{len(workers)} read-only workers"
    writers = [worker for worker in workers if worker["mode"] == "write"]
    if len(writers) >= 2 and all(worker["isolate"] for worker in writers):
        return "parallel", f"{len(writers)} isolated writers"
    return "balanced", "mixed modes or a non-isolated writer"


def enforce_strategy(
    requested: str,
    workers: Sequence[Dict[str, Any]],
    requested_concurrency: Optional[int],
    policy: Dict[str, Any],
) -> Tuple[str, int]:
    strategy = infer_strategy(workers)[0] if requested == "auto" else requested
    rules = STRATEGIES[strategy]
    max_workers = min(rules["maxWorkers"], policy["maxWorkers"])
    if len(workers) > max_workers:
        raise PlanError(f"strategy {strategy} allows at most {max_workers} workers")
    if strategy == "research" and any(worker["mode"] != "read-only" for worker in workers):
        raise PlanError("strategy research accepts read-only workers only")
    if strategy == "parallel" and any(
        worker["mode"] == "write" and not worker["isolate"] for worker in workers
    ):
        raise PlanError("strategy parallel requires every writer to use isolate: true")
    if strategy == "rescue" and (len(workers) != 1 or not workers[0].get("resume")):
        raise PlanError("strategy rescue requires exactly one worker with resume")
    concurrency = requested_concurrency or rules["maxConcurrent"]
    concurrency = min(concurrency, rules["maxConcurrent"], policy["maxConcurrent"])
    return strategy, concurrency


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{label} must be a non-empty string")
    return value.strip()


def optional_name(value: Any, label: str) -> Optional[str]:
    if value is None:
        return None
    text = require_string(value, label)
    if not SAFE_NAME_PATTERN.fullmatch(text) or text.startswith("-"):
        raise PlanError(f"{label} contains unsafe characters")
    return text


def normalized_provider(value: Any) -> str:
    provider = require_string(value, "worker.provider").lower()
    provider = PROVIDER_ALIASES.get(provider, provider)
    if provider != "auto" and provider not in PROVIDER_CAPABILITIES:
        raise PlanError(f"unsupported provider {provider}; use provider custom for another CLI")
    return provider


def validate_relative_path(value: Any, label: str) -> str:
    text = require_string(value, label).replace("\\", "/")
    path = Path(text)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise PlanError(f"{label} must be a safe relative path")
    return text


def load_task(worker: Dict[str, Any], plan_dir: Path) -> str:
    inline = worker.get("task")
    task_file = worker.get("taskFile")
    if (inline is None) == (task_file is None):
        raise PlanError(f"worker {worker.get('id', '?')} must set exactly one of task or taskFile")
    if inline is not None:
        task = require_string(inline, "worker.task")
    else:
        path = Path(require_string(task_file, "worker.taskFile"))
        if not path.is_absolute():
            path = plan_dir / path
        try:
            task = path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise PlanError(f"cannot read task file {path}: {error}") from error
        if not task:
            raise PlanError(f"task file {path} is empty")
    return f"{task.rstrip()}\n\n{WORKER_CONTRACT}\n"


def validate_plan(
    raw: Any,
    plan_path: Path,
    policy: Optional[Dict[str, Any]] = None,
    strategy_override: Optional[str] = None,
) -> Dict[str, Any]:
    policy = validate_policy(policy or DEFAULT_POLICY)
    if not isinstance(raw, dict):
        raise PlanError("plan must be a JSON object")
    unknown = set(raw) - {
        "version",
        "strategy",
        "preset",
        "maxConcurrent",
        "defaultTimeoutSeconds",
        "budget",
        "workers",
    }
    if unknown:
        raise PlanError(f"unknown plan fields: {', '.join(sorted(unknown))}")
    if raw.get("version") != PLAN_VERSION:
        raise PlanError(f"plan.version must be {PLAN_VERSION}")
    if strategy_override is None and "strategy" in raw and "preset" in raw:
        raise PlanError("use strategy, not strategy and legacy preset together")
    strategy_value = strategy_override or raw.get(
        "strategy", raw.get("preset", policy["defaultStrategy"])
    )
    requested_strategy = normalized_strategy(strategy_value, "plan.strategy")
    requested_concurrency = raw.get("maxConcurrent")
    if requested_concurrency is not None:
        if not isinstance(requested_concurrency, int) or isinstance(requested_concurrency, bool):
            raise PlanError("maxConcurrent must be an integer")
        if not 1 <= requested_concurrency <= MAX_WORKERS:
            raise PlanError(f"maxConcurrent must be between 1 and {MAX_WORKERS}")
    default_timeout = raw.get("defaultTimeoutSeconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(default_timeout, int) or isinstance(default_timeout, bool):
        raise PlanError("defaultTimeoutSeconds must be an integer")
    if not 1 <= default_timeout <= 86400:
        raise PlanError("defaultTimeoutSeconds must be between 1 and 86400")
    workers = raw.get("workers")
    if not isinstance(workers, list) or not workers:
        raise PlanError("workers must be a non-empty array")
    if len(workers) > MAX_WORKERS:
        raise PlanError(f"a plan may contain at most {MAX_WORKERS} workers")

    seen = set()
    normalized_workers: List[Dict[str, Any]] = []
    for index, item in enumerate(workers):
        if not isinstance(item, dict):
            raise PlanError(f"workers[{index}] must be an object")
        unknown = set(item) - {
            "id",
            "provider",
            "mode",
            "cwd",
            "task",
            "taskFile",
            "timeoutSeconds",
            "isolate",
            "seedPaths",
            "model",
            "effort",
            "tier",
            "resume",
            "command",
            "args",
            "promptTransport",
            "outputFormat",
            "allowUnsafe",
        }
        if unknown:
            raise PlanError(
                f"worker {item.get('id', index)} has unknown fields: {', '.join(sorted(unknown))}"
            )
        worker_id = require_string(item.get("id"), f"workers[{index}].id")
        if not ID_PATTERN.fullmatch(worker_id):
            raise PlanError(f"worker id {worker_id!r} is not safe")
        if worker_id in seen:
            raise PlanError(f"duplicate worker id {worker_id}")
        seen.add(worker_id)
        requested_provider = normalized_provider(item.get("provider"))
        mode = item.get("mode", "read-only")
        if mode not in ("read-only", "write"):
            raise PlanError(f"worker {worker_id} mode must be read-only or write")
        cwd = Path(require_string(item.get("cwd"), f"worker {worker_id}.cwd")).expanduser()
        if not cwd.is_absolute():
            cwd = (plan_path.parent / cwd).resolve()
        if not cwd.is_dir():
            raise PlanError(f"worker {worker_id} cwd is not a directory: {cwd}")
        timeout = item.get("timeoutSeconds", default_timeout)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 86400:
            raise PlanError(f"worker {worker_id} timeoutSeconds must be between 1 and 86400")
        isolate = item.get("isolate", False)
        if not isinstance(isolate, bool):
            raise PlanError(f"worker {worker_id} isolate must be a boolean")
        if isolate and mode != "write":
            raise PlanError(f"worker {worker_id} only needs isolate in write mode")
        seed_paths = item.get("seedPaths", [])
        if not isinstance(seed_paths, list):
            raise PlanError(f"worker {worker_id} seedPaths must be an array")
        seed_paths = [
            validate_relative_path(value, f"worker {worker_id}.seedPaths") for value in seed_paths
        ]
        if seed_paths and not isolate:
            raise PlanError(f"worker {worker_id} seedPaths requires isolate")
        model = optional_name(item.get("model"), f"worker {worker_id}.model")
        effort = optional_name(item.get("effort"), f"worker {worker_id}.effort")
        tier = item.get("tier", "standard")
        if tier not in MODEL_TIERS:
            raise PlanError(f"worker {worker_id} tier must be one of {', '.join(MODEL_TIERS)}")
        if tier != "standard" and (model or effort):
            raise PlanError(
                f"worker {worker_id} cannot combine tier {tier} with an explicit model or effort"
            )
        resume = optional_name(item.get("resume"), f"worker {worker_id}.resume")
        if resume and isolate:
            raise PlanError(f"worker {worker_id} cannot combine resume with isolate")
        task = load_task(item, plan_path.parent)
        normalized = {
            "id": worker_id,
            "provider": requested_provider,
            "requestedProvider": requested_provider,
            "mode": mode,
            "cwd": str(cwd),
            "timeoutSeconds": timeout,
            "isolate": isolate,
            "seedPaths": seed_paths,
            "model": model,
            "effort": effort,
            "tier": tier,
            "resume": resume,
            "task": task,
        }
        if requested_provider == "custom":
            normalized.update(validate_custom(item, worker_id))
        chosen_provider = choose_provider(requested_provider, normalized, policy)
        normalized["provider"] = chosen_provider
        if chosen_provider == requested_provider:
            normalized["routingNote"] = None
        elif requested_provider == "auto":
            normalized["routingNote"] = "selected by provider order"
        else:
            normalized["routingNote"] = (
                f"conserved provider {requested_provider} replaced by providerSwitch=automatic"
            )
        resolve_model_tier(normalized, policy)
        normalized["estimatedPromptTokens"] = estimated_prompt_tokens(task)
        normalized_workers.append(normalized)

    budget = {**policy["budget"], **validate_budget(raw.get("budget"), "plan.budget")}
    token_limit = budget.get("maxEstimatedPromptTokens")
    if token_limit is not None:
        estimate = sum(worker["estimatedPromptTokens"] for worker in normalized_workers)
        if estimate > token_limit:
            raise PlanError(
                f"estimated prompt tokens {estimate} exceed budget maxEstimatedPromptTokens {token_limit}"
            )

    strategy, max_concurrent = enforce_strategy(
        requested_strategy, normalized_workers, requested_concurrency, policy
    )
    if strategy == "economy":
        # Economy means the cheapest run: default its single worker to the cheap tier unless
        # the plan already pinned a tier or a model.
        worker = normalized_workers[0]
        if worker["tier"] == "standard" and not worker["model"] and not worker["effort"]:
            worker["tier"] = "cheap"
            resolve_model_tier(worker, policy)

    return {
        "version": PLAN_VERSION,
        "strategy": strategy,
        "requestedStrategy": requested_strategy,
        "maxConcurrent": max_concurrent,
        "defaultTimeoutSeconds": default_timeout,
        "budget": budget,
        "policy": policy,
        "workers": normalized_workers,
    }


def validate_custom(worker: Dict[str, Any], worker_id: str) -> Dict[str, Any]:
    command = require_string(worker.get("command"), f"worker {worker_id}.command")
    args = worker.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise PlanError(f"worker {worker_id}.args must be an array of strings")
    transport = worker.get("promptTransport", "stdin")
    if transport not in ("stdin", "argument"):
        raise PlanError(f"worker {worker_id}.promptTransport must be stdin or argument")
    output_format = worker.get("outputFormat", "text")
    if output_format not in ("text", "json", "jsonl"):
        raise PlanError(f"worker {worker_id}.outputFormat must be text, json or jsonl")
    allow_unsafe = worker.get("allowUnsafe", False)
    if not isinstance(allow_unsafe, bool):
        raise PlanError(f"worker {worker_id}.allowUnsafe must be a boolean")
    if not allow_unsafe and any(arg in UNSAFE_FLAGS for arg in args):
        raise PlanError(f"worker {worker_id} uses an unsafe flag without allowUnsafe")
    return {
        "command": command,
        "args": args,
        "promptTransport": transport,
        "outputFormat": output_format,
        "allowUnsafe": allow_unsafe,
    }


def run_git(cwd: Path, args: Sequence[str], input_bytes: Optional[bytes] = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise PlanError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


def is_inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path.resolve()), str(root.resolve())]) == str(root.resolve())
    except ValueError:
        return False


def reject_seed_symlinks(path: Path) -> None:
    if path.is_symlink():
        raise PlanError(f"seed path is a symlink: {path}")
    if not path.is_dir():
        return
    for current, directories, files in os.walk(path, followlinks=False):
        for name in [*directories, *files]:
            candidate = Path(current) / name
            if candidate.is_symlink():
                raise PlanError(f"seed path contains a symlink: {candidate}")


def prepare_worktree(worker: Dict[str, Any], run_id: str) -> Path:
    origin = Path(worker["cwd"])
    root = Path(run_git(origin, ["rev-parse", "--show-toplevel"]).decode().strip()).resolve()
    target = root / ".alethe" / "worktrees" / f"skill-{run_id}-{worker['id']}"
    if target.exists():
        raise PlanError(f"worktree target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    run_git(root, ["worktree", "add", "--detach", str(target), "HEAD"])

    seed_paths: List[str] = worker["seedPaths"]
    if seed_paths:
        patch = run_git(root, ["diff", "--binary", "HEAD", "--", *seed_paths])
        if patch.strip():
            run_git(target, ["apply", "-"], patch)
        for relative in seed_paths:
            source = root / relative
            if not source.exists() and not source.is_symlink():
                continue
            tracked = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
            if tracked:
                continue
            if not is_inside(source, root):
                raise PlanError(f"seed path escapes the repository: {relative}")
            reject_seed_symlinks(source)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(source, destination, follow_symlinks=False)
    return target


def command_help(executable: str, cache: Dict[str, str], lock: threading.Lock) -> str:
    with lock:
        if executable in cache:
            return cache[executable]
    try:
        result = subprocess.run(
            [executable, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=8,
            check=False,
        )
        output = result.stdout
    except (OSError, subprocess.TimeoutExpired):
        output = ""
    with lock:
        cache[executable] = output
    return output


def resolve_executable(name: str) -> str:
    expanded = str(Path(name).expanduser())
    if os.path.dirname(expanded):
        path = Path(expanded)
        if not path.is_file():
            raise PlanError(f"executable not found: {name}")
        return str(path.resolve())
    resolved = shutil.which(name)
    if not resolved:
        raise PlanError(f"executable not found on PATH: {name}")
    return resolved


def in_git_worktree(cwd: Path) -> bool:
    return subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--is-inside-work-tree"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def build_invocation(
    worker: Dict[str, Any],
    cwd: Path,
    worker_dir: Path,
    help_cache: Dict[str, str],
    help_lock: threading.Lock,
    timeout: int,
) -> Tuple[List[str], bool, str, Path]:
    provider = worker["provider"]
    mode = worker["mode"]
    model = worker["model"]
    effort = worker["effort"]
    resume = worker["resume"]
    result_path = worker_dir / "result.md"

    if provider == "codex":
        executable = resolve_executable("codex")
        if resume:
            args = [executable, "exec", "resume", "--json", "-o", str(result_path)]
            if model:
                args += ["--model", model]
            if not in_git_worktree(cwd):
                args.append("--skip-git-repo-check")
            args += [resume, "-"]
        else:
            sandbox = "read-only" if mode == "read-only" else "workspace-write"
            args = [
                executable,
                "exec",
                "--json",
                "--sandbox",
                sandbox,
                "--cd",
                str(cwd),
                "--output-last-message",
                str(result_path),
            ]
            if model:
                args += ["--model", model]
            if effort:
                args += ["--config", f'model_reasoning_effort="{effort}"']
            if not in_git_worktree(cwd):
                args.append("--skip-git-repo-check")
            args.append("-")
        return args, True, "jsonl", result_path

    if provider == "claude":
        executable = resolve_executable("claude")
        permission = "plan" if mode == "read-only" else "acceptEdits"
        args = [executable, "--print", "--output-format", "json", "--permission-mode", permission]
        help_text = command_help(executable, help_cache, help_lock)
        if "--permission-prompts" in help_text:
            args += ["--permission-prompts", "none"]
        if "--disable-slash-commands" in help_text:
            args.append("--disable-slash-commands")
        if model:
            args += ["--model", model]
        if effort:
            args += ["--effort", effort]
        if resume:
            args += ["--resume", resume]
        return args, True, "json", result_path

    if provider == "agy":
        executable = resolve_executable("agy")
        agent_mode = "plan" if mode == "read-only" else "accept-edits"
        # The prompt must be attached to --print: agy otherwise treats the next flag as the
        # prompt and drops the real one.
        args = [
            executable,
            "--output-format",
            "json",
            "--mode",
            agent_mode,
            "--print-timeout",
            f"{timeout}s",
        ]
        help_text = command_help(executable, help_cache, help_lock)
        if "--disable-slash-commands" in help_text:
            args.append("--disable-slash-commands")
        if model:
            args += ["--model", model]
        if effort:
            args += ["--effort", effort]
        if resume:
            args += ["--conversation", resume]
        args.append(f"--print={worker['task']}")
        return args, False, "json", result_path

    if provider == "kimi":
        executable = resolve_executable("kimi")
        args = [executable, "--prompt", worker["task"], "--output-format", "stream-json"]
        # --plan cannot combine with --prompt; read-only relies on the worker contract and
        # Kimi's default ask mode instead.
        if mode != "read-only":
            args.append("--yolo")
        if model:
            args += ["--model", model]
        if resume:
            args += ["--session", resume]
        return args, False, "jsonl", result_path

    executable = resolve_executable(worker["command"])
    replacements = {"{cwd}": str(cwd), "{result}": str(result_path)}
    custom_args = []
    for raw in worker["args"]:
        value = raw
        for marker, replacement in replacements.items():
            value = value.replace(marker, replacement)
        custom_args.append(value)
    stdin_prompt = worker["promptTransport"] == "stdin"
    if not stdin_prompt:
        custom_args.append(worker["task"])
    return [executable, *custom_args], stdin_prompt, worker["outputFormat"], result_path


def start_process(args: Sequence[str], cwd: Path, stdin: Any, stdout: Any, stderr: Any) -> subprocess.Popen:
    options: Dict[str, Any] = {
        "args": list(args),
        "cwd": str(cwd),
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
        "shell": False,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        options["start_new_session"] = True
    return subprocess.Popen(**options)


def stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def json_values(text: str, output_format: str) -> List[Any]:
    if not text.strip():
        return []
    try:
        if output_format == "json":
            return [json.loads(text)]
        if output_format == "jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return []
    return []


def text_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in ("result", "output", "text", "content", "response"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    message = value.get("message")
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            return "".join(parts).strip()
    for key in ("part", "data", "response"):
        nested = value.get(key)
        if isinstance(nested, dict):
            text = text_from_value(nested)
            if text:
                return text
    return ""


def result_text_from_values(values: Sequence[Any]) -> str:
    for value in reversed(values):
        if isinstance(value, dict) and value.get("type") == "result":
            text = text_from_value(value)
            if text:
                return text
    chunks = []
    for value in values:
        if not isinstance(value, dict):
            continue
        kind = value.get("type") or value.get("role")
        if kind not in ("assistant", "message", "text", "output"):
            continue
        text = text_from_value(value)
        if text:
            chunks.append(text)
    if chunks:
        return "\n".join(chunks).strip()
    for value in reversed(values):
        text = text_from_value(value)
        if text:
            return text
    return ""


def extract_session(values: Sequence[Any]) -> Optional[str]:
    for value in reversed(values):
        if not isinstance(value, dict):
            continue
        for key in (
            "session_id",
            "sessionId",
            "sessionID",
            "conversation_id",
            "conversationId",
            "thread_id",
        ):
            session = value.get(key)
            if isinstance(session, str) and session.strip():
                return session.strip()
    return None


def extract_metrics(values: Sequence[Any]) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        usage = value.get("usage") or value.get("tokenUsage") or value.get("token_usage")
        if isinstance(usage, dict):
            metrics["usage"] = usage
        cost = value.get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            metrics["totalCostUsd"] = cost
        part = value.get("part")
        if isinstance(part, dict):
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                metrics["usage"] = tokens
            part_cost = part.get("cost")
            if isinstance(part_cost, (int, float)) and not isinstance(part_cost, bool):
                metrics["totalCostUsd"] = part_cost
    return metrics


def classify_result(
    provider: str,
    output_format: str,
    stdout_path: Path,
    result_path: Path,
    exit_code: int,
    timed_out: bool,
) -> Tuple[str, str, List[Any], Optional[str]]:
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
    values = json_values(stdout_text, output_format)
    error: Optional[str] = None
    text = ""

    if result_path.is_file():
        text = result_path.read_text(encoding="utf-8", errors="replace").strip()
    elif output_format == "text":
        text = stdout_text.strip()
    else:
        text = result_text_from_values(values)
        if text:
            result_path.write_text(text + "\n", encoding="utf-8")

    if timed_out:
        return "timed_out", text, values, "worker exceeded its deadline"
    if exit_code != 0:
        return "failed", text, values, f"worker exited with status {exit_code}"
    if output_format in ("json", "jsonl") and not values:
        return "failed", text, values, "worker output was not valid structured JSON"
    if provider in ("claude", "agy") and values:
        final = values[-1]
        if isinstance(final, dict):
            if final.get("is_error") is True or str(final.get("subtype", "")).startswith("error"):
                error = f"worker returned {final.get('subtype', 'an error result')}"
            if provider == "claude" and final.get("type") != "result":
                error = "Claude output did not end with a result object"
    if error:
        return "failed", text, values, error
    if not text:
        return "failed", text, values, "worker produced no final result"
    return "succeeded", text, values, None


def capture_diff(cwd: Path, worker_dir: Path, isolated: bool) -> Optional[Path]:
    if not in_git_worktree(cwd):
        return None
    if isolated:
        untracked = run_git(cwd, ["ls-files", "--others", "--exclude-standard", "-z"])
        paths = [
            value.decode("utf-8", errors="surrogateescape")
            for value in untracked.split(b"\0")
            if value
        ]
        if paths:
            run_git(cwd, ["add", "-N", "--", *paths])
    status = run_git(cwd, ["status", "--short"])
    (worker_dir / "status.txt").write_bytes(status)
    patch = run_git(cwd, ["diff", "--binary", "HEAD"])
    if not patch.strip():
        return None
    diff_path = worker_dir / "diff.patch"
    diff_path.write_bytes(patch)
    return diff_path


class BatchState:
    def __init__(self, path: Path, initial: Dict[str, Any]) -> None:
        self.path = path
        self.value = initial
        self.lock = threading.Lock()
        atomic_write_json(self.path, self.value)

    def update_worker(self, worker_id: str, patch: Dict[str, Any]) -> None:
        with self.lock:
            self.value["workers"][worker_id].update(patch)
            atomic_write_json(self.path, self.value)

    def finish(self, status: str, extra: Optional[Dict[str, Any]] = None) -> None:
        with self.lock:
            self.value["status"] = status
            self.value["finishedAt"] = now_iso()
            if extra:
                self.value.update(extra)
            atomic_write_json(self.path, self.value)


class ProcessRegistry:
    def __init__(self) -> None:
        self.processes: Dict[int, subprocess.Popen] = {}
        self.lock = threading.Lock()

    def add(self, process: subprocess.Popen) -> None:
        with self.lock:
            self.processes[process.pid] = process

    def remove(self, process: subprocess.Popen) -> None:
        with self.lock:
            self.processes.pop(process.pid, None)

    def stop_all(self) -> None:
        with self.lock:
            processes = list(self.processes.values())
        for process in processes:
            stop_process_tree(process)


class BudgetTracker:
    """Thread-safe batch budget. Gates are checked at worker submit time, so with
    maxConcurrent > 1 they are approximate: already-running workers finish."""

    def __init__(self, budget: Dict[str, Any]) -> None:
        self.budget = budget
        self.lock = threading.Lock()
        self.calls = 0
        self.started_clock = time.monotonic()
        self.reported_cost = 0.0
        self.cost_seen = False
        self.reported_tokens = 0
        self.tokens_seen = False

    def remaining_wall(self) -> Optional[float]:
        limit = self.budget.get("maxWallSeconds")
        if limit is None:
            return None
        return limit - (time.monotonic() - self.started_clock)

    def admit(self) -> Optional[str]:
        with self.lock:
            max_calls = self.budget.get("maxCalls")
            if max_calls is not None and self.calls >= max_calls:
                return f"budget maxCalls {max_calls} exhausted"
            max_wall = self.budget.get("maxWallSeconds")
            if max_wall is not None and time.monotonic() - self.started_clock >= max_wall:
                return f"budget maxWallSeconds {max_wall} exhausted"
            max_cost = self.budget.get("maxReportedCostUsd")
            if max_cost is not None and self.cost_seen and self.reported_cost >= max_cost:
                return f"budget maxReportedCostUsd {max_cost} exhausted"
            self.calls += 1
            return None

    def record(self, metrics: Dict[str, Any]) -> None:
        with self.lock:
            cost = metrics.get("totalCostUsd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                self.cost_seen = True
                self.reported_cost += cost
            tokens = reported_total_tokens(metrics.get("usage"))
            if tokens is not None:
                self.tokens_seen = True
                self.reported_tokens += tokens

    def usage(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "calls": self.calls,
                "wallSeconds": round(time.monotonic() - self.started_clock, 3),
                "reportedCostUsd": round(self.reported_cost, 6) if self.cost_seen else None,
                "reportedTokens": self.reported_tokens if self.tokens_seen else None,
            }


def run_worker(
    worker: Dict[str, Any],
    run_id: str,
    run_dir: Path,
    state: BatchState,
    help_cache: Dict[str, str],
    help_lock: threading.Lock,
    registry: ProcessRegistry,
    tracker: BudgetTracker,
) -> Dict[str, Any]:
    worker_id = worker["id"]
    worker_dir = run_dir / "workers" / worker_id
    worker_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = worker_dir / "prompt.txt"
    stdout_path = worker_dir / "stdout.log"
    stderr_path = worker_dir / "stderr.log"
    outcome_path = worker_dir / "outcome.json"
    prompt_path.write_text(worker["task"], encoding="utf-8")
    started_at = now_iso()
    started_clock = time.monotonic()
    process: Optional[subprocess.Popen] = None
    timed_out = False
    exit_code = -1

    refusal = tracker.admit()
    if refusal is not None:
        outcome = {
            "id": worker_id,
            "provider": worker["provider"],
            "status": "skipped",
            "exitCode": None,
            "timedOut": False,
            "startedAt": started_at,
            "finishedAt": now_iso(),
            "durationSeconds": 0.0,
            "cwd": worker["cwd"],
            "worktree": None,
            "resultPath": None,
            "stdoutPath": None,
            "stderrPath": None,
            "diffPath": None,
            "sessionId": None,
            "metrics": {},
            "error": refusal,
        }
        atomic_write_json(outcome_path, outcome)
        state.update_worker(worker_id, outcome)
        return outcome

    try:
        cwd = prepare_worktree(worker, run_id) if worker["isolate"] else Path(worker["cwd"])
        timeout = worker["timeoutSeconds"]
        remaining_wall = tracker.remaining_wall()
        if remaining_wall is not None:
            timeout = max(1, int(min(timeout, remaining_wall)))
        args, stdin_prompt, output_format, result_path = build_invocation(
            worker, cwd, worker_dir, help_cache, help_lock, timeout
        )
        state.update_worker(
            worker_id,
            {
                "status": "running",
                "startedAt": started_at,
                "cwd": str(cwd),
                "worktree": str(cwd) if worker["isolate"] else None,
            },
        )
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            stdin_mode = subprocess.PIPE if stdin_prompt else subprocess.DEVNULL
            process = start_process(args, cwd, stdin_mode, stdout_file, stderr_file)
            registry.add(process)
            if stdin_prompt and process.stdin is not None:
                process.stdin.write(worker["task"].encode("utf-8"))
                process.stdin.close()
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                stop_process_tree(process)
                exit_code = process.returncode if process.returncode is not None else -1
            registry.remove(process)

        status, result_text, values, error = classify_result(
            worker["provider"], output_format, stdout_path, result_path, exit_code, timed_out
        )
        metrics = extract_metrics(values)
        tracker.record(metrics)
        diff_path = (
            capture_diff(cwd, worker_dir, worker["isolate"])
            if worker["mode"] == "write"
            else None
        )
        outcome = {
            "id": worker_id,
            "provider": worker["provider"],
            "status": status,
            "exitCode": exit_code,
            "timedOut": timed_out,
            "startedAt": started_at,
            "finishedAt": now_iso(),
            "durationSeconds": round(time.monotonic() - started_clock, 3),
            "cwd": str(cwd),
            "worktree": str(cwd) if worker["isolate"] else None,
            "resultPath": str(result_path),
            "stdoutPath": str(stdout_path),
            "stderrPath": str(stderr_path),
            "diffPath": str(diff_path) if diff_path else None,
            "sessionId": extract_session(values),
            "metrics": metrics,
            "error": error,
        }
        atomic_write_json(outcome_path, outcome)
        state.update_worker(worker_id, outcome)
        return outcome
    except Exception as error:  # Every worker failure must settle the batch state.
        if process is not None:
            stop_process_tree(process)
            registry.remove(process)
        outcome = {
            "id": worker_id,
            "provider": worker["provider"],
            "status": "failed",
            "exitCode": exit_code,
            "timedOut": timed_out,
            "startedAt": started_at,
            "finishedAt": now_iso(),
            "durationSeconds": round(time.monotonic() - started_clock, 3),
            "cwd": worker["cwd"],
            "worktree": None,
            "resultPath": None,
            "stdoutPath": str(stdout_path),
            "stderrPath": str(stderr_path),
            "diffPath": None,
            "sessionId": None,
            "metrics": {},
            "error": str(error),
        }
        atomic_write_json(outcome_path, outcome)
        state.update_worker(worker_id, outcome)
        return outcome


def default_run_dir() -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return Path(tempfile.gettempdir()) / f"alethe-orchestrate-{stamp}-{suffix}"


def run_batch(
    plan_path: Path,
    requested_run_dir: Optional[Path],
    policy_path: Path,
    strategy_override: Optional[str],
) -> int:
    plan = validate_plan(
        read_json(plan_path), plan_path.resolve(), load_policy(policy_path), strategy_override
    )
    run_dir = (requested_run_dir or default_run_dir()).expanduser().resolve()
    if run_dir.exists() and any(run_dir.iterdir()):
        raise PlanError(f"run directory is not empty: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_dir.name[-8:]
    initial_workers = {
        worker["id"]: {
            "id": worker["id"],
            "provider": worker["provider"],
            "status": "queued",
            "cwd": worker["cwd"],
            "worktree": None,
        }
        for worker in plan["workers"]
    }
    state = BatchState(
        run_dir / "state.json",
        {
            "version": STATE_VERSION,
            "runId": run_id,
            "status": "running",
            "strategy": plan["strategy"],
            "requestedStrategy": plan["requestedStrategy"],
            "maxConcurrent": plan["maxConcurrent"],
            "policyPath": str(policy_path),
            "planPath": str(plan_path.resolve()),
            "runDir": str(run_dir),
            "startedAt": now_iso(),
            "finishedAt": None,
            "budget": plan["budget"],
            "workers": initial_workers,
        },
    )
    help_cache: Dict[str, str] = {}
    help_lock = threading.Lock()
    registry = ProcessRegistry()
    tracker = BudgetTracker(plan["budget"])
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=plan["maxConcurrent"])
    futures: List[concurrent.futures.Future] = []
    try:
        futures = [
            executor.submit(
                run_worker,
                worker,
                run_id,
                run_dir,
                state,
                help_cache,
                help_lock,
                registry,
                tracker,
            )
            for worker in plan["workers"]
        ]
        outcomes = [future.result() for future in concurrent.futures.as_completed(futures)]
    except KeyboardInterrupt:
        registry.stop_all()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        state.finish("cancelled", {"budgetUsage": tracker.usage()})
        print(json.dumps({"status": "cancelled", "runDir": str(run_dir)}))
        return 130
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    succeeded = sum(outcome["status"] == "succeeded" for outcome in outcomes)
    status = "done" if succeeded == len(outcomes) else "partial" if succeeded else "failed"
    budget_usage = tracker.usage()
    state.finish(status, {"budgetUsage": budget_usage})
    print(
        json.dumps(
            {
                "status": status,
                "runDir": str(run_dir),
                "budget": plan["budget"],
                "budgetUsage": budget_usage,
                "workers": outcomes,
            },
            ensure_ascii=False,
        )
    )
    return 0 if status == "done" else 1


def dry_run_payload(plan: Dict[str, Any], plan_path: Path, policy_path: Path) -> Dict[str, Any]:
    if plan["requestedStrategy"] == "auto":
        strategy_reason = f"auto inferred: {infer_strategy(plan['workers'])[1]}"
    else:
        strategy_reason = "requested explicitly"
    wall_limit = plan["budget"].get("maxWallSeconds")
    workers = []
    for worker in plan["workers"]:
        effective_timeout = worker["timeoutSeconds"]
        if wall_limit is not None:
            effective_timeout = min(effective_timeout, wall_limit)
        workers.append(
            {
                "id": worker["id"],
                "requestedProvider": worker["requestedProvider"],
                "provider": worker["provider"],
                "switched": worker["provider"] != worker["requestedProvider"],
                "routingNote": worker["routingNote"],
                "tier": worker["tier"],
                "model": worker["model"],
                "effort": worker["effort"],
                "modelNote": worker["modelNote"],
                "mode": worker["mode"],
                "isolate": worker["isolate"],
                "seedPaths": worker["seedPaths"],
                "timeoutSeconds": worker["timeoutSeconds"],
                "effectiveTimeoutSeconds": effective_timeout,
                "promptChars": len(worker["task"]),
                "estimatedPromptTokens": worker["estimatedPromptTokens"],
                "telemetry": {
                    "tokenUsage": PROVIDER_CAPABILITIES.get(worker["provider"], {}).get(
                        "tokenUsage", False
                    )
                },
            }
        )
    return {
        "dryRun": True,
        "planPath": str(plan_path),
        "policyPath": str(policy_path),
        "strategy": plan["strategy"],
        "requestedStrategy": plan["requestedStrategy"],
        "strategyReason": strategy_reason,
        "maxConcurrent": plan["maxConcurrent"],
        "budget": plan["budget"],
        "workers": workers,
        "totals": {
            "workers": len(workers),
            "estimatedPromptTokens": sum(worker["estimatedPromptTokens"] for worker in workers),
        },
        "estimates": (
            "prompt-only chars/4 heuristic; provider-side context such as repository reads "
            "and tool calls is additional and cannot be estimated"
        ),
    }


def probe_provider(provider: str) -> Dict[str, Any]:
    command = "agy" if provider == "agy" else provider
    executable = shutil.which(command)
    version = None
    if executable:
        try:
            result = subprocess.run(
                [executable, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=5,
                check=False,
            )
            version = result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "provider": provider,
        "installed": executable is not None,
        "executable": executable,
        "version": version,
        "capabilities": PROVIDER_CAPABILITIES[provider],
    }


def doctor_payload(policy_path: Path) -> Dict[str, Any]:
    python_ok = sys.version_info >= (3, 9)
    exists = policy_path.exists()
    policy: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    try:
        policy = load_policy(policy_path)
    except PlanError as plan_error:
        error = str(plan_error)
    providers = [probe_provider(provider) for provider in BUILTIN_PROVIDERS]
    installed = [entry["provider"] for entry in providers if entry["installed"]]
    hints: List[str] = []
    if not python_ok:
        hints.append("python 3.9 or newer is required")
    if not installed:
        hints.append("no built-in provider is installed; install one or use provider custom")
    if not exists:
        hints.append("no saved policy yet; offer the onboarding interview or policy set")
    if error:
        hints.append(f"saved policy is invalid: {error}")
    elif policy and not policy["budget"]:
        hints.append("no default budget; policy set can add hard limits such as --max-calls")
    return {
        "python": {"version": sys.version.split()[0], "ok": python_ok},
        "policyPath": str(policy_path),
        "policy": {
            "exists": exists,
            "valid": error is None,
            "error": error,
            "configured": exists and error is None,
            "budget": policy["budget"] if policy else None,
        },
        "providers": providers,
        "installedProviders": installed,
        "hints": hints,
    }


def scaffold_plan(strategy: str, workers: int, cwd: str, provider: str) -> Dict[str, Any]:
    strategy = normalized_strategy(strategy, "scaffold strategy")
    provider = normalized_provider(provider)
    if strategy == "auto":
        strategy = "economy"
        workers = 1
    rules = STRATEGIES[strategy]
    if not 1 <= workers <= rules["maxWorkers"]:
        raise PlanError(f"strategy {strategy} allows between 1 and {rules['maxWorkers']} workers")
    if strategy == "rescue" and provider == "auto":
        raise PlanError("a rescue scaffold needs --provider naming the session owner")
    write_mode = strategy in ("parallel", "quality")
    entries: List[Dict[str, Any]] = []
    for index in range(workers):
        entry: Dict[str, Any] = {
            "id": f"worker-{index + 1}",
            "provider": provider,
            "mode": "write" if write_mode else "read-only",
            "cwd": cwd,
            "task": "TODO: one bounded unit — goal, definition of done, owned paths, checks.",
        }
        if strategy == "parallel":
            entry["isolate"] = True
        if strategy == "rescue":
            entry["resume"] = "TODO-session-id"
        if provider == "custom":
            entry["command"] = "TODO-executable"
        entries.append(entry)
    return {"version": PLAN_VERSION, "strategy": strategy, "workers": entries}


def strategies_payload() -> List[Dict[str, Any]]:
    return [
        {
            "strategy": name,
            "maxWorkers": rules["maxWorkers"],
            "maxConcurrent": rules["maxConcurrent"],
            "description": rules["description"],
        }
        for name, rules in STRATEGIES.items()
    ]


def update_policy(args: argparse.Namespace, policy_path: Path) -> Dict[str, Any]:
    policy = load_policy(policy_path)
    if args.policy_action == "reset":
        policy = dict(DEFAULT_POLICY)
        save_policy(policy_path, policy)
        return policy
    if args.policy_action == "show":
        return policy
    if args.strategy:
        policy["defaultStrategy"] = normalized_strategy(args.strategy, "policy strategy")
    if args.backend:
        policy["backend"] = args.backend
    if args.provider_switch:
        policy["providerSwitch"] = args.provider_switch
    if args.provider_order:
        policy["providerOrder"] = validate_provider_list(
            args.provider_order, "provider order", allow_empty=False
        )
    if args.conserve_providers is not None:
        policy["conserveProviders"] = validate_provider_list(
            args.conserve_providers, "conserve providers", allow_empty=True
        )
    if args.max_workers is not None:
        policy["maxWorkers"] = args.max_workers
    if args.max_concurrent is not None:
        policy["maxConcurrent"] = args.max_concurrent
    budget = dict(policy.get("budget", {}))
    for key, value in (
        ("maxCalls", args.max_calls),
        ("maxWallSeconds", args.max_wall_seconds),
        ("maxEstimatedPromptTokens", args.max_estimated_prompt_tokens),
        ("maxReportedCostUsd", args.max_reported_cost_usd),
    ):
        if value is not None:
            budget[key] = value
    policy["budget"] = validate_budget(budget, "policy.budget")
    tiers = {
        tier: {provider: dict(spec) for provider, spec in mapping.items()}
        for tier, mapping in policy.get("modelTiers", {}).items()
    }
    for tier, key, values in (
        ("cheap", "model", args.cheap_model),
        ("cheap", "effort", args.cheap_effort),
        ("strong", "model", args.strong_model),
    ):
        if values:
            for pair in values:
                provider, separator, value = pair.partition("=")
                if not separator or not provider.strip() or not value.strip():
                    raise PlanError(f"expected PROVIDER={key.upper()}, got {pair!r}")
                tiers.setdefault(tier, {}).setdefault(provider.strip(), {})[key] = value.strip()
    policy["modelTiers"] = validate_model_tiers(tiers, "policy.modelTiers")
    policy = validate_policy(policy)
    save_policy(policy_path, policy)
    return policy


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("strategies", help="list orchestration strategies and limits")
    probe_parser = subparsers.add_parser("probe", help="report available built-in providers")
    probe_parser.add_argument("providers", nargs="*", choices=["codex", "claude", "agy", "kimi"])
    doctor_parser = subparsers.add_parser("doctor", help="check the environment and saved policy")
    doctor_parser.add_argument("--policy", type=Path)
    scaffold_parser = subparsers.add_parser(
        "scaffold", help="print a starter plan for a strategy"
    )
    scaffold_parser.add_argument("strategy", choices=list(STRATEGIES))
    scaffold_parser.add_argument("--workers", type=int, default=1)
    scaffold_parser.add_argument("--cwd", default=".")
    scaffold_parser.add_argument("--provider", default="auto")
    scaffold_parser.add_argument("--write", type=Path)
    validate_parser = subparsers.add_parser("validate", help="validate a batch plan without running it")
    validate_parser.add_argument("plan", type=Path)
    validate_parser.add_argument("--policy", type=Path)
    validate_parser.add_argument("--strategy", choices=list(STRATEGIES))
    dry_run_parser = subparsers.add_parser(
        "dry-run", help="explain the strategy, providers, and budget a plan would use"
    )
    dry_run_parser.add_argument("plan", type=Path)
    dry_run_parser.add_argument("--policy", type=Path)
    dry_run_parser.add_argument("--strategy", choices=list(STRATEGIES))
    run_parser = subparsers.add_parser("run", help="run a batch plan")
    run_parser.add_argument("plan", type=Path)
    run_parser.add_argument("--run-dir", type=Path)
    run_parser.add_argument("--policy", type=Path)
    run_parser.add_argument("--strategy", choices=list(STRATEGIES))
    policy_parser = subparsers.add_parser("policy", help="show or change skill-only preferences")
    policy_parser.add_argument("policy_action", choices=["show", "set", "reset"])
    policy_parser.add_argument("strategy", nargs="?", choices=list(STRATEGIES))
    policy_parser.add_argument("--file", type=Path)
    policy_parser.add_argument("--backend", choices=["auto", "skill", "mcp"])
    policy_parser.add_argument("--provider-switch", choices=["ask", "automatic", "never"])
    policy_parser.add_argument("--provider-order", nargs="+", choices=list(BUILTIN_PROVIDERS))
    policy_parser.add_argument(
        "--conserve-providers", nargs="*", choices=list(BUILTIN_PROVIDERS)
    )
    policy_parser.add_argument("--max-workers", type=int)
    policy_parser.add_argument("--max-concurrent", type=int)
    policy_parser.add_argument("--max-calls", type=int)
    policy_parser.add_argument("--max-wall-seconds", type=int)
    policy_parser.add_argument("--max-estimated-prompt-tokens", type=int)
    policy_parser.add_argument("--max-reported-cost-usd", type=float)
    policy_parser.add_argument("--cheap-model", nargs="*", metavar="PROVIDER=MODEL")
    policy_parser.add_argument("--cheap-effort", nargs="*", metavar="PROVIDER=EFFORT")
    policy_parser.add_argument("--strong-model", nargs="*", metavar="PROVIDER=MODEL")
    args = parser.parse_args(argv)

    try:
        if args.command == "strategies":
            print(json.dumps(strategies_payload(), indent=2))
            return 0
        if args.command == "probe":
            providers = args.providers or ["codex", "claude", "agy", "kimi"]
            print(json.dumps([probe_provider(provider) for provider in providers], indent=2))
            return 0
        if args.command == "doctor":
            policy_path = (args.policy or default_policy_path()).expanduser().resolve()
            print(json.dumps(doctor_payload(policy_path), indent=2, ensure_ascii=False))
            return 0
        if args.command == "scaffold":
            plan = scaffold_plan(args.strategy, args.workers, args.cwd, args.provider)
            if args.write:
                atomic_write_json(args.write, plan)
            print(json.dumps(plan, indent=2))
            return 0
        if args.command == "policy":
            policy_path = (args.file or default_policy_path()).expanduser().resolve()
            policy = update_policy(args, policy_path)
            print(json.dumps({"path": str(policy_path), "policy": policy}, indent=2))
            return 0
        if args.command == "validate":
            policy_path = (args.policy or default_policy_path()).expanduser().resolve()
            plan = validate_plan(
                read_json(args.plan),
                args.plan.resolve(),
                load_policy(policy_path),
                args.strategy,
            )
            summary = {
                "valid": True,
                "strategy": plan["strategy"],
                "requestedStrategy": plan["requestedStrategy"],
                "maxConcurrent": plan["maxConcurrent"],
                "budget": plan["budget"],
                "policyPath": str(policy_path),
                "workers": [
                    {
                        "id": worker["id"],
                        "provider": worker["provider"],
                        "requestedProvider": worker["requestedProvider"],
                        "mode": worker["mode"],
                        "isolate": worker["isolate"],
                    }
                    for worker in plan["workers"]
                ],
            }
            print(json.dumps(summary, indent=2))
            return 0
        if args.command == "dry-run":
            policy_path = (args.policy or default_policy_path()).expanduser().resolve()
            plan = validate_plan(
                read_json(args.plan),
                args.plan.resolve(),
                load_policy(policy_path),
                args.strategy,
            )
            print(
                json.dumps(
                    dry_run_payload(plan, args.plan.resolve(), policy_path),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        policy_path = (args.policy or default_policy_path()).expanduser().resolve()
        return run_batch(args.plan, args.run_dir, policy_path, args.strategy)
    except PlanError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
