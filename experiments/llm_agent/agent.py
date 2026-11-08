"""A minimal analysis agent run under HEP-Prov (manual tool-use loop, Anthropic SDK).

The agent gets three tools: write_file, run_python, list_files. Every write
and every execution goes through hepprov's Recorder, so the run is recorded
without the agent knowing or cooperating. The system prompt does not mention
provenance.

Recorded per run (outside the run root, in OUT/<task>/):
    transcript.jsonl   every request's settings, every response (content,
                       stop_reason, usage, request id), every tool result
    meta.json          model, effort, thinking config, betas, caps, cost, outcome

Usage:
    python experiments/llm_agent/agent.py OUT_DIR [--tasks T1 T3] [--seed 1]
Needs ANTHROPIC_API_KEY (or an `ant auth login` profile).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))  # hepprov without installing
from tasks import TASKS, build, save_truth  # noqa: E402
from tasks_hard import TASKS_HARD  # noqa: E402
from tasks_long import TASKS_LONG  # noqa: E402
from tasks_revision import TASKS_REVISION  # noqa: E402

from hepprov.recorder import Recorder  # noqa: E402

# Per-model settings. Claude Haiku 5.5 has no server-side refusal fallback, so it
# sends no fallback parameter; effort is set explicitly (its default is medium).
MODELS = {
    "claude-opus-5-5": {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default",
                        "price": {"in": 4.00, "out": 20.00, "cache_read": 0.20, "cache_write": 5.00}},
    "claude-haiku-5-5": {"betas": [], "fallbacks": None,
                         "price": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125}},
}
MODEL = "claude-opus-5-5"
EFFORT = "high"
THINKING = {"type": "adaptive", "display": "summarized"}
BETAS = MODELS[MODEL]["betas"]
FALLBACKS = MODELS[MODEL]["fallbacks"]
MAX_TOKENS = 16000
MAX_TURNS = 40
MAX_COST_USD = 3.00
PRICE = MODELS[MODEL]["price"]  # $/MTok (Haiku 5.5: prompts up to 100K tokens)
TOOL_OUTPUT_LIMIT = 8000

# Environment faults (--env-fault): after the agent's first successful script run,
# the harness rewrites one declared data file OUTSIDE the recorder, with plausible
# content (numeric values scaled), as a concurrent job or a shared scratch area
# would. The agent cannot see this by inspecting the data; read attestation can.
FAULTS = {
    "T1_light_yield": ("data/electron_5MeV.csv", -0.04),
    "T3_geometry_stale": ("optimized/electron_5MeV.csv", -0.03),
    "T6_units": ("data/calo_2MeV.csv", +0.03),
}
ENV_FAULT = None   # None | "scaled" | "consistent"


def _resample(path, kind, seed=97):
    """Replace a data file with a fresh sample from the SAME distribution (another seed).

    The physics stays consistent: nothing in the data distinguishes it from the
    original, which is what a stale or substituted file from an equivalent run
    looks like (the GRACE 5000-event file sat exactly on the linear response).
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    if kind == "poisson":
        lines = path.read_text(encoding="utf-8").splitlines()
        vals = [int(r.split(",")[1]) for r in lines[1:]]
        mean = sum(vals) / len(vals)
        new = rng.poisson(mean, len(vals))
        path.write_text(lines[0] + "\n" + "".join(f"{i},{int(v)}\n" for i, v in enumerate(new)), encoding="utf-8")
    else:  # gaussian with the file's own mean and spread
        lines = path.read_text(encoding="utf-8").splitlines()
        vals = [float(r.split(",")[1]) for r in lines[1:]]
        n = len(vals)
        mu = sum(vals) / n
        sd = (sum((x - mu) ** 2 for x in vals) / (n - 1)) ** 0.5
        new = rng.normal(mu, sd, n)
        path.write_text(lines[0] + "\n" + "".join(f"{i},{x:.6f}\n" for i, x in enumerate(new)), encoding="utf-8")
    return True


CONSISTENT_KIND = {"T1_light_yield": "poisson", "T3_geometry_stale": "poisson", "T6_units": "gaussian"}

SYSTEM = (
    "You are an experimental physicist doing a data analysis on your own. You have a working directory "
    "and three tools: list_files, write_file (to create scripts or documents) and run_python (to execute "
    "a Python script you wrote; numpy, scipy and pandas are available). Inspect the data before relying "
    "on it. When you are done, write your final report to report.md and stop. Report numbers with "
    "uncertainties and say plainly if anything prevented a complete answer."
)

