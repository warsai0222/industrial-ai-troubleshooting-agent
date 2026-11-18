"""Render report.md and summary.md for an investigation from its audit.db log.

Deterministic by design: the causal chain in summary.md is derived by transitively
walking the final conclusion's evidence_steps back through the log, not freely
reconstructed -- same input always produces the same output.

Usage:
    .venv/bin/python scripts/render_investigation_report.py [investigation_id]

If investigation_id is omitted, uses the most recently modified investigations/<id>/.
"""
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INVESTIGATIONS_DIR = PROJECT_ROOT / "investigations"


def resolve_investigation_id(arg: str | None) -> str:
    if arg:
        return arg
    candidates = [d for d in INVESTIGATIONS_DIR.iterdir() if d.is_dir() and (d / "audit.db").exists()]
    if not candidates:
        raise SystemExit(f"No investigations found under {INVESTIGATIONS_DIR}")
    newest = max(candidates, key=lambda d: (d / "audit.db").stat().st_mtime)
    return newest.name


def load(investigation_id: str):
    db_path = INVESTIGATIONS_DIR / investigation_id / "audit.db"
    if not db_path.exists():
        raise SystemExit(f"No audit.db for investigation_id {investigation_id!r} at {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    steps = [dict(r) for r in conn.execute("SELECT * FROM steps ORDER BY step_number")]
    meta_row = conn.execute(
        "SELECT * FROM session_meta WHERE investigation_id = ?", (investigation_id,)
    ).fetchone()
    conn.close()
    meta = dict(meta_row) if meta_row else {}
    for s in steps:
        if s["evidence_steps"]:
            s["evidence_steps"] = json.loads(s["evidence_steps"])
        else:
            s["evidence_steps"] = []
    return meta, {s["step_number"]: s for s in steps}


def confidence_band(confidence: float | None) -> str:
    if confidence is None:
        return "n/a"
    if confidence >= 0.8:
        return "HIGH"
    if confidence >= 0.5:
        return "MEDIUM"
    return "LOW"


def render_step_line(s: dict) -> str:
    ts = s["timestamp"]
    if s["step_kind"] == "tool_call":
        params = json.loads(s["tool_params"]) if s["tool_params"] else {}
        result = json.loads(s["tool_result_summary"]) if s["tool_result_summary"] else {}
        params_str = ", ".join(f"{k}={v!r}" for k, v in params.items() if k != "investigation_id")
        return f"**Step {s['step_number']}** [{ts}] `{s['tool_name']}({params_str})` -> {json.dumps(result)}"
    ev = f", evidence: {s['evidence_steps']}" if s["evidence_steps"] else ""
    conf = f", confidence: {s['confidence']} ({confidence_band(s['confidence'])})" if s["confidence"] is not None else ""
    return f"**Step {s['step_number']}** [{ts}] *{s['reasoning_type']}*: {s['reasoning_text']}{ev}{conf}"


def transitive_evidence(steps: dict, step_number: int, seen: set | None = None) -> set:
    seen = seen if seen is not None else set()
    if step_number in seen or step_number not in steps:
        return seen
    seen.add(step_number)
    for ev in steps[step_number].get("evidence_steps", []):
        transitive_evidence(steps, ev, seen)
    return seen


def has_cause_vs_symptom_ancestor(steps: dict, conclusion_step_number: int) -> bool:
    chain = transitive_evidence(steps, conclusion_step_number)
    return any(steps[n]["reasoning_type"] == "cause_vs_symptom_check" for n in chain if steps[n]["step_kind"] == "reasoning")


def render_report(investigation_id: str, meta: dict, steps: dict) -> str:
    lines = [
        f"# Investigation Audit Report — {meta.get('title', investigation_id)}",
        "",
        f"Investigation ID: {investigation_id}  ",
        f"Started: {meta.get('started_at')}  ",
        f"Ended: {meta.get('ended_at') or '(in progress)'}  ",
        f"Status: {meta.get('final_status') or '(in progress)'}  ",
        f"Initial symptom: {meta.get('initial_symptom')}",
        "",
        "## Step-by-Step Log",
        "",
    ]
    for n in sorted(steps):
        lines.append(render_step_line(steps[n]))
        lines.append("")

    conclusions = [s for s in steps.values() if s["reasoning_type"] == "conclusion"]
    flagged = [s for s in conclusions if not has_cause_vs_symptom_ancestor(steps, s["step_number"])]
    lines.append("## Process Notes (auto-checked)")
    lines.append("")
    if flagged:
        for s in flagged:
            lines.append(f"- ⚠️ Step {s['step_number']} (conclusion) has no `cause_vs_symptom_check` step "
                          f"in its evidence chain. Review before accepting this conclusion.")
    else:
        lines.append("- All conclusion steps have a `cause_vs_symptom_check` in their evidence chain.")
    return "\n".join(lines) + "\n"


def render_summary(investigation_id: str, meta: dict, steps: dict) -> str:
    lines = [f"# Root Cause Summary — {meta.get('title', investigation_id)}", "",
             f"Investigation ID: {investigation_id}  ", f"Date: {meta.get('started_at')}", ""]

    conclusions = [s for s in steps.values() if s["reasoning_type"] == "conclusion"]
    if not conclusions:
        lines += ["## Root Cause", "", "No conclusion was logged for this investigation "
                  f"(status: {meta.get('final_status') or 'in progress'}).", ""]
    else:
        final = max(conclusions, key=lambda s: s["step_number"])
        lines += ["## Root Cause", "", final["reasoning_text"], "",
                  "## Confidence", "",
                  f"{final['confidence']} ({confidence_band(final['confidence'])})", "",
                  "## Causal Chain", ""]
        chain_steps = sorted(transitive_evidence(steps, final["step_number"]) - {final["step_number"]})
        if not chain_steps:
            lines.append("(no evidence_steps were cited on the final conclusion)")
        else:
            for i, n in enumerate(chain_steps, 1):
                s = steps[n]
                if s["step_kind"] == "tool_call":
                    result = json.loads(s["tool_result_summary"]) if s["tool_result_summary"] else {}
                    lines.append(f"{i}. {s['tool_name']} -> {json.dumps(result)}")
                else:
                    lines.append(f"{i}. ({s['reasoning_type']}) {s['reasoning_text']}")
        lines.append("")

    lines += ["## Recommended Corrective Actions", ""]
    actions = sorted((s for s in steps.values() if s["reasoning_type"] == "corrective_action"),
                      key=lambda s: s["step_number"])
    if not actions:
        lines.append("(none logged)")
    else:
        for s in actions:
            lines.append(f"- {s['reasoning_text']}")
    return "\n".join(lines) + "\n"


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    investigation_id = resolve_investigation_id(arg)
    meta, steps = load(investigation_id)

    out_dir = INVESTIGATIONS_DIR / investigation_id
    (out_dir / "report.md").write_text(render_report(investigation_id, meta, steps))
    (out_dir / "summary.md").write_text(render_summary(investigation_id, meta, steps))
    print(f"investigation_id={investigation_id}")
    print(f"wrote {out_dir / 'report.md'}")
    print(f"wrote {out_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
