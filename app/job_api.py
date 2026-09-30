"""Question job endpoints and bounded SSE replay using the existing local session boundary."""

import asyncio
import json
import time

from fastapi import HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.job_store import TERMINAL
from app.query_contracts import ClarificationInput, JobInput


def install_job_routes(app, svc):
    def get(jid, after=None):
        try:
            return svc().jobs.get(jid, after)
        except KeyError as exc:
            raise HTTPException(404, "Question task not found.") from exc

    @app.post("/api/query-jobs", status_code=202)
    def submit(data: JobInput):
        values = data.model_dump()
        request_id = values.pop("request_id")
        try:
            return svc().jobs.submit(values, request_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/query-jobs")
    def recent():
        return svc().jobs.recent()

    @app.get("/api/query-jobs/{jid}")
    def current(jid: str, after: int | None = Query(default=None, ge=0)):
        return get(jid, after)

    @app.post("/api/query-jobs/{jid}/clarifications")
    def clarify(jid: str, data: ClarificationInput):
        get(jid)
        try:
            return svc().jobs.clarify(jid, data.model_dump())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/query-jobs/{jid}/cancel")
    def cancel(jid: str):
        get(jid)
        return svc().jobs.cancel(jid)

    @app.get("/api/query-jobs/{jid}/events")
    async def events(jid: str, request: Request, after: int = Query(default=0, ge=0)):
        jobs = svc().jobs
        # Async handlers must not wait on the threading operation lock on the event-loop thread.
        await asyncio.to_thread(get, jid)
        try:
            after = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError as exc:
            raise HTTPException(400, "Invalid event number.") from exc
        if not jobs.streams.acquire(blocking=False):
            raise HTTPException(429, "Too many progress connections are open.")

        async def stream():
            cursor, deadline = after, time.monotonic() + 30
            try:
                while not jobs.closed and time.monotonic() < deadline:
                    if await request.is_disconnected():
                        return
                    try:
                        state = await asyncio.to_thread(jobs.get, jid)
                    except KeyError:
                        return
                    for event in state["events"]:
                        if event["seq"] > cursor:
                            cursor = event["seq"]
                            yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if state["state"] in TERMINAL or state["state"] == "awaiting_user":
                        return
                    yield ": keepalive\n\n"
                    await asyncio.sleep(0.5)
            finally:
                jobs.streams.release()

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )
