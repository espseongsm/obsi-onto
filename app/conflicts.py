"""Conservative conflict candidates, always anchored to two exact source quotes."""

import re
import uuid

from app.markdown import iso_date
from app.query_contracts import ConflictOutput

# Rules only compare explicit fields recorded on the same known date. Free prose uses the model.
FIELD = re.compile(r"^([^:\n]{1,80}?)\s+(출시일|마감일|확정일)\s*[:：]\s*(\d{4}-\d{2}-\d{2})\s*$")


def rule_candidates(evidence):
    seen, candidates = {}, []
    for item in evidence:
        if not item["record_date"]:
            continue
        for line in item["text"].splitlines():
            match = FIELD.fullmatch(line.strip())
            if not match:
                continue
            subject, field, value = match.groups()
            if iso_date(value) != value:
                continue
            key = (subject, field, item["record_date"])
            previous = seen.get(key)
            if previous and previous[0] != value and previous[1]["citation"] != item["citation"]:
                candidates.append(
                    {
                        "subject": subject,
                        "classification": "needs_context",
                        "question": f"Which record should be used for {subject}'s {field}?",
                        "reason": "Records with the same recorded date contain different dates. "
                        "Check the event and conditions.",
                        "a": {"citation": previous[1]["citation"], "quote": previous[2]},
                        "b": {"citation": item["citation"], "quote": line.strip()},
                    }
                )
            seen[key] = (value, item, line.strip())
    return candidates[:3]


def validate_candidates(output, evidence):
    parsed = ConflictOutput.model_validate(output)
    sources = {item["citation"]: item for item in evidence}
    result, seen = [], set()
    for item in parsed.conflicts:
        a, b = item.a, item.b
        pair = tuple(sorted((a.citation, b.citation)))
        if a.citation == b.citation or pair in seen or a.quote == b.quote:
            continue
        if any(
            side.citation not in sources or side.quote not in sources[side.citation]["text"]
            for side in (a, b)
        ):
            continue
        if not all(item.subject in sources[side.citation]["text"] for side in (a, b)):
            continue
        seen.add(pair)
        result.append(
            {**item.model_dump(), "id": uuid.uuid4().hex, "version": 1, "status": "unresolved"}
        )
    return result


def overlay(graph, conflicts):
    """Copy a job-specific overlay; never add inferred contradictions to the RDF store."""
    nodes = [{**node} for node in graph["nodes"]]
    edges = [{**edge} for edge in graph["edges"] if edge.get("origin") != "clarification"]
    ids = {node["id"] for node in nodes}
    highlights = set()
    for conflict in conflicts:
        a, b = ("section/" + conflict[side]["citation"][1:] for side in ("a", "b"))
        highlights.update((a, b))
        if a in ids and b in ids:
            edges.append(
                {
                    "source": a,
                    "target": b,
                    "kind": "conflict_candidate",
                    "label": "Possible conflict · confirmation needed"
                    if conflict["status"] == "unresolved"
                    else "Decision deferred"
                    if conflict["status"] == "deferred"
                    else "User confirmation",
                    "origin": "clarification",
                }
            )
    for node in nodes:
        node["conflict"] = node["id"] in highlights
        if highlights:
            node["matched"] = node["conflict"]
    return {**graph, "nodes": nodes, "edges": edges}
