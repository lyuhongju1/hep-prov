"""Static checks on the scripts a run executed (Python AST).

transcribed_literal   a float literal (>= 4 significant figures) equal, at its
                      printed precision, to a quantity an EARLIER step
                      produced. The audit's "transcription hardcoding": values
                      carried between steps by retyping printed output.
silent_skip           ``if not <exists/file list>: continue``, i.e. a missing
                      input silently dropped (audit: existence-guarded skip).
"""

from __future__ import annotations

import ast
import math
import re
from pathlib import Path

_SKIP_TEST = re.compile(r"exists|isfile|is_file|glob|files|paths|\blen\(")


def _sig(seg: str) -> int:
    d = seg.lstrip("+-").split("e")[0].split("E")[0].replace(".", "").lstrip("0")
    return len(d)


def transcribed_literals(code: str, prior: dict[str, float]) -> list[dict]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    out = []
    vals = [(k, v) for k, v in prior.items() if v and math.isfinite(v)]
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, float)):
            continue
        seg = ast.get_source_segment(code, node) or repr(node.value)
        if _sig(seg) < 4 or "e" in seg.lower():
            continue
        dec = len(seg.split(".")[1]) if "." in seg else 0
        for k, v in vals:
            if f"{v:.{dec}f}" == seg.lstrip("+"):
                out.append({"kind": "transcribed_literal", "literal": seg, "line": node.lineno, "equals": k})
                break
    return out


def silent_skips(code: str) -> list[dict]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.UnaryOp)
                and isinstance(node.test.op, ast.Not)
                and node.body and isinstance(node.body[0], ast.Continue)):
            test = ast.get_source_segment(code, node.test) or ""
            if _SKIP_TEST.search(test):
                out.append({"kind": "silent_skip", "line": node.lineno, "test": test})
    return out


def executed_scripts(manifest) -> list[tuple[int, str, Path]]:
    """(exec seq, label, script path) for every ``python script.py`` the run executed."""
    out = []
    for ev in manifest.events():
        if ev["kind"] != "exec":
            continue
        d = ev["data"]
        for a in d.get("cmd", [])[1:]:
            if a.endswith(".py"):
                p = Path(a)
                if not p.is_absolute():
                    p = Path(d.get("cwd") or manifest.root) / p
                out.append((ev["seq"], d.get("label") or f"exec#{ev['seq']}", p))
                break
    return out
