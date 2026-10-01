import threading

from rdflib import RDF, Literal

from app.markdown import searchable_body
from app.ontology import ONTO, N
from tests.conftest import write
from tests.test_query_jobs import finish, reply, submit


def test_sql_scope_preserves_heading_and_hashtag_semantics(service):
    write(service, "기록.md", "# 제목\n\n#투자 시장 점검\n\n##\t분석\n\n####### 본문")
    service.indexer.reconcile()
    rows = service.store.rows("SELECT * FROM sections")
    expected = {row["id"] for row in rows if searchable_body(row["text"])}
    actual = service.search.eligible({"domain": "all", "start": None, "end": None})
    assert set(actual) == expected
    assert {r["text"] for r in actual.values()} == {"#투자 시장 점검", "####### 본문"}


def test_indexed_neighbors_match_previous_sparql(service):
    write(service, "첫째.md", "---\ntopics: [공통]\n---\n첫 기록 [[둘째#주제]]")
    write(service, "둘째.md", "---\ntopics: [공통]\n---\n## 주제\n\n대상 기록\n\n다음 문단")
    write(service, "셋째.md", "전체 링크 [[첫째]]")
    service.indexer.reconcile()
    service.ontology.refresh()
    rows = service.store.rows("SELECT id FROM sections")
    graph = service.ontology.graph
    for row in rows[:2]:
        claim, source = N[f"claim/test-{row['id']}"], N[f"section/{row['id']}"]
        graph.add((claim, RDF.type, ONTO.Thought))
        graph.add((claim, ONTO.source, source))
        graph.add((claim, ONTO.about, N["topic/shared-test"]))
        graph.add((claim, ONTO.origin, Literal("user_review")))
    query = """PREFIX o: <https://obsi-onto.local/schema/>
    SELECT DISTINCT ?target ?via WHERE {
      ?seed o:note ?home .
      { ?edge o:source ?seed ; o:target ?via .
        { ?edge o:targetSection ?target . }
        UNION { FILTER NOT EXISTS { ?edge o:targetSection ?anchor }
                ?via o:contains ?target . }
      }
      UNION { ?target o:linksTo ?home . BIND(?home AS ?via) }
      UNION { ?a o:source ?seed ; o:about ?via . ?b o:about ?via ; o:source ?target . }
      UNION { ?seed o:about ?via . ?target o:about ?via . }
    } LIMIT 60"""
    for row in rows:
        sid = row["id"]
        expected = {
            (int(str(r.target).rsplit("/", 1)[1]), str(r.via))
            for r in graph.query(query, initBindings={"seed": N[f"section/{sid}"]})
            if "/section/" in str(r.target)
        }
        actual = service.ontology.neighbors([sid])
        assert {(r["section_id"], r["via"]) for r in actual} == expected


def test_semantic_conflict_resume_passes_separate_user_context(service, monkeypatch):
    write(service, "첫째.md", "프로젝트 B는 우선 출시한다.")
    write(service, "둘째.md", "프로젝트 B는 출시를 보류한다.")
    service.indexer.reconcile()
    service.generator.enabled = True
    calls = []

    def model(question, evidence, task="answer", context=None):
        calls.append(task)
        if task == "conflicts":
            return {
                "conflicts": [
                    {
                        "subject": "프로젝트 B",
                        "classification": "needs_context",
                        "question": "어느 출시 기준을 적용할까요?",
                        "reason": "출시 여부 확인이 필요합니다.",
                        "a": {"citation": evidence[0]["citation"], "quote": evidence[0]["text"]},
                        "b": {"citation": evidence[1]["citation"], "quote": evidence[1]["text"]},
                    }
                ]
            }
        assert context["clarifications"][0]["source"] == "user_clarification"
        assert context["clarifications"][0]["choice"] == "both"
        return {
            "sentences": [
                {
                    "text": "별도 맥락의 기록을 함께 유지합니다.",
                    "citations": [item["citation"] for item in evidence],
                }
            ]
        }

    monkeypatch.setattr(service.generator, "request", model)
    jid = submit(service, generate=True)
    job = finish(service, jid, "awaiting_user")
    service.jobs.clarify(jid, reply(job, "both"))
    assert finish(service, jid)["result"]["generated"]
    assert calls == ["conflicts", "answer"]


def test_suggestions_model_wait_does_not_block_clear_or_publish_late(service, monkeypatch):
    write(service, "기록.md", "프로젝트 B 계획")
    service.indexer.reconcile()
    service.suggestions.reset()
    service.generator.enabled = True
    entered, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {}

    monkeypatch.setattr(service.generator, "request", slow)
    future = service.jobs.pool.submit(service.suggestions.refresh)
    assert entered.wait(3)
    try:
        assert service.operation.acquire(timeout=1)
        try:
            service.clear()
        finally:
            service.operation.release()
    finally:
        release.set()
        future.result(timeout=5)
    assert not service.store.get("suggestions", {}).get("items")
