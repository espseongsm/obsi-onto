"""A validated RDF projection of the currently published SQLite revision."""

import json
import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import quote

from pyshacl import validate
from rdflib import RDF, RDFS, Graph, Literal, Namespace

from app.config import ROOT
from app.markdown import strings

ONTO = Namespace("https://obsi-onto.local/schema/")
N = Namespace("https://obsi-onto.local/entity/")


def resolve_links(store):
    notes = store.rows("SELECT * FROM notes WHERE state='ready'")
    sections = store.rows("SELECT * FROM sections")
    by_path = {n["path"].removesuffix(".md"): n for n in notes}
    by_id = {n["id"]: n for n in notes}
    by_note = {}
    for section in sections:
        by_note.setdefault(section["note_id"], []).append(section)
    names = {}
    for note in notes:
        for name in {PurePosixPath(note["path"]).stem, note["title"], *json.loads(note["aliases"])}:
            names.setdefault(name, []).append(note)
    with store.transaction() as db:
        for link in store.rows(
            "SELECT l.*, n.path, n.id note_id FROM links l "
            "JOIN sections s ON s.id=l.section_id "
            "JOIN notes n ON n.id=s.note_id"
        ):
            target = link["target"].removesuffix(".md")
            candidates = []
            if link["kind"] == "external":
                state = "external_unverified"
            else:
                relative = posixpath.normpath(str(PurePosixPath(link["path"]).parent / target))
                if not target:
                    candidates = [by_id[link["note_id"]]] if link["note_id"] in by_id else []
                elif relative in by_path:
                    candidates = [by_path[relative]]
                elif target in by_path:
                    candidates = [by_path[target]]
                elif link["kind"] == "wiki" and "/" not in target:
                    candidates = names.get(target, [])
                state = (
                    "resolved"
                    if len(candidates) == 1
                    else ("ambiguous" if candidates else "unresolved")
                )
            selected = candidates[0]["id"] if state == "resolved" else None
            anchor = None
            if selected and link["fragment"]:
                fragment = link["fragment"]
                matches = [
                    s
                    for s in by_note.get(selected, [])
                    if (
                        (
                            fragment.startswith("^")
                            and re.search(
                                r"(?:^|\s)" + re.escape(fragment) + r"(?:\s|$)", s["text"]
                            )
                        )
                        or (
                            not fragment.startswith("^")
                            and (
                                s["heading"] == fragment
                                or s["heading"].replace(" ", "-").lower() == fragment.lower()
                            )
                        )
                    )
                ]
                if not matches:
                    state = "missing_anchor"
                else:
                    substantive = [
                        s for s in matches if not re.fullmatch(r"\s*#{1,6}\s+[^\n]+", s["text"])
                    ]
                    anchor = (substantive or matches)[0]["id"]
            db.execute(
                "UPDATE links SET status=?,target_note=?,target_section=? WHERE id=?",
                (state, selected, anchor, link["id"]),
            )


