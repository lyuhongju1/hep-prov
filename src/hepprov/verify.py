"""Verifier: check every number in an agent's report against the run's evidence.

Method (from the GRACE artifact audit, grace_audit adfa1a25):

- **Tiers.** ``log``/``data``: primitive evidence (per-event logs and event
  tables, via plugins). ``emit``: values a script declared with
  ``hepprov.emit``. ``json``/``stdout``: intermediate results.
- **Verdicts.**
  - *verified*: equals a primitive or emitted quantity at the claim's printed
    precision.
  - *copy*: equals only an intermediate.
  - *diagnosed*: reproduced only by a formula in a bounded grammar, with the
    formula and its decoy-measured null rate attached. This is a screening
    verdict, not a finding.
  - *unexplained*: no match.
- **Exact digits.** A match must reproduce every printed digit.
- **Null calibration.** Each diagnosis is re-run on 100 jittered decoys of
  equal precision. The fraction that also match is that diagnosis's
  chance-match rate.
- **Gates.** Pair formulas only for claims with at least 4 significant
  figures. More than 5 distinct matching formulas counts as ambiguous.

In **attested mode** (a manifest exists), the pool contains only files this
run wrote or declared, at the version written. A number that matches a
stale or leftover file does not count as support.
"""

from __future__ import annotations

import json
import math
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from hepprov.plugins import hep

NUM = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+(?:\.\d{1,12})?|\d+\.\d{1,12}|\d{2,})(?!\w|\.\d)")
PRIMITIVE = ("log", "data", "emit")
SIG_GATE = 4
MULT_CAP = 5
N_DECOYS = 100
MEV_SCALES = (0.1, 2.0, 5.0)  # 1.0 is the plain ratio


@dataclass
class Claim:
    id: str
    text: str
    value: float
    decimals: int
    sig: int
    ctx: str
    pos: int = -1


@dataclass
class ClaimVerdict:
    claim: Claim
    verdict: str
    support: list[str] = field(default_factory=list)
    formula: str = ""
    null_rate: float | None = None


def extract_claims(text: str) -> list[Claim]:
    out = []
    for i, m in enumerate(NUM.finditer(text)):
        s = m.group(0).replace(",", "")
        # a leading minus sign belongs to the number when it isn't a hyphen inside a word or range
        st = m.start()
        if st >= 1 and text[st - 1] in "-−" and (st == 1 or not (text[st - 2].isalnum() or text[st - 2] in ".)")):
            s = "-" + s
        v = float(s)
        if v in (0.0, 1.0, 2.0):
            continue
        # skip arXiv ids and years
        before = text[max(0, m.start() - 12):m.start()].lower()
        after = text[m.end():m.end() + 1]
        arxiv_like = re.fullmatch(r"\d{4}\.\d{4,5}", s) is not None and (
            "arxiv" in before or before.endswith("references/") or after in ("-", "/"))
        date_stamp = "." not in s and re.fullmatch(r"-?(19|20)\d{6}", s) is not None   # YYYYMMDD, date-based seeds
        if arxiv_like or date_stamp or (1900 <= v <= 2100 and "." not in s):
            continue
        # metadata, not measurements: inline code spans and seed/size/version contexts
        line_start = text.rfind("\n", 0, m.start()) + 1
        # only the word immediately before or after the number counts
        prev_w = re.findall(r"[\w+]+", text[max(line_start, m.start() - 30):m.start()])
        next_w = re.findall(r"[\w+]+", text[m.end():m.end() + 30])
        near = {w.lower() for w in prev_w[-1:] + next_w[:1]}
        if text[line_start:m.start()].count("`") % 2 == 1 or near & METADATA_WORDS:
            continue
        dec = len(s.split(".")[1]) if "." in s else 0
        sig = len(s.lstrip("-").replace(".", "").lstrip("0"))
        ctx = text[max(0, m.start() - 50):m.end() + 30].replace("\n", " ")
        out.append(Claim(f"R{i:04d}", s, v, dec, sig, ctx, m.start()))
    return out


def _fmt_eq(value: float, claim: Claim) -> bool:
    if not math.isfinite(value):
        return False
    try:
        return f"{value:.{claim.decimals}f}" == claim.text
    except (ValueError, OverflowError):
        return False


