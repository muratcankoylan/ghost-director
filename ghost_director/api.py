"""Loopback HTTP boundary and durable SSE projection.

The injected command handler owns application orchestration. Inspection, replay
and event reconnect never invoke it. The API does not contain provider secrets.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import inspect
import json
import re
import time
from typing import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ghost_director.domain.models import Artifact, Command, Event, Snapshot
from ghost_director.jobs.store import BudgetExceeded, Conflict, InvalidTransition, NotFound, ProductionStore


CommandHandler = Callable[[dict], Awaitable[dict | None] | dict | None]


def event_cursor(after: str, last_event_id: str | None) -> int:
    """A reconnect cursor supersedes the URL used for the initial stream."""
    value = last_event_id if last_event_id not in {None, ""} else after
    if not re.fullmatch(r"[0-9]{1,18}", value):
        raise ValueError("Invalid event cursor; request a fresh snapshot.")
    return int(value)


def public_artifact(record: dict) -> dict:
    return Artifact.model_validate({key: record[key] for key in Artifact.model_fields if key in record}).model_dump(mode="json")


def public_event(record: dict) -> dict:
    return Event.model_validate({key: record[key] for key in Event.model_fields if key in record}).model_dump(mode="json")


def public_snapshot(record: dict) -> dict:
    from ghost_director.references import REFERENCE_TEMPLATES, reference_asset_projection
    filtered = {key: record[key] for key in Snapshot.model_fields if key in record}
    filtered['reference_templates'] = REFERENCE_TEMPLATES
    versions = {item['id']: item for item in filtered.get('reference_versions', [])}
    filtered['reference_assets'] = [reference_asset_projection(item, versions[item['revision_id']])
        if item['revision_id'] in versions else item for item in filtered.get('reference_assets', [])]
    filtered["artifacts"] = [public_artifact(row) for row in filtered.get("artifacts", [])]
    filtered["events"] = [public_event(row) for row in filtered.get("events", [])]
    return Snapshot.model_validate(filtered).model_dump(mode="json")


async def event_stream(store: ProductionStore, after: int, disconnected, *, poll_seconds: float = 0.2, heartbeat_seconds: float = 15):
    """Read durable pages before waiting; no in-memory subscription race exists."""
    # Flush headers/body immediately even when the snapshot already included
    # every event. This is transport state, never a fabricated model event.
    yield ": connected\n\n"
    last_activity = time.monotonic()
    while not await disconnected():
        rows = await asyncio.to_thread(store.events, after, limit=200)
        if rows:
            for row in rows:
                after = row["seq"]
                yield f"id: {after}\nevent: production\ndata: {json.dumps(public_event(row), separators=(',', ':'))}\n\n"
            last_activity = time.monotonic()
            continue
        if time.monotonic() - last_activity >= heartbeat_seconds:
            yield ": transport heartbeat\n\n"
            last_activity = time.monotonic()
        await asyncio.sleep(poll_seconds)


def create_app(store: ProductionStore, command_handler: CommandHandler | None = None, *, allowed_origins: list[str] | None = None, recovery_handler: CommandHandler | None = None, shutdown_handler: Callable[[], Awaitable[None] | None] | None = None) -> FastAPI:
    origins = allowed_origins or ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"]
    background: set[asyncio.Task] = set()

    async def resume_known_jobs(recovery):
        try:
            result = recovery_handler(recovery)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await asyncio.to_thread(store.append_event, "recovery.failed", {"error": f"{type(exc).__name__}: {exc}"})

    @asynccontextmanager
    async def lifespan(app):
        app.state.recovery = await asyncio.to_thread(store.recover)
        if recovery_handler is not None:
            task = asyncio.create_task(resume_known_jobs(app.state.recovery))
            background.add(task)
            task.add_done_callback(background.discard)
        yield
        # Graceful shutdown waits briefly for safe completion. Interrupted POSTs
        # remain submitting and are classified unknown on next startup.
        if background:
            _, pending = await asyncio.wait(background, timeout=2)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        if shutdown_handler is not None:
            closed = shutdown_handler()
            if inspect.isawaitable(closed):
                await closed

    app = FastAPI(title="Ghost Director", version="1.0", lifespan=lifespan)
    app.state.store = store
    app.state.background_tasks = background
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Last-Event-ID", "X-Ghost-View-Mode"])

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            return JSONResponse({"detail": "Origin is not allowed."}, status_code=403)
        image_upload = re.fullmatch(r'/api/productions/[^/]+/references/[^/]+/images', request.url.path)
        if request.method == "POST" and not image_upload and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return JSONResponse({"detail": "Commands require application/json."}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(NotFound)
    async def not_found(_request, exc):
        return JSONResponse({"detail": store.redactor.text(str(exc))}, status_code=404)

    @app.exception_handler(Conflict)
    @app.exception_handler(InvalidTransition)
    @app.exception_handler(BudgetExceeded)
    async def conflict(_request, exc):
        return JSONResponse({"detail": store.redactor.text(str(exc))}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc):
        # Pydantic errors may contain the raw rejected input, so don't return it.
        return JSONResponse({"detail": [{"loc": list(item["loc"]), "type": item["type"], "msg": store.redactor.text(item["msg"])} for item in exc.errors()]}, status_code=422)

    @app.exception_handler(ValueError)
    async def invalid_value(_request, exc):
        return JSONResponse({"detail": store.redactor.text(str(exc))}, status_code=422)

    def production(production_id):
        if production_id != store.production_id:
            raise NotFound("Production does not exist.")

    @app.get("/health")
    async def health():
        return {"ok": True, "production_id": store.production_id, "service": "ghost-director"}

    @app.get("/api/productions/{production_id}/snapshot")
    async def snapshot(production_id: str):
        production(production_id)
        return public_snapshot(await asyncio.to_thread(store.snapshot))

    @app.get("/api/productions/{production_id}/budget")
    async def budget(production_id: str):
        production(production_id)
        return await asyncio.to_thread(store.budget)

    @app.post("/api/productions/{production_id}/references/{asset_id}/images")
    async def reference_image(production_id: str, asset_id: str, request: Request, expected_revision_id: str, slot: str, state: str = 'default'):
        from ghost_director.references import ReferenceRegistry, read_image_upload
        production(production_id)
        if request.headers.get('x-ghost-view-mode') == 'replay':
            raise Conflict('Replay cannot upload references.')
        raw, mime = await read_image_upload(request)
        return await asyncio.to_thread(ReferenceRegistry(store).attach_image, asset_id, expected_revision_id, raw, slot=slot, state=state, mime_type=mime, provenance={'source': 'upload', 'notes': 'Supplied by the local operator; approval pending.'})

    @app.get("/api/productions/{production_id}/scenes/{revision_id}")
    async def scene_revision(production_id: str, revision_id: str):
        production(production_id)
        return await asyncio.to_thread(store.scene, revision_id)

    @app.get("/api/productions/{production_id}/jobs/{job_id}")
    async def job(production_id: str, job_id: str):
        production(production_id)
        return await asyncio.to_thread(store.get_job, job_id)

    @app.get("/api/productions/{production_id}/events")
    async def events(production_id: str, request: Request, after: str = "0"):
        production(production_id)
        cursor = event_cursor(after, request.headers.get("last-event-id"))
        # Validate before streaming response headers are sent.
        await asyncio.to_thread(store.events, cursor, limit=1)
        return StreamingResponse(event_stream(store, cursor, request.is_disconnected), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/productions/{production_id}/events/page")
    async def event_page(production_id: str, after: int = 0, limit: int = 200):
        production(production_id)
        return {"events": [public_event(row) for row in await asyncio.to_thread(store.events, after, limit=limit)]}

    @app.get("/api/productions/{production_id}/artifacts/{artifact_id}")
    async def artifact_metadata(production_id: str, artifact_id: str):
        production(production_id)
        return public_artifact(await asyncio.to_thread(store.artifact, artifact_id))

    @app.get("/api/productions/{production_id}/artifacts/{artifact_id}/content")
    async def artifact_content(production_id: str, artifact_id: str):
        production(production_id)
        metadata = await asyncio.to_thread(store.artifact, artifact_id)
        path = await asyncio.to_thread(store.artifact_path, artifact_id)
        mime = metadata["mime_type"]
        if mime in {"text/html", "application/xhtml+xml", "image/svg+xml", "application/javascript", "text/javascript"}:
            mime = "text/plain"
        return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/productions/{production_id}/runs/{run_id}")
    async def run(production_id: str, run_id: str):
        production(production_id)
        return await asyncio.to_thread(store.get_run, run_id)

    @app.get("/api/productions/{production_id}/runs/{run_id}/replay")
    async def replay(production_id: str, run_id: str):
        production(production_id)
        result = await asyncio.to_thread(store.replay, run_id)
        result["events"] = [public_event(row) for row in result["events"]]
        result["artifacts"] = [public_artifact(row) for row in result["artifacts"]]
        return result

    @app.get("/api/productions/{production_id}/runs/{run_id}/snapshot")
    async def recorded_snapshot(production_id: str, run_id: str):
        production(production_id)
        return public_snapshot(await asyncio.to_thread(store.run_snapshot, run_id))

    async def execute(command: dict):
        try:
            if command_handler is not None:
                result = command_handler(command)
                if inspect.isawaitable(result):
                    result = await result
            elif command["operation"] in {"pause", "resume"}:
                result = await asyncio.to_thread(store.set_paused, command["operation"] == "pause")
            elif command["operation"] == "cancel_job":
                result = await asyncio.to_thread(store.request_cancel, command["payload"]["job_id"])
            else:
                raise RuntimeError("No application command handler is attached.")
            await asyncio.to_thread(store.complete_command, command["id"], result=result if isinstance(result, dict) else None)
        except asyncio.CancelledError:
            # Preserve accepted command/paid-job uncertainty for startup
            # recovery; cancelling a coroutine does not cancel the remote bill.
            raise
        except Exception as exc:
            await asyncio.to_thread(store.complete_command, command["id"], error=f"{type(exc).__name__}: {exc}")

    @app.post("/api/productions/{production_id}/commands", status_code=202)
    async def command(production_id: str, request: Request, body: Command):
        production(production_id)
        if request.headers.get("x-ghost-view-mode") == "replay":
            raise Conflict("Replay cannot issue mutation commands.")
        if command_handler is None and body.operation not in {"pause", "resume", "cancel_job"}:
            raise HTTPException(503, "Application command handler is not attached.")
        data = body.model_dump(mode="json")
        receipt = await asyncio.to_thread(store.submit_command, data)
        if not receipt["duplicate"]:
            task = asyncio.create_task(execute(data))
            background.add(task)
            task.add_done_callback(background.discard)
        return receipt

    @app.get("/api/productions/{production_id}/commands/{command_id}")
    async def command_receipt(production_id: str, command_id: str):
        production(production_id)
        return await asyncio.to_thread(store.command_receipt, command_id)

    return app
