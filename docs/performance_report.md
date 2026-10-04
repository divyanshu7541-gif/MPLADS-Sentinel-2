# Performance report (full pipeline, full real data)

- **Run:** 2026-09-28 04:12 UTC, by `backend_v2/scripts/perf_pipeline.py`.
- **Database:** a fresh PostgreSQL 16 instance (`postgres:16-alpine`), empty and migrated to head.
- **Machine:** Docker Desktop on a Windows laptop. Linux x86_64, 4 CPUs visible, Python 3.12.14.
- **Stages:** each stage runs as its own process, in the CI order.

## Volume

| | |
| --- | --- |
| Works in the snapshot | 122,965 |
| Payments | 107,826 |
| Raw rows ingested | 655,306 |
| Scored works (risk_result rows) | 195,012 |

## Per-stage timings

| Stage | Script | Seconds | Peak RSS (MB) | Complexity | Estimated seconds at 3× |
| --- | --- | ---: | ---: | --- | ---: |
| P0-P2 register, parse + contract, reconcile | `run_ingest.py` | 411.3 | 518 | linear | 1,234 |
| P3-P5 normalise, link + lifecycle, entity resolution | `run_normalize.py` | 214.3 | 473 | linear | 643 |
| P4 identity split (house-key collisions) | `run_identity_split.py` | 18.4 | 290 | linear | 55 |
| P6 peer groups, leave-one-out baselines | `run_context.py` | 88.4 | 469 | group_quadratic | 796 |
| P6 six base signals | `run_signals.py` | 533.5 | 902 | group_quadratic | 4,802 |
| P6 context + signals + fusion, confidence, compliance C1-C9 (one complete run) | `run_risk.py` | 826.4 | 1,521 | group_quadratic | 7,438 |
| P8 quality checks + P9 publish pointer swap | `publish_run.py` | 4.9 | 154 | linear | 15 |
| P7 ML scoring (B4 atypicality, evidence only) | `run_atypicality.py` | 237.9 | 581 | n_log_n | 778 |
| map build for the published run | `run_geo.py` | 160.1 | 858 | linear | 480 |
| entity metrics + offline graph | `run_entities.py` | 289.7 | 635 | n_log_n | 947 |
| serving read model (served_work) | `run_serving.py` | 199.1 | 955 | linear | 597 |

## Summary

| Measure | Value |
| --- | --- |
| Full pipeline as scripted in CI | **2,984.0 s (49.7 min)** |
| Minimal path to one published run | **2,362.1 s (39.4 min)** |
| Peak memory of any one stage | 1,521 MB |

**The two totals.** Each of `run_context.py`, `run_signals.py` and `run_risk.py` builds a
complete analysis run on its own: `run_risk.py` recomputes context and signals internally. The
CI order therefore makes 3 runs and publishes the last. The minimal path drops the two
redundant standalone steps; it is derived from these measured stage times, not timed separately.

**§5 target:** "a full run over about 130,000 works and 108,000 payments in minutes on a
laptop-class machine … a target to verify, not a measured figure".
- The measured figures are 39–50 minutes.
- BLUEPRINT sets no numeric limit, so this report does not declare a pass or a fail.
- The slowest stages are fusion with compliance, signals, and ingest.
- For comparison, the old prototype took about 24 seconds for 64,000 rows, but did far less:
  - no reconciliation;
  - no leave-one-out baselines;
  - no compliance panel;
  - no persisted evidence.

## Extrapolation to 3× (§12)

The 3× estimate is **17,785 s (296.4 min)** for the scripted pipeline. It is an
estimate, not a measurement. Each stage is scaled by its stated complexity:

- **Linear stages:** 3×.
- **n·log n stages:** about 3.3×.
- **Group-quadratic stages:** 9×. This covers leave-one-out baselines and blocked near-duplicate
  comparison, including `run_risk.py`, which recomputes both. It is the worst case: it assumes
  every peer group and comparison block also triples in size, rather than more groups appearing.

**Memory:** peak memory scales about linearly with the rows a stage holds in memory, so roughly
4,563 MB at 3×.

## Reproducibility (the same data rebuilt from an empty database)

These are the pipeline's own output hashes (BLUEPRINT.md §4/§5: "same inputs, config and seed must
reproduce the same output hash"), recorded on the published run.

| Hash | This run | Reference run | |
| --- | --- | --- | --- |
| `phase4_signals_output_hash` | `7f98f33d96d1ca3c…` | `7f98f33d96d1ca3c…` | identical |
| `phase5_risk_output_hash` | `708357bbff1bbdda…` | `708357bbff1bbdda…` | identical |
| `phase5_fusion_config_hash` | `953866a5ebbd97cf…` | `953866a5ebbd97cf…` | identical |

**Result: REPRODUCED.** Every hash above matches the
reference.

**The md5 regression checksum is a different, stricter guard.** It hashes the stored floats to
full precision:
- This run's value is `a4de5f570b6f04e8566a08932789c069`.
- The pinned reference is `97f08303369f9ed6c50e46d68a4609f5` (run 1, when this report was made). Since 2026-09-28 the published run is 44, with checksum `c4d589e0e0fc40621d4e011a64a7233d` (the intentional reset after the authority-state fix; `docs/validation_report_v1.md`); pass that value as `--reference-md5` for a rebuild of the current input.
- A rebuilt database differs from it only in the last bits of the stored floats:
  - at most 4.3e-14 on a 0–100 risk;
  - one unit in the last place on confidence and on the pre-multiplier score;
  - every tier, count, configuration hash and confidence component is identical.
- The cause is floating-point summation order: rows reach pandas in a different physical order on
  a fresh database.
- So the md5 guards the STORED published run against any change, which is how every phase uses
  it. The rounded output hashes are the right test of rebuild reproducibility.

Stage logs: `ci-artifacts/perf/`.
