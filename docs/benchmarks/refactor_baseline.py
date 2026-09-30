"""Isolated architecture timing: synthetic notes, fake embeddings, no network."""

import cProfile
import io
import json
import platform
import pstats
import statistics
import sys
import tempfile
import time
from pathlib import Path

from app.config import Config
from app.graph_view import graph_view
from app.service import Service
from tests.conftest import FakeEmbedder


def measure(work, repeats=3):
    elapsed = []
    for _ in range(repeats):
        start = time.perf_counter()
        work()
        elapsed.append(round((time.perf_counter() - start) * 1000, 3))
    return {"median_ms": statistics.median(elapsed), "samples_ms": elapsed}


def benchmark(count):
    with tempfile.TemporaryDirectory(prefix="obsi-architecture-") as temp:
        base = Path(temp).resolve()
        vault = base / "vault"
        vault.mkdir()
        for index in range(count):
            (vault / f"note-{index:05d}.md").write_text(
                "---\ndomain: work\ndate: 2026-09-01\n"
                f"topics: [프로젝트{index % 20}]\n---\n"
                f"# 기록 {index}\n\n## 업무\n"
                f"프로젝트 진행 검토 기록 {index}. 담당자와 일정 및 요구사항을 확인했다.\n\n"
                f"연결된 문서 [[note-{(index + 1) % count:05d}#업무]]의 근거를 검토한다.\n",
                encoding="utf-8",
            )
        config = Config(
            data_dir=base / "data",
            embedding_provider="local",
            embedding_dim=384,
            generation_url="",
            generation_model="",
            external_generation=False,
            external_embedding=False,
            external_suggestions=False,
        )
        svc = Service(config, FakeEmbedder())
        svc.store.put("vault", str(vault))
        svc.store.put("excludes", [])
        result = {"notes": count}
        try:
            result["initial_index"] = measure(svc.indexer.reconcile, 1)
            result["sections"] = svc.store.rows("SELECT count(*) n FROM sections")[0]["n"]
            assert result["sections"] == count * 4, result["sections"]
            assert (
                svc.store.rows("SELECT count(*) n FROM notes WHERE state='ready'")[0]["n"] == count
            )
            result["unchanged_scan"] = measure(svc.indexer.reconcile)
            result["unchanged_metrics"] = dict(svc.indexer.metrics)

            def rebuild():
                svc.ontology.version = -1
                svc.ontology.refresh()

            result["rdf_rebuild_shacl"] = measure(rebuild)
            result["triples"] = len(svc.ontology.graph)
            result["warm_graph_view"] = measure(
                lambda: graph_view(svc.ontology, vault, query="프로젝트")
            )
            spec = {"domain": "all", "start": None, "end": None, "intent": "search"}
            for mode in ("lexical", "semantic", "hybrid"):
                result[f"retrieve_{mode}"] = measure(
                    lambda mode=mode: svc.search.retrieve("프로젝트 진행 검토", spec, mode)
                )
            result["ask_no_llm"] = measure(
                lambda: svc.search.ask("프로젝트 진행 검토", generate=False)
            )
            path = vault / "note-00000.md"
            path.write_text(path.read_text() + "\n추가 업무 결정 기록.\n")
            svc.indexer.invalidate([path.name])
            result["one_note_index"] = measure(lambda: svc.indexer.reconcile(paths={path.name}), 1)
            result["one_note_rdf_refresh"] = measure(svc.ontology.refresh, 1)
            profiler = cProfile.Profile()
            profiler.runcall(rebuild)
            stream = io.StringIO()
            pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(
                12
            )
            result["rdf_profile"] = stream.getvalue()
            profiler = cProfile.Profile()
            profiler.runcall(svc.search.retrieve, "프로젝트 진행 검토", spec, "hybrid")
            stream = io.StringIO()
            pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(
                12
            )
            result["retrieval_profile"] = stream.getvalue()
        finally:
            svc.close()
        return result


def main():
    output = Path(sys.argv[1])
    payload = {
        "date": "2026-09-28",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "clock": "perf_counter wall-clock milliseconds",
        "method": (
            "100/500/1000 synthetic notes; one forward anchored link; 20 shared topics; "
            "384D FakeEmbedder; no watcher/server/ONNX/LLM; three sequential repetitions except "
            "cold index and single-note update; cProfile runs separately, excluded from timings"
        ),
        "results": [],
    }
    for count in (100, 500, 1000):
        result = benchmark(count)
        payload["results"].append(result)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {k: v for k, v in result.items() if not k.endswith("profile")}, ensure_ascii=False
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
