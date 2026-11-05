"""将来源局部事实按 canonical name 聚合为有出处的展示图。"""

from rag_engine.graph_extract import normalize_name
from rag_engine.graph_sources import digest


def aggregate_graph(
    rows, entity_rows, kb: str, mode: str, limit: int, max_edges: int, tenant: str = ""
) -> dict:
    max_edges = min(max_edges, limit * 4, 2000)
    nodes = {}
    edges = {}

    def add_node(canonical, name, source, model):
        key = "entity-" + digest(tenant, kb, canonical)
        node = nodes.setdefault(
            key,
            {
                "id": key,
                "type": "ENTITY",
                "name": name,
                "label": name,
                "documentId": None,
                "documentCode": None,
                "documentVersion": None,
                "sourceLocator": None,
                "properties": {},
                "_sources": {},
                "_models": set(),
            },
        )
        node["_sources"][source["pointId"]] = source
        node["_models"].add(model)
        return key

    for row in entity_rows:
        add_node(row["subject"], row["subjectName"], row["source"], row.get("model") or "unknown")
    for row in rows:
        source = row["source"]
        model = row.get("model") or "unknown"
        left = add_node(row["subject"], row["subjectName"], source, model)
        right = add_node(row["object"], row["objectName"], source, model)
        key = "relation-" + digest(left, normalize_name(row["predicate"]), right)
        edge = edges.setdefault(
            key,
            {
                "id": key,
                "source": left,
                "target": right,
                "type": "RELATED_TO",
                "label": row["predicate"],
                "sourceLocator": None,
                "properties": {},
                "_sources": {},
                "_models": set(),
            },
        )
        edge["_sources"][source["pointId"]] = source
        edge["_models"].add(model)
    selected_nodes = sorted(nodes.values(), key=lambda value: (value["name"], value["id"]))[:limit]
    selected_ids = {node["id"] for node in selected_nodes}
    selected_edges = [
        edge
        for edge in sorted(edges.values(), key=lambda value: value["id"])
        if edge["source"] in selected_ids and edge["target"] in selected_ids
    ][:max_edges]
    for item in selected_nodes + selected_edges:
        sources = sorted(item.pop("_sources").values(), key=lambda value: value["pointId"])
        models = sorted(item.pop("_models"))
        item["properties"] = {
            "sources": sources[:8],
            "sourceCount": len(sources),
            "sourceTruncated": len(sources) > 8,
            "extractionMethod": "LLM_EXTRACTED",
            "model": models[0] if len(models) == 1 else "mixed",
            "models": models,
        }
    return {
        "knowledgeBase": kb,
        "viewMode": mode,
        "nodeCount": len(selected_nodes),
        "edgeCount": len(selected_edges),
        "truncated": len(nodes) > len(selected_nodes) or len(edges) > len(selected_edges),
        "nodeTypeCounts": {"ENTITY": len(selected_nodes)} if selected_nodes else {},
        "relationTypeCounts": {"RELATED_TO": len(selected_edges)} if selected_edges else {},
        "nodes": selected_nodes,
        "edges": selected_edges,
        "backend": "neo4j",
        "build": None,
    }
