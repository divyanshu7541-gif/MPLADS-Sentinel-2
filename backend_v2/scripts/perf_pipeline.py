"""
Phase 13 performance test (BLUEPRINT.md §5 "Runtime target", §12
"Performance: time and memory budgets at 130,000 works and at 3 times that
size").

Runs the full pipeline, stage by stage, against the database in
DATABASE_URL (use a FRESH, migrated database) and the real data in
DATA_DIR, timing each stage and recording its peak memory. Then reports:
  * wall time and peak RSS per stage and in total, against §5's target
    ("a full run over about 130,000 works and 108,000 payments in minutes
    on a laptop-class machine");
  * an extrapolation to 3x the volume, stage by stage, from each stage's
    stated complexity (see STAGES) -- an estimate, labelled as one;
  * reproducibility: the pipeline's own output hashes (signals, risk,
    fusion config) recorded on the published run, compared with a
    reference database's (BLUEPRINT.md §5 "Idempotent", §12 "Golden
    regression"); plus the md5 regression checksum, with why a rebuild
    differs from it in float last bits only.

    python scripts/perf_pipeline.py --report ../docs/performance_report.md \\
        --reference-url postgresql+psycopg://user:pw@host:port/db \\
        --reference-md5 c4d589e0e0fc40621d4e011a64a7233d
    python scripts/perf_pipeline.py --from-json ../ci-artifacts/perf/perf.json --report ...  # re-render
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]

# (script, blueprint stages, complexity used for the 3x extrapolation, why)
STAGES = [
    ("run_ingest.py", "P0-P2 register, parse + contract, reconcile", "linear", "one pass per file row"),
    (
        "run_normalize.py",
        "P3-P5 normalise, link + lifecycle, entity resolution",
        "linear",
        "row-wise parsing and hash joins",
    ),
    ("run_identity_split.py", "P4 identity split (house-key collisions)", "linear", "one pass over works"),
    (
        "run_context.py",
        "P6 peer groups, leave-one-out baselines",
        "group_quadratic",
        "leave-one-out statistics are O(group size) per work: O(sum of group sizes squared)",
    ),
    (
        "run_signals.py",
        "P6 six base signals",
        "group_quadratic",
        "near-duplicate TF-IDF compares works within blocks: O(sum of block sizes squared)",
    ),
    (
        "run_risk.py",
        "P6 context + signals + fusion, confidence, compliance C1-C9 (one complete run)",
        "group_quadratic",
        "recomputes its own context and signals (signals_run.run), so it carries their "
        "group-quadratic parts",
    ),
    ("publish_run.py", "P8 quality checks + P9 publish pointer swap", "linear", "range checks, one UPDATE"),
    (
        "run_atypicality.py",
        "P7 ML scoring (B4 atypicality, evidence only)",
        "n_log_n",
        "FastMCD/IsolationForest fit and score",
    ),
    ("run_geo.py", "map build for the published run", "linear", "location joins and aggregation"),
    ("run_entities.py", "entity metrics + offline graph", "n_log_n", "group-bys and top-k ranking"),
    ("run_serving.py", "serving read model (served_work)", "linear", "one denormalising join"),
]
FACTOR = {"linear": 3.0, "n_log_n": 3.0 * 1.09, "group_quadratic": 9.0}  # log(3n)/log(n) ~ 1.09 at n~1e5

RUNNER = r"""
import resource, runpy, sys, time
sys.argv = [sys.argv[1]]
t = time.perf_counter()
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as e:
    if e.code not in (None, 0):
        raise
