"""Knowledge-graph tools -- boiler_kg.json, lazily loaded into a networkx.MultiDiGraph.

64 nodes (equipment, valves, 30 sensors, 3 control loops, 1 process relationship,
2 boundary nodes), 98 edges (CONNECTS_TO for physical/process flow with `stream` +
`hop`; HAS_SENSOR; HAS_PV/HAS_MANIPULATED_VARIABLE/HAS_FEEDBACK/ACTS_ON for control
loops; RELATES_TO for the one ProcessRelationship node). See boiler_kg.md for the full
schema and Mermaid diagram.

Every tool that takes an `equipment_id` resolves it through
mcp_server.equipment_crosswalk.resolve_equipment_id first, so callers can pass either
the RAG docs' equipment vocabulary or a raw KG node id.
"""
import json

import networkx as nx

from mcp_server import config, session_store
from mcp_server.equipment_crosswalk import KG_TO_DOCS, resolve_equipment_id

_graph: nx.MultiDiGraph | None = None


def _load_graph() -> nx.MultiDiGraph:
    global _graph
    if _graph is None:
        with open(config.KG_PATH) as f:
            data = json.load(f)
        _graph = nx.node_link_graph(data, edges="links")
    return _graph


def _node_dict(g: nx.MultiDiGraph, node_id: str) -> dict:
    return {"id": node_id, **g.nodes[node_id]}


def _parent_equipment(g: nx.MultiDiGraph, tag_name: str) -> str | None:
    preds = [u for u, _, d in g.in_edges(tag_name, data=True) if d.get("relation") == "HAS_SENSOR"]
    return preds[0] if preds else None


def _sensors_of(g: nx.MultiDiGraph, equipment_id: str) -> list[dict]:
    out = []
    for _, tag, d in g.out_edges(equipment_id, data=True):
        if d.get("relation") != "HAS_SENSOR":
            continue
        attrs = g.nodes[tag]
        out.append({"tag_name": tag, "point": d.get("point"), "instrument_type": attrs.get("instrument_type"),
                     "units": attrs.get("units"), "normal_min": attrs.get("normal_min"),
                     "normal_max": attrs.get("normal_max")})
    return out


def _bfs(g: nx.MultiDiGraph, start_ids: list[str], stream: str | None, max_hops: int, direction: str) -> list[dict]:
    """direction: 'upstream' walks in_edges, 'downstream' walks out_edges, both only
    along CONNECTS_TO edges."""
    visited = set(start_ids)
    frontier = list(start_ids)
    results = []
    hop = 0
    edge_iter = g.in_edges if direction == "upstream" else g.out_edges
    while frontier and hop < max_hops:
        hop += 1
        next_frontier = []
        for node in frontier:
            for u, v, d in edge_iter(node, data=True):
                if d.get("relation") != "CONNECTS_TO":
                    continue
                if stream and d.get("stream") != stream:
                    continue
                neighbor = u if direction == "upstream" else v
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                results.append({"id": neighbor, "hop": hop, "stream": d.get("stream")})
                next_frontier.append(neighbor)
        frontier = next_frontier
    return results