# ---------------------------------------------------------------- pool

def json_leaves(path: Path, tag: str) -> tuple[dict[str, float], dict[str, list[float]]]:
    leaves: dict[str, float] = {}
    fams: dict[str, list[float]] = {}

    def walk(o, p):
        if isinstance(o, bool):
            return
        if isinstance(o, (int, float)):
            leaves[f"{tag}:{path.name}:{p}"] = float(o)
        elif isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{p}.{k}" if p else str(k))
        elif isinstance(o, list):
            nums = [x for x in o if isinstance(x, (int, float)) and not isinstance(x, bool)]
            if 2 <= len(nums) == len(o) <= 8:
                fams[f"{tag}:{path.name}:{p}[]"] = [float(x) for x in nums]
            for i, v in enumerate(o):
                walk(v, f"{p}[{i}]")
    try:
        walk(json.loads(path.read_text(encoding="utf-8")), "")
    except (OSError, ValueError):
        pass
    return leaves, fams


_SKIP_JSON = ("reasoning", "memory", "benchmark", "metadata", "extracted", "profile",
              "run_integrity", "write_manifest")


@dataclass
class Pool:
    values: dict[str, float] = field(default_factory=dict)
    families: dict[str, list[float]] = field(default_factory=dict)

    def tier(self, name: str) -> str:
        return name.split(":", 1)[0]

    def add(self, vals: dict[str, float], fams: dict[str, list[float]] | None = None) -> None:
        self.__dict__.pop("_ix", None)
        self.values.update({k: v for k, v in vals.items() if isinstance(v, (int, float)) and math.isfinite(v)})
        if fams:
            self.families.update(fams)


def attested_files(manifest) -> list[Path]:
    """Files whose current content is a version this run wrote or declared."""
    from hepprov.hashing import sha256_file

    ok: dict[str, set[str]] = {}
    for ev in manifest.events():
        d = ev["data"]
        if ev["kind"] == "declare" and not d.get("external"):
            ok.setdefault(d["path"], set()).add(d["sha256"])
        elif ev["kind"] == "exec":
            for rel, sha in d.get("writes", {}).items():
                if sha:
                    ok.setdefault(rel, set()).add(sha)
    out = []
    for rel, shas in ok.items():
        p = manifest.root / rel
        try:
            if sha256_file(p) in shas:
                out.append(p)
        except OSError:
            pass
    return out


def emitted_values(manifest) -> dict[str, float]:
    """``emit`` tier: values declared by scripts under ``hepprov exec`` (latest per name and step)."""
    out: dict[str, float] = {}
    for ev in manifest.events():
        if ev["kind"] == "exec":
            label = ev["data"].get("label") or f"exec#{ev['seq']}"
            for e in ev["data"].get("emits", []):
                out[f"emit:{label}:{e['name']}"] = float(e["value"])
    return out


TABLE_MAX_ROWS = 200


def csv_leaves(path: Path, tag: str = "table", max_rows: int = TABLE_MAX_ROWS) -> dict[str, float]:
    """A CSV as evidence.

    Small CSVs (<= max_rows rows) are result tables: every numeric cell is an
    intermediate (``table`` tier). Larger CSVs are per-event data: each
    numeric column is summarized as primitive evidence (``data`` tier: n, sum,
    mean, population and sample std, sigma/mean, min, max), like the
    per-event logs. Treating 2,000 event rows as 2,000 claims-in-waiting
    would make the formula search intractable and its chance rates
    meaningless.
    """
    import csv
    import statistics as st
    try:
        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            rows = list(csv.reader(f))
    except OSError:
        return {}
    if not rows:
        return {}
    header, body = rows[0], rows[1:]
    out: dict[str, float] = {}
    if len(body) <= max_rows:
        for i, row in enumerate(body):
            for j, cell in enumerate(row):
                try:
                    v = float(cell)
                except ValueError:
                    continue
                col = header[j] if j < len(header) else str(j)
                out[f"{tag}:{path.name}:{i}.{col}"] = v
        return out
    for j, col in enumerate(header):
        vals = []
        for row in body:
            if j < len(row):
                try:
                    x = float(row[j])
                except ValueError:
                    continue
                if math.isfinite(x):
                    vals.append(x)
        if len(vals) < 2 or col.lower() in ("event", "eventid", "event_id", "id", "index"):
            continue
        n, mu = len(vals), st.fmean(vals)
        pre = f"data:{path.as_posix()}:{col}"
        out.update({f"{pre}:n": float(n), f"{pre}:sum": float(sum(vals)), f"{pre}:mean": mu,
                    f"{pre}:std": st.pstdev(vals), f"{pre}:std_sample": st.stdev(vals),
                    f"{pre}:min": min(vals), f"{pre}:max": max(vals)})
        if mu:
            out[f"{pre}:rel_std"] = st.pstdev(vals) / mu
            out[f"{pre}:rel_std_sample"] = st.stdev(vals) / mu
        out[f"{pre}:n_rows_total"] = float(len(body))
    return out


