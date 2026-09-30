"""Bounded hybrid retrieval, live source verification and immutable evidence snapshots."""

import json
import re
import uuid
from datetime import date, timedelta
from urllib.parse import quote, urlencode

from app.files import read_stable
from app.graph_view import graph_view
from app.indexer import now
from app.markdown import digest, iso_date, search_tokens

STOP = {
    "내",
    "내가",
    "나는",
    "어떤",
    "어떻게",
    "무엇",
    "무엇이었나",
    "왜",
    "좀",
    "알려줘",
    "정리",
    "요약",
    "해줘",
    "기록",
    "기록한",
    "대한",
    "관한",
    "이번",
    "지난주",
    "이번주",
    "주",
    "한",
    "것",
    "있나",
    "있어",
    "최근",
    "그리고",
    "관해",
    "했나",
}


def plan(question, domain="all", start=None, end=None):
    intent = next(
        (
            name
            for word, name in (
                ("비교", "compare"),
                ("달라", "compare"),
                ("연결", "connections"),
                ("몇", "count"),
                ("요약", "summarize"),
            )
            if word in question
        ),
        "find",
    )
    today = date.today()
    if not start and not end:
        if "지난주" in question or "지난 주" in question:
            start = today - timedelta(days=today.weekday() + 7)
            end = start + timedelta(days=6)
        elif "이번 주" in question or "이번주" in question:
            start, end = today - timedelta(days=today.weekday()), today
        elif "이번 달" in question or "이번달" in question:
            start, end = today.replace(day=1), today
        elif "오늘" in question:
            start = end = today
        elif "어제" in question:
            start = end = today - timedelta(days=1)
    if domain == "all":
        if "업무" in question:
            domain = "work"
        elif "투자" in question:
            domain = "investment"
    return {
        "intent": intent,
        "domain": domain,
        "start": str(start) if start else None,
        "end": str(end) if end else None,
        "date_basis": "기록일",
        "max_graph_hops": 2,
    }


def fts_query(question):
    words = re.findall(r"[\w]+", question.lower())
    selected = []
    for word in words:
        word = re.sub(r"(에서는|으로|에서|에게|대한|관한|에는|을|를|은|는|이|가|에|의)$", "", word)
        if len(word) >= 2 and word not in STOP:
            selected.extend(search_tokens(word).split())
    return " OR ".join('"' + token + '"' for token in dict.fromkeys(selected))


