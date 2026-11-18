---
name: investigation-report
description: Render the SME audit report and root-cause summary for a plant-troubleshooting investigation from its logged audit trail. Invoke immediately after audit_end_investigation, or whenever the user asks for the investigation report, write-up, or summary.
---

# Investigation Report

Renders two markdown documents from an investigation's audit log
(`investigations/<investigation_id>/audit.db`, written in real time by the
`audit_log_reasoning` tool and automatically by every historian/alarm/kg/docs tool call
that was given an `investigation_id`):

- **`report.md`** — the full chronological step-by-step log (every tool call and every
  reasoning step), for an SME to review.
- **`summary.md`** — root cause, confidence, numbered causal chain, and recommended
  corrective actions.

## How to run it

This is **not** free-form summarization — run the deterministic renderer so the output
is reproducible and the causal chain is a real derivation from the logged
`evidence_steps`, not a freshly-written narrative:

```
.venv/bin/python scripts/render_investigation_report.py [investigation_id]
```

- If you have the `investigation_id` (returned by `audit_start_investigation`, or
  mentioned by the user), pass it.
- If not, omit it — the script defaults to the most recently modified
  `investigations/<id>/` directory.

The script writes `report.md` and `summary.md` into `investigations/<investigation_id>/`
and prints the resolved `investigation_id` and both file paths.

## After running it

1. Read both files.
2. If `report.md`'s "Process Notes" section flags any conclusion for missing a
   `cause_vs_symptom_check` in its evidence chain, surface that warning prominently to
   the user before presenting the root cause as settled — it means the investigation
   skipped the cause-vs-symptom discipline required by this project (see CLAUDE.md).
3. Present `summary.md`'s content to the user directly (root cause, confidence,
   causal chain, corrective actions), and give them the paths to both files so they
   can open `report.md` for the full audit trail.
4. If no `conclusion` step exists yet (investigation still in progress or ended as
   `inconclusive`/`escalated`), say so plainly rather than implying a root cause was
   found.
