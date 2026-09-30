import json

import pytest

from app.models import validate_endpoint
from app.search import Search, plan
from tests.conftest import write


def test_alias_anchor_ambiguous_and_external_links(service):
    write(service, "대상.md", "---\naliases: [별칭]\n---\n# 제목\n\n근거 문장 ^block")
    write(service, "다른/중복.md", "첫 문서")
    write(service, "또/중복.md", "둘째 문서")
    write(
        service,
        "링크.md",
        "[[별칭#제목]] [[대상#^block]] [[중복]] [[대상#없는제목]] "
        "[자료](https://example.com) [내부](대상.md#제목)",
    )
    service.indexer.reconcile()
    states = {link["raw"]: link["status"] for link in service.store.rows("SELECT * FROM links")}
    assert states["별칭#제목"] == "resolved"
    assert states["대상#^block"] == "resolved"
    assert states["중복"] == "ambiguous"
    assert states["대상#없는제목"] == "missing_anchor"
    assert states["https://example.com"] == "external_unverified"
    service.ontology.refresh()
    assert service.ontology.validation["conforms"]


def test_all_retrieval_paths_obey_domain_and_date(service):
    write(
        service,
        "2026-09-24.md",
        "## 업무\n계약 조회 개선 [[오래된]]\n\n## 투자\n계약 회사 매수 검토",
    )
    write(service, "오래된.md", "---\ndate: 2025-01-01\ndomain: work\n---\n계약 조회 개선")
    service.indexer.reconcile()
    for mode in ("lexical", "semantic", "hybrid"):
        result = service.search.ask(
            "계약", domain="work", start="2026-09-01", end="2026-09-30", mode=mode
        )
        assert result["evidence"]
        assert all(
            e["path"] == "2026-09-24.md" and "work" in json.loads(e["domains"])
            for e in result["evidence"]
        )
        assert not any("매수" in e["text"] for e in result["evidence"])


def test_vector_search_is_real_sqlite_vec_and_rank_fusion(service):
    write(service, "업무.md", "## 업무\n고객 문의 결제 상태 조회")
    service.indexer.reconcile()
    result = service.search.ask("고객 문의 결제 상태", mode="semantic")
    assert any("의미" in e["routes"] for e in result["evidence"])


def test_graph_adds_explicitly_connected_context(service):
    write(service, "고객.md", "## 업무\n고객 포털 상담 [[근거]]")
    write(service, "근거.md", "## 업무\n인터뷰에서 계약 상태 조회가 어렵다는 의견")
    service.indexer.reconcile()
    plain = service.search.ask("포털 상담", mode="lexical")
    result = service.search.ask("포털 상담", mode="hybrid")
    assert not any(e["path"] == "근거.md" for e in plain["evidence"])
    assert any(e["path"] == "근거.md" and "관계" in e["routes"] for e in result["evidence"])
    assert result["paths"]


def test_current_source_verification_detects_unobserved_change(service):
    file = write(service, "노트.md", "업무 비공개 계약 근거")
    service.indexer.reconcile()
    file.write_text("변경된 다른 내용")
    result = service.search.ask("계약 근거", mode="lexical")
    assert not result["evidence"]
    assert service.store.rows("SELECT state FROM notes")[0]["state"] == "pending"


def test_bad_generation_citations_fall_back_to_sources(service, monkeypatch):
    write(service, "노트.md", "업무 결제 검토")
    service.indexer.reconcile()
    service.generator.enabled = True
    monkeypatch.setattr(
        service.generator,
        "request",
        lambda *args: {"sentences": [{"text": "근거 없는 사실", "citations": ["S999999"]}]},
    )
    result = service.search.ask("결제 검토")
    assert not result["generated"]
    assert all("근거 없는 사실" not in s["text"] for s in result["sentences"])
    with pytest.raises(ValueError):
        Search.validate_sentences({"sentences": [{"text": "인용 없음", "citations": []}]}, [])


def test_candidate_review_and_source_change_invalidate_relationship(service, monkeypatch):
    file = write(service, "노트.md", "## 투자\nA기업 매수는 검토만 했다.")
    service.indexer.reconcile()
    run = service.search.ask("A기업 매수", mode="lexical", generate=False)
    source = next(e for e in run["evidence"] if "검토만" in e["text"])
    service.generator.enabled = True
    monkeypatch.setattr(
        service.generator,
        "request",
        lambda *args: {
            "candidates": [
                {
                    "citation": source["citation"],
                    "kind": "Activity",
                    "topic": "A기업",
                    "quote": "A기업 매수는 검토만 했다.",
                    "event_date": "2026-09-24",
                    "activity_state": "planned",
                }
            ]
        },
    )
    extracted = service.search.extract(run["id"])
    cid = extracted["created"][0]
    assert service.store.rows("SELECT event_date FROM candidates")[0]["event_date"] is None
    service.ontology.refresh()
    assert "A기업" not in service.ontology.graph.serialize(format="turtle")
    service.review(cid, True)
    assert "A기업" in service.ontology.graph.serialize(format="turtle")
    file.write_text("## 투자\nA기업 기록 수정")
    service.indexer.reconcile()
    service.ontology.refresh()
    assert not service.store.rows("SELECT * FROM candidates")


def test_model_configuration_requires_separate_external_permission():
    validate_endpoint("http://127.0.0.1:11434/v1", False)
    with pytest.raises(ValueError):
        validate_endpoint("https://example.com/v1", False)
    with pytest.raises(ValueError):
        validate_endpoint("http://example.com/v1", True)
    validate_endpoint("https://example.com/v1", True)


def test_relative_period_is_record_date_only():
    p = plan("지난주 업무를 요약해줘")
    assert p["domain"] == "work" and p["start"] and p["end"]
    assert p["date_basis"] == "기록일"


def test_unrelated_edit_preserves_confirmed_relationship(service):
    file = write(service, "노트.md", "## 투자\n\nA기업 매수 검토\n\n## 개인\n\n산책했다")
    service.indexer.reconcile()
    source = service.store.rows("SELECT * FROM sections WHERE text='A기업 매수 검토'")[0]
    with service.store.transaction() as db:
        db.execute(
            "INSERT INTO candidates(section_id,revision,kind,topic,quote,activity_state,status) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                source["id"],
                source["revision"],
                "Claim",
                "A기업",
                source["text"],
                "unknown",
                "accepted",
            ),
        )
    file.write_text(file.read_text().replace("산책했다", "오래 산책했다"))
    service.indexer.reconcile()
    candidate = service.store.rows("SELECT * FROM candidates")[0]
    assert candidate["status"] == "accepted"
    assert candidate["revision"] == 2


def test_frontmatter_topics_and_external_source_provenance(service):
    write(service, "노트.md", "---\nproject: 고객 포털\n---\n근거 [자료](https://example.com)")
    service.indexer.reconcile()
    service.ontology.refresh()
    graph = service.ontology.graph.serialize(format="turtle")
    assert "고객 포털" in graph and "frontmatter" in graph
    assert "verified false" in graph and "https://example.com" in graph