class Search:
    def __init__(self, store, embedder, ontology, generator, indexer):
        self.store, self.embedder, self.ontology = store, embedder, ontology
        self.generator, self.indexer = generator, indexer

    def eligible(self, spec):
        sql = (
            "SELECT s.*,n.path,n.title,n.hash note_hash,n.record_date,n.date_source,n.aliases "
            "FROM sections s JOIN notes n ON n.id=s.note_id "
            "WHERE n.state='ready' AND n.revision=s.revision"
        )
        params = []
        if spec["domain"] != "all":
            sql += " AND EXISTS(SELECT 1 FROM json_each(s.domains) WHERE value=?)"
            params.append(spec["domain"])
        for key, op in (("start", ">="), ("end", "<=")):
            if spec[key]:
                sql += f" AND n.record_date {op} ?"
                params.append(spec[key])
        rows = self.store.rows(sql, params)
        return {
            r["id"]: r
            for r in rows
            if not (
                r["text"].startswith("---\n")
                or re.fullmatch(r"(?:\s*#{1,6}\s+[^\n]+\n?)+", r["text"])
            )
        }

    def retrieve(self, question, spec, mode="hybrid", limit=12):
        eligible = self.eligible(spec)
        scores, routes, paths, warnings = {}, {}, [], []

        def rank(ids, route, weight=1.0):
            for position, sid in enumerate(ids, 1):
                if sid in eligible:
                    scores[sid] = scores.get(sid, 0) + weight / (60 + position)
                    routes.setdefault(sid, []).append(route)

        terms = fts_query(question)
        if terms:
            hits = self.store.rows(
                "SELECT rowid FROM words WHERE words MATCH ? ORDER BY rank", (terms,)
            )
            rank([h["rowid"] for h in hits if h["rowid"] in eligible][:60], "단어")
        if mode != "lexical" and self.embedder.ready and eligible:
            try:
                vector = self.embedder.embed([question], query=True)[0]
                # Filter before ranking: out-of-scope nearest neighbors cannot crowd out the scope.
                vector_ids = [
                    r["id"]
                    for r in self.store.rows(
                        "SELECT s.id FROM sections s JOIN notes n ON n.id=s.note_id "
                        "WHERE n.vector_state=?",
                        (self.embedder.key,),
                    )
                    if r["id"] in eligible
                ]
                hits = self.store.rows(
                    "SELECT v.rowid,vec_distance_cosine(v.embedding,?) distance FROM vectors v "
                    "WHERE v.rowid IN (SELECT value FROM json_each(?)) "
                    "ORDER BY distance LIMIT 60",
                    (vector, json.dumps(vector_ids)),
                )
                rank([h["rowid"] for h in hits if h["distance"] < 0.65], "의미", 0.5)
            except Exception as exc:
                warnings.append(
                    f"의미 검색을 사용할 수 없어 단어 검색을 사용했습니다 ({type(exc).__name__})."
                )
        elif mode != "lexical":
            warnings.append("로컬 임베딩 모델을 준비하면 의미 검색이 함께 동작합니다.")
        if mode == "hybrid" and scores:
            seeds = sorted(scores, key=scores.get, reverse=True)[:5]
            paths = [p for p in self.ontology.neighbors(seeds) if p["section_id"] in eligible]
            rank(list(dict.fromkeys(p["section_id"] for p in paths)), "관계", 0.15)
        ordered = sorted(scores, key=scores.get, reverse=True)[:limit]
        if spec["intent"] == "compare":
            ordered.sort(key=lambda sid: eligible[sid]["record_date"] or "9999")
        evidence = []
        for sid in ordered:
            row = eligible[sid]
            evidence.append(
                {
                    **row,
                    "citation": f"S{sid}",
                    "routes": routes[sid],
                    "uri": "obsidian://open?"
                    + urlencode(
                        {
                            "path": str(self.indexer.root / row["path"])
                            + ("#" + row["heading"] if row["heading"] else "")
                        },
                        quote_via=quote,
                    ),
                }
            )
        return evidence, paths, warnings, len(scores)

    def verify(self, evidence):
        verified, unavailable, read_cache = [], [], {}
        for item in evidence:
            try:
                if item["path"] not in read_cache:
                    read_cache[item["path"]] = read_stable(self.indexer.root, item["path"])[0]
                text = read_cache[item["path"]]
                rows = self.store.rows(
                    "SELECT hash,state,revision FROM notes WHERE id=?", (item["note_id"],)
                )
                if (
                    not rows
                    or rows[0]["state"] != "ready"
                    or rows[0]["revision"] != item["revision"]
                ):
                    raise ValueError("색인 갱신 중")
                span = "\n".join(text.splitlines()[item["start"] - 1 : item["end"]])
                if digest(text) != item["note_hash"] or digest(span) != item["hash"]:
                    self.indexer.invalidate([item["path"]])
                    raise ValueError("원문 변경")
                verified.append(item)
            except (OSError, ValueError, UnicodeError):
                if self.store.rows("SELECT id FROM notes WHERE id=?", (item["note_id"],)):
                    self.indexer.invalidate([item["path"]])
                unavailable.append(item["path"])
        return verified, sorted(set(unavailable))

    def ask(self, question, domain="all", start=None, end=None, mode="hybrid", generate=True):
        spec = plan(question, domain, start, end)
        evidence, paths, warnings, matches = self.retrieve(question, spec, mode)
        evidence, unavailable = self.verify(evidence)
        if unavailable:
            warnings.append(
                "변경 중이거나 읽을 수 없는 노트를 제외했습니다: " + ", ".join(unavailable)
            )
        sentences, generated = [], False
        if generate and self.generator.enabled and evidence:
            try:
                output = self.generator.request(question, self.model_evidence(evidence))
                sentences = self.validate_sentences(output, evidence)
                generated = True
            except Exception as exc:
                warnings.append(
                    f"답변 생성/인용 검증 실패로 원문 근거를 표시합니다 ({type(exc).__name__})."
                )
        verified, changed = self.verify(evidence)
        if changed:
            evidence, sentences, generated = verified, [], False
            warnings.append("답변 작성 중 변경된 근거를 제외했습니다.")
        if not sentences:
            sentences = [
                {"text": item["text"], "citations": [item["citation"]]} for item in evidence
            ]
        pending = self.store.rows("SELECT count(*) n FROM notes WHERE state!='ready'")[0]["n"]
        warnings.append("인용은 당시 기록입니다. 현재 사실·보유·실제 매매로 추정하지 않습니다.")
        if spec["start"] or spec["end"]:
            warnings.append("기간은 기록일 기준입니다. 날짜 미상 기록은 기간 검색에서 제외됩니다.")
        if spec["domain"] != "all":
            warnings.append(
                "영역은 제목·태그·명시 속성으로 분류합니다. 미분류 기록은 전체 범위에서 찾으세요."
            )
        if matches > len(evidence) or pending:
            warnings.append(
                f"검색 후보 {matches}개 중 검증된 {len(evidence)}개 문단을 표시합니다. "
                f"갱신·오류 노트 {pending}개. 기간 전체를 빠짐없이 요약한 결과는 아닙니다."
            )
        self.ontology.refresh()
        result = {
            "id": uuid.uuid4().hex,
            "created_at": now(),
            "question": question,
            "plan": spec,
            "mode": mode,
            "generated": generated,
            "sentences": sentences,
            "evidence": evidence,
            "paths": paths,
            "graph": graph_view(self.ontology, self.indexer.root, evidence=evidence),
            "warnings": warnings,
            "message": "기록에서 찾은 근거입니다."
            if evidence
            else "질문에 답할 근거가 부족합니다.",
            "validation": self.ontology.validation,
            "candidate_count": matches,
        }
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?)",
                (
                    result["id"],
                    result["created_at"],
                    question,
                    json.dumps(result, ensure_ascii=False),
                ),
            )
        return result

    @staticmethod
    def model_evidence(evidence):
        return [
            {k: item[k] for k in ("citation", "text", "record_date", "heading")}
            for item in evidence
        ]

    @staticmethod
    def validate_sentences(output, evidence):
        sentences = output.get("sentences")
        allowed = {item["citation"] for item in evidence}
        if not isinstance(sentences, list) or not sentences:
            raise ValueError("문장별 인용이 없습니다.")
        for item in sentences:
            if (
                not isinstance(item.get("text"), str)
                or not item.get("citations")
                or not set(item["citations"]).issubset(allowed)
            ):
                raise ValueError("출처를 확인할 수 없는 생성 문장입니다.")
        return sentences

    def extract(self, run_id):
        rows = self.store.rows("SELECT payload FROM runs WHERE id=?", (run_id,))
        if not rows:
            raise ValueError("질문 실행 기록이 없습니다.")
        run = json.loads(rows[0]["payload"])
        evidence, _ = self.verify(run["evidence"])
        output = self.generator.request(run["question"], self.model_evidence(evidence), "extract")
        evidence, _ = self.verify(evidence)
        by_id = {e["citation"]: e for e in evidence}
        accepted = []
        for item in output.get("candidates", [])[:30]:
            source = by_id.get(item.get("citation"))
            if (
                not source
                or item.get("kind") not in {"Claim", "Activity"}
                or not item.get("quote")
                or item["quote"] not in source["text"]
                or not item.get("topic")
                or item["topic"] not in source["text"]
            ):
                continue
            event_date = iso_date(item.get("event_date"))
            if event_date and event_date not in item["quote"]:
                event_date = None
            state = item.get("activity_state", "unknown")
            if state not in {"planned", "completed", "unknown"}:
                state = "unknown"
            with self.store.transaction() as db:
                cid = db.execute(
                    "INSERT INTO candidates(section_id,revision,kind,topic,quote,"
                    "event_date,activity_state) VALUES(?,?,?,?,?,?,?)",
                    (
                        source["id"],
                        source["revision"],
                        item["kind"],
                        item["topic"],
                        item["quote"],
                        event_date,
                        state,
                    ),
                ).lastrowid
            accepted.append(cid)
        return {"created": accepted}
