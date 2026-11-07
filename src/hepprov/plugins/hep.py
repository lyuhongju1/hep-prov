"""HEP plugin: primitive-evidence quantities from simulation logs and event files.

Per-event text logs (Geant4/Opticks "N hits detected", Celeritas "Event i
processed: h hits, e MeV") and per-event tables (``*_events.parquet``/csv
with nHits / totalEdep) are reduced to per-(geometry, particle, energy)
statistics: n_events, total, mean, sigma, sigma/mean, mean per MeV. These are
the "log" tier: numbers a report can be verified against by identity.

The parsing rules port grace_audit adfa1a25 (common.py). Batched sub-runs
(``batch_*`` directories) are merged into their parent energy point.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

RX_OPT = re.compile(r"(\d+)\s+hits detected")
RX_CEL = re.compile(r"Event\s+(\d+)\s+processed:\s+(\d+)\s+hits,\s+([\d.eE+-]+)\s+MeV")
PARTS = ["electron", "proton", "neutron", "gamma", "kaonp", "kaonm", "kaon", "mum", "mup",
         "pip", "pim", "e-", "mu-", "em", "ep"]
RX_TAIL = re.compile(r"^(.*)_(" + "|".join(map(re.escape, PARTS)) + r")_geant4\.log$")
RX_E = re.compile(r"energy_([\d.]+)GeV")


def _key(path: Path, stem_geom: str, particle: str) -> str:
    m = RX_E.search(path.as_posix())
    e = float(m.group(1)) if m else None
    return f"{stem_geom}/{particle}/{e}"


def _stats(prefix: str, values: list[float], energy_gev: float | None) -> dict[str, float]:
    n = len(values)
    if not n:
        return {}
    tot = sum(values)
    mu = tot / n
    var = sum((x - mu) ** 2 for x in values) / n
    out = {f"{prefix}:n_events": float(n), f"{prefix}:total": float(tot), f"{prefix}:mean": mu}
    if energy_gev:
        out[f"{prefix}:mean_per_mev"] = mu / (energy_gev * 1000.0)
    if mu > 0:
        out[f"{prefix}:sigma"] = math.sqrt(var)
        out[f"{prefix}:rel_sigma"] = math.sqrt(var) / mu
    if n > 1:
        out[f"{prefix}:sem"] = math.sqrt(var * n / (n - 1)) / math.sqrt(n)   # standard error of the mean
    return out


def parse_log(text: str) -> dict:
    o = [int(x) for x in RX_OPT.findall(text)]
    if o:
        return {"fmt": "opticks", "nhits": o, "edep": []}
    c = RX_CEL.findall(text)
    if c:
        return {"fmt": "celeritas", "nhits": [int(h) for _, h, _ in c], "edep": [float(e) for _, _, e in c]}
    return {"fmt": "none", "nhits": [], "edep": []}


def log_quantities(files: Iterable[Path]) -> dict[str, float]:
    """``log:`` tier from per-event Geant4 logs, batches merged per energy point."""
    cells_n: dict[str, list] = defaultdict(list)
    cells_e: dict[str, list] = defaultdict(list)
    energies: dict[str, float | None] = {}
    for f in sorted(files):
        if not f.name.endswith("geant4.log"):
            continue
        m = RX_TAIL.match(f.name)
        geom, part = (m.group(1), m.group(2)) if m else (f.name[:-len("_geant4.log")], "?")
        k = _key(f, geom, part)
        em = RX_E.search(f.as_posix())
        energies[k] = float(em.group(1)) if em else None
        try:
            parsed = parse_log(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        cells_n[k].extend(parsed["nhits"])
        cells_e[k].extend(parsed["edep"])
    out: dict[str, float] = {}
    for k, v in cells_n.items():
        out.update(_stats(f"log:{k}:nhits", v, energies[k]))
    for k, v in cells_e.items():
        out.update(_stats(f"log:{k}:edep", v, energies[k]))
    return out


def ratio_families(logq: dict[str, float]) -> dict[str, list[float]]:
    """Per-energy (particle / electron) mean-signal ratios, e.g. quenching factors from logs.

    Families of these ratios across energies are where a reported "average
    quenching factor" should come from. Their mean and std are in the grammar.
    """
    mus: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for k, v in logq.items():
        m = re.match(r"log:(.+)/([^/]+)/([\d.]+|None):nhits:mean$", k)
        if m:
            mus[(m.group(1), m.group(3))][m.group(2)] = v
    fams: dict[str, list[float]] = defaultdict(list)
    for (g, _e), d in sorted(mus.items(), key=lambda t: (t[0][0], float(t[0][1]) if t[0][1] != "None" else -1)):
        el = d.get("electron") or d.get("e-") or d.get("em")
        if not el:
            continue
        for pa, v in sorted(d.items()):
            if pa not in ("electron", "e-", "em"):
                fams[f"log:{g}:{pa}/electron_ratio"].append(v / el)
    return {k: v for k, v in fams.items() if len(v) >= 2}


BATCH_RATIO_MIN = 0.7
LINEARITY_MAX = 0.25
EM = ("electron", "e-", "em", "gamma")


def invariants(logq: dict[str, float], files: Iterable[Path]) -> list[dict]:
    """I3 batch-ratio and I4 linearity over log-derived sweeps (thresholds as in the audit).

    I3: per-event signal per MeV at an energy that ran in ``batch_*``
    sub-runs, relative to the lowest unbatched energy, must be >= 0.7.
    I4 (EM particles only; hadron/ion light yield is quenched non-linearly):
    the worst residual of a linear fit of mean signal vs energy must be <= 25%.
    """
    batched = set()
    for f in files:
        parts = f.parts
        for i, p in enumerate(parts[:-1]):
            if p.startswith("batch_") and i > 0:
                m = RX_E.search(parts[i - 1])
                if m:
                    batched.add(float(m.group(1)))
    sweeps: dict[tuple[str, str], dict[float, float]] = defaultdict(dict)
    for k, v in logq.items():
        m = re.match(r"log:(.+)/([^/]+)/([\d.]+):nhits:mean$", k)
        if m and float(m.group(3)) > 0:
            sweeps[(m.group(1), m.group(2))][float(m.group(3))] = v
    out = []
    for (g, pa), d in sorted(sweeps.items()):
        es = sorted(d)
        if len(es) < 2:
            continue
        ref = next((e for e in es if e not in batched), None)
        if ref is not None and d[ref] > 0:
            for e in es:
                if e in batched:
                    r = (d[e] / e) / (d[ref] / ref)
                    if r < BATCH_RATIO_MIN:
                        out.append({"code": "I3", "sweep": f"{g}/{pa}", "energy_gev": e, "ratio": round(r, 3),
                                    "detail": f"signal/MeV at batched {e*1000:g} MeV is {r:.2f}x "
                                              f"unbatched {ref*1000:g} MeV"})
        if pa in EM and len(es) >= 3:
            n = len(es); ys = [d[e] for e in es]
            sx, sy = sum(es), sum(ys); sxx = sum(x * x for x in es); sxy = sum(x * y for x, y in zip(es, ys))
            den = n * sxx - sx * sx
            if den:
                b = (n * sxy - sx * sy) / den; a = (sy - b * sx) / n
                worst = max(abs(a + b * x - y) / max(abs(y), 1e-9) for x, y in zip(es, ys))
                if worst > LINEARITY_MAX:
                    out.append({"code": "I4", "sweep": f"{g}/{pa}", "worst_residual": round(worst, 3),
                                "detail": f"mean signal vs energy departs from linear by {worst:.0%}"})
    return out


def event_table_quantities(files: Iterable[Path]) -> dict[str, float]:
    """``data:`` tier from per-event tables (parquet/csv) with nHits/totalEdep columns."""
    out: dict[str, float] = {}
    for f in sorted(files):
        if not (f.name.endswith("_events.parquet") or f.name.endswith("_events.csv")):
            continue
        try:
            import pandas as pd
            df = pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(f)
        except Exception:
            continue
        em = RX_E.search(f.as_posix())
        e = float(em.group(1)) if em else None
        for col in ("nHits", "totalEdep", "nPhotons"):
            if col in df.columns and len(df):
                out.update(_stats(f"data:{f.as_posix()}:{col}", [float(x) for x in df[col]], e))
    return out