def pool_from_files(files: Iterable[Path]) -> Pool:
    files = sorted(set(files))
    pool = Pool()
    logq = hep.log_quantities(files)
    pool.add(logq, hep.ratio_families(logq))
    object.__setattr__(pool, "invariants", hep.invariants(logq, files))
    pool.add(hep.event_table_quantities(files))
    for f in files:
        if f.suffix == ".json" and not any(k in f.name for k in _SKIP_JSON):
            pool.add(*json_leaves(f, "json"))
        elif f.suffix == ".csv" and not f.name.endswith("_events.csv"):
            pool.add(csv_leaves(f))
        elif f.suffix == ".csv":
            pass  # *_events.csv: handled by the HEP plugin (event_table_quantities)
    return pool


# ---------------------------------------------------------------- grammar

def _family_variants(fams: dict[str, list[float]]) -> dict[str, list[float]]:
    out = {}
    for k, v in fams.items():
        out[k] = v
        out[f"{k}+[1.0]"] = v + [1.0]          # placeholder appended
        if len(v) > 2:
            for i in range(len(v)):
                out[f"{k}drop[{i}]+[1.0]"] = v[:i] + [1.0] + v[i + 1:]   # placeholder substituted
    return out


PAIR_GRAMMAR_MAX = 1500   # pair formulas are O(n^2); above this only identity and family formulas run


def _formulas(pool: Pool, exclude: str | None = None):
    """Yield (description, value) over the bounded grammar."""
    vals = {k: v for k, v in pool.values.items() if k != exclude}
    if len(vals) > PAIR_GRAMMAR_MAX:
        object.__setattr__(pool, "grammar_truncated", True)
        for k, fam in _family_variants(pool.families).items():
            n = len(fam)
            mu = sum(fam) / n
            yield f"mean({k})", mu
            yield f"std({k})", math.sqrt(sum((x - mu) ** 2 for x in fam) / n)
        return
    for k, fam in _family_variants(pool.families).items():
        n = len(fam)
        mu = sum(fam) / n
        yield f"mean({k})", mu
        yield f"std({k})", math.sqrt(sum((x - mu) ** 2 for x in fam) / n)
    items = list(vals.items())
    for i, (ka, a) in enumerate(items):
        for kb, b in items:
            if ka == kb:
                continue
            if b:
                yield f"{ka} / {kb}", a / b
                yield f"({ka} - {kb}) / {kb} * 100", (a - b) / b * 100
                for e in MEV_SCALES:  # unit-loss class: a ratio missing or doubling an energy factor
                    yield f"{ka} / {kb} / {e}", a / b / e
                    yield f"{ka} / {kb} * {e}", a / b * e
            yield f"{ka} * (1 + {kb}/100)", a * (1 + b / 100)
            yield f"{ka} * {kb}", a * b
            yield f"{ka} - {kb}", a - b
            if b:
                yield f"{ka} / {kb} * 100", a / b * 100
            if ka < kb:  # a + b is symmetric: once per pair
                yield f"{ka} + {kb}", a + b


class _Index:
    """All grammar values of a pool, indexed by their printed form at each precision."""

    def __init__(self, pool: Pool):
        self.formulas = [(d, v) for d, v in _formulas(pool) if math.isfinite(v)]
        self._by_dec: dict[int, dict[str, list[str]]] = {}

    def lookup(self, text: str, decimals: int) -> list[str]:
        idx = self._by_dec.get(decimals)
        if idx is None:
            idx = {}
            for d, v in self.formulas:
                try:
                    idx.setdefault(f"{v:.{decimals}f}", []).append(d)
                except (ValueError, OverflowError):
                    pass
            self._by_dec[decimals] = idx
        return idx.get(text, [])


