from fastapi.testclient import TestClient

from app.api import create_app
from app.graph_overview import graph_overview
from tests.conftest import write


def test_overview_includes_all_ready_notes_without_detail_limit_or_model_calls(service):
    for i in range(90):
        write(service, f"노트{i:03}.md", f"기록 {i}")
    write(service, "Private/제외.md", "비공개")
    service.indexer.reconcile()
    service.indexer.invalidate(["노트000.md"])
    calls = service.embedder.calls
    overview = graph_overview(service.ontology, service.indexer.root)
    assert overview["total_notes"] == overview["total_nodes"] == 89
    assert len(overview["nodes"]) == 89 and overview["omitted_nodes"] == 0
    assert overview["projection"] == "notes" and overview["scope"] == "overview"
    assert all(n["kind"] == "Note" and n["uri"] for n in overview["nodes"])
    assert not any(n["path"] in {"노트000.md", "Private/제외.md"} for n in overview["nodes"])
    assert service.embedder.calls == calls


def test_overview_folds_real_section_links_tags_and_topics_into_notes(service):
    write(service, "대상.md", "## 분석\n\n근거")
    write(
        service, "출처.md", "---\nproject: 프로젝트\ntags: [개발]\n---\n[[대상#분석]]\n\n[[대상]]"
    )
    write(service, "미해결.md", "[[없는노트]]")
    service.indexer.reconcile()
    data = graph_overview(service.ontology, service.indexer.root)
    nodes = {n["id"]: n for n in data["nodes"]}
    links = [e for e in data["edges"] if e["kind"] == "linksTo"]
    assert len(links) == 1 and links[0]["support_count"] == 2
    assert nodes[links[0]["source"]]["path"] == "출처.md"
    assert nodes[links[0]["target"]]["path"] == "대상.md"
    assert {n["kind"] for n in nodes.values()} == {"Note", "Tag", "Topic"}
    assert {e["kind"] for e in data["edges"]} == {"linksTo", "taggedWith", "about"}
    assert all(nodes[e["source"]]["kind"] == "Note" for e in data["edges"])
    service.indexer.invalidate(["대상.md"])
    assert not any(
        e["kind"] == "linksTo"
        for e in graph_overview(service.ontology, service.indexer.root)["edges"]
    )


def test_empty_overview_has_no_phantom_nodes(service):
    data = graph_overview(service.ontology, service.indexer.root)
    assert data["nodes"] == data["edges"] == []
    assert data["total_notes"] == 0


def test_overview_api_preserves_auth_and_detailed_search(service, monkeypatch):
    write(service, "기록.md", "고객 계약 근거")
    service.indexer.reconcile()
    monkeypatch.setattr(service, "start", lambda: None)
    with TestClient(create_app(service.config, service)) as client:
        assert client.get("/api/graph?overview=true").status_code == 403
        client.headers["x-obsi-token"] = client.get("/api/session").json()["token"]
        data = client.get("/api/graph?overview=true").json()
        assert data["scope"] == "overview" and data["total_notes"] == 1
        found = client.get("/api/graph", params={"q": "계약", "overview": True}).json()
        assert found["scope"] == "vault"
        assert any(n["kind"] == "Section" and n["matched"] for n in found["nodes"])