class Ontology:
    def __init__(self, store):
        self.store, self.version = store, -1
        self.graph = Graph()
        self.validation = {"conforms": True, "report": "Not indexed yet."}

    def refresh(self):
        with self.store.lock:
            self._refresh()

    def _refresh(self):
        version = self.store.get("generation", 0)
        if version == self.version:
            return
        graph = Graph().parse(ROOT / "ontology/schema.ttl")
        graph.bind("o", ONTO)
        graph.add((N.vault, RDF.type, ONTO.Vault))
        notes = self.store.rows("SELECT * FROM notes WHERE state='ready'")
        note_ids = {note["id"] for note in notes}
        for note in notes:
            node = N["note/" + note["id"]]
            graph.add((node, RDF.type, ONTO.Note))
            graph.add((node, RDFS.label, Literal(note["title"])))
            graph.add((node, ONTO.path, Literal(note["path"])))
            graph.add((N.vault, ONTO.contains, node))
        active = self.store.rows(
            "SELECT s.* FROM sections s JOIN notes n ON n.id=s.note_id "
            "WHERE n.state='ready' AND n.revision=s.revision"
        )
        ids = {s["id"] for s in active}
        for section in active:
            node = N[f"section/{section['id']}"]
            graph.add((node, RDF.type, ONTO.Section))
            graph.add((node, ONTO.note, N["note/" + section["note_id"]]))
            graph.add((N["note/" + section["note_id"]], ONTO.contains, node))
            for prop, value in (
                (ONTO.sourceHash, section["hash"]),
                (ONTO.startLine, section["start"]),
                (ONTO.endLine, section["end"]),
                (ONTO.revision, section["revision"]),
            ):
                graph.add((node, prop, Literal(value)))
            for tag in json.loads(section["tags"]):
                tag_node = N["tag/" + quote(tag, safe="")]
                graph.add((tag_node, RDF.type, ONTO.Tag))
                graph.add((tag_node, RDFS.label, Literal(tag)))
                graph.add((node, ONTO.taggedWith, tag_node))
        # Frontmatter subjects are document context, not inferred claims or activities.
        fronts = {s["note_id"]: s for s in active if s["start"] == 1}
        for note in notes:
            metadata = json.loads(note["metadata"])
            front = fronts.get(note["id"])
            if not front:
                continue
            for field in ("topics", "project", "company", "people"):
                for label in strings(metadata.get(field)):
                    label = label.removeprefix("[[").removesuffix("]]")
                    topic = N["topic/" + quote(label, safe="")]
                    graph.add((topic, RDF.type, ONTO.Topic))
                    graph.add((topic, RDFS.label, Literal(label)))
                    graph.add((topic, ONTO.topicKind, Literal(field)))
                    graph.add((N[f"section/{front['id']}"], ONTO.about, topic))
                    graph.add((N[f"section/{front['id']}"], ONTO.origin, Literal("frontmatter")))
        for link in self.store.rows("SELECT * FROM links WHERE status='external_unverified'"):
            if link["section_id"] in ids:
                external = N[f"external/{link['id']}"]
                graph.add((external, RDF.type, ONTO.Source))
                graph.add((external, ONTO.url, Literal(link["raw"])))
                graph.add((external, ONTO.verified, Literal(False)))
                graph.add((external, ONTO.source, N[f"section/{link['section_id']}"]))
        for link in self.store.rows("SELECT * FROM links WHERE status='resolved'"):
            if link["section_id"] not in ids or link["target_note"] not in note_ids:
                continue
            source = N[f"section/{link['section_id']}"]
            target = N[f"note/{link['target_note']}"]
            graph.add((source, ONTO.linksTo, target))
            relation = N[f"relation/{link['id']}"]
            graph.add((relation, RDF.type, ONTO.Relation))
            graph.add((relation, ONTO.source, source))
            graph.add((relation, ONTO.target, target))
            graph.add((relation, ONTO.origin, Literal("explicit_link")))
            if link["target_section"] in ids:
                graph.add((relation, ONTO.targetSection, N[f"section/{link['target_section']}"]))
        for candidate in self.store.rows("SELECT * FROM candidates WHERE status='accepted'"):
            if candidate["section_id"] not in ids:
                continue
            node, topic = (
                N[f"claim/{candidate['id']}"],
                N["topic/" + quote(candidate["topic"], safe="")],
            )
            graph.add((node, RDF.type, ONTO[candidate["kind"]]))
            graph.add((node, ONTO.source, N[f"section/{candidate['section_id']}"]))
            graph.add((node, ONTO.about, topic))
            graph.add((N[f"section/{candidate['section_id']}"], ONTO.records, node))
            graph.add((node, ONTO.origin, Literal("user_review")))
            graph.add((node, ONTO.quote, Literal(candidate["quote"])))
            graph.add((node, ONTO.activityState, Literal(candidate["activity_state"])))
            if candidate["event_date"]:
                graph.add((node, ONTO.eventDate, Literal(candidate["event_date"])))
            graph.add((topic, RDF.type, ONTO.Topic))
            graph.add((topic, RDFS.label, Literal(candidate["topic"])))
        conforms, _, report = validate(graph, shacl_graph=str(ROOT / "ontology/shapes.ttl"))
        if not conforms:
            raise ValueError("Relationship provenance validation failed: " + str(report))
        self.graph, self.version = graph, version
        self.validation = {"conforms": True, "report": str(report)}

    def neighbors(self, section_ids):
        # Expand the same fixed relations through indexed triples, without SPARQL parsing.
        with self.store.lock:
            self.refresh()
            graph, results = self.graph, []
            for sid in section_ids:
                seed = N[f"section/{sid}"]
                pairs = set()
                for home in graph.objects(seed, ONTO.note):
                    pairs.update((target, home) for target in graph.subjects(ONTO.linksTo, home))
                for edge in graph.subjects(ONTO.source, seed):
                    for via in graph.objects(edge, ONTO.target):
                        anchors = list(graph.objects(edge, ONTO.targetSection))
                        pairs.update(
                            (target, via)
                            for target in (anchors or graph.objects(via, ONTO.contains))
                        )
                    for via in graph.objects(edge, ONTO.about):
                        for other in graph.subjects(ONTO.about, via):
                            pairs.update(
                                (target, via) for target in graph.objects(other, ONTO.source)
                            )
                for via in graph.objects(seed, ONTO.about):
                    pairs.update((target, via) for target in graph.subjects(ONTO.about, via))
                valid = sorted(
                    (str(target), str(via))
                    for target, via in pairs
                    if str(target).startswith(str(N) + "section/")
                )
                results.extend(
                    {
                        "section_id": int(target.rsplit("/", 1)[1]),
                        "from": sid,
                        "via": via,
                        "kind": "graph",
                    }
                    for target, via in valid[:60]
                )
            return results