def register(mcp):
    @mcp.tool()
    def kg_query_equipment(equipment_id: str, investigation_id: str | None = None) -> dict:
        """Look up one or more KG equipment nodes by id. Accepts either the RAG docs'
        equipment vocabulary (e.g. 'economizer', 'cyclone_separator_left') or a raw KG
        node id (e.g. 'coal_bin') -- resolves through equipment_crosswalk.py. Some docs
        ids map to more than one KG node (e.g. 'economizer' -> both economiser stages);
        check `resolved_ids` to see how many nodes were returned."""
        g = _load_graph()
        resolved = resolve_equipment_id(equipment_id, known_kg_ids=set(g.nodes))
        nodes = [_node_dict(g, rid) for rid in resolved]
        result = {"input": equipment_id, "resolved_ids": resolved, "nodes": nodes}
        if investigation_id:
            session_store.log_tool_step(investigation_id, "kg_query_equipment",
                                         {"equipment_id": equipment_id}, {"resolved_ids": resolved})
        return result

    @mcp.tool()
    def kg_list_equipment(type_filter: str | None = None, investigation_id: str | None = None) -> list[dict]:
        """List all equipment/boundary nodes (id, type, label), each annotated with its
        RAG-docs-vocabulary alias(es) if any exist (via equipment_crosswalk.py).
        type_filter does a case-insensitive substring match against the node `type`
        (e.g. 'HeatExchanger', 'Fan')."""
        g = _load_graph()
        out = []
        for nid, attrs in g.nodes(data=True):
            ntype = attrs.get("type", "")
            if not (ntype.startswith("Equipment") or ntype.startswith("Boundary") or ntype.startswith("Valve")):
                continue
            if type_filter and type_filter.lower() not in ntype.lower():
                continue
            out.append({"id": nid, "type": ntype, "label": attrs.get("label"),
                         "docs_equipment_ids": KG_TO_DOCS.get(nid, [])})
        if investigation_id:
            session_store.log_tool_step(investigation_id, "kg_list_equipment",
                                         {"type_filter": type_filter}, {"n_results": len(out)})
        return out

    @mcp.tool()
    def kg_get_sensors_for_equipment(equipment_id: str, investigation_id: str | None = None) -> dict:
        """Sensors measuring a piece of equipment (via HAS_SENSOR edges). equipment_id
        accepts either vocabulary; results are grouped per resolved KG node since some
        docs-vocabulary ids resolve to more than one."""
        g = _load_graph()
        resolved = resolve_equipment_id(equipment_id, known_kg_ids=set(g.nodes))
        by_node = {rid: _sensors_of(g, rid) for rid in resolved}
        result = {"input": equipment_id, "resolved_ids": resolved, "sensors_by_node": by_node}
        if investigation_id:
            n_sensors = sum(len(v) for v in by_node.values())
            session_store.log_tool_step(investigation_id, "kg_get_sensors_for_equipment",
                                         {"equipment_id": equipment_id}, {"n_sensors": n_sensors})
        return result

    @mcp.tool()
    def kg_get_upstream_equipment(
        equipment_id: str, stream: str | None = None, max_hops: int = 5, investigation_id: str | None = None,
    ) -> list[dict]:
        """Walk upstream from equipment_id along CONNECTS_TO edges (physical/process
        flow), optionally restricted to one `stream`, up to max_hops. Returns nodes
        (equipment and any valves on the path) nearest-to-farthest, each with its hop
        distance and the stream of the edge that reached it."""
        g = _load_graph()
        starts = resolve_equipment_id(equipment_id, known_kg_ids=set(g.nodes))
        hits = _bfs(g, starts, stream, max_hops, "upstream")
        out = [{**h, "label": g.nodes[h["id"]].get("label"), "type": g.nodes[h["id"]].get("type")} for h in hits]
        if investigation_id:
            session_store.log_tool_step(investigation_id, "kg_get_upstream_equipment",
                                         {"equipment_id": equipment_id, "stream": stream, "max_hops": max_hops},
                                         {"n_results": len(out)})
        return out

    @mcp.tool()
    def kg_get_downstream_equipment(
        equipment_id: str, stream: str | None = None, max_hops: int = 5, investigation_id: str | None = None,
    ) -> list[dict]:
        """Same as kg_get_upstream_equipment but walking forward (downstream) along
        CONNECTS_TO edges."""
        g = _load_graph()
        starts = resolve_equipment_id(equipment_id, known_kg_ids=set(g.nodes))
        hits = _bfs(g, starts, stream, max_hops, "downstream")
        out = [{**h, "label": g.nodes[h["id"]].get("label"), "type": g.nodes[h["id"]].get("type")} for h in hits]
        if investigation_id:
            session_store.log_tool_step(investigation_id, "kg_get_downstream_equipment",
                                         {"equipment_id": equipment_id, "stream": stream, "max_hops": max_hops},
                                         {"n_results": len(out)})
        return out

    @mcp.tool()
    def kg_trace_stream(stream: str, investigation_id: str | None = None) -> dict:
        """Full ordered equipment path for one process stream end-to-end, with sensors
        attached at each hop. Valid streams: Coal, FlueGas, Solids, PrimaryAir,
        SecondaryAir, FeedWater, Steam, DesuperheatingWater, ContinuousBlowdown."""
        g = _load_graph()
        stream_edges = [(u, v, d) for u, v, d in g.edges(data=True)
                         if d.get("relation") == "CONNECTS_TO" and d.get("stream") == stream]
        if not stream_edges:
            raise ValueError(f"No CONNECTS_TO edges found for stream {stream!r}. Valid streams: "
                              "Coal, FlueGas, Solids, PrimaryAir, SecondaryAir, FeedWater, Steam, "
                              "DesuperheatingWater, ContinuousBlowdown.")
        stream_edges.sort(key=lambda e: e[2].get("hop", 0))

        seen = set()
        ordered_nodes = []
        for u, v, d in stream_edges:
            if u not in seen:
                seen.add(u); ordered_nodes.append(u)
            if v not in seen:
                seen.add(v); ordered_nodes.append(v)

        path = [{"id": nid, "label": g.nodes[nid].get("label"), "type": g.nodes[nid].get("type"),
                 "sensors": _sensors_of(g, nid)} for nid in ordered_nodes]
        edges_out = [{"source": u, "target": v, "hop": d.get("hop"), "note": d.get("note")} for u, v, d in stream_edges]
        result = {"stream": stream, "path": path, "edges": edges_out}
        if investigation_id:
            session_store.log_tool_step(investigation_id, "kg_trace_stream", {"stream": stream},
                                         {"n_nodes": len(path)})
        return result

    @mcp.tool()
    def kg_find_path(from_equipment_id: str, to_equipment_id: str, investigation_id: str | None = None) -> dict:
        """Shortest physical path (CONNECTS_TO edges only) between two equipment nodes.
        Both params accept either equipment vocabulary. Returns an empty `path` if no
        CONNECTS_TO path exists between them (not an error)."""
        g = _load_graph()
        known = set(g.nodes)
        from_ids = resolve_equipment_id(from_equipment_id, known_kg_ids=known)
        to_ids = resolve_equipment_id(to_equipment_id, known_kg_ids=known)

        h = nx.DiGraph()
        for u, v, d in g.edges(data=True):
            if d.get("relation") == "CONNECTS_TO":
                h.add_edge(u, v, stream=d.get("stream"), hop=d.get("hop"))

        best = None
        for f in from_ids:
            for t in to_ids:
                try:
                    p = nx.shortest_path(h, f, t)
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                if best is None or len(p) < len(best):
                    best = p
        path_out = [{"id": nid, "label": g.nodes[nid].get("label")} for nid in (best or [])]
        result = {"from": from_equipment_id, "to": to_equipment_id, "path": path_out,
                   "connected": best is not None}
        if investigation_id:
            session_store.log_tool_step(investigation_id, "kg_find_path",
                                         {"from_equipment_id": from_equipment_id, "to_equipment_id": to_equipment_id},
                                         {"connected": best is not None, "path_length": len(path_out)})
        return result

    @mcp.tool()
    def kg_get_related_sensors(tag_name: str, investigation_id: str | None = None) -> dict:
        """For a sensor tag: its parent equipment, sibling sensors on that same
        equipment, and any control loops where this tag is the PV or feedback."""
        g = _load_graph()
        if tag_name not in g.nodes or g.nodes[tag_name].get("type") != "Sensor":
            raise ValueError(f"{tag_name!r} is not a known sensor tag in the knowledge graph.")
        parent = _parent_equipment(g, tag_name)
        siblings = [s for s in (_sensors_of(g, parent) if parent else []) if s["tag_name"] != tag_name]
        loops = []
        for u, _, d in g.in_edges(tag_name, data=True):
            if d.get("relation") in ("HAS_PV", "HAS_FEEDBACK"):
                loops.append({"loop_id": u, "label": g.nodes[u].get("label"), "role": d["relation"]})
        result = {"tag_name": tag_name, "parent_equipment": parent, "sibling_sensors": siblings,
                   "control_loops": loops}
        if investigation_id:
            session_store.log_tool_step(investigation_id, "kg_get_related_sensors", {"tag_name": tag_name},
                                         {"parent_equipment": parent, "n_siblings": len(siblings), "n_loops": len(loops)})
        return result

    @mcp.tool()
    def kg_get_control_loop(
        loop_id: str | None = None, tag_name: str | None = None, investigation_id: str | None = None,
    ) -> dict:
        """Resolve a control-loop node's PV, manipulated variable, feedback, and
        acted-on equipment. Pass either loop_id directly (e.g.
        'steam_temp_attemperation_primary') or tag_name to find the loop(s) where that
        tag is the PV or feedback. Also returns the loop's `confidence` field
        ('explicit' if evidenced by a named regulating-valve tag, else
        'inferred_standard_practice') -- don't treat inferred loops as verified fact."""
        g = _load_graph()
        if loop_id:
            loop_ids = [loop_id]
        elif tag_name:
            loop_ids = [u for u, _, d in g.in_edges(tag_name, data=True)
                        if d.get("relation") in ("HAS_PV", "HAS_FEEDBACK")]
        else:
            raise ValueError("Provide either loop_id or tag_name")

        loops_out = []
        for lid in loop_ids:
            if lid not in g.nodes or g.nodes[lid].get("type") not in ("ControlLoop", "ProcessRelationship"):
                raise ValueError(f"{lid!r} is not a known ControlLoop/ProcessRelationship node.")
            attrs = g.nodes[lid]
            pv, mv, fb, acts_on, related = [], [], [], [], []
            for _, v, d in g.out_edges(lid, data=True):
                rel = d.get("relation")
                if rel == "HAS_PV":
                    pv.append(v)
                elif rel == "HAS_MANIPULATED_VARIABLE":
                    mv.append(v)
                elif rel == "HAS_FEEDBACK":
                    fb.append(v)
                elif rel == "ACTS_ON":
                    acts_on.append(v)
                elif rel == "RELATES_TO":
                    related.append(v)
            loops_out.append({
                "loop_id": lid, "label": attrs.get("label"), "type": attrs.get("type"),
                "confidence": attrs.get("confidence"), "note": attrs.get("note"),
                "pv": pv, "manipulated_variable": mv, "feedback": fb, "acts_on": acts_on, "related": related,
            })
        result = {"loops": loops_out}
        if investigation_id:
            session_store.log_tool_step(investigation_id, "kg_get_control_loop",
                                         {"loop_id": loop_id, "tag_name": tag_name},
                                         {"n_loops": len(loops_out)})
        return result

    @mcp.tool()
    def kg_get_upstream_sensors(tag_name: str, max_hops: int = 5, investigation_id: str | None = None) -> list[dict]:
        """THE key root-cause-support tool: starting from the equipment that `tag_name`
        is mounted on, walk upstream along CONNECTS_TO edges and collect every sensor
        found on each upstream piece of equipment, nearest-to-farthest. Use this whenever
        a tag is abnormal to find what else is measured upstream that could be driving
        it -- then re-check those tags with historian_get_stats/historian_find_excursions
        over the same window before concluding anything is a root cause."""
        g = _load_graph()
        if tag_name not in g.nodes or g.nodes[tag_name].get("type") != "Sensor":
            raise ValueError(f"{tag_name!r} is not a known sensor tag in the knowledge graph.")
        parent = _parent_equipment(g, tag_name)
        if parent is None:
            return []
        upstream_nodes = _bfs(g, [parent], None, max_hops, "upstream")
        out = []
        for u in upstream_nodes:
            for s in _sensors_of(g, u["id"]):
                out.append({**s, "equipment_id": u["id"], "equipment_label": g.nodes[u["id"]].get("label"),
                            "hop": u["hop"], "stream": u["stream"]})
        if investigation_id:
            session_store.log_tool_step(investigation_id, "kg_get_upstream_sensors",
                                         {"tag_name": tag_name, "max_hops": max_hops},
                                         {"n_upstream_sensors": len(out)})
        return out