def _index(pool: Pool) -> _Index:
    ix = getattr(pool, "_ix", None)
    if ix is None:
        ix = _Index(pool)
        object.__setattr__(pool, "_ix", ix)
    return ix


def _matches(target: Claim, pool: Pool) -> list[str]:
    return _index(pool).lookup(target.text, target.decimals)


def _decoy_rate(target: Claim, pool: Pool, n: int = N_DECOYS) -> float:
    """Fraction of jittered decoys of equal precision that some grammar formula also matches."""
    rng = random.Random(f"{target.text}|{len(pool.values)}")
    ix = _index(pool)
    hit = 0
    for _ in range(n):
        s = f"{target.value * rng.uniform(0.5, 1.5):.{target.decimals}f}"
        if ix.lookup(s, target.decimals):
            hit += 1
    return hit / n


def classify(claim: Claim, pool: Pool, grammar: bool = True, exclude: str | None = None) -> ClaimVerdict:
    """``exclude``: the claim's own pool entry (for intermediate targets), never its own support."""
    ident = [k for k, v in pool.values.items() if k != exclude and _fmt_eq(v, claim)]
    prim = [k for k in ident if pool.tier(k) in PRIMITIVE]
    if prim:
        return ClaimVerdict(claim, "verified", prim[:5])
    if ident:
        return ClaimVerdict(claim, "copy", ident[:5])
    if grammar:
        hits = [h for h in _matches(claim, pool) if not exclude or exclude not in h]
        if claim.sig < SIG_GATE:  # below the gate only family statistics (as in the audit)
            hits = [h for h in hits if _FAM_RE.match(h)]
        if hits and len(hits) <= MULT_CAP:
            return ClaimVerdict(claim, "diagnosed", formula=hits[0], support=hits,
                                null_rate=_decoy_rate(claim, pool))
    return ClaimVerdict(claim, "unexplained")


def intermediate_claims(pool: Pool) -> list[tuple[Claim, str]]:
    """Numeric leaves of results JSONs as targets: is the intermediate itself derived from evidence?"""
    out = []
    for k, v in pool.values.items():
        if pool.tier(k) != "json" or abs(v) in (0.0, 1.0):
            continue
        s = f"{v:.10g}"   # every stored digit up to 10 significant figures (exact-digit gate)
        if "." not in s or "e" in s:
            continue
        dec = len(s.split(".")[1])
        sig = len(s.lstrip("-").replace(".", "").lstrip("0"))
        out.append((Claim("J:" + k.split(":", 1)[1][:70], s, float(s), dec, sig, k), k))
    return out


