"""Historian tools -- boiler_historian.duckdb (historian_data + tags tables).

86,400 rows at 5-second intervals, 2022-03-27 14:28:54 -> 2022-04-01 14:28:49 (a fixed
5-day STATIC snapshot, not a live feed). 30 sensor tags as columns of historian_data;
tags table has one row per tag with description/units/normal_min/normal_max.
"""
from datetime import datetime

import duckdb

from mcp_server import config, session_store


def _parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"Could not parse timestamp {s!r}; use 'YYYY-MM-DD HH:MM:SS'") from e


def _clip_window(start: str, end: str) -> tuple[str, str, list[str]]:
    win_start = _parse_dt(config.DATA_WINDOW_START)
    win_end = _parse_dt(config.DATA_WINDOW_END)
    s, e = _parse_dt(start), _parse_dt(end)
    warnings = []
    if s < win_start:
        warnings.append(f"start {start!r} is before the data window start "
                         f"({config.DATA_WINDOW_START}); clipped to it")
        s = win_start
    if e > win_end:
        warnings.append(f"end {end!r} is after the data window end "
                         f"({config.DATA_WINDOW_END}); clipped to it")
        e = win_end
    if s > e:
        raise ValueError(f"start ({s}) is after end ({e})")
    return s.strftime("%Y-%m-%d %H:%M:%S"), e.strftime("%Y-%m-%d %H:%M:%S"), warnings


def _valid_tag_columns(con) -> set[str]:
    rows = con.execute("DESCRIBE historian_data").fetchall()
    return {r[0] for r in rows if r[0] != "timestamp"}


def _check_tags(con, tags: list[str]) -> None:
    valid = _valid_tag_columns(con)
    unknown = [t for t in tags if t not in valid]
    if unknown:
        raise ValueError(f"Unknown historian tag(s): {unknown}. Use historian_list_tags to see valid tags.")


