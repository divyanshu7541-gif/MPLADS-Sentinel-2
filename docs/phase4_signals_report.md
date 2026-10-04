# Phase 4 Base Signals Report

> **Superseded in part (added 2026-09-26).** For **portfolio_concentration**, **district_authority_pattern** and **temporal_anomaly**, this report describes a version that gave every work its MP's or authority's worst score instead of its own. Those three signals were corrected at the start of Phase 5. **[phase4_addendum.md](phase4_addendum.md) supersedes this report for those three signals.** Everything else here is unchanged and kept as the historical record of the Phase 4 run as reviewed.

Generated 2026-09-25T15:32:28.803524+00:00 by `scripts/run_signals.py`.

analysis_run id 1, engine `context_v1` (Phase 3's context engine, reused unchanged)
output_hash `003c1432c9a0f8ec62c7e868b3de3ce149c8271ff22f1060a44c91859e3b1466` (peer_group/work_context only -- signals do not change this hash)
Works in scope: 97,506 ({'LS': 78368, 'RS': 19138})

## 1. What this run is, and is not

- **No fusion, no risk score, no confidence, no ML** in this phase (Phase 4 brief). Each signal's `score`/`eligible`/`direction`/`evidence` stands alone; `reliability`/`dispersion` are quality inputs recorded for Phase 5's confidence engine and are never read by any score computation here (see section 4).
- **House-neutral by construction:** no signal function reads `house` at all -- every score comes only from the work-type/state/MP/authority population as a whole. `house` is copied onto each row purely so a read-time filter can select which rows are returned (see section 5).
- **Cost anomaly is two-sided** (unusually cheap scores the same as unusually expensive). BLUEPRINT.md §6 lists one-sided-vs-two-sided as a still-gated policy choice -- flagged here, not settled by this phase.
- **Provisional constants** (block sizes, n-gram width, batch-day/burst thresholds, minimum portfolio sizes, the log-scale MAD floor): every one is a documented, reasoned starting point in `app/analytics/signals.py`'s module docstring and per-signal comments, none is a fitted or hand-labelled value -- BLUEPRINT.md §6 lists calibration itself as a gated policy change.

## 2. Coverage per signal

| Signal | Weight | Eligible | Mean | Median | P90 | P99 | Max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cost_anomaly | 25% | 95,059 (97.49%) | 0.491 | 0.500 | 0.994 | 1.000 | 1.000 |
| near_duplicate | 20% | 97,504 (100.0%) | 0.490 | 0.462 | 0.960 | 1.000 | 1.000 |
| portfolio_concentration | 10% | 96,012 (98.47%) | 0.966 | 1.000 | 1.000 | 1.000 | 1.000 |
| district_authority_pattern | 10% | 95,855 (98.31%) | 0.991 | 1.000 | 1.000 | 1.000 | 1.000 |
| temporal_anomaly | 10% | 97,504 (100.0%) | 0.985 | 1.000 | 1.000 | 1.000 | 1.000 |
| lifecycle_delay | 10% | 52,934 (54.29%) | 0.744 | 0.833 | 1.000 | 1.000 | 1.000 |

"Eligible" works have a real score; the rest are "not evaluated" (never a score of 0) -- BLUEPRINT.md §6 confidence. Ineligibility reasons are signal-specific (e.g. cost anomaly needs a qualifying peer group AND a usable amount; lifecycle delay only applies to still-open works).

## 3. Spot-check sample, per signal (for manual review)

Three groups per signal: the overall top scores, the top scores restricted to Rajya Sabha (guaranteed RS representation even though RS is the smaller House by volume), and a near-median "typical" sample -- so both known-anomalous-looking and known-normal-looking works are here to check explanations against, across both Houses, per the STOP CONDITION.

### cost_anomaly

| work_key | house | mp | score | direction | evidence |
| --- | --- | --- | --- | --- | --- |
| **top overall** | | | | | |
| 178201 | LS | DR. PRABHA MALLIKARJUN | 1.000 | above | level=L1; ratio_to_peer_median=2.0; z_log_scale=14.206699082890463; peer_median=500000.0; amount_used=1000000.0 |
| 10747 | RS | SMT. DARSHANA SINGH (2022-28) | 1.000 | above | level=L2; ratio_to_peer_median=16.480223792076337; z_log_scale=24.14295205926346; peer_median=303496.0; amount_used=5001682.0 |
| 178210 | LS | MALAIYARASAN D | 1.000 | below | level=L3; ratio_to_peer_median=0.4967453333333333; z_log_scale=-9.903346983566623; peer_median=750000.0; amount_used=372559.0 |
| 101445 | RS | DR. JOHN BRITTAS (2021-27) | 1.000 | above | level=L2; ratio_to_peer_median=6.896042; z_log_scale=39.576575662351615; peer_median=500000.0; amount_used=3448021.0 |
| 101413 | RS | DR. JOHN BRITTAS (2021-27) | 1.000 | above | level=L1; ratio_to_peer_median=1.7295376546827101; z_log_scale=11.228783604912989; peer_median=494035.5; amount_used=854453.0 |
| **top, Rajya Sabha** | | | | | |
| 178233 | RS | SHRI JAIRAM RAMESH (2022-28) | 1.000 | above | level=L1; ratio_to_peer_median=2.0014290203205087; z_log_scale=14.221338406851656; peer_median=499643.0; amount_used=1000000.0 |
| 178232 | RS | SHRI JAIRAM RAMESH (2022-28) | 1.000 | above | level=L1; ratio_to_peer_median=2.0014290203205087; z_log_scale=14.221338406851656; peer_median=499643.0; amount_used=1000000.0 |
| **typical (near median)** | | | | | |
| 153858 | LS | GAJENDRA SINGH PATEL | 0.500 | below | level=L1; ratio_to_peer_median=0.8713171622600318; z_log_scale=-0.6744907594765952; peer_median=190546.0; amount_used=166026.0 |
| 234142 | LS | SAPTAGIRI SANKAR ULAKA | 0.500 | below | level=L1; ratio_to_peer_median=0.6666666666666666; z_log_scale=-0.6744907594765952; peer_median=300000.0; amount_used=200000.0 |
| 234141 | LS | SAPTAGIRI SANKAR ULAKA | 0.500 | below | level=L1; ratio_to_peer_median=0.6666666666666666; z_log_scale=-0.6744907594765952; peer_median=300000.0; amount_used=200000.0 |

### near_duplicate

| work_key | house | mp | score | direction | evidence |
| --- | --- | --- | --- | --- | --- |
| **top overall** | | | | | |
| 97895 | RS | SMT. SUMITRA BALMIK (2022-28) | 1.000 |  | matched_work_key=97888; match_basis=mp; cosine_similarity=1.0; date_gap_days=0 |
| 97888 | RS | SMT. SUMITRA BALMIK (2022-28) | 1.000 |  | matched_work_key=97895; match_basis=mp; cosine_similarity=1.0; date_gap_days=0 |
| 150944 | LS | RADHE SHYAM RATHIYA | 1.000 |  | matched_work_key=150939; match_basis=mp; cosine_similarity=1.0; date_gap_days=0 |
| 277308 | LS | SHRI GURJEET SINGH AUJLA | 1.000 |  | matched_work_key=277310; match_basis=mp; cosine_similarity=1.0; date_gap_days=0 |
| 277310 | LS | SHRI GURJEET SINGH AUJLA | 1.000 |  | matched_work_key=277308; match_basis=mp; cosine_similarity=1.0; date_gap_days=0 |
| **top, Rajya Sabha** | | | | | |
| 159538 | RS | SMT. RANJEET RANJAN (2022-28) | 1.000 |  | matched_work_key=159539; match_basis=mp; cosine_similarity=1.0; date_gap_days=0 |
| 159539 | RS | SMT. RANJEET RANJAN (2022-28) | 1.000 |  | matched_work_key=159538; match_basis=mp; cosine_similarity=1.0; date_gap_days=0 |
| 159535 | RS | SMT. RANJEET RANJAN (2022-28) | 1.000 |  | matched_work_key=159536; match_basis=mp; cosine_similarity=1.0; date_gap_days=0 |
| **typical (near median)** | | | | | |
| 185944 | LS | ANURAG SHARMA | 0.462 |  | matched_work_key=185938; match_basis=mp; cosine_similarity=0.4621193800969827; date_gap_days=0 |
| 185938 | LS | ANURAG SHARMA | 0.462 |  | matched_work_key=185944; match_basis=mp; cosine_similarity=0.4621193800969827; date_gap_days=0 |
| 274901 | LS | LAXMIKANT PAPPU NISHAD | 0.462 |  | matched_work_key=274964; match_basis=mp; cosine_similarity=0.4618858575176574; date_gap_days=0 |

### portfolio_concentration

| work_key | house | mp | score | direction | evidence |
| --- | --- | --- | --- | --- | --- |
| **top overall** | | | | | |
| 102045 | RS | SHRI GULAM ALI (2022-28) | 1.000 | above | mp_type_share=0.8275862068965517; peer_mean_share=0.00974025974025974; z=40.8922973578146; activity_type_id=62 |
| 97117 | RS | DR. RADHA MOHAN DAS AGRAWAL (2022-28) | 1.000 | above | mp_type_share=0.3181818181818182; peer_mean_share=0.007423498121545325; z=13.763662099061369; activity_type_id=82 |
| 105595 | RS | SHRI BRIJ LAL (2020-26) | 1.000 | above | mp_type_share=0.7482993197278912; peer_mean_share=0.0; z=37.41496598639456; activity_type_id=76 |
| 106208 | RS | DR. DINESH SHARMA (2023-26) | 1.000 | above | mp_type_share=0.22727272727272727; peer_mean_share=0.005551314073797162; z=10.044046112964145; activity_type_id=45 |
| 199424 | LS | MALVIKA DEVI | 1.000 | above | mp_type_share=0.36065573770491804; peer_mean_share=0.0005850663458662433; z=18.00353356795259; activity_type_id=50 |
| **top, Rajya Sabha** | | | | | |
| 9784 | RS | SHRI SURENDRA SINGH NAGAR (2022-28) | 1.000 | above | mp_type_share=0.47058823529411764; peer_mean_share=0.005147098913231027; z=16.49002488457982; activity_type_id=55 |
| 101991 | RS | SHRI GULAM ALI (2022-28) | 1.000 | above | mp_type_share=0.8275862068965517; peer_mean_share=0.00974025974025974; z=40.8922973578146; activity_type_id=62 |
| 248347 | RS | SHRI KARTIKEYA SHARMA (2022-28) | 1.000 | above | mp_type_share=0.5365853658536586; peer_mean_share=0.002145444413485651; z=26.721996072008647; activity_type_id=82 |
| **typical (near median)** | | | | | |
| 160367 | LS | SAUMITRA KHAN | 1.000 | above | mp_type_share=0.1111111111111111; peer_mean_share=0.014733316857773782; z=3.6038758568636124; activity_type_id=16 |
| 160371 | LS | SAUMITRA KHAN | 1.000 | above | mp_type_share=0.1111111111111111; peer_mean_share=0.014733316857773782; z=3.6038758568636124; activity_type_id=16 |
| 160372 | LS | SAUMITRA KHAN | 1.000 | above | mp_type_share=0.1111111111111111; peer_mean_share=0.014733316857773782; z=3.6038758568636124; activity_type_id=16 |

### district_authority_pattern

| work_key | house | mp | score | direction | evidence |
| --- | --- | --- | --- | --- | --- |
| **top overall** | | | | | |
| 101459 | RS | DR. JOHN BRITTAS (2021-27) | 1.000 | above | driving_component=share; type_share=0.2968036529680365; peer_mean_share=0.002068388211759159; z_share=14.736763237813868; z_amount=-0.645666916412434 |
| 99997 | RS | SHRI NARESH BANSAL (2020-26) | 1.000 | above | driving_component=amount; type_share=0.004807692307692308; peer_mean_share=0.006321555644263883; z_share=-0.07569316682857875; z_amount=15.453076786355926 |
| 99996 | RS | SHRI NARESH BANSAL (2020-26) | 1.000 | above | driving_component=amount; type_share=0.004807692307692308; peer_mean_share=0.006321555644263883; z_share=-0.07569316682857875; z_amount=15.453076786355926 |
| 99995 | RS | SHRI NARESH BANSAL (2020-26) | 1.000 | above | driving_component=amount; type_share=0.004807692307692308; peer_mean_share=0.006321555644263883; z_share=-0.07569316682857875; z_amount=15.453076786355926 |
| 99994 | RS | SHRI NARESH BANSAL (2020-26) | 1.000 | above | driving_component=amount; type_share=0.004807692307692308; peer_mean_share=0.006321555644263883; z_share=-0.07569316682857875; z_amount=15.453076786355926 |
| **top, Rajya Sabha** | | | | | |
| **typical (near median)** | | | | | |
| 166107 | RS | SHRI SHAMBHU SHARAN PATEL (2022-28) | 1.000 | above | driving_component=share; type_share=0.15242494226327943; peer_mean_share=0.01531551820881113; z_share=4.93014293389148; z_amount=-1.1866984140211927 |
| 166097 | RS | SHRI SHAMBHU SHARAN PATEL (2022-28) | 1.000 | above | driving_component=share; type_share=0.15242494226327943; peer_mean_share=0.01531551820881113; z_share=4.93014293389148; z_amount=-1.1866984140211927 |
| 166098 | RS | SHRI SHAMBHU SHARAN PATEL (2022-28) | 1.000 | above | driving_component=share; type_share=0.15242494226327943; peer_mean_share=0.01531551820881113; z_share=4.93014293389148; z_amount=-1.1866984140211927 |

### temporal_anomaly

| work_key | house | mp | score | direction | evidence |
| --- | --- | --- | --- | --- | --- |
| **top overall** | | | | | |
| 10083 | RS | SHRI MUKUL WASNIK (2022-28) | 1.000 | above | entity_type=mp; date_field=sanction; burst_week=2025-01-06/2025-01-12; burst_week_count=37; typical_weekly_count=2.0 |
| 97886 | RS | SMT. SUMITRA BALMIK (2022-28) | 1.000 | above | entity_type=district_authority; date_field=recommended; burst_week=2025-03-17/2025-03-23; burst_week_count=76; typical_weekly_count=1.5 |
| 97887 | RS | SMT. SUMITRA BALMIK (2022-28) | 1.000 | above | entity_type=district_authority; date_field=recommended; burst_week=2025-03-17/2025-03-23; burst_week_count=76; typical_weekly_count=1.5 |
| 97888 | RS | SMT. SUMITRA BALMIK (2022-28) | 1.000 | above | entity_type=district_authority; date_field=recommended; burst_week=2025-03-17/2025-03-23; burst_week_count=76; typical_weekly_count=1.5 |
| 97894 | RS | SMT. SUMITRA BALMIK (2022-28) | 1.000 | above | entity_type=district_authority; date_field=recommended; burst_week=2025-03-17/2025-03-23; burst_week_count=76; typical_weekly_count=1.5 |
| **top, Rajya Sabha** | | | | | |
| **typical (near median)** | | | | | |
| 212092 | LS | SANATAN PANDEY | 1.000 | above | entity_type=mp; date_field=recommended; burst_week=2024-10-21/2024-10-27; burst_week_count=102; typical_weekly_count=6.5 |
| 212091 | LS | SANATAN PANDEY | 1.000 | above | entity_type=mp; date_field=recommended; burst_week=2024-10-21/2024-10-27; burst_week_count=102; typical_weekly_count=6.5 |
| 212090 | LS | SANATAN PANDEY | 1.000 | above | entity_type=mp; date_field=recommended; burst_week=2024-10-21/2024-10-27; burst_week_count=102; typical_weekly_count=6.5 |

### lifecycle_delay

| work_key | house | mp | score | direction | evidence |
| --- | --- | --- | --- | --- | --- |
| **top overall** | | | | | |
| 81788 | RS | SHRI ARUN SINGH (2020-26) | 1.000 | above | driving_component=age_exceedance; age_days=921; age_exceedance=1.0; payment_share=0.0; payment_share_z=-1.5119668801306763 |
| 103009 | RS | SHRI NARESH BANSAL (2020-26) | 1.000 | above | driving_component=age_exceedance; age_days=765; age_exceedance=1.0; payment_share=0.75; payment_share_z=-0.1644238423593654 |
| 98410 | RS | DR. VIKRAMJIT SINGH SAHNEY (2022-28) | 1.000 | above | driving_component=age_exceedance; age_days=705; age_exceedance=1.0; payment_share=0.7610636; payment_share_z=0.6782256283370937 |
| 8694 | RS | SHRI JAVED ALI KHAN (2022-28) | 1.000 | above | driving_component=age_exceedance; age_days=1026; age_exceedance=1.0; payment_share=0.9889384027187765; payment_share_z=0.2303529292396248 |
| 87020 | RS | SHRI KAPIL SIBAL (2022-28) | 1.000 | above | driving_component=age_exceedance; age_days=760; age_exceedance=1.0; payment_share=0.0; payment_share_z=-1.764507252375754 |
| **top, Rajya Sabha** | | | | | |
| 105224 | RS | DR. DINESH SHARMA (2023-26) | 1.000 | above | driving_component=age_exceedance; age_days=899; age_exceedance=1.0; payment_share=0.8463761481935089; payment_share_z=-5.875414270407268 |
| **typical (near median)** | | | | | |
| 208008 | LS | BHAGIRATH CHAUDHARY | 0.833 | above | driving_component=age_exceedance; age_days=373; age_exceedance=0.8333333333333334; payment_share=0.996024; payment_share_z=1.0747598121424957 |
| 233792 | LS | ANDREW J. SYNGKON | 0.833 | above | driving_component=age_exceedance; age_days=187; age_exceedance=0.8333333333333334; payment_share=1.0; payment_share_z=1.2034135708074356 |
| 254859 | LS | SHRI GURJEET SINGH AUJLA | 0.833 | above | driving_component=age_exceedance; age_days=178; age_exceedance=0.8333333333333334; payment_share=0.9883333333333333; payment_share_z=0.46200346433500605 |

## 4. Correctness checks on this run

- **cost_anomaly:** eligible-with-null-score = 0, ineligible-with-a-score = 0, all scores in [0,1] = True
- **near_duplicate:** eligible-with-null-score = 0, ineligible-with-a-score = 0, all scores in [0,1] = True
- **portfolio_concentration:** eligible-with-null-score = 0, ineligible-with-a-score = 0, all scores in [0,1] = True
- **district_authority_pattern:** eligible-with-null-score = 0, ineligible-with-a-score = 0, all scores in [0,1] = True
- **temporal_anomaly:** eligible-with-null-score = 0, ineligible-with-a-score = 0, all scores in [0,1] = True
- **lifecycle_delay:** eligible-with-null-score = 0, ineligible-with-a-score = 0, all scores in [0,1] = True
- No signal's score computation reads `reliability`, `n_usable_excl_self`, `distinct_other_mps`, or any other peer/entity COUNT as a multiplicand -- verified by `tests/test_phase4_signals_unit.py::test_no_reliability_or_peer_count_term_multiplies_any_score` (a source-level check, not just a unit test, per the acceptance criteria).

## 5. House-neutrality

| Signal | LS eligible | RS eligible |
| --- | --- | --- |
| cost_anomaly | 77,226 | 17,833 |
| near_duplicate | 78,366 | 19,138 |
| portfolio_concentration | 77,082 | 18,930 |
| district_authority_pattern | 77,275 | 18,580 |
| temporal_anomaly | 78,366 | 19,138 |
| lifecycle_delay | 43,872 | 9,062 |

`grep -n house app/analytics/signals.py` returns nothing: no signal function reads House anywhere, so filtering signal_result by house at read time can never change a returned work's own score -- proven directly (not just tested) in `tests/test_phase4_signals_context.py::test_house_filter_never_changes_a_signal_score`.
