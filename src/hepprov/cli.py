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
    p = sub.add_parser("audit", help="run every detector on a recorded run; exit 1 if anything is flagged")
    p.add_argument("report", nargs="?")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("verify", help="check every number in a report against the run's evidence")
    p.add_argument("report")
    p.add_argument("--archive", action="store_true",
                   help="no manifest: use every file under --root as evidence (retrospective audit)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-grammar", action="store_true")
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
    if a.cmd == "audit":
        from hepprov.audit import audit_run
        res = audit_run(root, a.report)
        if a.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"{res['n_flags']} flag(s)" + (f"; report claims {res['verify_counts']}" if res["verify_counts"] else ""))
            for f in res["flags"]:
                where = f.get("path") or f.get("step") or f.get("sweep") or f.get("claim") or ""
                print(f"  [{f['detector']}:{f['kind']}] {where}  {f['detail'][:150]}")
        return 1 if res["flags"] else 0
    if a.cmd == "verify":
        from hepprov.verify import attested_files, emitted_values, pool_from_files, stdout_values, verify_report
        if a.archive:
            files = [p for p in root.rglob("*") if p.is_file() and ".hepprov" not in p.parts]
            pool = pool_from_files(files)
        else:
            m = Manifest(root)
            pool = pool_from_files(attested_files(m))
            pool.add(emitted_values(m))
            pool.add(stdout_values(m))
        res = verify_report(Path(a.report).read_text(encoding="utf-8", errors="ignore"),
                            pool, grammar=not a.no_grammar)
        res["mode"] = "archive" if a.archive else "attested"
        if a.json:
            print(json.dumps(res, indent=2))
        else:
            c = res["counts"]
            print(f"{res['n_claims']} numeric claims ({res['mode']}, pool {res['pool_size']}): "
                  + ", ".join(f"{k} {c.get(k, 0)}" for k in ("verified", "copy", "diagnosed", "unexplained")))
            for v in res["verdicts"]:
                if v["verdict"] == "diagnosed":
                    print(f"  [diagnosed null={v['null_rate']:.0%}] {v['text']}  = {v['formula']}")
                    print(f"      ...{v['ctx']}...")
            for c in res.get("corroborated", []):
                print(f"  [corroborated joint-null={c['joint_null']:.0%}] {c['mean_value']} ± {c['std_value']} "
                      f"= mean/std({c['family_variant']})")
            for i in res.get("invariants", []):
                print(f"  [{i['code']}] {i['sweep']}: {i['detail']}")
            im = res.get("intermediates")
            if im:
                c = im["counts"]
                print(f"{im['n_claims']} intermediate values (results JSONs): "
                      + ", ".join(f"{k} {c.get(k, 0)}" for k in ("verified", "copy", "diagnosed", "unexplained")))
                for v in sorted(im["verdicts"], key=lambda x: x["null_rate"] if x["null_rate"] is not None else 1):
                    if v["verdict"] == "diagnosed" and v["null_rate"] is not None and v["null_rate"] <= 0.05:
                        print(f"  [diagnosed null={v['null_rate']:.0%}] {v['text']} ({v['id']})  = {v['formula']}")
                for c in im.get("corroborated", []):
                    print(f"  [corroborated joint-null={c['joint_null']:.0%}] {c['mean_value']} ± {c['std_value']} "
                          f"= mean/std({c['family_variant']})")
        return 0
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