def _summ(verdicts: list[ClaimVerdict]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    return {"counts": counts, "n_claims": len(verdicts),
            "verdicts": [{**asdict(v.claim), "verdict": v.verdict, "support": v.support,
                          "formula": v.formula, "null_rate": v.null_rate} for v in verdicts]}


_FAM_RE = re.compile(r"^(mean|std)\((.+)\)$")


def corroborate(verdicts: list[ClaimVerdict], pool: Pool, n: int = N_DECOYS) -> list[dict[str, Any]]:
    """Promote pairs where mean(F) and std(F) of the SAME family variant explain a value and its uncertainty.

    One family match alone is weak (a mean of a few numbers matches many
    targets). Two matches on the same variant are much stronger. The joint
    null is measured directly: jitter both targets and count decoy pairs that
    some single variant explains jointly.
    """
    fams = _family_variants(pool.families)
    by_var: dict[str, dict[str, ClaimVerdict]] = {}
    for v in verdicts:
        if v.verdict != "diagnosed":
            continue
        for f in v.support:
            m = _FAM_RE.match(f)
            if m:
                by_var.setdefault(m.group(2), {})[m.group(1)] = v
    out = []
    for var, d in by_var.items():
        if "mean" not in d or "std" not in d or d["mean"] is d["std"]:
            continue
        cm, cs = d["mean"].claim, d["std"].claim
        stats = []
        for F in fams.values():
            k = len(F)
            mu = sum(F) / k
            stats.append((mu, math.sqrt(sum((x - mu) ** 2 for x in F) / k)))
        rng = random.Random(f"joint|{cm.text}|{cs.text}")
        hits = 0
        for _ in range(n):
            a = f"{cm.value * rng.uniform(0.5, 1.5):.{cm.decimals}f}"
            b = f"{cs.value * rng.uniform(0.5, 1.5):.{cs.decimals}f}"
            if any(f"{mu:.{cm.decimals}f}" == a and f"{sd:.{cs.decimals}f}" == b for mu, sd in stats):
                hits += 1
        out.append({"family_variant": var, "mean_claim": cm.id, "mean_value": cm.text,
                    "std_claim": cs.id, "std_value": cs.text, "joint_null": hits / n,
                    "single_nulls": [d["mean"].null_rate, d["std"].null_rate]})
    return out


NEIGHBORHOOD = 400   # characters: same table row / paragraph
_ALIAS = re.compile(r"ev\[(R\d+)\]:\S+|claim:(R\d+)=\S+|text:(R\d+)=\S+")


def derive_in_text(verdicts: list[ClaimVerdict], pool: Pool | None = None) -> None:
    """Re-check unexplained claims against nearby numbers of the same report.

    Prose and tables derive numbers from neighbours ("+0.660850 (2.609061 ->
    3.269912)", a difference column next to its two inputs). For each
    unexplained claim, the local pool is the claims within NEIGHBORHOOD
    characters, with their full-precision evidence values. Entries equal to
    the target at its precision are excluded, since a repeated number
    explains nothing. Verdicts:

    - ``derived``: reproduced from supported neighbours (or their full-precision
      evidence, which matters because the report rounds the inputs);
    - ``derived_from_text``: reproduced only with unsupported neighbours
      (internally consistent, not evidenced).

    Each carries the formula and a decoy null over that local pool.
    """
    for v in verdicts:
        if v.verdict != "unexplained" or v.claim.sig < SIG_GATE or v.claim.pos < 0:
            continue
        sup, txt = Pool(), Pool()
        for u in verdicts:
            if u is v or abs(u.claim.pos - v.claim.pos) > NEIGHBORHOOD or _fmt_eq(u.claim.value, v.claim):
                continue
            if u.verdict in ("verified", "copy"):
                for k in u.support[:2]:
                    if pool is not None and k in pool.values and not _fmt_eq(pool.values[k], v.claim):
                        sup.values[f"ev[{u.claim.id}]:{k}"] = pool.values[k]
                sup.values[f"claim:{u.claim.id}={u.claim.text}"] = u.claim.value
            txt.values[f"text:{u.claim.id}={u.claim.text}"] = u.claim.value
        txt.values.update(sup.values)
        for tier, lp in (("derived", sup), ("derived_from_text", txt)):
            if len(lp.values) < 2:
                continue
            hits = _matches(v.claim, lp)
            # count distinct formulas over CLAIMS, not over aliases of one claim
            # (its printed value and its full-precision evidence values)
            distinct = {_ALIAS.sub(lambda m: m.group(1) or m.group(2) or m.group(3), h) for h in hits}
            if hits and len(distinct) <= MULT_CAP:
                v.verdict, v.formula, v.support = tier, hits[0], hits
                v.null_rate = _decoy_rate(v.claim, lp)
                break


METADATA_WORDS = {"seed", "seeds", "bytes", "byte", "kb", "mb", "gb", "gib", "mib", "version", "gcc", "g++", "commit", "sha256", "sha", "job", "hostname", "pid", "port"}


def verify_report(report_text: str, pool: Pool, grammar: bool = True,
                  intermediates: bool = True) -> dict[str, Any]:
    rv = [classify(c, pool, grammar) for c in extract_claims(report_text)]
    derive_in_text(rv, pool)
    res = _summ(rv)
    res["pool_size"] = len(pool.values)
    res["grammar_truncated"] = bool(getattr(pool, "grammar_truncated", False))
    res["corroborated"] = corroborate(rv, pool)
    res["invariants"] = getattr(pool, "invariants", [])
    if intermediates:
        iv = [classify(c, pool, grammar, exclude=k) for c, k in intermediate_claims(pool)]
        res["intermediates"] = _summ(iv)
        res["intermediates"]["corroborated"] = corroborate(iv, pool)
    return res
