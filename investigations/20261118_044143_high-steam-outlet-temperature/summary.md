# Root Cause Summary — High steam outlet temperature

Investigation ID: 20261118_044143_high-steam-outlet-temperature  
Date: 2026-11-18T04:41:43+00:00

## Root Cause

The root cause was insufficient primary desuperheater spray water flow (YJJWSLL) failing to attemperate steam enough, allowing TE_8332A to exceed its 548 degC Hi setpoint.

## Confidence

0.6 (MEDIUM)

## Causal Chain

1. historian_find_excursions -> {"n_excursions": 6, "first_excursion": {"start_ts": "2022-03-30 11:25:04", "end_ts": "2022-03-30 11:29:29", "peak_value": 550.49, "peak_ts": "2022-03-30 11:27:39", "duration_sec": 270}}
2. alarm_get_context_window -> {"n_events": 64}
3. (observation) TE_8332A excursion began 2022-03-30 11:25:04, peaked 550.49 degC at 2022-03-30 11:27:39. 64 alarm events in the surrounding 40-minute window.
4. kg_get_upstream_sensors -> {"n_upstream_sensors": 18}
5. historian_find_excursions -> {"n_excursions": 0, "first_excursion": null}
6. (cause_vs_symptom_check) Checked 18 sensors upstream of TE_8332A (incl. YJJWSLL, the primary desuperheater spray flow feedback for this control loop) for excursions preceding the TE_8332A peak -- testing whether TE_8332A is a symptom of an upstream/control deviation rather than itself the root cause.

## Recommended Corrective Actions

- Inspect the primary desuperheater spray nozzle and control valve (TV_8329ZC) for fouling or a sticking actuator per mnt_desuperheater_nozzle.
