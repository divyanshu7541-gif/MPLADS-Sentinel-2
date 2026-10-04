# Phase 10 + 11 report: entity/graph analytics and the copilot

**Status:** implemented, then revised once against the owner's live review (2026-09-27): `payee_type`/`review_status` added to the payee entity response, MP-tenure concentration built (was deferred), and a pre-existing Phase 6 test/report discrepancy investigated and corrected (see "Corrections after live review" below). Stopped again at STOP CONDITION before Phase 12.

**Data:** scratch database, run 1 (Snapshot A), published default config `v4-candidate`.

**risk_result checksum:** `97f08303369f9ed6c50e46d68a4609f5` before and after the migration and the entity/graph build. `scripts/run_entities.py` records both values, and `test_risk_result_unchanged_by_the_entity_build` asserts them; `build_entities` itself aborts (raises, no commit) if they ever differ.

## What was built

| Piece | Where |
|---|---|
| Migration `056cfc96cbac` | `entity_metric`, `graph_node`, `graph_edge`; widens `work_evidence_fact.fact`'s CHECK to two new facts. |
| Payee retyping | `app/entities/payee_typing.py`: fixes a real Phase 2 bug (below). |
| Work-evidence facts | `app/entities/facts.py`: `identical_payment_repeated`, `multi_payee_work`. |
| Entity metrics | `app/entities/metrics.py`: concentration, price position, reach, district authority profile, implementing agency profile. |
| Graph | `app/entities/graph_build.py`: MPs, works, payees, authorities, computed offline. |
| Orchestrator + CLI | `app/entities/build.py`, `scripts/run_entities.py`. |
| Endpoints | `app/api/entities.py`: real `GET /api/graph-data` (moved out of `app/api/maps.py`), and the new `GET /api/entities/{entity_type}/{entity_id}`. |
| Copilot | `app/api/chat.py` (real, was a Phase 0 stub), `app/analytics/methodology.py` (generates the methodology text), `app/core/chat_config.py` (copied from `backend/`, same design). |
| CI | `scripts/run_entities.py` runs before pytest. |
| Tests | `tests/test_phase10_entities.py`, `tests/test_phase11_chat.py`. |
| Contract | "Phase 10/11 additions" section in `docs/frontend_contract.md`. Every change is additive; both chat endpoints and `graph-data`'s shape are unchanged. |

## A real bug found and fixed: payee typing

BLUEPRINT.md §8 opens with "every payee carries a type ... before any concentration metric is shown." Inspecting the 22,992 payees the Phase 2 classifier (`app/ingest/p5_payee.py`) had stamped `"private_firm"` showed that outcome was its **default for "matched no rule,"** not evidence of being a firm. Real examples pulled from that bucket:

- Bare personal names with no honorific: `Noli devi`, `Radha jat`, `mukesh`, `suwa lal meena`.
- Government/statutory role titles the old rule simply didn't have tokens for: `BDO Kunihar`, `Sarpanch Shri Jindava`, `CEO Sonkatch`, `cmo sanawad`, `chairman habitation works committee ...`.
- A work description, not a name at all, sitting in the vendor-name field: `CONST OF ROAD FROM B B LINKED ROAD TO UPEN DAS HOUSE AT BAMUN BARADI` (a source-data quality issue, left as-is, typed `unclassified`).

**The fix** (`classify_payee_type_v2`), each addition checked against real counts before being kept:

1. Government/statutory tokens extended with roles actually observed in the sample (`BDO`, `SACHIV`, `SARPANCH`, `CEO`, `CMO`, `CHAIRMAN`, `COMMITTEE`, `HABITATION`, `VILLAGE`).
2. A `manufacturer` type: a positive keyword list (`INDUSTRIES`, `ENGINEERING`, `STEEL`, `CEMENT`, `MOTORS`, ...) that BLUEPRINT's own prose names as a category the payee field mixes in.
3. **The default for "matched nothing" changes from `private_firm` to `unclassified`.** `private_firm` is now only ever a positive match on a real firm-suffix token (`ENTERPRISES`, `TRADERS`, `PVT`, `LTD`, ...) — exactly BLUEPRINT's "ambiguous cases queued for review." An unclassified payee still gets typed and still participates in reach/concentration math (excluding it would bias the market shares it's part of); it just carries an honest "not positively identified" label instead of a guessed one.

