"""Persist bounded, source-backed question suggestions after vault settings change."""

import json
from itertools import zip_longest

from app.indexer import now


class Suggestions:
    def __init__(self, store, search, generator, operation):
        self.store, self.search, self.generator = store, search, generator
        self.operation = operation

    @property
    def model_enabled(self):
        return self.generator.enabled and (
            not self.generator.config.external_generation
            or self.generator.config.external_suggestions
        )

    def reset(self):
        self.store.put(
            "suggestions",
            {
                "state": "pending",
                "items": [],
                "message": "Suggested questions will be prepared after indexing.",
            },
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
                f"Summarize the key points and reasoning recorded in {row['title'][:120]}.",
                [row],
            )
            for row in evidence[:6]
        ]

    def validate(self, output, evidence, excerpts):
        proposed = output.get("questions") if isinstance(output, dict) else None
        if not isinstance(proposed, list) or not 1 <= len(proposed) <= 6:
            raise ValueError("Invalid suggested question format.")
        by_id = {row["citation"]: row for row in evidence}
        texts = {
            row["citation"]: "\n".join(str(row[key]) for key in ("title", "heading", "text"))
            for row in excerpts
        }
        items, seen = [], set()
        for item in proposed:
            if not isinstance(item, dict):
                raise ValueError("Invalid suggested question format.")
            for key, limit in (("title", 80), ("question", 300), ("topic", 120)):
                if not isinstance(item.get(key), str) or not 1 <= len(item[key].strip()) <= limit:
                    raise ValueError("Invalid suggested question length.")
            citations = item.get("citations")
            if (
                not isinstance(citations, list)
                or not citations
                or any(not isinstance(cid, str) or cid not in by_id for cid in citations)
            ):
                raise ValueError("The suggested question's evidence could not be verified.")
            topic, question = item["topic"].strip(), item["question"].strip()
            if topic not in question or not any(topic in texts[cid] for cid in citations):
                raise ValueError("The question's subject is not present in the source text.")
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
        with self.operation, self.store.lock:
            epoch = self.store.get("vault_epoch")
            cached = self.store.get("suggestions", {})
            if cached.get("state") != "pending":
                return
            self.store.put(
                "suggestions",
                {
                    "state": "generating",
                    "items": [],
                    "message": "Preparing suggested questions from the current vault.",
                },
            )
            evidence = self.sample()
        items, state, message = self._generate(evidence)
        with self.operation, self.store.lock:
            if epoch != self.store.get("vault_epoch"):
                return
            verified, changed = self.search.verify(evidence)
            if changed:
                items, state = self.fallback(verified), "fallback"
                message = (
                    "Basic questions were prepared, excluding notes that changed during generation."
                )
            if not verified:
                items, state = [], "empty"
                message = (
                    "No source text is available for suggested questions. "
                    "Check exclusions and index status."
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

    def _generate(self, evidence):
        items, state = self.fallback(evidence), "fallback"
        message = (
            "Basic questions based on note titles. "
            "Generated suggestions require a model and transmission permission."
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
                    "Suggest up to 6 useful questions to ask about this vault.",
                    excerpts,
                    task="suggestions",
                )
                items = self.validate(output, evidence, excerpts)
                state, message = (
                    "ready",
                    "Questions based on a sample of notes in the current vault. "
                    "Whether they can be answered is checked after retrieval.",
                )
            except Exception:
                message = (
                    "Question generation failed. "
                    "Basic questions were prepared from note titles instead."
                )
        return items, state, message
