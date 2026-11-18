"""Build boiler_kg.json: a labeled-property-graph model of the CFB boiler process.

Canonical, re-runnable version of the knowledge-graph generator (the original was a
one-off script in a session scratchpad). Equipment node ids here are already aligned
with the RAG docs' `equipment:` frontmatter vocabulary where a 1:1 rename made sense
(cyclone_separator_left/right, steam_outlet) -- see mcp_server/equipment_crosswalk.py
for the remaining genuine granularity mismatches (economizer, secondary_air_preheater,
furnace_chamber/furnace_lower_hearth), which can't be fixed by renaming because the KG
is intentionally more granular than the docs there.

Sources (recorded per-node/edge in a `provenance` field):
  - pfd        : directly read off Fig. 3 (process flow diagram) of Hu et al. 2025,
                 Scientific Data, "A long-tailed distribution time-series dataset in
                 boiler equipment" (DOI 10.1038/s41597-025-05096-4)
  - columns_csv: tag name / description as originally given in columns.csv (now
                 absorbed into boiler_historian.duckdb's `tags` table)
  - inferred   : standard CFB boiler engineering practice, not explicitly shown in the
                 PFD or stated in the (paywalled) paper text -- flagged so it can be
                 corrected against the real P&ID / control narrative later.

Usage:
    .venv/bin/python scripts/build_kg.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_server.equipment_crosswalk import DOCS_TO_KG

OUT_PATH = Path(__file__).resolve().parent.parent / "boiler_kg.json"

nodes = {}
edges = []


def node(id_, type_, label, **props):
    if id_ in nodes:
        raise ValueError(f"duplicate node id {id_}")
    nodes[id_] = {"id": id_, "type": type_, "label": label, **props}


def edge(src, dst, relation, **props):
    if src not in nodes:
        raise ValueError(f"edge source {src!r} not defined (relation={relation})")
    if dst not in nodes:
        raise ValueError(f"edge target {dst!r} not defined (relation={relation})")
    edges.append({"source": src, "target": dst, "relation": relation, **props})


# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------
node("coal_bin", "Equipment.Storage", "Coal Bin", provenance="pfd")
node("coal_feeder", "Equipment.Feeder", "Coal Feeder", provenance="pfd")
node("furnace", "Equipment.CombustionChamber", "CFB Furnace", provenance="pfd",
     note="Circulating fluidized bed combustion chamber; coal + return solids burned on a fluidized bed. "
          "RAG docs split this into 'furnace_chamber' and 'furnace_lower_hearth' equipment ids, but "
          "ds_cfb_furnace.md's sensor list has no distinct upper/lower zone assignment to split this "
          "node on -- both docs ids resolve to this one node via equipment_crosswalk.py.")
node("steam_drum", "Equipment.Vessel", "Steam Drum", provenance="inferred",
     note="Not drawn as a distinct vessel in Fig. 3, but required by PTCA_8322A "
          "('steam drum pressure') and by the saturated-steam feed into the low temp "
          "superheater. Placed between the economiser/furnace water circuit and the "
          "low temp superheater. VERIFY against real P&ID.")
node("cyclone_separator_left", "Equipment.Separator", "Left Cyclone Separator", provenance="pfd")
node("cyclone_separator_right", "Equipment.Separator", "Right Cyclone Separator", provenance="pfd")
node("loop_seal_chamber_left", "Equipment.LoopSeal", "Left Loop Seal / Secondary-Wind Chamber",
     provenance="pfd",
     note="Purple chamber under the left cyclone dipleg, aerated by 'secondary wind' per "
          "Fig. 3 label -- a non-mechanical (L-valve/loop-seal) solids return device.")
node("ash_reclaimer_right", "Equipment.AshHandling", "Reclaimer",
     provenance="pfd",
     note="Purple chamber under the right cyclone dipleg, labelled 'Reclaimer' in Fig. 3. "
          "Handles bed-material reclaim / coarse ash removal from the right cyclone leg.")
node("return_air_injection_left", "Equipment.InjectionPoint", "Return Air Injection (left)",
     provenance="pfd", note="'Return Air' point in Fig. 3 where recirculated solids + "
     "fluidizing air re-enter the furnace from the left loop seal leg.")
node("return_air_injection_right", "Equipment.InjectionPoint", "Return Air Injection (right)",
     provenance="inferred", note="Mirrored right-side return-air injection point, inferred to "
     "match FT_8306B ('return air chamber flow, right') -- Fig. 3 only labels one "
     "'Return Air' point explicitly; the left/right split is inferred from the sensor pair.")
node("rewind_fan_interlock_valve", "Valve.CheckValve", "Rewind Fan Interlock (check valve)",
     provenance="pfd", note="Red 'M' + check-valve symbol on the solids-return line in Fig. 3; "
     "prevents backflow into the cyclone diplegs / loop seal.")
node("high_temp_superheater", "Equipment.HeatExchanger.Superheater", "High Temperature Superheater", provenance="pfd")
node("low_temp_superheater", "Equipment.HeatExchanger.Superheater", "Low Temperature Superheater", provenance="pfd")
node("primary_desuperheater", "Equipment.HeatExchanger.Desuperheater", "Primary Desuperheater", provenance="pfd",
     note="First-stage attemperator, between low temp and high temp superheaters.")
node("secondary_desuperheater", "Equipment.HeatExchanger.Desuperheater", "Secondary Desuperheater", provenance="pfd",
     note="Second-stage attemperator, between high temp superheater and the main steam header.")
node("upper_stage_economiser", "Equipment.HeatExchanger.Economiser", "Upper Stage Economiser", provenance="pfd",
     note="RAG docs use one generic 'economizer' equipment id covering both economiser stages -- "
          "resolves to this node + lower_stage_economiser via equipment_crosswalk.py.")
node("lower_stage_economiser", "Equipment.HeatExchanger.Economiser", "Lower Stage Economiser", provenance="pfd")
node("secondary_air_preheater_1", "Equipment.HeatExchanger.AirPreheater", "Secondary Air Preheater (stage 1)",
     provenance="pfd", note="Upper of the two 'Secondary air preheater' boxes in Fig. 3, in the "
     "flue-gas path immediately after the economisers, before the primary air preheater. RAG docs "
     "use one generic 'secondary_air_preheater' id covering both stages -- resolves to this node + "
     "secondary_air_preheater_2 via equipment_crosswalk.py.")
node("primary_air_preheater", "Equipment.HeatExchanger.AirPreheater", "Primary Air Preheater", provenance="pfd")
node("secondary_air_preheater_2", "Equipment.HeatExchanger.AirPreheater", "Secondary Air Preheater (stage 2)",
     provenance="pfd", note="Lower of the two 'Secondary air preheater' boxes in Fig. 3, in the "
     "flue-gas path after the primary air preheater, before the ID fan.")
node("primary_fan", "Equipment.Fan", "Primary Fan", provenance="pfd", note="Forced-draft fan, primary (fluidizing) air.")
node("secondary_fan", "Equipment.Fan", "Secondary Fan", provenance="pfd", note="Forced-draft fan, secondary (overfire) air.")
node("induced_draft_fan", "Equipment.Fan", "Induced Draft Fan (Ventilator)", provenance="pfd",
     note="Labelled 'Ventilator' in Fig. 3; pulls flue gas through the backpass to the "
          "chimney. Mapped to the YFJ3_* tags (motor current, bearing vibration) based on "
          "tag description -- Fig. 3 itself does not name an 'IDF', only 'Ventilator'.")
node("chimney", "Equipment.Stack", "Chimney", provenance="pfd")
node("steamer_bag", "Equipment.Vessel", "Steamer Bag (continuous blowdown flash tank)", provenance="pfd")
node("mains_water_supply", "Boundary.Utility", "Mains Water Supply", provenance="pfd")
node("steam_outlet", "Boundary.Outlet", "Main Steam Header / Boiler Outlet", provenance="pfd",
     note="Named 'steam_outlet' to match the RAG docs' equipment id (originally "
          "'main_steam_header' in this KG; renamed for cross-reference consistency).")

node("primary_desuperheater_spray_valve", "Valve.ControlValve",
     "Primary Desuperheater Spray Control Valve", provenance="columns_csv",
     tag="TV_8329ZC", note="Named 'Primary desuperheater outlet steam temperature "
     "regulating valve' in the historian tags table -- explicitly a control (regulating) valve.")
node("secondary_desuperheater_spray_valves", "Valve.ControlValve",
     "Secondary Desuperheater Spray Valves", provenance="pfd",
     note="Multiple 'M' valves shown feeding the secondary desuperheater in Fig. 3; no "
          "individual flow/position tag exists among the 30 historian tags -- do not "
          "invent one.")
node("continuous_blowdown_valve", "Valve.MotorOperated", "Continuous Blowdown Valve", provenance="pfd",
     note="'M' valve on the Continuous Sewage line between the steam drum and the Steamer Bag.")

# ---------------------------------------------------------------------------
# Control loops
# ---------------------------------------------------------------------------
node("steam_temp_attemperation_primary", "ControlLoop", "Primary Steam-Temperature Attemperation Loop",
     provenance="columns_csv", confidence="explicit",
     note="TV_8329ZC is literally named a 'regulating valve' -- this loop is directly "
          "evidenced by the tag set, not inferred.")
node("furnace_draft_balance", "ControlLoop", "Furnace Draft / Negative-Pressure Balance",
     provenance="inferred", confidence="inferred_standard_practice",
     note="Standard CFB practice: ID fan draw vs. FD (primary+secondary) fan input is "
          "balanced to hold slight furnace negative pressure. No explicit draft-controller "
          "output tag exists among the 30 historian tags -- only the PV side (PT_8313A-F) "
          "is instrumented in this dataset.")
node("combustion_o2_trim", "ControlLoop", "Combustion Excess-Air (O2 Trim) Control",
     provenance="inferred", confidence="inferred_standard_practice",
     note="Standard practice: flue-gas O2 (AIR_8301A/B) trims primary/secondary air flow. "
          "No explicit controller-output tag exists among the 30 historian tags.")
node("bed_inventory_recirculation", "ProcessRelationship", "Bed Inventory / Solids Recirculation",
     provenance="inferred", confidence="inferred_standard_practice",
     note="Loop-seal aeration ('secondary wind') controls solids recirculation rate back "
          "to the furnace; cyclone dP (ZCLCCY/YCLCCY) reflects solids capture, hearth dP "
          "(SXLTCYZ/SXLTCYY) reflects bed density. Related rather than a classic single "
          "PID loop in the available data.")

# ---------------------------------------------------------------------------
# Sensors (all 30 historian tags)
# ---------------------------------------------------------------------------
# normal_min/normal_max and their sources are read live from boiler_historian.duckdb's
# tags table (the authoritative source -- see mcp_server/historian_tools.py's docstring)
# rather than hardcoded here, so the KG can never drift out of sync with it. Ranges are
# derived from real DCS alarm setpoints where available (range_source = "dcs_setpoint");
# for tags with no setpoint on one or both sides, that bound is left null rather than
# guessed (a null bound means "no known DCS threshold on that side", not "unbounded is
# fine") -- see mcp_server/historian_tools.py's historian_list_tags docstring for the
# full explanation, including the "observed_bound_no_alarms" fallback for tags that
# never alarmed in the 5-day window at all.
import duckdb

HISTORIAN_DB_PATH = Path(__file__).resolve().parent.parent / "boiler_historian.duckdb"
with duckdb.connect(str(HISTORIAN_DB_PATH), read_only=True) as _con:
    TAG_ROWS = _con.execute(
        "SELECT tag_name, description, units, normal_min, normal_max, "
        "range_source_min, range_source_max FROM tags ORDER BY tag_name"
    ).fetchall()

INSTRUMENT_TYPE = {
    "PT_8313A": "pressure", "PT_8313B": "pressure", "PT_8313C": "pressure",
    "PT_8313D": "pressure", "PT_8313E": "pressure", "PT_8313F": "pressure",
    "PTCA_8322A": "pressure", "PTCA_8324": "pressure",
    "TE_8319A": "temperature", "TE_8319B": "temperature", "TE_8313B": "temperature",
    "TE_8303": "temperature", "TE_8304": "temperature", "TE_8332A": "temperature",
    "TV_8329ZC": "valve_position",
    "FT_8301": "flow", "FT_8302": "flow", "FT_8306A": "flow", "FT_8306B": "flow",
    "AIR_8301A": "oxygen_analyzer", "AIR_8301B": "oxygen_analyzer",
    "YFJ3_AI": "current", "YFJ3_ZD1": "vibration", "YFJ3_ZD2": "vibration",
    "SXLTCYZ": "differential_pressure", "SXLTCYY": "differential_pressure",
    "ZCLCCY": "differential_pressure", "YCLCCY": "differential_pressure",
    "YJJWSLL": "flow", "ZZQBCHLL": "flow",
}

for tag, desc, units, lo, hi, lo_src, hi_src in TAG_ROWS:
    node(tag, "Sensor", desc, provenance="columns_csv", instrument_type=INSTRUMENT_TYPE[tag],
         units=units, normal_min=lo, normal_max=hi, range_source_min=lo_src, range_source_max=hi_src)

SENSOR_LOCATION = {
    "PT_8313A": ("furnace", "upper furnace, point A"),
    "PT_8313B": ("furnace", "upper furnace, point B"),
    "PT_8313C": ("furnace", "upper furnace, point C"),
    "PT_8313D": ("furnace", "upper furnace, point D"),
    "PT_8313E": ("furnace", "upper furnace, point E"),
    "PT_8313F": ("furnace", "upper furnace, point F"),
    "PTCA_8322A": ("steam_drum", "drum pressure"),
    "PTCA_8324": ("steam_outlet", "boiler outlet"),
    "TE_8319A": ("lower_stage_economiser", "flue gas outlet, left duct"),
    "TE_8319B": ("lower_stage_economiser", "flue gas outlet, right duct"),
    "TE_8313B": ("furnace", "upper furnace, right side"),
    "TE_8303": ("primary_air_preheater", "air outlet"),
    "TE_8304": ("secondary_air_preheater_2", "air outlet (final stage)"),
    "TE_8332A": ("steam_outlet", "boiler outlet - PRIMARY KPI"),
    "TV_8329ZC": ("primary_desuperheater_spray_valve", "valve position"),
    "FT_8301": ("primary_fan", "outlet"),
    "FT_8302": ("secondary_fan", "outlet"),
    "FT_8306A": ("return_air_injection_left", "return air chamber flow"),
    "FT_8306B": ("return_air_injection_right", "return air chamber flow"),
    "AIR_8301A": ("upper_stage_economiser", "flue gas inlet, left duct"),
    "AIR_8301B": ("upper_stage_economiser", "flue gas inlet, right duct"),
    "YFJ3_AI": ("induced_draft_fan", "motor current"),
    "YFJ3_ZD1": ("induced_draft_fan", "bearing vibration, drive end"),
    "YFJ3_ZD2": ("induced_draft_fan", "bearing vibration, non-drive end"),
    "SXLTCYZ": ("furnace", "hearth differential pressure, left"),
    "SXLTCYY": ("furnace", "hearth differential pressure, right"),
    "ZCLCCY": ("cyclone_separator_left", "differential pressure"),
    "YCLCCY": ("cyclone_separator_right", "differential pressure"),
    "YJJWSLL": ("primary_desuperheater", "spray water flow"),
    "ZZQBCHLL": ("steam_outlet", "compensated main steam flow"),
}
for tag, (eq, point) in SENSOR_LOCATION.items():
    edge(eq, tag, "HAS_SENSOR", point=point, provenance="columns_csv+pfd")

# ---------------------------------------------------------------------------
# Process connectivity (CONNECTS_TO edges, grouped by stream)
# ---------------------------------------------------------------------------
def stream_path(stream, path, provenance="pfd", note=None, start_hop=1):
    for i in range(len(path) - 1):
        kwargs = {"stream": stream, "hop": start_hop + i, "provenance": provenance}
        if note:
            kwargs["note"] = note
        edge(path[i], path[i + 1], "CONNECTS_TO", **kwargs)


# Coal
stream_path("Coal", ["coal_bin", "coal_feeder", "furnace"])

# Flue gas (combustion products through the full backpass). furnace -> cyclone -> HT
# superheater is two parallel (left/right) branches that converge; both get hop 1-2,
# the shared downstream run continues the hop count from where they converge.
stream_path("FlueGas", ["furnace", "cyclone_separator_left", "high_temp_superheater"])
stream_path("FlueGas", ["furnace", "cyclone_separator_right", "high_temp_superheater"])
stream_path("FlueGas", [
    "high_temp_superheater", "low_temp_superheater", "upper_stage_economiser",
    "lower_stage_economiser", "secondary_air_preheater_1", "primary_air_preheater",
    "secondary_air_preheater_2", "induced_draft_fan", "chimney",
], start_hop=3)

# Solids recirculation (defines the "circulating" in CFB). The left leg routes through
# the rewind-fan-interlock check valve inline (not a parallel path to return_air_injection_left).
stream_path("Solids", [
    "cyclone_separator_left", "loop_seal_chamber_left", "rewind_fan_interlock_valve",
    "return_air_injection_left", "furnace",
])
stream_path("Solids", ["cyclone_separator_right", "ash_reclaimer_right", "return_air_injection_right", "furnace"],
            provenance="inferred", note="Right-leg mirrored from the left leg; Fig. 3 draws one explicit "
            "'Return Air' point and this topology is inferred to match it.")

# Primary air
stream_path("PrimaryAir", ["primary_fan", "primary_air_preheater", "furnace"],
            note="Fluidizing air to the furnace windbox/grate.")

# Secondary air
stream_path("SecondaryAir", [
    "secondary_fan", "secondary_air_preheater_1", "secondary_air_preheater_2", "furnace",
], note="Overfire / secondary combustion air, preheated across both secondary APH stages.")
edge("secondary_fan", "loop_seal_chamber_left", "CONNECTS_TO", stream="SecondaryAir", hop=1, provenance="pfd",
     note="Fig. 3 labels this tap 'secondary wind' -- aeration/fluidizing air for the loop seal, "
          "not furnace combustion air (parallel branch off the secondary fan, not inline with the "
          "furnace-bound secondary air path).")

# Feedwater -> steam drum -> superheat -> main steam
stream_path("FeedWater", ["mains_water_supply", "lower_stage_economiser", "upper_stage_economiser", "steam_drum"],
            provenance="inferred", note="Fig. 3 labels the economiser return path only as "
            "'to mains water supply'; routed here to the inferred steam drum, which is the "
            "standard destination for economiser outlet in this boiler type. VERIFY.")
stream_path("Steam", [
    "steam_drum", "low_temp_superheater", "primary_desuperheater",
    "high_temp_superheater", "secondary_desuperheater", "steam_outlet",
])

# Desuperheating (attemperation) spray water
edge("mains_water_supply", "primary_desuperheater_spray_valve", "CONNECTS_TO", stream="DesuperheatingWater", provenance="pfd")
edge("primary_desuperheater_spray_valve", "primary_desuperheater", "CONNECTS_TO", stream="DesuperheatingWater", provenance="pfd")
edge("mains_water_supply", "secondary_desuperheater_spray_valves", "CONNECTS_TO", stream="DesuperheatingWater", provenance="pfd")
edge("secondary_desuperheater_spray_valves", "secondary_desuperheater", "CONNECTS_TO", stream="DesuperheatingWater", provenance="pfd")

# Continuous blowdown
edge("steam_drum", "continuous_blowdown_valve", "CONNECTS_TO", stream="ContinuousBlowdown", provenance="pfd")
edge("continuous_blowdown_valve", "steamer_bag", "CONNECTS_TO", stream="ContinuousBlowdown", provenance="pfd")

# ---------------------------------------------------------------------------
# Control-loop relationships
# ---------------------------------------------------------------------------
edge("steam_temp_attemperation_primary", "TE_8332A", "HAS_PV", provenance="columns_csv")
edge("steam_temp_attemperation_primary", "primary_desuperheater_spray_valve", "HAS_MANIPULATED_VARIABLE", provenance="columns_csv")
edge("steam_temp_attemperation_primary", "YJJWSLL", "HAS_FEEDBACK", provenance="columns_csv")
edge("steam_temp_attemperation_primary", "primary_desuperheater", "ACTS_ON", provenance="columns_csv")

for tag in ["PT_8313A", "PT_8313B", "PT_8313C", "PT_8313D", "PT_8313E", "PT_8313F"]:
    edge("furnace_draft_balance", tag, "HAS_PV", provenance="inferred")
for eq in ["induced_draft_fan", "primary_fan", "secondary_fan"]:
    edge("furnace_draft_balance", eq, "HAS_MANIPULATED_VARIABLE", provenance="inferred")

for tag in ["AIR_8301A", "AIR_8301B"]:
    edge("combustion_o2_trim", tag, "HAS_PV", provenance="inferred")
for eq in ["primary_fan", "secondary_fan"]:
    edge("combustion_o2_trim", eq, "HAS_MANIPULATED_VARIABLE", provenance="inferred")
for tag in ["FT_8301", "FT_8302"]:
    edge("combustion_o2_trim", tag, "HAS_FEEDBACK", provenance="inferred")

for tag in ["FT_8306A", "FT_8306B", "SXLTCYZ", "SXLTCYY", "ZCLCCY", "YCLCCY"]:
    edge("bed_inventory_recirculation", tag, "RELATES_TO", provenance="inferred")
edge("bed_inventory_recirculation", "loop_seal_chamber_left", "RELATES_TO", provenance="inferred")
edge("bed_inventory_recirculation", "ash_reclaimer_right", "RELATES_TO", provenance="inferred")

# ---------------------------------------------------------------------------
# Validate: every equipment_crosswalk.py target id must exist in this graph, so a
# future regen can't silently drift out of sync with the RAG docs' equipment vocabulary.
# ---------------------------------------------------------------------------
missing = {kg_id for kg_ids in DOCS_TO_KG.values() for kg_id in kg_ids if kg_id not in nodes}
if missing:
    raise AssertionError(f"equipment_crosswalk.py targets missing from the generated graph: {missing}")

# ---------------------------------------------------------------------------
# Emit node-link JSON (networkx-compatible: directed, multigraph)
# ---------------------------------------------------------------------------
graph = {
    "directed": True,
    "multigraph": True,
    "graph": {
        "name": "boiler_process_knowledge_graph",
        "description": "Process connectivity, instrumentation, and control-loop graph for "
                        "the CFB boiler described in boiler_historian.duckdb.",
        "sources": [
            "Fig. 3 (process flow diagram), Hu, W. et al. 'A long-tailed distribution "
            "time-series dataset in boiler equipment', Scientific Data (2025), "
            "DOI 10.1038/s41597-025-05096-4 -- full text not accessible; PFD figure only.",
            "boiler_historian.duckdb's tags table (tag name -> description)",
        ],
        "node_count": len(nodes),
        "edge_count": len(edges),
    },
    "nodes": list(nodes.values()),
    "links": edges,
}

with open(OUT_PATH, "w") as f:
    json.dump(graph, f, indent=2)

print(f"nodes={len(nodes)} edges={len(edges)}")
print(f"wrote {OUT_PATH}")