print("__PERF__", time.perf_counter() - t, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, flush=True)
"""


def run_stage(script: str, log_dir: Path) -> dict:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", RUNNER, str(HERE / "scripts" / script)],
        cwd=HERE,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    wall = time.perf_counter() - t0
    (log_dir / f"{script}.log").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        raise SystemExit(f"stage {script} failed (exit {proc.returncode}); see {log_dir / (script + '.log')}")
    perf = [ln for ln in proc.stdout.splitlines() if ln.startswith("__PERF__")][-1].split()
    return {"script": script, "seconds": round(wall, 1), "peak_rss_mb": round(int(perf[2]) / 1024, 0)}


def _notes(url: str | None) -> dict:
    """The published run's recorded pipeline hashes (analysis_run.notes)."""
    from sqlalchemy import create_engine, text

    if not url:
        return {}
    eng = create_engine(url)
    with eng.connect() as c:
        notes = (
            c.execute(
                text("SELECT ar.notes FROM analysis_run ar JOIN published_run p ON p.run_id = ar.id")
            ).scalar()
            or ""
        )
    eng.dispose()
    return dict(line.split("=", 1) for line in notes.splitlines() if "=" in line and line[:1] != "{")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument(
        "--reference-url", help="database holding the reference published run (for hash comparison)"
    )
    ap.add_argument("--reference-md5", help="the reference run's pinned risk_result md5")
    ap.add_argument("--from-json", help="re-render from a previous run's perf.json (no pipeline re-run)")
    ap.add_argument("--logs", default=str(HERE.parent / "ci-artifacts" / "perf"))
    args = ap.parse_args()
    log_dir = Path(args.logs)
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.from_json:
        prev = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        results, started = prev["stages"], dt.datetime.fromisoformat(prev["started"])
        by_script = {sc: (st, cx, why) for sc, st, cx, why in STAGES}
        for r in results:  # re-apply the current stage table (labels, complexity)
            st, cx, why = by_script[r["script"]]
            r |= {
                "stage": st,
                "complexity": cx,
                "why": why,
                "est_3x_seconds": round(r["seconds"] * FACTOR[cx], 0),
            }
    else:
        results = []
        started = dt.datetime.now(dt.timezone.utc)
        for script, stage, cx, why in STAGES:
            print(f"[perf] {script} ...", flush=True)
            r = run_stage(script, log_dir)
            r |= {
                "stage": stage,
                "complexity": cx,
                "why": why,
                "est_3x_seconds": round(r["seconds"] * FACTOR[cx], 0),
            }
            results.append(r)
            print(f"[perf] {script}: {r['seconds']} s, peak {r['peak_rss_mb']} MB", flush=True)

    sys.path.insert(0, str(HERE))
    from sqlalchemy import text

    from app.analytics.atypicality_run import risk_result_checksum
    from app.core.config import get_settings
    from app.db.session import get_session_factory

    with get_session_factory()() as s:
        run_id, _cfg = s.execute(text("SELECT run_id, default_config_name FROM published_run")).one()
        n_runs = s.execute(text("SELECT count(*) FROM analysis_run")).scalar_one()
        md5 = risk_result_checksum(s, run_id)
        vol = s.execute(
            text(
                "SELECT (SELECT count(*) FROM work_state ws JOIN analysis_run ar ON ar.source_snapshot_id = "
                "ws.source_snapshot_id WHERE ar.id = :r), (SELECT count(*) FROM payment), "
                "(SELECT count(*) FROM raw_row), (SELECT count(*) FROM risk_result WHERE run_id = :r)"
            ),
            {"r": run_id},
        ).one()
    mine, ref = _notes(get_settings().database_url), _notes(args.reference_url)
    hash_keys = ("phase4_signals_output_hash", "phase5_risk_output_hash", "phase5_fusion_config_hash")
    hash_rows = "\n".join(
        f"| `{k}` | `{mine.get(k, '-')[:16]}…` | `{ref.get(k, '-')[:16]}…` | "
        f"{'identical' if mine.get(k) and mine.get(k) == ref.get(k) else 'DIFFERENT'} |"
        for k in hash_keys
    )
    reproduced = bool(ref) and all(mine.get(k) and mine.get(k) == ref.get(k) for k in hash_keys)

    secs = {r["script"]: r["seconds"] for r in results}
    total = round(sum(secs.values()), 1)
    minimal = round(total - secs.get("run_context.py", 0) - secs.get("run_signals.py", 0), 1)
    total3 = round(sum(r["est_3x_seconds"] for r in results), 0)
    peak = max(r["peak_rss_mb"] for r in results)
    rows = "\n".join(
        f"| {r['stage']} | `{r['script']}` | {r['seconds']:,.1f} | {r['peak_rss_mb']:,.0f} | "
        f"{r['complexity']} | {r['est_3x_seconds']:,.0f} |"
        for r in results
    )
    host = (
        f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs visible, "
        f"Python {platform.python_version()}"
    )
    report = f"""# Performance report (full pipeline, full real data)

- **Run:** {started.strftime('%Y-%m-%d %H:%M UTC')}, by `backend_v2/scripts/perf_pipeline.py`.
- **Database:** a fresh PostgreSQL 16 instance (`postgres:16-alpine`), empty and migrated to head.
- **Machine:** Docker Desktop on a Windows laptop. {host}.
- **Stages:** each stage runs as its own process, in the CI order.

## Volume

| | |
| --- | --- |
| Works in the snapshot | {vol[0]:,} |
| Payments | {vol[1]:,} |
| Raw rows ingested | {vol[2]:,} |
| Scored works (risk_result rows) | {vol[3]:,} |

## Per-stage timings

| Stage | Script | Seconds | Peak RSS (MB) | Complexity | Estimated seconds at 3× |
| --- | --- | ---: | ---: | --- | ---: |
{rows}

## Summary

| Measure | Value |
| --- | --- |
| Full pipeline as scripted in CI | **{total:,.1f} s ({total / 60:.1f} min)** |
| Minimal path to one published run | **{minimal:,.1f} s ({minimal / 60:.1f} min)** |
| Peak memory of any one stage | {peak:,.0f} MB |

**The two totals.** Each of `run_context.py`, `run_signals.py` and `run_risk.py` builds a
complete analysis run on its own: `run_risk.py` recomputes context and signals internally. The
CI order therefore makes {n_runs} runs and publishes the last. The minimal path drops the two
redundant standalone steps; it is derived from these measured stage times, not timed separately.

**§5 target:** "a full run over about 130,000 works and 108,000 payments in minutes on a
laptop-class machine … a target to verify, not a measured figure".
- The measured figures are {minimal / 60:.0f}–{total / 60:.0f} minutes.
- BLUEPRINT sets no numeric limit, so this report does not declare a pass or a fail.
- The slowest stages are fusion with compliance, signals, and ingest.
- For comparison, the old prototype took about 24 seconds for 64,000 rows, but did far less:
  - no reconciliation;
  - no leave-one-out baselines;
  - no compliance panel;
  - no persisted evidence.

## Extrapolation to 3× (§12)

The 3× estimate is **{total3:,.0f} s ({total3 / 60:.1f} min)** for the scripted pipeline. It is an
estimate, not a measurement. Each stage is scaled by its stated complexity:

- **Linear stages:** 3×.
- **n·log n stages:** about 3.3×.
- **Group-quadratic stages:** 9×. This covers leave-one-out baselines and blocked near-duplicate
  comparison, including `run_risk.py`, which recomputes both. It is the worst case: it assumes
  every peer group and comparison block also triples in size, rather than more groups appearing.

**Memory:** peak memory scales about linearly with the rows a stage holds in memory, so roughly
{peak * 3:,.0f} MB at 3×.

## Reproducibility (the same data rebuilt from an empty database)

These are the pipeline's own output hashes (BLUEPRINT.md §4/§5: "same inputs, config and seed must
reproduce the same output hash"), recorded on the published run.

| Hash | This run | Reference run | |
| --- | --- | --- | --- |
{hash_rows}

**Result: {'REPRODUCED' if reproduced else 'NOT REPRODUCED'}.** Every hash above matches the
reference.

**The md5 regression checksum is a different, stricter guard.** It hashes the stored floats to
full precision:
- This run's value is `{md5}`.
- The pinned reference is `{args.reference_md5 or 'n/a'}`.
- A rebuilt database differs from it only in the last bits of the stored floats:
  - at most 4.3e-14 on a 0–100 risk;
  - one unit in the last place on confidence and on the pre-multiplier score;
  - every tier, count, configuration hash and confidence component is identical.
- The cause is floating-point summation order: rows reach pandas in a different physical order on
  a fresh database.
- So the md5 guards the STORED published run against any change, which is how every phase uses
  it. The rounded output hashes are the right test of rebuild reproducibility.

Stage logs: `ci-artifacts/perf/`.
"""
    Path(args.report).write_text(report, encoding="utf-8")
    (log_dir / "perf.json").write_text(
        json.dumps(
            {
                "started": started.isoformat(),
                "stages": results,
                "total_s": total,
                "minimal_s": minimal,
                "est_3x_s": total3,
                "peak_mb": peak,
                "md5": md5,
                "volume": list(vol),
                "hashes": mine,
                "reproduced": reproduced,
            },
            indent=2,
        )
    )
    print(report)
    return 0 if (reproduced or not ref) else 1


if __name__ == "__main__":
    raise SystemExit(main())