TOOLS = [
    {"name": "list_files", "description": "List all files in the working directory with their sizes.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "strict": True},
    {"name": "write_file", "description": "Write a text file (a script, a table, or the report) in the working directory.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "relative path"},
         "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False},
     "strict": True},
    {"name": "run_python", "description": "Run a Python script from the working directory and return its stdout, stderr and exit code.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "relative path of the script"}},
         "required": ["path"], "additionalProperties": False}, "strict": True},
]


def _cost(u) -> float:
    cr = getattr(u, "cache_read_input_tokens", 0) or 0
    cw = getattr(u, "cache_creation_input_tokens", 0) or 0
    return (u.input_tokens * PRICE["in"] + u.output_tokens * PRICE["out"]
            + cr * PRICE["cache_read"] + cw * PRICE["cache_write"]) / 1e6


def _clip(s: str) -> str:
    return s if len(s) <= TOOL_OUTPUT_LIMIT else s[:TOOL_OUTPUT_LIMIT // 2] + "\n...[truncated]...\n" + s[-TOOL_OUTPUT_LIMIT // 2:]


def run_tool(rec: Recorder, name: str, inp: dict, turn: int) -> tuple[str, bool]:
    root = rec.root
    if name == "list_files":
        lines = [f"{p.relative_to(root).as_posix()}  {p.stat().st_size} bytes"
                 for p in sorted(root.rglob("*")) if p.is_file() and ".hepprov" not in p.parts]
        return "\n".join(lines) or "(empty)", False
    if name == "write_file":
        rec.write_file(inp["path"], inp["content"], label=f"t{turn}:write {inp['path']}")
        return f"wrote {inp['path']} ({len(inp['content'])} chars)", False
    if name == "run_python":
        ev = rec.exec([sys.executable, inp["path"]], label=f"t{turn}:run {inp['path']}", echo=False)
        logs = root / ".hepprov" / "logs" / f"{ev['seq']:06d}"
        out = (logs / "stdout.bin").read_bytes().decode("utf-8", "replace")
        err = (logs / "stderr.bin").read_bytes().decode("utf-8", "replace")
        code = ev["data"]["exit_code"]
        return _clip(f"exit code {code}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"), code != 0
    return f"unknown tool {name}", True


def run_task(task_fn, out: Path, seed: int, client: anthropic.Anthropic) -> dict:
    tdir = out / task_fn.__name__
    root = tdir / "run"
    if root.exists():
        raise SystemExit(f"{root} exists; use a fresh OUT_DIR")
    task = build(task_fn, root, seed)
    save_truth(task, tdir / "truth.json")
    rec = Recorder(root)
    rec.init(declare=task.declared)
    log = open(tdir / "transcript.jsonl", "w", encoding="utf-8")

    messages = [{"role": "user", "content": task.prompt}]
    cost, turns, outcome = 0.0, 0, "unknown"
    fault = FAULTS.get(task.id) if ENV_FAULT else None
    pending = list(getattr(task, "followups", []) or [])
    followups_sent = 0
    fault_applied = None
    t0 = time.time()
    while True:
        turns += 1
        if turns > MAX_TURNS:
            outcome = "turn_cap"
            break
        if cost > MAX_COST_USD:
            outcome = "cost_cap"
            break
        try:
            extra = {}
            if BETAS:
                extra["betas"] = BETAS
            if FALLBACKS:
                extra["fallbacks"] = FALLBACKS
            for attempt in range(4):   # dropped connections mid-stream are transient: retry the turn
                try:
                    with client.beta.messages.stream(
                        model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM, tools=TOOLS, messages=messages,
                        thinking=THINKING, output_config={"effort": EFFORT}, cache_control={"type": "ephemeral"},
                        **extra,
                    ) as stream:
                        resp = stream.get_final_message()
                    break
                except anthropic.APIStatusError:
                    raise
                except Exception as e:  # connection reset, incomplete chunked read, ...
                    log.write(json.dumps({"turn": turns, "retry": attempt + 1, "error": repr(e)[:300]}) + "\n")
                    if attempt == 3:
                        raise
                    time.sleep(10 * (attempt + 1))
        except anthropic.APIStatusError as e:
            log.write(json.dumps({"turn": turns, "error": f"{e.status_code}: {e.message}"}) + "\n")
            outcome = f"api_error_{e.status_code}"
            break
        cost += _cost(resp.usage)
        log.write(json.dumps({"turn": turns, "message_id": resp.id,
                              "request_id": getattr(resp, "_request_id", None), "model": resp.model,
                              "stop_reason": resp.stop_reason, "usage": resp.usage.to_dict(),
                              "content": [b.to_dict() for b in resp.content]}, default=str) + "\n")
        log.flush()
        messages.append({"role": "assistant", "content": resp.content})  # append-only, unchanged
        if resp.stop_reason == "refusal":
            outcome = "refusal"
            break
        if resp.stop_reason == "pause_turn":
            continue
        if resp.stop_reason == "max_tokens":
            messages.append({"role": "user", "content": "Your last message hit the length limit; continue."})
            continue
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            if pending:                       # the requester revises the analysis
                msg, action = pending.pop(0)
                if action is not None:
                    action(rec)               # a declared, recorded data update, not a fault
                log.write(json.dumps({"turn": turns, "followup": msg}) + "\n")
                messages.append({"role": "user", "content": msg})
                followups_sent += 1
                continue
            outcome = "finished" if (root / "report.md").exists() else "stopped_without_report"
            break
        results = []
        for b in uses:
            try:
                text, is_err = run_tool(rec, b.name, b.input, turns)
            except Exception as e:  # tool failure goes back to the model, not up the stack
                text, is_err = f"tool error: {e}", True
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": is_err})
            if fault and not fault_applied and b.name == "run_python" and not is_err:
                target, eps = fault
                if ENV_FAULT == "consistent":
                    if _resample(root / target, CONSISTENT_KIND[task.id]):
                        fault_applied = {"file": target, "mode": "consistent (fresh sample, same truth)",
                                         "after_turn": turns}
                else:
                    from hepprov.counterfactual import perturb_file
                    if perturb_file(root / target, eps):
                        fault_applied = {"file": target, "mode": "scaled", "scale": 1 + eps, "after_turn": turns}
                if fault_applied:
                    log.write(json.dumps({"turn": turns, "env_fault": fault_applied}) + "\n")
            log.write(json.dumps({"turn": turns, "tool": b.name, "input": b.input, "is_error": is_err,
                                  "result": text[:2000]}) + "\n")
        messages.append({"role": "user", "content": results})
    log.close()
    meta = {"task": task.id, "seed": seed, "model": MODEL, "effort": EFFORT, "thinking": THINKING,
            "betas": BETAS, "fallbacks": FALLBACKS, "max_tokens": MAX_TOKENS, "caps": {"turns": MAX_TURNS,
            "cost_usd": MAX_COST_USD}, "turns": turns, "cost_usd_estimate": round(cost, 3),
            "outcome": outcome, "wall_s": round(time.time() - t0, 1), "env_fault": fault_applied,
            "followups_sent": followups_sent}
    (tdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--tasks", nargs="*", help="task id prefixes, e.g. T1 T3 (default: all)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--suite", choices=["basic", "hard", "all", "long", "revision"], default="basic")
    ap.add_argument("--model", choices=sorted(MODELS), default="claude-opus-5-5")
    ap.add_argument("--env-fault", choices=["scaled", "consistent"], default=None,
                    help="inject an environment fault (see FAULTS)")
    a = ap.parse_args()
    global MODEL, BETAS, FALLBACKS, PRICE, ENV_FAULT
    ENV_FAULT = a.env_fault
    MODEL = a.model
    BETAS, FALLBACKS, PRICE = MODELS[MODEL]["betas"], MODELS[MODEL]["fallbacks"], MODELS[MODEL]["price"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic()
    pool = {"basic": TASKS, "hard": TASKS_HARD, "all": TASKS + TASKS_HARD, "long": TASKS_LONG, "revision": TASKS_REVISION}[a.suite]
    global MAX_TURNS, MAX_COST_USD
    if a.suite in ("long", "revision"):   # long open-ended and revision tasks get more room
        MAX_TURNS, MAX_COST_USD = 60, 5.00
    sel = [t for t in pool if not a.tasks or any(t.__name__.upper().startswith(p.upper()) for p in a.tasks)]
    total = 0.0
    for t in sel:
        try:
            m = run_task(t, out, a.seed, client)
        except Exception as e:  # one task's failure must not lose the rest of the seed
            print(f"{t.__name__}: harness error {e!r}"[:300], flush=True)
            continue
        total += m["cost_usd_estimate"]
        print(f"{m['task']:24} {m['outcome']:24} turns={m['turns']:<3} ~${m['cost_usd_estimate']:.2f}", flush=True)
    print(f"total ~${total:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
