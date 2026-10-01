"""Whole-vault note map; section-level relations are folded into their source notes."""

from urllib.parse import quote, urlencode

from rdflib import RDF, RDFS

from app.graph_view import node_id
from app.ontology import ONTO, N


def graph_overview(ontology, root):
    with ontology.store.lock:
        ontology.refresh()
        graph, store = ontology.graph, ontology.store
        notes = store.rows("SELECT * FROM notes WHERE state='ready' ORDER BY path")
        nodes = {}
        owners = {}
        for note in notes:
            key = "note/" + note["id"]
            owners[N[key]] = key
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
        for section in store.rows(
            "SELECT s.id,s.note_id FROM sections s JOIN notes n ON n.id=s.note_id "
            "WHERE n.state='ready' AND s.revision=n.revision"
        ):
            owners[N[f"section/{section['id']}"]] = "note/" + section["note_id"]
        for kind in (ONTO.Claim, ONTO.Activity):
            for subject in graph.subjects(RDF.type, kind):
                owner = owners.get(graph.value(subject, ONTO.source))
                if owner:
                    owners[subject] = owner

        edges = {}

        def connect(source, target, kind, label, origin):
            if not source or not target or source == target:
                return
            key = (source, target, kind, origin)
            if key not in edges:
                edges[key] = {
                    "source": source,
                    "target": target,
                    "kind": kind,
                    "label": label,
                    "origin": origin,
                    "support_count": 0,
                }
            edges[key]["support_count"] += 1

        for predicate, kind, label, origin in (
            (ONTO.taggedWith, "Tag", "태그", "explicit_tag"),
            (ONTO.about, "Topic", "주제", "frontmatter"),
        ):
            for source, target in graph.subject_objects(predicate):
                owner = owners.get(source)
                if not owner:
                    continue
                key = node_id(target)
                nodes[key] = {
                    "id": key,
                    "kind": kind,
                    "label": str(graph.value(target, RDFS.label) or ""),
                    "matched": False,
                    "evidence": False,
                }
                edge_origin = (
                    "user_review" if node_id(source).startswith(("claim/", "activity/")) else origin
                )
                connect(owner, key, node_id(predicate).rsplit("/", 1)[-1], label, edge_origin)
        for relation in graph.subjects(RDF.type, ONTO.Relation):
            source = owners.get(graph.value(relation, ONTO.source))
            target = graph.value(relation, ONTO.targetSection) or graph.value(relation, ONTO.target)
            connect(source, owners.get(target), "linksTo", "명시 링크", "explicit_link")
        return {
            "nodes": list(nodes.values()),
            "edges": [edges[key] for key in sorted(edges)],
            "generation": ontology.version,
            "scope": "overview",
            "projection": "notes",
            "query": "",
            "total_nodes": len(nodes),
            "total_notes": len(notes),
            "matched_nodes": 0,
            "omitted_nodes": 0,
            "omitted_edges": 0,
            "limit": None,
        }
