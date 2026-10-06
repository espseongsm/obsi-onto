from fastapi.testclient import TestClient
from rdflib import RDF, RDFS, Literal

from app.api import create_app
from app.graph_overview import MAX_EDGES, MAX_NODES, graph_overview
from app.ontology import ONTO, N
from tests.conftest import write


def test_overview_preserves_all_ready_notes_below_budget_without_model_calls(service):
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


def test_large_overview_limits_rows_and_response_with_exact_note_count(service, monkeypatch):
    with service.store.transaction() as db:
        db.executemany(
            "INSERT INTO notes(id,path,title,state) VALUES(?,?,?,'ready')",
            [(str(i), f"Note{i:04}.md", f"Note {i}") for i in range(1200)],
        )
    queries = []
    rows = service.store.rows

    def track(sql, params=()):
        queries.append(sql)
        return rows(sql, params)

    service.ontology.refresh()
    monkeypatch.setattr(service.store, "rows", track)
    data = graph_overview(service.ontology, service.indexer.root)
    assert len(data["nodes"]) == data["limit"] == MAX_NODES
    assert data["total_notes"] == data["total_nodes"] == 1200
    assert data["omitted_nodes"] == 1200 - MAX_NODES
    assert data["edges"] == []
    assert any("ORDER BY path LIMIT ?" in sql for sql in queries)
    assert not any("FROM sections" in sql for sql in queries)


def test_dense_overview_bounds_edges_and_counts_distinct_links_between_shown_nodes(service):
    with service.store.transaction() as db:
        db.executemany(
            "INSERT INTO notes(id,path,title,state) VALUES(?,?,?,'ready')",
            [(str(i), f"Note{i:02}.md", f"Note {i}") for i in range(30)],
        )
    service.ontology.refresh()
    graph = service.ontology.graph
    for i in range(30):
        section = N[f"section/{i}"]
        graph.add((section, RDF.type, ONTO.Section))
        graph.add((N[f"note/{i}"], ONTO.contains, section))
        graph.add((section, ONTO.note, N[f"note/{i}"]))
        for j in range(30):
            if i == j:
                continue
            relation = N[f"relation/{i}/{j}"]
            graph.add((relation, RDF.type, ONTO.Relation))
            graph.add((relation, ONTO.source, section))
            graph.add((relation, ONTO.target, N[f"note/{j}"]))
    data = graph_overview(service.ontology, service.indexer.root)
    assert len(data["nodes"]) == 30 and data["omitted_nodes"] == 0
    assert len(data["edges"]) == data["edge_limit"] == MAX_EDGES
    assert data["total_edges"] == 30 * 29
    assert data["omitted_edges"] == 30 * 29 - MAX_EDGES
    assert data["edge_count_scope"] == "displayed_nodes"
    ids = {node["id"] for node in data["nodes"]}
    assert all(edge["source"] in ids and edge["target"] in ids for edge in data["edges"])
    assert len({(e["source"], e["target"]) for e in data["edges"]}) == MAX_EDGES


def test_auxiliary_nodes_share_the_same_budget_and_omission_count(service):
    write(service, "One.md", "content")
    service.indexer.reconcile()
    service.ontology.refresh()
    graph = service.ontology.graph
    section = next(graph.subjects(RDF.type, ONTO.Section))
    for i in range(1000):
        tag = N[f"tag/{i}"]
        graph.add((tag, RDF.type, ONTO.Tag))
        graph.add((tag, RDFS.label, Literal(str(i))))
        graph.add((section, ONTO.taggedWith, tag))
    data = graph_overview(service.ontology, service.indexer.root)
    assert len(data["nodes"]) == MAX_NODES
    assert data["total_nodes"] == 1001 and data["omitted_nodes"] == 1001 - MAX_NODES
    assert len(data["edges"]) == MAX_NODES - 1


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