def register(mcp):
    @mcp.tool()
    def historian_list_tags(name_filter: str | None = None, investigation_id: str | None = None) -> list[dict]:
        """List the 30 historian sensor tags with description, units, and normal operating
        range. normal_min/normal_max come from the real DCS alarm setpoints in the alarm
        log where available (range_source_min/max = "dcs_setpoint"); for the ~1/3 of tags
        with no setpoint on one or both sides, that bound is null rather than guessed --
        check range_source_min/max ("dcs_setpoint" vs "observed_bound_no_alarms", the
        latter meaning the tag never alarmed in this 5-day window so its full observed
        range is used). A null bound means there is no known DCS threshold on that side,
        not that any value is acceptable. Optionally filter by a case-insensitive
        substring match against the tag name or description. Always confirm a tag name
        here before assuming it exists -- never invent a tag name."""
        with duckdb.connect(str(config.HISTORIAN_DB_PATH), read_only=True) as con:
            query = ("SELECT tag_name, description, source_description, units, "
                      "normal_min, normal_max, range_source_min, range_source_max FROM tags")
            params = []
            if name_filter:
                query += " WHERE tag_name ILIKE ? OR description ILIKE ?"
                like = f"%{name_filter}%"
                params = [like, like]
            query += " ORDER BY tag_name"
            cur = con.execute(query, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        result = [dict(zip(cols, r)) for r in rows]
        if investigation_id:
            session_store.log_tool_step(investigation_id, "historian_list_tags",
                                         {"name_filter": name_filter}, {"n_tags": len(result)})
        return result

    @mcp.tool()
    def historian_get_window(investigation_id: str | None = None) -> dict:
        """Return the historian's fixed data window (start/end timestamp, sample interval,
        row count). This is a STATIC 5-day snapshot, not a live feed -- there is no "now".
        Call this before resolving any relative time phrase ("the last 3 hours", "today")
        since there is no live clock to resolve it against; anchor relative requests to
        this window's end instead."""
        with duckdb.connect(str(config.HISTORIAN_DB_PATH), read_only=True) as con:
            row = con.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM historian_data").fetchone()
        result = {"start": str(row[0]), "end": str(row[1]), "interval_sec": 5, "row_count": row[2]}
        if investigation_id:
            session_store.log_tool_step(investigation_id, "historian_get_window", {}, result)
        return result

    @mcp.tool()
    def historian_get_series(
        tags: list[str],
        start: str,
        end: str,
        downsample_sec: int = 0,
        agg: str = "avg",
        investigation_id: str | None = None,
    ) -> dict:
        """Retrieve historian time-series data for one or more tags over [start, end]
        (timestamps as 'YYYY-MM-DD HH:MM:SS'). Bounds outside the fixed 5-day data window
        are clipped with a warning, not silently emptied -- check the `warnings` field.
        If downsample_sec=0 and the raw row count would exceed ~2000 points/tag, the
        server auto-picks a coarser bucket itself and sets downsampled=true in the
        response rather than returning unbounded raw data -- check `downsampled` and
        `effective_downsample_sec` rather than assuming you got raw 5-second data.
        agg (avg/min/max/first) controls how points are aggregated into each bucket when
        downsampling is in effect."""
        if agg not in ("avg", "min", "max", "first"):
            raise ValueError("agg must be one of: avg, min, max, first")
        start, end, warnings = _clip_window(start, end)

        with duckdb.connect(str(config.HISTORIAN_DB_PATH), read_only=True) as con:
            _check_tags(con, tags)

            span_sec = (_parse_dt(end) - _parse_dt(start)).total_seconds()
            effective_downsample = downsample_sec
            downsampled_flag = downsample_sec > 0
            if downsample_sec == 0:
                raw_points = span_sec / 5 + 1
                if raw_points > config.MAX_SERIES_POINTS_PER_TAG:
                    effective_downsample = (int(span_sec / config.MAX_SERIES_POINTS_PER_TAG / 5) + 1) * 5
                    downsampled_flag = True

            series = {}
            if downsampled_flag:
                agg_fn = {"avg": "AVG", "min": "MIN", "max": "MAX", "first": "FIRST"}[agg]
                for tag in tags:
                    # NOTE: "/" between two integers in DuckDB is true (float) division,
                    # not floor division like Python -- must use "//" here or every row
                    # gets its own near-unique bucket and no downsampling actually happens.
                    # NOTE: to_timestamp() returns TIMESTAMP WITH TIME ZONE (shifted to the
                    # session's local zone), which would silently disagree with every other
                    # (naive) timestamp this server returns -- build the bucket as a plain
                    # TIMESTAMP via interval arithmetic instead.
                    rows = con.execute(
                        f"SELECT TIMESTAMP '1970-01-01' + to_seconds((epoch(timestamp)::BIGINT // ?) * ?) "
                        f'AS bucket, {agg_fn}("{tag}") AS value FROM historian_data '
                        f'WHERE timestamp BETWEEN ? AND ? GROUP BY bucket ORDER BY bucket',
                        [effective_downsample, effective_downsample, start, end],
                    ).fetchall()
                    series[tag] = [{"ts": str(r[0]), "value": r[1]} for r in rows]
            else:
                for tag in tags:
                    rows = con.execute(
                        f'SELECT timestamp, "{tag}" FROM historian_data '
                        f'WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp',
                        [start, end],
                    ).fetchall()
                    series[tag] = [{"ts": str(r[0]), "value": r[1]} for r in rows]

        result = {
            "series": series,
            "downsampled": downsampled_flag,
            "effective_downsample_sec": effective_downsample if downsampled_flag else 0,
            "warnings": warnings,
        }
        if investigation_id:
            summary = {"tags": tags, "start": start, "end": end,
                       "n_points_per_tag": {t: len(v) for t, v in series.items()},
                       "downsampled": downsampled_flag}
            session_store.log_tool_step(investigation_id, "historian_get_series",
                                         {"tags": tags, "start": start, "end": end,
                                          "downsample_sec": downsample_sec, "agg": agg}, summary)
        return result

    @mcp.tool()
    def historian_get_stats(tags: list[str], start: str, end: str, investigation_id: str | None = None) -> dict:
        """Min/max/avg/stddev and % of time outside the tag's normal operating range, per
        tag, over [start, end]. Use this to confirm and size an anomaly before drilling
        further (e.g. "how far out of range, and for how much of the window")."""
        start, end, warnings = _clip_window(start, end)
        with duckdb.connect(str(config.HISTORIAN_DB_PATH), read_only=True) as con:
            _check_tags(con, tags)
            range_rows = con.execute(
                "SELECT tag_name, normal_min, normal_max, range_source_min, range_source_max FROM tags"
            ).fetchall()
            ranges = {r[0]: r[1:] for r in range_rows}

            stats = {}
            for tag in tags:
                lo, hi, lo_src, hi_src = ranges.get(tag, (None, None, None, None))
                mn, mx, avg, sd, n = con.execute(
                    f'SELECT MIN("{tag}"), MAX("{tag}"), AVG("{tag}"), STDDEV("{tag}"), COUNT(*) '
                    f'FROM historian_data WHERE timestamp BETWEEN ? AND ?',
                    [start, end],
                ).fetchone()
                pct_out = None
                if (lo is not None or hi is not None) and n:
                    conditions, cond_params = [], []
                    if lo is not None:
                        conditions.append(f'"{tag}" < ?'); cond_params.append(lo)
                    if hi is not None:
                        conditions.append(f'"{tag}" > ?'); cond_params.append(hi)
                    (out_n,) = con.execute(
                        f'SELECT COUNT(*) FROM historian_data WHERE timestamp BETWEEN ? AND ? '
                        f'AND ({" OR ".join(conditions)})',
                        [start, end, *cond_params],
                    ).fetchone()
                    pct_out = round(100.0 * out_n / n, 2)
                stats[tag] = {
                    "min": mn, "max": mx, "avg": avg, "stddev": sd, "n_samples": n,
                    "normal_min": lo, "normal_max": hi,
                    "range_source_min": lo_src, "range_source_max": hi_src,
                    "pct_time_out_of_normal_range": pct_out,
                }
        result = {"stats": stats, "start": start, "end": end, "warnings": warnings}
        if investigation_id:
            summary = {t: {"avg": s["avg"], "pct_out": s["pct_time_out_of_normal_range"]} for t, s in stats.items()}
            session_store.log_tool_step(investigation_id, "historian_get_stats",
                                         {"tags": tags, "start": start, "end": end}, summary)
        return result

    @mcp.tool()
    def historian_find_excursions(
        tag: str, start: str, end: str, direction: str = "either", investigation_id: str | None = None,
    ) -> dict:
        """Find contiguous runs where `tag` was outside its normal operating range within
        [start, end]. direction: 'above', 'below', or 'either' (default). Returns each
        run's start/end timestamp, duration, and peak value/time. This is the core tool
        for pinning down when an abnormal condition actually began -- compare `start_ts`
        across tags (e.g. via kg_get_upstream_sensors) to test whether one tag's excursion
        precedes another's, which is evidence of symptom vs. cause, not just correlation."""
        if direction not in ("above", "below", "either"):
            raise ValueError("direction must be one of: above, below, either")
        start, end, warnings = _clip_window(start, end)
        with duckdb.connect(str(config.HISTORIAN_DB_PATH), read_only=True) as con:
            _check_tags(con, [tag])
            lo, hi = con.execute("SELECT normal_min, normal_max FROM tags WHERE tag_name = ?", [tag]).fetchone()
            rows = con.execute(
                f'SELECT timestamp, "{tag}" FROM historian_data '
                f'WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp',
                [start, end],
            ).fetchall()

        def is_out(v):
            if v is None:
                return False
            if direction in ("above", "either") and hi is not None and v > hi:
                return True
            if direction in ("below", "either") and lo is not None and v < lo:
                return True
            return False

        excursions = []
        run = None
        for ts, val in rows:
            if is_out(val):
                if run is None:
                    run = {"start_ts": ts, "end_ts": ts, "peak_value": val, "peak_ts": ts}
                else:
                    run["end_ts"] = ts
                    is_new_peak = val < run["peak_value"] if direction == "below" else val > run["peak_value"]
                    if is_new_peak:
                        run["peak_value"] = val
                        run["peak_ts"] = ts
            else:
                if run is not None:
                    excursions.append(run)
                    run = None
        if run is not None:
            excursions.append(run)

        for ex in excursions:
            ex["duration_sec"] = int((ex["end_ts"] - ex["start_ts"]).total_seconds()) + 5
            ex["start_ts"] = str(ex["start_ts"])
            ex["end_ts"] = str(ex["end_ts"])
            ex["peak_ts"] = str(ex["peak_ts"])

        result = {"tag": tag, "normal_min": lo, "normal_max": hi, "direction": direction,
                   "excursions": excursions, "warnings": warnings}
        if investigation_id:
            summary = {"n_excursions": len(excursions),
                       "first_excursion": excursions[0] if excursions else None}
            session_store.log_tool_step(investigation_id, "historian_find_excursions",
                                         {"tag": tag, "start": start, "end": end, "direction": direction},
                                         summary)
        return result
