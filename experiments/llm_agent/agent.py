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

from hepprov.recorder import Recorder  # noqa: E402

MODEL = "claude-opus-5-5"
EFFORT = "high"
THINKING = {"type": "adaptive", "display": "summarized"}
BETAS = ["server-side-fallback-2026-07-01"]
FALLBACKS = "default"
MAX_TOKENS = 16000
MAX_TURNS = 40
MAX_COST_USD = 3.00
PRICE = {"in": 4.00, "out": 20.00, "cache_read": 0.20, "cache_write": 5.00}  # $/MTok, claude-opus-5-5
TOOL_OUTPUT_LIMIT = 8000

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
            with client.beta.messages.stream(
                model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM, tools=TOOLS, messages=messages,
                thinking=THINKING, output_config={"effort": EFFORT}, cache_control={"type": "ephemeral"},
                betas=BETAS, fallbacks=FALLBACKS,
            ) as stream:
                resp = stream.get_final_message()
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
            outcome = "finished" if (root / "report.md").exists() else "stopped_without_report"
            break
        results = []
        for b in uses:
            try:
                text, is_err = run_tool(rec, b.name, b.input, turns)
            except Exception as e:  # tool failure goes back to the model, not up the stack
                text, is_err = f"tool error: {e}", True
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": is_err})
            log.write(json.dumps({"turn": turns, "tool": b.name, "input": b.input, "is_error": is_err,
                                  "result": text[:2000]}) + "\n")
        messages.append({"role": "user", "content": results})
    log.close()
    meta = {"task": task.id, "seed": seed, "model": MODEL, "effort": EFFORT, "thinking": THINKING,
            "betas": BETAS, "fallbacks": FALLBACKS, "max_tokens": MAX_TOKENS, "caps": {"turns": MAX_TURNS,
            "cost_usd": MAX_COST_USD}, "turns": turns, "cost_usd_estimate": round(cost, 3),
            "outcome": outcome, "wall_s": round(time.time() - t0, 1)}
    (tdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--tasks", nargs="*", help="task id prefixes, e.g. T1 T3 (default: all)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--suite", choices=["basic", "hard"], default="basic")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic()
    pool = TASKS_HARD if a.suite == "hard" else TASKS
    sel = [t for t in pool if not a.tasks or any(t.__name__.upper().startswith(p.upper()) for p in a.tasks)]
    total = 0.0
    for t in sel:
        m = run_task(t, out, a.seed, client)
        total += m["cost_usd_estimate"]
        print(f"{m['task']:24} {m['outcome']:24} turns={m['turns']:<3} ~${m['cost_usd_estimate']:.2f}", flush=True)
    print(f"total ~${total:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
