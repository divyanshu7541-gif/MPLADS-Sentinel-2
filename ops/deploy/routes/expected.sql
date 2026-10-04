-- Expected values for fixes.py, computed from the BASE tables (work, risk_result,
-- published_run) -- never from the serving layer or the API the browser checks exercise.
WITH p AS (SELECT run_id, default_config_name AS c FROM published_run),
     w AS (SELECT w.work_key FROM work w JOIN source_snapshot s ON s.id = w.first_seen_snapshot_id
           WHERE s.code = 'snapshot_a' AND btrim(w.raw_mp_name) = 'ASHISH DUBEY')
SELECT json_build_object(
  'mp', 'ASHISH DUBEY',
  'mp_total_works', (SELECT count(*) FROM w),
  'mp_scored_works', (SELECT count(*) FROM risk_result r, p
                      WHERE r.run_id = p.run_id AND r.config_name = p.c AND r.work_key IN (SELECT work_key FROM w)),
  'mp_high_priority', (SELECT count(*) FROM risk_result r, p
                       WHERE r.run_id = p.run_id AND r.config_name = p.c AND r.tier IN ('CRITICAL', 'HIGH')
                         AND r.work_key IN (SELECT work_key FROM w)),
  'record', '251224',
  'record_risk', (SELECT r.risk FROM risk_result r, p
                  WHERE r.run_id = p.run_id AND r.config_name = p.c AND r.work_key = '251224'),
  'total_scored', (SELECT count(*) FROM risk_result r, p WHERE r.run_id = p.run_id AND r.config_name = p.c),
  'rs_scored', (SELECT count(*) FROM risk_result r, p WHERE r.run_id = p.run_id AND r.config_name = p.c AND r.house = 'RS'),
  'published_run', (SELECT run_id FROM p)
);