A blanket "all-lowercase → individual" rule was tried against the real data first and **rejected**: it also matched real government titles written in lowercase (`sarpanch gp musadehi`, `cmo sanawad`), which would have been a worse error than leaving them unclassified. No such rule shipped; a bare personal name with no honorific and no organisation token is `unclassified`, not guessed as `individual`.

**Effect on the loaded data** (29,583 payees, run against the real dataset, not simulated):

| | Before | After |
|---|---|---|
| `private_firm` | 22,992 | 5,364 |
| `unclassified` | 0 | 17,122 |
| `manufacturer` | 0 | 953 |
| `statutory_or_government` | 5,735 | 5,849 |
| `individual` | 856 | 295 |

18,883 payees changed type. The largest single move is `private_firm -> unclassified` (16,927) -- the bug this phase fixes. `payee_type` is not read anywhere in Phase 3-9's scoring (`grep` confirms no `payee_type` reference outside `app/ingest` and this phase's own modules), so this fix cannot and does not touch `risk_result`.

`GET /api/entities/payee/{id}` now returns the fixed type live, not only in the database, in an additive `attributes` field: `{"payee_type": "unclassified", "review_status": "unreviewed"}` for payee 39900 (`BHAGWATI CHAND`), confirmed against the running server (previous review round).

## Sample payee profiles (for the owner's review)

**High-volume, low-per-work-amount payee** -- the same one BLUEPRINT.md §8 itself names ("the payee with the most works (785) received INR 3.5 crore in total from a single MP, about INR 45,000 per work"):

- Payee 39900, canonical name `BHAGWATI CHAND` -- a bare personal name, correctly typed `unclassified` under the fixed classifier (it was `private_firm` before). 785 works, ₹3.53 crore total, ≈₹44,955/work.
- `GET /api/entities/payee/39900` returns its `reach` row (785 works, descriptive) and any `price_position` rows it qualifies for (≥5 works at one work type against level-1 peers).

**Naturally-concentrated small market** -- BLUEPRINT.md §8's own example ("a payee whose raw Herfindahl is high but whose local market is naturally concentrated... only 2 payees exist in that stratum... NOT flagged as unusual"):

- District authority 464, `MYSURU(DEPUTY COMMISSIONER MYSORE_IDA)`: Herfindahl 0.845 (high -- most payments concentrated in very few payees) over 192 payments to 7 distinct payees, but its **percentile against the permutation null is exactly 0.5** -- squarely typical for the (district, work-type, FY) strata its payments actually fall in. It is not flagged as unusual, because the null itself is just as concentrated.
- District authority 389, `Kasganj(DISTRICT MAGISTRATE KASGANJ KANSHIRAMNAGAR_IDA)`: 3 distinct payees, Herfindahl 0.549, percentile 0.535 -- again typical, not unusual, for a stratum-mix with genuinely few payees.
- Contrast: district authority 67, `BASTI(DISTRICT MAGISTRATE BASTI_IDA)`, has a similarly high raw Herfindahl (0.653) but sits at percentile 0.31 -- *less* concentrated than its null would suggest, illustrating that the same raw number lands differently depending on the real stratum-mix.

`GET /api/entities/district_authority/464` (and `/389`, `/67`) return these rows with their full `denominator`/`interval`/`peer_definition` wording.

## Entity metrics (run 1)

| Metric | Rows | Eligible |
|---|---|---|
| `reach` (payee) | 29,583 | 29,583 (always descriptive) |
| `price_position` (payee x work type) | 24,120 | 1,400 (≥5 priced works at that work type) |
| `concentration` (district authority + MP tenure) | 1,389 (710 + 679) | 1,389 (≥5 payments, ≥2 payees) |
| `district_authority_profile` | 692 | 691 (1 authority has no work with both a recommendation and a sanction date) |
| `implementing_agency_profile` | 1,377 | 1,215 (≥10 works; some have no completed work with both dates) |

**MP-tenure concentration.** BLUEPRINT.md §8's stated grain is "MP tenure, district authority," but `work.tenure_id` is **0% populated** in this dataset (`SELECT count(tenure_id) FROM work` = 0 of 129,019 rows) -- a pre-existing gap, not something this phase introduces. Phase 3/4's own MP identity for peer grouping and signals is the normalised `raw_mp_name` text (`app.ingest.normalize.normalize_name`), never `tenure_id`; this phase's `reach` metric and the graph's MP nodes already used that same identity, and this grain now does too.

