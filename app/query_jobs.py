"""Bounded question workers; model/user waits never hold the indexing operation lock."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.answers import prepare, publish, verify_snapshot
from app.conflicts import overlay, rule_candidates, validate_candidates
from app.conversations import context, retrieval_question
from app.indexer import now
from app.job_store import TERMINAL, JobStore
from app.query_contracts import Question


class QueryJobs:
    def __init__(self, service):
        self.service = service
        self.repo = JobStore(service.store)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="obsi-question")
        self.futures = {}
        self.closed = False
        self.streams = threading.BoundedSemaphore(8)

    def submit(self, request, request_id=None):
        request = Question.model_validate(request).model_dump()
        with self.service.operation:
            if self.closed or not self.service.indexer.root:
                raise ValueError("Connect a vault first.")
            prior = context(
                self.service.store,
                request.get("conversation_id"),
                self.service.store.get("vault_epoch"),
            )
            job, created = self.repo.create(request_id or uuid.uuid4().hex, request, prior)
            if created:
                self._schedule(job["id"])
            return self.repo.public(job)

    def _schedule(self, jid):
        # Also bound cancelled requests whose HTTP call has not returned yet.
        self.futures = {key: future for key, future in self.futures.items() if not future.done()}
        if len(self.futures) >= 8:
            job = self.repo.get(jid)
            self.repo.save(
                job, "failed", "Too many requests are running. Please try again shortly."
            )
            return
        self.futures[jid] = self.pool.submit(self._run, jid)

    def get(self, jid, after=None):
        with self.service.operation:
            if after is not None:
                rows = self.service.store.rows(
                    "SELECT id,state,seq FROM query_jobs WHERE id=?", (jid,)
                )
                if not rows:
                    raise KeyError(jid)
                if rows[0]["seq"] == after:
                    return {**rows[0], "unchanged": True}
            return self.repo.public(self.repo.get(jid))

    def recent(self):
        return self.service.store.rows(
            "SELECT id,state,created_at FROM query_jobs WHERE state NOT IN "
            "('completed','cancelled','failed','interrupted','stale') "
            "ORDER BY created_at DESC LIMIT 8"
        )

    def _active(self, jid):
        job = self.repo.get(jid)
        if self.closed or job["state"] in TERMINAL:
            return None
        if job["epoch"] != self.service.store.get("vault_epoch"):
            self.repo.save(
                job, "cancelled", "The task was cancelled because the vault settings changed."
            )
            return None
        return job

    def cancel(self, jid):
        with self.service.operation:
            job = self.repo.get(jid)
            if job["state"] not in TERMINAL:
                self.repo.save(
                    job, "cancelled", "Question cancelled. Any late response will be discarded."
                )
            return self.repo.public(job)

    def invalidate(self):
        # Caller owns operation. The epoch also guards jobs deleted by clear().
        self.service.store.put("vault_epoch", uuid.uuid4().hex)
        for row in self.recent():
            self.cancel(row["id"])

    def clarify(self, jid, reply):
        with self.service.operation, self.service.store.lock:
            job = self.repo.get(jid)
            payload = job["payload"]
            previous = next(
                (r for r in payload["clarifications"] if r["request_id"] == reply["request_id"]),
                None,
            )
            if previous:
                if any(previous[key] != value for key, value in reply.items()):
                    raise ValueError("A request ID cannot be reused for a different confirmation.")
                return self.repo.public(job)
            if not self._active(jid) or job["state"] != "awaiting_user":
                raise ValueError("This task is not awaiting a response.")
            question = payload["confirmation"]
            if reply["question_id"] != question["id"] or reply["version"] != question["version"]:
                raise ValueError(
                    "The clarification question has changed. Review the latest question."
                )
            if verify_snapshot(self.service.search, payload["snapshot"]):
                payload["confirmation"] = None
                payload["clarifications"] = []
                payload["conflicts"] = []
                self.repo.save(
                    job,
                    "stale",
                    "The evidence changed, so your earlier choice was not applied. "
                    "Please ask again.",
                )
                return self.repo.public(job)
            response = {
                **reply,
                "created_at": now(),
                "source": "user_clarification",
                "scope": "this_answer",
                "conflict": question,
            }
            payload["clarifications"].append(response)
            for conflict in payload["conflicts"]:
                if conflict["id"] == question["id"]:
                    conflict["status"] = "deferred" if reply["choice"] == "defer" else "clarified"
            if reply["choice"] == "defer":
                # Partial-answer action defers remaining issues as well; never choose a winner.
                for conflict in payload["conflicts"]:
                    if conflict["status"] == "unresolved":
                        conflict["status"] = "deferred"
            payload["confirmation"] = None
            self.repo.save(
                job,
                "queued",
                "Your confirmation was saved. Rechecking the evidence before continuing.",
            )
            self._schedule(jid)
            return self.repo.public(job)

    def _run(self, jid):
        try:
            self._execute(jid)
        except KeyError:
            pass  # Explicit clear erased this job while a model call was in flight.
        except Exception:
            with self.service.operation:
                try:
                    job = self._active(jid)
                    if job:
                        self.repo.save(
                            job, "failed", "The task could not be completed. Please ask again."
                        )
                except KeyError:
                    pass

    def _execute(self, jid):
        svc, search = self.service, self.service.search
        with svc.operation:
            job = self._active(jid)
            if not job:
                return
            request = job["payload"]["request"]
            retrieval = retrieval_question(request["question"], job["payload"].get("conversation"))
            fresh = "snapshot" not in job["payload"]
            self.repo.save(
                job,
                "retrieving" if fresh else "verifying",
                "Searching for evidence within the question's scope."
                if fresh
                else "Revalidating the confirmed evidence.",
            )
        if fresh:
            vector, vector_warning = search.query_vector(retrieval, request["mode"])
            with svc.operation, svc.store.lock:
                job = self._active(jid)
                if not job:
                    return
                snapshot = prepare(search, request, vector, retrieval)
                if vector_warning:
                    snapshot["warnings"].append(vector_warning)
                job["payload"]["snapshot"] = snapshot
                self.repo.save(
                    job,
                    "verifying",
                    "Checking the original text and version of the retrieved passages.",
                    candidate_count=snapshot["candidate_count"],
                )
                verify_snapshot(search, snapshot)
                self.repo.save(
                    job,
                    "checking_conflicts",
                    "Comparing records for differences in dates, conditions, and claims.",
                    verified_count=len(snapshot["evidence"]),
                )
            conflicts, warning = self._check_conflicts(request, snapshot["evidence"])
            with svc.operation, svc.store.lock:
                job = self._active(jid)
                if not job:
                    return
                job["payload"]["conflicts"] = conflicts
                if warning:
                    job["payload"]["snapshot"]["warnings"].append(warning)
                self.repo.save(
                    job,
                    "checking_conflicts",
                    "Reviewing the evidence comparison.",
                    conflict_count=len(conflicts),
                )
        with svc.operation, svc.store.lock:
            job = self._active(jid)
            if not job:
                return
            payload, snapshot = job["payload"], job["payload"]["snapshot"]
            if verify_snapshot(search, snapshot):
                payload["conflicts"], payload["clarifications"] = [], []
                self.repo.save(
                    job,
                    "stale",
                    "The evidence changed during processing. Ask again using the latest index.",
                )
                return
            snapshot["graph"] = overlay(snapshot["graph"], payload["conflicts"])
            remaining = next((c for c in payload["conflicts"] if c["status"] == "unresolved"), None)
            if remaining:
                payload["confirmation"] = remaining
                self.repo.save(
                    job,
                    "awaiting_user",
                    "Please confirm how to interpret these two source passages.",
                )
                return
            self.repo.save(
                job,
                "generating",
                "Writing an answer using verified evidence and your confirmations."
                if request["generate"] and svc.generator.enabled
                else "Preparing the retrieved source evidence and knowledge graph.",
            )
        sentences, generated, warning = self._generate(request, snapshot["evidence"], payload)
        with svc.operation, svc.store.lock:
            job = self._active(jid)
            if not job:
                return
            payload, snapshot = job["payload"], job["payload"]["snapshot"]
            self.repo.save(
                job, "finalizing", "Rechecking sentence citations and the current source versions."
            )
            changed = verify_snapshot(search, snapshot)
            if changed:
                if payload["clarifications"] or payload["conflicts"]:
                    payload["clarifications"], payload["conflicts"] = [], []
                    self.repo.save(
                        job,
                        "stale",
                        "The verified evidence changed, so the answer was not published.",
                    )
                    return
                sentences, generated = [], False
            if warning:
                snapshot["warnings"].append(warning)
            # Publish answer and status atomically under the shared cancellation boundary.
            with svc.store.transaction():
                result = publish(
                    search,
                    request,
                    snapshot,
                    sentences,
                    generated,
                    payload["conflicts"],
                    payload["clarifications"],
                    request.get("conversation_id") or jid,
                    job["epoch"],
                )
                payload["run_id"] = result["id"]
                self.repo.save(
                    job,
                    "completed",
                    "Answer and evidence saved."
                    if result["generated"]
                    else "Source evidence and knowledge graph saved.",
                    verified_count=len(result["evidence"]),
                )

    def _check_conflicts(self, request, evidence):
        rules = validate_candidates({"conflicts": rule_candidates(evidence)}, evidence)
        if rules or len(evidence) < 2:
            return rules, None
        generator = self.service.generator
        if not request["generate"] or not generator.enabled:
            return (
                [],
                "Only explicit dates were compared using rules. "
                "Semantic conflict checks require a generation model.",
            )
        try:
            output = generator.request(
                request["question"], self.service.search.model_evidence(evidence), "conflicts"
            )
            return validate_candidates(output, evidence), None
        except Exception:
            return (
                [],
                "The semantic conflict check failed. This does not mean there are no conflicts.",
            )

    def _generate(self, request, evidence, payload):
        if request["generate"] and not self.service.generator.enabled:
            return [], False, self.service.generator.unavailable_reason
        if (
            not request["generate"]
            or not evidence
            or any(c["status"] == "deferred" for c in payload["conflicts"])
        ):
            return [], False, None
        try:
            args = [request["question"], self.service.search.model_evidence(evidence)]
            if payload["clarifications"] or payload.get("conversation"):
                args += [
                    "answer",
                    {
                        "clarifications": payload["clarifications"],
                        "conversation": payload.get("conversation", []),
                    },
                ]
            output = self.service.generator.request(*args)
            return self.service.search.validate_sentences(output, evidence), True, None
        except Exception:
            return (
                [],
                False,
                self.service.generator.unavailable_reason
                if not self.service.generator.enabled
                else "Answer generation or citation validation failed. "
                "Showing source evidence instead.",
            )

    def ask(self, request):
        job = self.submit(request)
        future = self.futures.get(job["id"])
        if future:
            future.result()
        result = self.get(job["id"])
        # Compatibility clients must handle awaiting_user explicitly; no automatic clarification.
        return result["result"] or result

    def close(self):
        with self.service.operation:
            self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)
