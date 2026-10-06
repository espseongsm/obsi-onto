"""Whole-vault note map; section-level relations are folded into their source notes."""

from urllib.parse import quote, urlencode

from rdflib import RDF, RDFS

from app.graph_view import node_id
from app.ontology import ONTO, N

MAX_NODES = 240
MAX_EDGES = 480


def graph_overview(ontology, root):
    with ontology.store.lock:
        ontology.refresh()
        graph, store = ontology.graph, ontology.store
        total_notes = store.rows("SELECT count(*) n FROM notes WHERE state='ready'")[0]["n"]
        notes = store.rows(
            "SELECT id,path,title,revision,record_date,hash FROM notes "
            "WHERE state='ready' ORDER BY path LIMIT ?",
            (MAX_NODES,),
        )
        nodes = {}
        for note in notes:
            key = "note/" + note["id"]
            nodes[key] = {
                "id": key,
                "kind": "Note",
                "label": note["title"],
                "path": note["path"],
                "revision": note["revision"],
                "record_date": note["record_date"],
                "hash": note["hash"],
                "matched": False,
                "evidence": False,
            }
            if root:
                nodes[key]["uri"] = "obsidian://open?" + urlencode(
                    {"path": str(root / note["path"])}, quote_via=quote
                )
        note_ids = set(nodes)

        def owner(source):
            if source is None:
                return None
            if (source, RDF.type, ONTO.Note) in graph:
                return node_id(source)
            section = source
            if (source, RDF.type, ONTO.Section) not in graph:
                section = graph.value(source, ONTO.source)
            note = graph.value(section, ONTO.note) if section else None
            return node_id(note) if note else None

        total_nodes = total_notes
        for predicate, kind in (
            (ONTO.taggedWith, "Tag"),
            (ONTO.about, "Topic"),
        ):
            for target in graph.subjects(RDF.type, ONTO[kind]):
                total_nodes += 1
                if len(nodes) >= MAX_NODES or not any(
                    owner(source) in note_ids for source in graph.subjects(predicate, target)
                ):
                    continue
                key = node_id(target)
                nodes[key] = {
                    "id": key,
                    "kind": kind,
                    "label": str(graph.value(target, RDFS.label) or ""),
                    "matched": False,
                    "evidence": False,
                }
        edges, total_edges = [], 0
        # Fold one selected note at a time. Its possible targets are bounded by the node budget;
        # do not materialize every vault section, owner, or edge merely to discard it later.
        for source in sorted(note_ids):
            outgoing = {}

            def connect(target, kind, label, origin, source=source, outgoing=outgoing):
                if target not in nodes or source == target:
                    return
                key = (source, target, kind, origin)
                if key not in outgoing:
                    outgoing[key] = {
                        "source": source,
                        "target": target,
                        "kind": kind,
                        "label": label,
                        "origin": origin,
                        "support_count": 0,
                    }
                outgoing[key]["support_count"] += 1

            for section in graph.objects(N[source], ONTO.contains):
                for predicate, label, origin in (
                    (ONTO.taggedWith, "태그", "explicit_tag"),
                    (ONTO.about, "주제", "frontmatter"),
                ):
                    for target in graph.objects(section, predicate):
                        connect(
                            node_id(target), node_id(predicate).rsplit("/", 1)[-1], label, origin
                        )
                for record in graph.subjects(ONTO.source, section):
                    if (record, RDF.type, ONTO.Relation) in graph:
                        target = graph.value(record, ONTO.targetSection) or graph.value(
                            record, ONTO.target
                        )
                        connect(owner(target), "linksTo", "명시 링크", "explicit_link")
                    elif any(
                        (record, RDF.type, kind) in graph for kind in (ONTO.Claim, ONTO.Activity)
                    ):
                        for target in graph.objects(record, ONTO.about):
                            connect(node_id(target), "about", "주제", "user_review")
            total_edges += len(outgoing)
            for key in sorted(outgoing):
                if len(edges) < MAX_EDGES:
                    edges.append(outgoing[key])
        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "generation": ontology.version,
            "scope": "overview",
            "projection": "notes",
            "query": "",
            "total_nodes": total_nodes,
            "total_notes": total_notes,
            "total_edges": total_edges,
            "edge_count_scope": "displayed_nodes",
            "matched_nodes": 0,
            "omitted_nodes": total_nodes - len(nodes),
            "omitted_edges": total_edges - len(edges),
            "limit": MAX_NODES,
            "edge_limit": MAX_EDGES,
        }
