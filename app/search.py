"""Bounded hybrid retrieval, live source verification and immutable evidence snapshots."""

import json
import re
from datetime import date, timedelta
from urllib.parse import quote, urlencode

from app.files import excluded, read_stable
from app.markdown import digest, iso_date, search_tokens
from app.query_contracts import AnswerOutput

_QUERY_VECTOR = object()

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

    def scope(self, spec):
        clauses = [
            "n.state='ready'",
            "n.revision=s.revision",
            "searchable_body(s.text)",
        ]
        params = []
        if spec["domain"] != "all":
            clauses.append("EXISTS(SELECT 1 FROM json_each(s.domains) WHERE value=?)")
            params.append(spec["domain"])
        for key, op in (("start", ">="), ("end", "<=")):
            if spec[key]:
                clauses.append(f"n.record_date {op} ?")
                params.append(spec[key])
        return " AND ".join(clauses), params

    def eligible(self, spec, ids=None, metadata_only=False):
        scope, params = self.scope(spec)
        columns = (
            "s.id,n.record_date"
            if metadata_only
            else ("s.*,n.path,n.title,n.hash note_hash,n.record_date,n.date_source,n.aliases")
        )
        sql = f"SELECT {columns} FROM sections s JOIN notes n ON n.id=s.note_id WHERE {scope}"
        if ids is not None:
            sql += " AND s.id IN (SELECT value FROM json_each(?))"
            params.append(json.dumps(list(ids)))
        return {r["id"]: r for r in self.store.rows(sql, params)}

    def query_vector(self, question, mode):
        if mode == "lexical" or not self.embedder.ready:
            return None, None
        try:
            return self.embedder.embed([question], query=True)[0], None
        except Exception:
            return None, "Semantic search failed. Keyword search was used instead."

    def source(self, row, routes):
        return {
            **row,
            "citation": f"S{row['id']}",
            "routes": routes,
            "uri": "obsidian://open?"
            + urlencode(
                {
                    "path": str(self.indexer.root / row["path"])
                    + ("#" + row["heading"] if row["heading"] else "")
                },
                quote_via=quote,
            ),
        }

    def supplement(self, evidence, spec):
        if not evidence:
            return evidence
        scope, params = self.scope(spec)
        rows = self.store.rows(
            f"SELECT s.id FROM sections s JOIN notes n ON n.id=s.note_id WHERE {scope} "
            "AND s.note_id IN (SELECT value FROM json_each(?)) "
            "AND s.id NOT IN (SELECT value FROM json_each(?)) ORDER BY n.path,s.start LIMIT 12",
            (
                *params,
                json.dumps(list(dict.fromkeys(e["note_id"] for e in evidence))),
                json.dumps([e["id"] for e in evidence]),
            ),
        )
        additional = self.eligible(spec, [r["id"] for r in rows])
        return evidence + [
            self.source(additional[r["id"]], ["같은 노트 맥락"])
            for r in rows
            if r["id"] in additional
        ]

    def retrieve(self, question, spec, mode="hybrid", limit=12, query_vector=_QUERY_VECTOR):
        eligible = self.eligible(spec, metadata_only=True)
        scores, routes, paths, warnings = {}, {}, [], []

        def rank(ids, route, weight=1.0):
            for position, sid in enumerate(ids, 1):
                if sid in eligible:
                    scores[sid] = scores.get(sid, 0) + weight / (60 + position)
                    routes.setdefault(sid, []).append(route)

        terms = fts_query(question)
        if terms:
            scope, params = self.scope(spec)
            hits = self.store.rows(
                "SELECT words.rowid FROM words JOIN sections s ON s.id=words.rowid "
                f"JOIN notes n ON n.id=s.note_id WHERE words MATCH ? AND {scope} "
                "ORDER BY rank LIMIT 60",
                (terms, *params),
            )
            rank([h["rowid"] for h in hits if h["rowid"] in eligible][:60], "단어")
        if mode != "lexical" and self.embedder.ready and eligible:
            try:
                vector = (
                    self.embedder.embed([question], query=True)[0]
                    if query_vector is _QUERY_VECTOR
                    else query_vector
                )
                if vector is None:
                    raise ValueError("No semantic search vector is available.")
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
                    "Semantic search is unavailable; "
                    f"keyword search was used instead ({type(exc).__name__})."
                )
        elif mode != "lexical":
            warnings.append("Prepare the local embedding model to enable semantic search.")
        if mode == "hybrid" and scores:
            seeds = sorted(scores, key=scores.get, reverse=True)[:5]
            paths = [p for p in self.ontology.neighbors(seeds) if p["section_id"] in eligible]
            rank(list(dict.fromkeys(p["section_id"] for p in paths)), "관계", 0.15)
        ordered = sorted(scores, key=scores.get, reverse=True)[:limit]
        if spec["intent"] == "compare":
            ordered.sort(key=lambda sid: eligible[sid]["record_date"] or "9999")
        rows = self.eligible(spec, ordered)
        evidence = [self.source(rows[sid], routes[sid]) for sid in ordered if sid in rows]
        return evidence, paths, warnings, len(scores)

    def verify(self, evidence):
        verified, unavailable, read_cache = [], [], {}
        for item in evidence:
            try:
                if not self.indexer.root or excluded(item["path"], self.store.get("excludes", [])):
                    raise ValueError("Excluded evidence")
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
                    raise ValueError("Updating index")
                span = "\n".join(text.splitlines()[item["start"] - 1 : item["end"]])
                if digest(text) != item["note_hash"] or digest(span) != item["hash"]:
                    self.indexer.invalidate([item["path"]])
                    raise ValueError("Source text changed")
                verified.append(item)
            except (OSError, ValueError, UnicodeError):
                if self.store.rows("SELECT id FROM notes WHERE id=?", (item["note_id"],)):
                    self.indexer.invalidate([item["path"]])
                unavailable.append(item["path"])
        return verified, sorted(set(unavailable))

    def ask(self, question, domain="all", start=None, end=None, mode="hybrid", generate=True):
        return self.jobs.ask(
            {
                "question": question,
                "domain": domain,
                "start": start,
                "end": end,
                "mode": mode,
                "generate": generate,
            }
        )

    @staticmethod
    def model_evidence(evidence):
        return [
            {k: item[k] for k in ("citation", "text", "record_date", "heading")}
            for item in evidence
        ]

    @staticmethod
    def validate_sentences(output, evidence):
        sentences = AnswerOutput.model_validate(output).model_dump()["sentences"]
        allowed = {item["citation"] for item in evidence}
        if any(not set(item["citations"]).issubset(allowed) for item in sentences):
            raise ValueError("A generated sentence has an unverifiable citation.")
        return sentences

    def extract(self, run_id):
        service = self.jobs.service
        with service.operation, self.store.lock:
            epoch = self.store.get("vault_epoch")
            rows = self.store.rows("SELECT payload FROM runs WHERE id=?", (run_id,))
            if not rows:
                raise ValueError("Question run not found.")
            run = json.loads(rows[0]["payload"])
            evidence, _ = self.verify(run["evidence"])
        output = self.generator.request(run["question"], self.model_evidence(evidence), "extract")
        with service.operation, self.store.lock:
            if not self.store.rows("SELECT id FROM runs WHERE id=?", (run_id,)):
                raise ValueError("The saved answer was deleted. Ask again for new suggestions.")
            if epoch != self.store.get("vault_epoch"):
                raise ValueError(
                    "The vault settings changed, so the relationship suggestions were discarded."
                )
            evidence, _ = self.verify(evidence)
            return self._publish_candidates(output, evidence)

    def _publish_candidates(self, output, evidence):
        by_id = {e["citation"]: e for e in evidence}
        if not isinstance(output, dict) or not isinstance(output.get("candidates"), list):
            raise ValueError("Invalid relationship candidate format.")
        accepted = []
        for item in output["candidates"][:30]:
            if not isinstance(item, dict) or any(
                not isinstance(item.get(k), str) for k in ("citation", "quote", "topic", "kind")
            ):
                continue
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
