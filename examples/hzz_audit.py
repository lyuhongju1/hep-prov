"""HZZ agent reports vs archived results: identity support (with chance rates) plus in-text derivation."""
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

from hepprov.verify import pool_from_files, verify_report

repo, out = Path(sys.argv[1]), Path(sys.argv[2])
files = [p for p in (repo / "results").rglob("*") if p.is_file() and p.suffix in (".json", ".csv")]
pool = pool_from_files(files)
vals = list(pool.values.values())
_ix: dict[int, set] = {}


def ix(d):
    if d not in _ix:
        s = set()
        for v in vals:
            try:
                s.add(f"{v:.{d}f}")
            except (ValueError, OverflowError):
                pass
        _ix[d] = s
    return _ix[d]


rows = []
for doc in sorted((repo / "docs").glob("*.md")):
    res = verify_report(doc.read_text(encoding="utf-8", errors="ignore"), pool, grammar=False, intermediates=False)
    for v in res["verdicts"]:
        if v["sig"] < 3:
            continue
        rng = random.Random(v["text"])
        null_id = sum(f"{v['value'] * rng.uniform(0.5, 1.5):.{v['decimals']}f}" in ix(v["decimals"])
                      for _ in range(50)) / 50
        rows.append({"doc": doc.name, "text": v["text"], "sig": v["sig"], "verdict": v["verdict"],
                     "identity_null": null_id, "derived_null": v["null_rate"], "formula": v["formula"],
                     "ctx": v["ctx"]})

tab = defaultdict(Counter)
for r in rows:
    tab["3" if r["sig"] == 3 else "4" if r["sig"] == 4 else ">=5"][r["verdict"]] += 1
print(f"{'sig':>4} {'claims':>6} {'copy':>6} {'derived':>8} {'unexpl':>7}   expected chance copies")
for s in ("3", "4", ">=5"):
    c = tab[s]; n = sum(c.values())
    exp = sum(r["identity_null"] for r in rows if (("3" if r["sig"] == 3 else "4" if r["sig"] == 4 else ">=5") == s))
    print(f"{s:>4} {n:>6} {c['copy']:>6} {c['derived']:>8} {c['unexplained']:>7}   {exp:>8.1f}")
hi = [r for r in rows if r["sig"] >= 5]
print(f"\n>=5 sig figs: {len(hi)} claims; copy {sum(r['verdict']=='copy' for r in hi)}, "
      f"derived {sum(r['verdict']=='derived' for r in hi)} "
      f"(derived with null<=5%: {sum(r['verdict']=='derived' and r['derived_null'] <= 0.05 for r in hi)}), "
      f"unexplained {sum(r['verdict']=='unexplained' for r in hi)}")
out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