Within one snapshot, `house` alone determines the tenure label (`TENURE_LABELS = ("18th Lok Sabha", "Sitting MP")`, one per house -- `app/models/reference.py`), so there is no multi-term ambiguity to resolve here: `(normalised MP name, house)` **is** the tenure identity for this dataset, with no start/end dates needed to disambiguate. `entity_metric.entity_id` is an Integer with no backing table for a name, so `app/entities/metrics.py`'s `mp_tenure_id()` derives a stable synthetic id: the first 8 hex digits of `sha1(f"{mp}|{house}")`, mod 2×10⁹. Collision risk (~730 distinct name/house pairs into a ~2×10⁹ id space) is about 2×10⁻⁷ -- negligible, and documented rather than hidden. `GET /api/entities/mp_tenure/{id}` resolves the id purely from that run's `entity_metric` rows (there is no other table that could confirm a synthetic id independently), so an id with no stored metric is a plain 404, not a guess.

**Built:** 679 MP-tenure concentration rows alongside 710 district-authority rows (1,389 total), using the exact same permutation draws (the null only depends on the district/work-type/FY stratum a payment falls in, never on which entity grouping is being scored, so both groupings' Herfindahl figures are read off one shared set of 200 shuffles, not two independent ones). Example: MP tenure "PANKAJ CHOWDHARY (LS)" -- Herfindahl 0.892 across 94 payments to 8 distinct payees, at the 100th percentile of its own null (fully concentrated relative to what the same district/type/FY strata look like elsewhere nationally).

## The graph (run 1)

122 nodes (25 MPs, 25 district authorities, 25 payees, 47 bridging works), 97 edges (47 `recommends`, 44 `executes`, 6 `pays`). Bounds are judgement calls, fixed before looking at the output: `TOP_ENTITIES = 25` per type, `TOP_WORKS_PER_MP = 3` bridging works. `GET /api/graph-data`'s `house` filter now really filters edges (an edge is tagged with its one backing work's House); nodes are unfiltered, so a node can appear with no edges under a filter -- documented in the contract addendum.

## The copilot

- **Methodology, generated:** `app/analytics/methodology.py` builds the risk-methodology section of the chat system prompt directly from `fusion.config_dict()` and `confidence.COMPONENT_WEIGHTS` -- weights, the active threshold, the corroboration table, tier thresholds, and the confidence component weights. A changed weight or threshold shows up in the chatbot's own explanation the next time it answers, with nothing to remember to update by hand. The hand-authored navigation/page-map text is untouched, since it describes UI, not scoring.
- **"Explain this work" guard, code-level:** a message using an explain/why/risk/score/flagged/priority verb *and* naming something work-key-shaped fetches that work's actual stored `risk_result` row (the published run's default config) and its `forecast_result` rows, if any, before any reply is built. No stored row -> the reply says so explicitly and stops there; the LLM is never asked to guess a number, and (with no Gemini key configured, as in this environment) the Gemini call is skipped entirely for this path.
- **House-context:** a grounded reply always names the work's House. For a Rajya Sabha work it states plainly that RS members have no constituency, so a constituency-level view doesn't apply -- verified against a real scored RS work in the loaded data (`tests/test_phase11_chat.py::test_explain_an_rs_work_states_house_and_never_implies_a_constituency`).
- **Fraud-language decline:** unchanged in spirit from `backend/app/api/chat.py`, tested against four adversarial phrasings (a fraud-probability request, a guilty/corruption question, a "fraudulent" percentage request, an "accuse" request) -- each declines and redirects, and none of the four replies contains a `%` (no fabricated probability).
- **Fallback:** with no `GEMINI_API_KEY` configured (as in this environment), every question is answered by the local, rule-based guide, unchanged in design from `backend/app/api/chat.py`.

## Judgment calls for the owner to confirm

1. **Concentration minimums:** `MIN_PAYMENTS_FOR_CONCENTRATION = 5` payments and at least 2 distinct payees before a Herfindahl is computed at all; `N_PERMUTATIONS = 200` draws for the null.
2. **Price-position minimum:** `MIN_WORKS_FOR_PRICE_POSITION = 5` of a payee's own works at one work type; `N_BOOTSTRAP = 1000` resamples for the interval.
3. **Profile minimums:** `MIN_WORKS_FOR_DISTRICT_PROFILE = 10` and `MIN_WORKS_FOR_AGENCY_PROFILE = 10`, matching the existing `MIN_AUTHORITY_PORTFOLIO`/`MIN_PORTFOLIO_SIZE` precedent in `app/analytics/signals.py`.
4. **Graph bounds:** `TOP_ENTITIES = 25`, `TOP_WORKS_PER_MP = 3` -- picked to keep the graph small and exploratory, not tuned to this run's output.
5. **MP-tenure concentration's synthetic id:** a sha1-derived hash of (normalised name, house), not a real table row (see above) -- a documented, negligible collision risk rather than an invented tenure table.
6. **"District authority profile" headline stat** is the recommendation-to-sanction lag median; sanction-to-completion lag, open-backlog age and payment-ageing are carried in `detail` rather than each getting their own percentile, to keep one row per authority.

