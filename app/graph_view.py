"""Bounded, source-backed views of the published ontology, also saved with answers."""

from urllib.parse import quote, urlencode

from rdflib import RDF, RDFS

from app.ontology import ONTO, N

MAX_NODES = 80
MAX_EDGES = 200
KINDS = ("Note", "Section", "Topic", "Tag", "Claim", "Activity")


def node_id(uri):
    return str(uri).removeprefix(str(N))


def graph_view(ontology, root, evidence=None, query=""):
    # Watcher invalidation also takes this lock. Do not mix publication generations.
    with ontology.store.lock:
        ontology.refresh()
        return _project(ontology, root, evidence, query.strip().casefold())


def _project(ontology, root, evidence, query):
    graph, store = ontology.graph, ontology.store
    snapshot = evidence is not None
    sources = {item["id"]: item for item in evidence or []}
    sections = store.rows(
        "SELECT s.*,n.path,n.title,n.record_date FROM sections s "
        "JOIN notes n ON n.id=s.note_id WHERE n.state='ready' AND n.revision=s.revision"
    )
    if snapshot:
        sections = [s for s in sections if s["id"] in sources]
    by_section = {f"section/{s['id']}": s for s in sections}
    note_ids = {s["note_id"] for s in sections}
    notes = {
        f"note/{n['id']}": n
        for n in store.rows("SELECT * FROM notes WHERE state='ready'")
        if not snapshot or n["id"] in note_ids
    }
    nodes, edges = {}, []
    for kind in KINDS:
        for uri in sorted(graph.subjects(RDF.type, ONTO[kind]), key=str):
            key = node_id(uri)
            source = None
            if kind == "Note":
                source = notes.get(key)
                if not source:
                    continue
            elif kind == "Section":
                source = by_section.get(key)
                if not source:
                    continue
            elif kind in {"Claim", "Activity"}:
                source = by_section.get(node_id(graph.value(uri, ONTO.source)))
                if not source:
                    continue
            label = str(graph.value(uri, RDFS.label) or "")
            if kind == "Section":
                label = source["heading"] or source["title"]
            elif kind in {"Claim", "Activity"}:
                label = str(graph.value(uri, ONTO.quote) or "")
            node = {"id": key, "kind": kind, "label": label}
            match_text = label
            if source:
                node.update(
                    path=source["path"],
                    revision=source["revision"],
                    record_date=source["record_date"],
                )
                if kind != "Note":
                    node.update(
                        section_id=source["id"],
                        start=source["start"],
                        end=source["end"],
                        hash=source["hash"],
                        excerpt=source["text"][:2000],
                        citation=sources.get(source["id"], {}).get("citation"),
                        routes=sources.get(source["id"], {}).get("routes", []),
                    )
                else:
                    node["hash"] = source["hash"]
                if root:
                    path = str(root / source["path"])
                    if source.get("heading"):
                        path += "#" + source["heading"]
                    node["uri"] = "obsidian://open?" + urlencode({"path": path}, quote_via=quote)
                match_text += " " + source["path"] + " " + source.get("text", "")
            if kind in {"Claim", "Activity"}:
                node["event_date"] = str(graph.value(uri, ONTO.eventDate) or "")
                node["activity_state"] = str(graph.value(uri, ONTO.activityState) or "unknown")
            node["matched"] = (
                kind == "Section" and bool(set(node.get("routes", [])) & {"단어", "의미"})
                if snapshot
                else bool(query and query in match_text.casefold())
            )
            node["evidence"] = kind == "Section" and source["id"] in sources
            nodes[key] = node

    def connect(source, target, relation, label, origin):
        a, b = node_id(source), node_id(target)
        if a in nodes and b in nodes:
            edges.append(
                {"source": a, "target": b, "kind": relation, "label": label, "origin": origin}
            )

    for predicate, label, origin in (
        (ONTO.contains, "노트의 문단", "structure"),
        (ONTO.taggedWith, "태그", "explicit_tag"),
        (ONTO.about, "주제", "frontmatter"),
        (ONTO.records, "검토된 판단·활동", "user_review"),
    ):
        for source, target in graph.subject_objects(predicate):
            edge_origin = "user_review" if str(source).startswith(str(N) + "claim/") else origin
            connect(source, target, node_id(predicate).rsplit("/", 1)[-1], label, edge_origin)
    for relation in graph.subjects(RDF.type, ONTO.Relation):
        target = graph.value(relation, ONTO.targetSection) or graph.value(relation, ONTO.target)
        connect(graph.value(relation, ONTO.source), target, "linksTo", "명시 링크", "explicit_link")

    # Remove topics/tags that are only supported by out-of-scope sections.
    connected = {e[end] for e in edges for end in ("source", "target")}
    nodes = {key: n for key, n in nodes.items() if key in connected or n["kind"] == "Note"}
    priority = {"linksTo": 0, "records": 1, "about": 2, "contains": 3, "taggedWith": 4}
    edges.sort(key=lambda e: (priority[e["kind"]], e["source"], e["target"]))
    if snapshot:
        seeds = [f"section/{sid}" for sid in sources if f"section/{sid}" in nodes]
    elif query:
        seeds = [key for key, node in nodes.items() if node["matched"]]
    else:
        seeds = sorted(
            (key for key, node in nodes.items() if node["kind"] == "Note"),
            key=lambda key: nodes[key]["path"],
        )[:12]
    chosen = set(seeds[:MAX_NODES])
    ordered = list(seeds[:MAX_NODES])
    # Two bounded rounds keep neighboring context, without growing via every common tag.
    for _ in range(2):
        frontier = set(chosen)
        for edge in edges:
            if edge["source"] in frontier or edge["target"] in frontier:
                for key in (edge["source"], edge["target"]):
                    if key not in chosen and len(chosen) < MAX_NODES:
                        chosen.add(key)
                        ordered.append(key)
    selected_edges = [e for e in edges if e["source"] in chosen and e["target"] in chosen]
    return {
        "nodes": [nodes[key] for key in ordered],
        "edges": selected_edges[:MAX_EDGES],
        "generation": ontology.version,
        "scope": "answer" if snapshot else "vault",
        "query": query,
        "total_nodes": len(nodes),
        "matched_nodes": sum(n["matched"] for n in nodes.values()),
        "omitted_nodes": len(nodes) - len(chosen),
        "omitted_edges": len(selected_edges) - min(len(selected_edges), MAX_EDGES),
        "limit": MAX_NODES,
    }
