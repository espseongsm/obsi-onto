"""Source snapshots and final publication, shared by synchronous and job APIs."""

import json
import uuid

from app.conflicts import overlay
from app.graph_view import graph_view
from app.indexer import now
from app.search import plan


def prepare(search, request, query_vector, retrieval_query=None):
    spec = plan(request["question"], request["domain"], request["start"], request["end"])
    evidence, paths, warnings, matches = search.retrieve(
        retrieval_query or request["question"],
        spec,
        request["mode"],
        query_vector=query_vector,
    )
    # Bound additional context to eligible paragraphs from the same notes; retain scope filters.
    evidence = search.supplement(evidence, spec)
    return {
        "plan": spec,
        "evidence": evidence,
        "paths": paths,
        "warnings": warnings,
        "candidate_count": matches,
        "prepared_at": now(),
    }


def verify_snapshot(search, snapshot):
    evidence, unavailable = search.verify(snapshot["evidence"])
    snapshot["evidence"] = evidence
    if unavailable:
        snapshot["warnings"].append("Evidence that changed or could not be read was excluded.")
    if unavailable or "graph" not in snapshot:
        snapshot["graph"] = graph_view(search.ontology, search.indexer.root, evidence=evidence)
    return unavailable


def publish(
    search,
    request,
    snapshot,
    sentences,
    generated,
    conflicts,
    clarifications,
    conversation_id=None,
    vault_epoch=None,
):
    evidence, spec = snapshot["evidence"], snapshot["plan"]
    warnings = snapshot["warnings"]
    if not sentences:
        sentences = [{"text": item["text"], "citations": [item["citation"]]} for item in evidence]
    pending = search.store.rows("SELECT count(*) n FROM notes WHERE state!='ready'")[0]["n"]
    warnings.append(
        "Citations reflect records from that time, not verified current facts, "
        "holdings, or completed trades."
    )
    if spec["start"] or spec["end"]:
        warnings.append(
            "Date filters use recorded dates. "
            "Undated records are excluded from date-filtered searches."
        )
    if spec["domain"] != "all":
        warnings.append(
            "Domains are classified using titles, tags, and explicit properties. "
            "Use All records to find unclassified notes."
        )
    if snapshot["candidate_count"] > len(evidence) or pending:
        warnings.append(
            f"Showing {len(evidence)} verified passages; "
            f"{pending} notes are pending or have errors. "
            "This is not a complete summary of every record in the selected period."
        )
    if any(c["choice"] == "defer" for c in clarifications):
        warnings.append(
            "Deferred questions show both source records without choosing a conclusion."
        )
    search.ontology.refresh()
    graph = overlay(graph_view(search.ontology, search.indexer.root, evidence=evidence), conflicts)
    result = {
        "id": uuid.uuid4().hex,
        "created_at": now(),
        "question": request["question"],
        "conversation_id": conversation_id,
        "vault_epoch": vault_epoch,
        "plan": spec,
        "mode": request["mode"],
        "generated": generated,
        "sentences": sentences,
        "evidence": evidence,
        "paths": snapshot["paths"],
        "graph": graph,
        "warnings": warnings,
        "validation": search.ontology.validation,
        "message": "Evidence found in your notes."
        if evidence
        else "There is not enough evidence to answer this question.",
        "candidate_count": snapshot["candidate_count"],
        "conflicts": conflicts,
        "clarifications": clarifications,
        "verified_at": now(),
    }
    with search.store.transaction() as db:
        db.execute(
            "INSERT INTO runs VALUES(?,?,?,?)",
            (
                result["id"],
                result["created_at"],
                request["question"],
                json.dumps(result, ensure_ascii=False),
            ),
        )
    return result
