"""Persist bounded, source-backed question suggestions after vault settings change."""

import json
from itertools import zip_longest

from app.indexer import now


class Suggestions:
    def __init__(self, store, search, generator):
        self.store, self.search, self.generator = store, search, generator

    @property
    def model_enabled(self):
        return self.generator.enabled and (
            not self.generator.config.external_generation
            or self.generator.config.external_suggestions
        )

    def reset(self):
        self.store.put(
            "suggestions",
            {"state": "pending", "items": [], "message": "색인 완료 후 예상 질문을 만듭니다."},
        )

    def sample(self):
        rows = list(self.search.eligible({"domain": "all", "start": None, "end": None}).values())
        rows.sort(key=lambda row: (row["path"], row["start"]))
        rows.sort(key=lambda row: row["record_date"] or "", reverse=True)
        buckets = {key: [] for key in ("work", "investment", "personal", "all")}
        seen = set()
        for row in rows:
            if row["note_id"] in seen or not row["text"].strip():
                continue
            seen.add(row["note_id"])
            domains = json.loads(row["domains"])
            domain = next((key for key in buckets if key in domains), "all")
            if len(buckets[domain]) < 24:
                buckets[domain].append({**row, "citation": f"S{row['id']}"})
        mixed = [row for group in zip_longest(*buckets.values()) for row in group if row]
        return self.search.verify(mixed[:24])[0]

    @staticmethod
    def card(title, question, sources):
        common = set.intersection(*(set(json.loads(row["domains"])) for row in sources))
        domain = next((key for key in ("work", "investment", "personal") if key in common), "all")
        return {
            "title": title,
            "question": question,
            "domain": domain,
            "sources": [
                {"path": row["path"], "heading": row["heading"], "start": row["start"]}
                for row in sources
            ],
        }

    def fallback(self, evidence):
        return [
            self.card(
                row["title"][:80],
                f"{row['title'][:120]}에 기록한 핵심 내용과 판단 근거를 정리해줘",
                [row],
            )
            for row in evidence[:6]
        ]

    def validate(self, output, evidence, excerpts):
        proposed = output.get("questions") if isinstance(output, dict) else None
        if not isinstance(proposed, list) or not 1 <= len(proposed) <= 6:
            raise ValueError("예상 질문 형식이 올바르지 않습니다.")
        by_id = {row["citation"]: row for row in evidence}
        texts = {
            row["citation"]: "\n".join(str(row[key]) for key in ("title", "heading", "text"))
            for row in excerpts
        }
        items, seen = [], set()
        for item in proposed:
            if not isinstance(item, dict):
                raise ValueError("예상 질문 형식이 올바르지 않습니다.")
            for key, limit in (("title", 80), ("question", 300), ("topic", 120)):
                if not isinstance(item.get(key), str) or not 1 <= len(item[key].strip()) <= limit:
                    raise ValueError("예상 질문 길이가 올바르지 않습니다.")
            citations = item.get("citations")
            if (
                not isinstance(citations, list)
                or not citations
                or any(not isinstance(cid, str) or cid not in by_id for cid in citations)
            ):
                raise ValueError("예상 질문의 근거를 확인할 수 없습니다.")
            topic, question = item["topic"].strip(), item["question"].strip()
            if topic not in question or not any(topic in texts[cid] for cid in citations):
                raise ValueError("질문 대상이 원문에 없습니다.")
            if question in seen:
                continue
            seen.add(question)
            items.append(
                self.card(
                    item["title"].strip(),
                    question,
                    [by_id[cid] for cid in dict.fromkeys(citations)],
                )
            )
        return items

    def refresh(self):
        cached = self.store.get("suggestions", {})
        if cached.get("state") != "pending":
            return
        self.store.put(
            "suggestions",
            {
                "state": "generating",
                "items": [],
                "message": "현재 볼트에서 예상 질문을 만들고 있습니다.",
            },
        )
        evidence = self.sample()
        items, state = self.fallback(evidence), "fallback"
        message = (
            "노트 제목으로 만든 기본 질문입니다. 모델 생성은 모델 설정과 전송 허용이 필요합니다."
        )
        if evidence and self.model_enabled:
            excerpts = [
                {
                    "citation": row["citation"],
                    "title": row["title"][:120],
                    "heading": row["heading"][:120],
                    "record_date": row["record_date"],
                    "text": row["text"][:600],
                }
                for row in evidence
            ]
            try:
                output = self.generator.request(
                    "이 볼트에서 물어볼 만한 예상 질문을 최대 6개 제안해줘.",
                    excerpts,
                    task="suggestions",
                )
                items = self.validate(output, evidence, excerpts)
                state, message = (
                    "ready",
                    "현재 볼트의 일부 노트를 바탕으로 만든 질문입니다. "
                    "답변 가능 여부는 검색 후 확인합니다.",
                )
            except Exception:
                message = "예상 질문 생성에 실패해 노트 제목으로 기본 질문을 만들었습니다."
        verified, changed = self.search.verify(evidence)
        if changed:
            items, state = self.fallback(verified), "fallback"
            message = "생성 중 바뀐 노트를 제외하고 기본 질문을 만들었습니다."
        if not verified:
            items, state = [], "empty"
            message = (
                "예상 질문을 만들 수 있는 노트 본문이 없습니다. 제외 규칙과 색인 상태를 확인하세요."
            )
        self.store.put(
            "suggestions",
            {
                "state": state,
                "items": items,
                "message": message,
                "generated_at": now(),
                "sampled_notes": len(verified),
                "model_enabled": self.model_enabled,
            },
        )
