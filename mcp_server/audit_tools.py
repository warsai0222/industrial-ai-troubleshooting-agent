"""Audit-logging tools -- investigations/<investigation_id>/audit.db (SQLite, WAL).

These are the only tools the assistant calls explicitly to log reasoning; every
historian/alarm/kg/docs tool call logs itself automatically via session_store when
given the same investigation_id.
"""
from mcp_server import config, session_store


def register(mcp):
    @mcp.tool()
    def audit_start_investigation(title: str, initial_symptom: str) -> dict:
        """Start a new investigation (creates investigations/<id>/audit.db) and return
        its investigation_id. Call this FIRST, before any other tool, whenever the user
        describes a plant issue to troubleshoot. Pass the returned investigation_id to
        every subsequent historian/alarm/kg/docs tool call (so each is logged as a
        numbered step in real time) and to every audit_log_reasoning call."""
        investigation_id = session_store.start_investigation(title, initial_symptom)
        return {"investigation_id": investigation_id}

    @mcp.tool()
    def audit_log_reasoning(
        investigation_id: str,
        reasoning_type: str,
        text: str,
        evidence_steps: list[int] | None = None,
        confidence: float | None = None,
    ) -> dict:
        """Log one reasoning step in real time, as it happens -- not reconstructed from
        memory afterward. reasoning_type must be one of: hypothesis, observation,
        conclusion, rejection, corrective_action, cause_vs_symptom_check.
        evidence_steps cites the step_number(s) (tool-call or reasoning, from this
        investigation) this step is based on. confidence (0.0-1.0) is REQUIRED on
        'conclusion' steps: >=0.8 HIGH (multiple independent sources agree, the causal
        mechanism is identified, and alternatives were checked and ruled out), 0.5-0.79
        MEDIUM (partly circumstantial, or an alternative not fully excluded), <0.5 LOW.
        Do not log a 'conclusion' without first logging a 'cause_vs_symptom_check' step
        (citing the evidence that the implicated variable is a true cause and not a
        downstream symptom of something else) among its evidence_steps -- the report
        skill flags conclusions that skip this."""
        if reasoning_type not in config.REASONING_TYPES:
            raise ValueError(f"reasoning_type must be one of {config.REASONING_TYPES}")
        if reasoning_type == "conclusion" and confidence is None:
            raise ValueError("confidence is required when reasoning_type='conclusion'")
        step_number = session_store.log_reasoning(investigation_id, reasoning_type, text, evidence_steps, confidence)
        return {"step_number": step_number}

    @mcp.tool()
    def audit_end_investigation(investigation_id: str, final_status: str) -> dict:
        """Close out an investigation. final_status must be one of: root_cause_found,
        inconclusive, escalated. After calling this, invoke the investigation-report
        skill to render report.md and summary.md from the logged steps."""
        if final_status not in config.FINAL_STATUSES:
            raise ValueError(f"final_status must be one of {config.FINAL_STATUSES}")
        total_steps = session_store.end_investigation(investigation_id, final_status)
        return {"investigation_id": investigation_id, "total_steps": total_steps}
