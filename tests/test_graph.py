import json

from app.graph_view import MAX_EDGES, MAX_NODES, graph_view
from tests.conftest import write


def test_graph_has_real_links_anchor_provenance_and_no_similarity_edges(service):
    write(service, "기업.md", "## 분석\n\n현금흐름 확인")
    write(service, "생각.md", "---\ncompany: A기업\ntags: [투자]\n---\n판단 [[기업#분석]]")
    write(service, "미해결.md", "[[없는노트]] [[기업#없는제목]]")
    service.indexer.reconcile()
    calls = service.embedder.calls
    data = graph_view(service.ontology, service.indexer.root, query="판단")
    nodes = {node["id"]: node for node in data["nodes"]}
    links = [edge for edge in data["edges"] if edge["kind"] == "linksTo"]
    assert len(links) == 1
    source, target = (nodes[links[0][end]] for end in ("source", "target"))
    assert source["path"] == "생각.md" and source["matched"]
    assert target["path"] == "기업.md" and target["excerpt"] == "현금흐름 확인"
    assert target["start"] == target["end"] == 3
    assert target["hash"] and target["revision"] == 1
    assert target["uri"].startswith("obsidian://open?")
    assert all(e["kind"] in {"contains", "taggedWith", "linksTo", "about"} for e in data["edges"])
    assert service.embedder.calls == calls


def test_answer_graph_respects_verified_scope_and_history_is_immutable(service):
    file = write(
        service, "2026-09-24.md", "## 업무\n\n고객 결제 개선 [[오래된]]\n\n## 투자\n\n기업 매수"
    )
    write(service, "오래된.md", "---\ndate: 2020-01-01\n---\n고객 결제")
    service.indexer.reconcile()
    result = service.search.ask("고객 결제", domain="work", start="2026-09-01", mode="lexical")
    graph = result["graph"]
    assert graph["scope"] == "answer" and graph["nodes"]
    sections = [n for n in graph["nodes"] if n["kind"] == "Section"]
    assert {n["section_id"] for n in sections} == {e["id"] for e in result["evidence"]}
    assert all(n["matched"] and n["citation"] for n in sections)
    assert not any(
        n.get("path") == "오래된.md" or "매수" in n.get("excerpt", "") for n in graph["nodes"]
    )
    assert not any(e["kind"] == "linksTo" for e in graph["edges"])
    file.unlink()
    service.indexer.reconcile()
    current = graph_view(service.ontology, service.indexer.root)
    assert not any(n.get("path") == "2026-09-24.md" for n in current["nodes"])
    saved = json.loads(
        service.store.rows("SELECT payload FROM runs WHERE id=?", (result["id"],))[0]["payload"]
    )
    assert saved["graph"] == graph


def test_only_reviewed_current_thoughts_are_shown(service):
    write(service, "일지.md", "A기업의 현금흐름을 확인하자")
    service.indexer.reconcile()
    section = service.store.rows("SELECT * FROM sections")[0]
    with service.store.transaction() as db:
        cid = db.execute(
            "INSERT INTO candidates(section_id,revision,kind,topic,quote,activity_state) "
            "VALUES(?,?,?,?,?,?)",
            (section["id"], section["revision"], "Claim", "A기업", section["text"], "unknown"),
        ).lastrowid
    assert not any(
        n["kind"] == "Claim" for n in graph_view(service.ontology, service.indexer.root)["nodes"]
    )
    service.review(cid, True)
    data = graph_view(service.ontology, service.indexer.root, query="A기업")
    claim = next(n for n in data["nodes"] if n["kind"] == "Claim")
    assert claim["section_id"] == section["id"] and claim["excerpt"] == section["text"]
    assert any(e["source"] == claim["id"] and e["origin"] == "user_review" for e in data["edges"])
    service.indexer.invalidate(["일지.md"])
    assert not graph_view(service.ontology, service.indexer.root)["nodes"]


def test_bounded_graph_and_no_match_do_not_trigger_model(service):
    write(service, "노트.md", "\n\n".join(f"## 문단 {i}\n\n본문 {i}" for i in range(90)))
    service.indexer.reconcile()
    calls = service.embedder.calls
    data = graph_view(service.ontology, service.indexer.root)
    assert len(data["nodes"]) == MAX_NODES and data["omitted_nodes"] > 0
    assert len(data["edges"]) <= MAX_EDGES
    ids = {n["id"] for n in data["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in data["edges"])
    assert not graph_view(service.ontology, service.indexer.root, query="존재하지않음")["nodes"]
    assert service.embedder.calls == calls


def test_answer_with_no_verified_evidence_has_no_graph(service):
    file = write(service, "노트.md", "고객 계약 근거")
    service.indexer.reconcile()
    file.write_text("바뀐 기록")
    result = service.search.ask("고객 계약", mode="lexical")
    assert not result["evidence"] and not result["graph"]["nodes"]


def test_invalidation_removes_incoming_link_to_pending_note(service):
    write(service, "출처.md", "근거 [[대상]]")
    write(service, "대상.md", "대상 내용")
    service.indexer.reconcile()
    service.indexer.invalidate(["대상.md"])
    data = graph_view(service.ontology, service.indexer.root)
    assert not any(e["kind"] == "linksTo" for e in data["edges"])
    assert not any(n.get("path") == "대상.md" for n in data["nodes"])
