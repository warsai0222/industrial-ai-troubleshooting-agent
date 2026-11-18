"""Alarm tools -- boiler_alarms.duckdb (single table: alarm_events, 6005 rows).

Schema: alarm_id, tag_name, alarm_level (Lo/LoLo/Hi/HiHi/HiHiHi/Alert/Alarm/Trip),
priority (Critical/High/Medium/Low), message, setpoint, activated_at, activated_value,
acknowledged_at (nullable), cleared_at (nullable -- NULL means still active at the end
of the 5-day window), duration_sec (nullable), max_deviation. Only 22 of the 30
historian tags ever alarm in this window; a tag never appearing here is expected, not
an error. There is no separate setpoint-register table -- `setpoint` is per-row.
"""
from collections import Counter
from datetime import datetime, timedelta

import duckdb

from mcp_server import config, session_store


def _jsonify(v):
    return str(v) if isinstance(v, datetime) else v


def _rows_to_dicts(cols, rows):
    return [{c: _jsonify(v) for c, v in zip(cols, r)} for r in rows]


def register(mcp):
    @mcp.tool()
    def alarm_query(
        tag_name: str | None = None,
        alarm_level: str | None = None,
        priority: str | None = None,
        start: str | None = None,
        end: str | None = None,
        active_only: bool = False,
        limit: int = 200,
        investigation_id: str | None = None,
    ) -> list[dict]:
        """Query alarm events with optional filters: tag_name, alarm_level (one of Lo,
        LoLo, Hi, HiHi, HiHiHi, Alert, Alarm, Trip), priority (Critical, High, Medium,
        Low), and an activated_at range [start, end]. active_only=True restricts to
        alarms with cleared_at IS NULL (still active at the end of the 5-day window --
        only 21 of 6005 rows qualify). Results ordered newest-activated first, capped
        at `limit`."""
        clauses, params = [], []
        if tag_name:
            clauses.append("tag_name = ?"); params.append(tag_name)
        if alarm_level:
            clauses.append("alarm_level = ?"); params.append(alarm_level)
        if priority:
            clauses.append("priority = ?"); params.append(priority)
        if start:
            clauses.append("activated_at >= ?"); params.append(start)
        if end:
            clauses.append("activated_at <= ?"); params.append(end)
        if active_only:
            clauses.append("cleared_at IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with duckdb.connect(str(config.ALARMS_DB_PATH), read_only=True) as con:
            cur = con.execute(f"SELECT * FROM alarm_events {where} ORDER BY activated_at DESC LIMIT ?", params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        result = _rows_to_dicts(cols, rows)
        if investigation_id:
            session_store.log_tool_step(investigation_id, "alarm_query",
                                         {"tag_name": tag_name, "alarm_level": alarm_level,
                                          "priority": priority, "start": start, "end": end,
                                          "active_only": active_only}, {"n_results": len(result)})
        return result

    @mcp.tool()
    def alarm_get_stats(start: str, end: str, group_by: str = "tag_name", investigation_id: str | None = None) -> dict:
        """Alarm counts and duration stats within [start, end] (by activated_at), grouped
        by tag_name, alarm_level, or priority. Use this for shift-level summaries or to
        see which tag/level/priority dominates a window before drilling into individual
        events."""
        if group_by not in ("tag_name", "alarm_level", "priority"):
            raise ValueError("group_by must be one of: tag_name, alarm_level, priority")
        with duckdb.connect(str(config.ALARMS_DB_PATH), read_only=True) as con:
            rows = con.execute(
                f"SELECT {group_by}, COUNT(*), SUM(duration_sec), AVG(duration_sec) "
                f"FROM alarm_events WHERE activated_at BETWEEN ? AND ? "
                f"GROUP BY {group_by} ORDER BY COUNT(*) DESC",
                [start, end],
            ).fetchall()
        stats = [{"group": r[0], "count": r[1], "total_duration_sec": r[2], "avg_duration_sec": r[3]} for r in rows]
        result = {"group_by": group_by, "start": start, "end": end, "stats": stats}
        if investigation_id:
            session_store.log_tool_step(investigation_id, "alarm_get_stats",
                                         {"start": start, "end": end, "group_by": group_by},
                                         {"n_groups": len(stats)})
        return result

    @mcp.tool()
    def alarm_get_active_at(timestamp: str, investigation_id: str | None = None) -> list[dict]:
        """All alarms active at a specific moment: activated_at <= timestamp AND
        (cleared_at IS NULL OR cleared_at > timestamp)."""
        with duckdb.connect(str(config.ALARMS_DB_PATH), read_only=True) as con:
            cur = con.execute(
                "SELECT * FROM alarm_events WHERE activated_at <= ? "
                "AND (cleared_at IS NULL OR cleared_at > ?) ORDER BY activated_at",
                [timestamp, timestamp],
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        result = _rows_to_dicts(cols, rows)
        if investigation_id:
            session_store.log_tool_step(investigation_id, "alarm_get_active_at",
                                         {"timestamp": timestamp}, {"n_active": len(result)})
        return result

    @mcp.tool()
    def alarm_get_context_window(
        timestamp: str, before_min: int = 30, after_min: int = 30, investigation_id: str | None = None,
    ) -> dict:
        """All alarms with activated_at within [timestamp - before_min, timestamp +
        after_min], ordered by activated_at, each annotated with minutes_offset from
        `timestamp`. This is the primary "what else was happening around this moment"
        tool -- the earliest-activating alarm in the window is often the better lead
        than the one that prompted the investigation."""
        ts = datetime.fromisoformat(timestamp)
        win_start = (ts - timedelta(minutes=before_min)).strftime("%Y-%m-%d %H:%M:%S")
        win_end = (ts + timedelta(minutes=after_min)).strftime("%Y-%m-%d %H:%M:%S")
        with duckdb.connect(str(config.ALARMS_DB_PATH), read_only=True) as con:
            cur = con.execute(
                "SELECT * FROM alarm_events WHERE activated_at BETWEEN ? AND ? ORDER BY activated_at",
                [win_start, win_end],
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        events = _rows_to_dicts(cols, rows)
        for e in events:
            e_ts = datetime.fromisoformat(e["activated_at"])
            e["minutes_offset"] = round((e_ts - ts).total_seconds() / 60, 2)
        result = {"timestamp": timestamp, "window_start": win_start, "window_end": win_end, "events": events}
        if investigation_id:
            session_store.log_tool_step(investigation_id, "alarm_get_context_window",
                                         {"timestamp": timestamp, "before_min": before_min, "after_min": after_min},
                                         {"n_events": len(events)})
        return result

    @mcp.tool()
    def alarm_detect_floods(
        start: str, end: str, window_sec: int = 600, min_count: int = 10, investigation_id: str | None = None,
    ) -> dict:
        """Detect ISA-18.2-style alarm floods: periods where at least `min_count` alarms
        activated within any `window_sec`-second sliding window. CFB upsets commonly
        cascade into floods that obscure the true first-out alarm -- within a detected
        flood, treat the EARLIEST activation as the primary lead and every later one as a
        candidate symptom requiring its own cause-vs-symptom check, not as independently
        causal just because it has a higher priority."""
        with duckdb.connect(str(config.ALARMS_DB_PATH), read_only=True) as con:
            rows = con.execute(
                "SELECT activated_at, tag_name FROM alarm_events WHERE activated_at BETWEEN ? AND ? ORDER BY activated_at",
                [start, end],
            ).fetchall()
        times = [r[0] for r in rows]
        tags = [r[1] for r in rows]
        n = len(times)
        flood_idx = set()
        j = 0
        for i in range(n):
            while (times[i] - times[j]).total_seconds() > window_sec:
                j += 1
            if i - j + 1 >= min_count:
                flood_idx.update(range(j, i + 1))

        floods = []
        idxs = sorted(flood_idx)
        if idxs:
            run_start = prev = idxs[0]
            for k in idxs[1:]:
                if k == prev + 1:
                    prev = k
                else:
                    floods.append((run_start, prev))
                    run_start = prev = k
            floods.append((run_start, prev))

        result_floods = []
        for s_idx, e_idx in floods:
            top = Counter(tags[s_idx:e_idx + 1]).most_common(3)
            result_floods.append({
                "window_start": str(times[s_idx]),
                "window_end": str(times[e_idx]),
                "alarm_count": e_idx - s_idx + 1,
                "earliest_tag": tags[s_idx],
                "top_tags": [{"tag_name": t, "count": c} for t, c in top],
            })
        result = {"start": start, "end": end, "window_sec": window_sec, "min_count": min_count, "floods": result_floods}
        if investigation_id:
            session_store.log_tool_step(investigation_id, "alarm_detect_floods",
                                         {"start": start, "end": end, "window_sec": window_sec, "min_count": min_count},
                                         {"n_floods": len(result_floods)})
        return result
