"""Reproducible retrieval evaluation on fictional notes; no remote model calls."""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ROOT, Config
from app.models import Embedder
from app.search import plan
from app.service import Service

CASES = [
    ("고객이 결제 상태를 찾기 어렵다고 말한 기록", "2026-09-21.md", "고객이 결제 상태"),
    ("프로젝트 B에서 대시보드를 뒤로 미룬 결정", "2026-09-24.md", "개발을 뒤로 미뤘다"),
    ("계약 상태 화면 초안을 작성한 기록", "2026-09-24.md", "초안을 작성했고"),
    ("결제 화면을 만들 예정이라고 한 계획", "2026-09-21.md", "작성할 예정이다"),
    ("내가 A기업을 긍정적으로 본 이유", "2026-09-21.md", "영업현금흐름이 개선"),
    ("A기업 매수 판단을 보류한 이유", "2026-09-24.md", "매수 판단을 보류"),
    ("주식 두 주를 판 가상 거래 기록", "2026-09-24.md", "2주를 주당 100원"),
    ("매수 전에 확인해야 하는 투자 원칙", "투자 원칙.md", "부채 상환 능력"),
    ("좋은 이야기만으로 수익성을 가정하지 않는다는 원칙", "투자 원칙.md", "좋은 이야기만으로"),
    ("판단이 바뀌면 무엇을 기록하나", "투자 원칙.md", "바뀐 이유와 확인할 자료"),
    ("다음 논의를 짧게 하기 위한 내 생각", "2026-09-21.md", "다음 논의가 짧아질"),
    ("담당자 없이 해결한 문의 비율", "프로젝트 B.md", "해결한 문의 비율"),
    ("고객 포털이라는 프로젝트의 목적", "프로젝트 B.md", "셀프서비스 포털"),
    ("에이기업의 분석 기록은 어디에 연결되나", "A기업.md", "가정을 재검토했다"),
    ("매수 검토가 실제 거래는 아니었던 기록", "2026-09-21.md", "실제 거래하지 않았다"),
    ("고객 인터뷰를 근거로 계약 조회를 먼저 제공", "2026-09-24.md", "고객 인터뷰가 근거"),
    ("매출이 돈으로 들어오는지 확인하지 못한 판단", "2026-09-24.md", "현금 유입으로 이어지는지"),
    ("만든 화면을 동료에게 검토 요청한 기록", "2026-09-24.md", "동료 검토를 요청"),
    ("결정 이유를 문서로 남기려는 생각", "2026-09-21.md", "문서에 결정 이유"),
    ("확인되지 않은 실적을 당시 가정이라고 표시한 기록", "2026-09-21.md", "검증된 실적이 아니다"),
]


def main():
    config = Config()
    embedder = Embedder(config)
    if not embedder.ready:
        raise SystemExit(
            "앱 설정에서 로컬 모델을 먼저 준비하세요. OBSI_DATA_DIR는 앱과 같아야 합니다."
        )
    results, detail = {}, []
    with tempfile.TemporaryDirectory(prefix="obsi-eval-") as directory:
        service = Service(Config(data_dir=Path(directory)), embedder)
        service.store.put("vault", str(ROOT / "examples/vault"))
        started = time.perf_counter()
        service.indexer.reconcile()
        service.ontology.refresh()
        initial = {**service.indexer.metrics, "total_seconds": time.perf_counter() - started}
        service.indexer.reconcile()
        unchanged = service.indexer.metrics.copy()
        for mode in ("lexical", "semantic", "hybrid"):
            hit, rr, precision, valid, total, elapsed = 0, 0, 0, 0, 0, 0
            for question, path, needle in CASES:
                started = time.perf_counter()
                evidence, _, warnings, _ = service.search.retrieve(
                    question, plan(question), mode, 5
                )
                elapsed += time.perf_counter() - started
                ranks = [
                    i + 1
                    for i, e in enumerate(evidence)
                    if e["path"] == path and needle in e["text"]
                ]
                hit += bool(ranks)
                rr += 1 / min(ranks) if ranks else 0
                precision += len(ranks) / max(len(evidence), 1)
                checked, _ = service.search.verify(evidence)
                valid += len(checked)
                total += len(evidence)
                detail.append(
                    {
                        "mode": mode,
                        "question": question,
                        "rank": min(ranks) if ranks else None,
                        "expected_path": path,
                        "warnings": warnings,
                    }
                )
            results[mode] = {
                "hit_at_5": hit / len(CASES),
                "mrr_at_5": rr / len(CASES),
                "strict_precision_at_5": precision / len(CASES),
                "source_verification": valid / max(total, 1),
                "mean_seconds": elapsed / len(CASES),
            }
        service.close()
    report = {
        "model": config.embedding_model,
        "model_fingerprint": embedder.key,
        "fixture_notes": 5,
        "questions": len(CASES),
        "metrics": results,
        "initial_index": initial,
        "unchanged_scan": unchanged,
        "cases": detail,
    }
    output = ROOT / "docs/evaluation.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps({"metrics": results, "unchanged_scan": unchanged}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
