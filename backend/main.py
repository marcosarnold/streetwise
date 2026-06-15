"""FastAPI app: REST endpoints + SSE stream for mobility events."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend import pipeline
from backend.store import get_active_events, get_event, init_db

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

POLL_INTERVAL_SECONDS = 5 * 60
HEARTBEAT_SECONDS = 30

app_state = {
    "last_poll_at": None,
    "sources_healthy": {"cta": None, "metra": None, "reddit": None},
}

_subscribers: list[asyncio.Queue] = []
_loop: asyncio.AbstractEventLoop | None = None


def _broadcast(message: dict) -> None:
    if _loop is None:
        return
    for queue in _subscribers:
        _loop.call_soon_threadsafe(queue.put_nowait, message)


def _run_source(name: str, cycle_fn) -> None:
    try:
        records = cycle_fn()
        app_state["sources_healthy"][name] = True
        for record in records:
            event_type = "new_event" if record.pop("_is_new", False) else "update_event"
            _broadcast({"type": event_type, "event": _serialize(record)})
    except Exception as exc:
        app_state["sources_healthy"][name] = False
        print(f"[pipeline] {name} cycle failed: {exc}")


def poll_cycle() -> None:
    _run_source("cta", pipeline.run_cta_cycle)
    _run_source("metra", pipeline.run_metra_cycle)
    _run_source("reddit", pipeline.run_reddit_cycle)
    app_state["last_poll_at"] = datetime.now(timezone.utc).isoformat()


def _serialize(event: dict) -> dict:
    """Ensure the sources field is a JSON array, not a JSON-encoded string."""
    event = dict(event)
    if isinstance(event.get("sources"), str):
        event["sources"] = json.loads(event["sources"]) if event["sources"] else []
    return event


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()

    init_db()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        poll_cycle,
        "interval",
        seconds=POLL_INTERVAL_SECONDS,
        next_run_time=datetime.now(),
    )
    scheduler.start()

    yield

    scheduler.shutdown()


app = FastAPI(title="Streetwise", lifespan=lifespan)


@app.get("/events")
def list_events(
    min_confidence: float = 0.4,
    event_type: str | None = None,
    limit: int | None = None,
):
    events = get_active_events(min_confidence=min_confidence, event_type=event_type, limit=limit)
    return [_serialize(e) for e in events]


@app.get("/events/stream")
async def stream_events():
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.append(queue)

    async def event_generator():
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                    yield f"data: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    yield 'data: {"type": "ping"}\n\n'
        finally:
            _subscribers.remove(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/events/{event_id}")
def get_event_by_id(event_id: str):
    event = get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return _serialize(event)


@app.get("/status")
def status():
    return {
        "last_poll_at": app_state["last_poll_at"],
        "events_active": len(get_active_events()),
        "sources_healthy": app_state["sources_healthy"],
    }


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
