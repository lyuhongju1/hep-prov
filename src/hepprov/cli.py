"""hepprov command line.

    hepprov init [--root DIR] [--declare FILE ...]
    hepprov declare FILE [--note TEXT]
    hepprov exec [--label NAME] -- CMD ...
    hepprov check [--json]
    hepprov log
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hepprov.attest import attest
from hepprov.manifest import Manifest
from hepprov.recorder import Recorder


def _root(a) -> Path:
    return Path(a.root).resolve()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hepprov")
    ap.add_argument("--root", default=".", help="run root (default: current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--declare", nargs="*", default=[])
    p = sub.add_parser("declare"); p.add_argument("file"); p.add_argument("--note", default="")
    p = sub.add_parser("exec"); p.add_argument("--label", default=""); p.add_argument("command", nargs=argparse.REMAINDER)
    p = sub.add_parser("check"); p.add_argument("--json", action="store_true")
    sub.add_parser("log")
    a = ap.parse_args(argv)
    root = _root(a)

    if a.cmd == "init":
        ev = Recorder(root).init(a.declare)
        n = len(ev["data"]["preexisting"])
        print(f"hepprov: initialized {root} ({n} pre-existing file(s) recorded as undeclared)")
        return 0
    if a.cmd == "declare":
        ev = Recorder(root).declare(a.file, a.note)
        print(f"hepprov: declared {ev['data']['path']} sha256={ev['data']['sha256'][:12]}")
        return 0
    if a.cmd == "exec":
        cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
        if not cmd:
            ap.error("exec needs a command after --")
        ev = Recorder(root).exec(cmd, label=a.label)
        return int(ev["data"]["exit_code"])
    if a.cmd == "check":
        m = Manifest(root)
        chain = m.verify_chain()
        res = attest(m)
        res["chain_ok"] = not chain
        res["chain_errors"] = [vars(e) for e in chain]
        res["head"] = m.head()
        if a.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"chain: {'OK' if not chain else 'BROKEN'} (head {res['head'][:12]})")
            for e in chain:
                print(f"  line {e.line}: {e.reason}")
            print(f"reads attested: {res['reads']}, violations: {len(res['violations'])}")
            for v in res["violations"]:
                print(f"  [{v['kind']}] {v['path']} in {v['label']}: {v['detail']}")
            for rel, steps in res["overwrites"].items():
                print(f"  [overwrite] {rel}: {' -> '.join(steps)}")
        return 0 if (res["ok"] and not chain) else 1
    if a.cmd == "log":
        for ev in Manifest(root).events():
            d = ev["data"]
            extra = ""
            if ev["kind"] == "exec":
                extra = (f"{d.get('label') or ' '.join(d['cmd'])[:60]}  exit={d['exit_code']} "
                         f"writes={len(d['writes'])} reads={len(d['reads'])} py={len(d['python_processes'])}")
            print(f"{ev['seq']:4d} {ev['kind']:16s} {extra}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