## Tests

`tests/test_phase10_entities.py` and `tests/test_phase11_chat.py`, both permanent and read-only.

- **Payee typing:** the reviewed examples above, each asserted by name; the old default-to-`private_firm` bug is asserted gone; idempotence.
- **No-guilt-by-association:** `risk_result`'s live schema has no payee/agency/entity-derived column; `app/analytics/fusion.py`'s source text never mentions `entity_metric`.
- **Permutation-null:** BLUEPRINT's own "naturally concentrated small market" example, read back from the real data.
- **Wording rule:** every `entity_metric` row (eligible or not) has a non-null `denominator`, `interval`, `peer_definition`, both directly against the database and through the `GET /api/entities/...` response model.
- **Phase 9's corrected location:** the district authority profile's peer definition cites the Phase 9 `resolved_state`, not the stored (buggy) one.
- **Graph:** contract shape, bounded size, edges reference declared nodes, the House filter, no base-table scan at request time.
- **Isolation:** a dead database is 503 on both new endpoints; other endpoints keep working.
- **Chat:** methodology reflects a patched config (config-drift), the missing-work-key fail-closed reply, the four adversarial fraud prompts, the local-guide fallback with no key, the RS/LS house-context replies, and the NOT_EVALUATED-work reply never showing a number.

## Deviations from the brief

- `GET /api/graph-data` moved from `app/api/maps.py` to the new `app/api/entities.py` -- same route, same shape, just relocated to sit with the rest of Phase 10's code.
- The new `GET /api/entities/{entity_type}/{entity_id}` endpoint is not in the frontend contract (no page calls it), the same posture as Phase 9's `GET /api/geo/areas/{key}/works`.

## Corrections after live review (2026-09-27)

The owner asked for live `curl` output against the running server, not a description of it, then asked for three specific gaps to be closed before Phase 12 rather than deferred:

1. **Phase 6's stale `eligible_hits == 12173` assertion.** Investigated first, per the owner's instruction not to just patch the number: `model_version` shows `b4_atypicality_robust_mahalanobis` was computed exactly once for run 1, hours before any Phase 9/10/11 code existed, and nothing in those phases writes `atypicality_result`/`work_evidence_fact`/`payment`/`work_context` -- so this isn't a Phase 9/10/11 regression. Both of the figure's own marginal totals still match the Phase 6 report exactly (16,911 fact rows; 76,732 Mahalanobis-eligible works); only their intersection (12,173 documented vs. 13,659 live) had drifted, almost certainly a stale one-time cross-tabulation from before Phase 9 rebuilt this database, never re-verified since. `docs/phase6_atypicality_report.md` now has a dated correction section, and the test asserts the verified current value, 13,659. *Retracted 2026-09-28 (Phase 13.y): the drift was Phase 10's own two new fact types. The test query had no fact-type filter, so it counted all three; 12,173 was correct. The test now filters by fact type and computes the expected count live from the payment table (12,176 on run 44). See the retraction in `docs/phase6_atypicality_report.md`.*
2. **`payee_type` missing from the entity API.** `EntityProfileOut` gained an additive `attributes` field: `{"payee_type", "review_status"}` for a payee, `{"agency_type"}` for an agency, `{"district_key", "resolved_state"}` (the Phase 9 corrected location) for a district authority, `{}` for `mp_tenure` (nothing else to show for a synthetic identity).
3. **MP-tenure concentration synthesised**, not left parked -- see "MP-tenure concentration" above. The first working version (two full permutation-null passes, one per grouping, computed the same way as the original district-authority-only code) took several minutes longer than acceptable; it was rewritten to share one set of shuffles across both groupings and to compute each iteration's Herfindahl with two vectorised `groupby().sum()` calls instead of a per-group Python callback, before being shipped.

All three verified together: `scripts/run_entities.py` re-run against the scratch database, `risk_result` checksummed before and after (unchanged, `97f08303369f9ed6c50e46d68a4609f5`), and the full backend_v2 suite run three times (see the closing summary).
