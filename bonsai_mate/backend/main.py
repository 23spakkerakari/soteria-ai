"""Bonsai Mate API + static frontend.  Run:  uvicorn backend.main:app --port 8000"""
import asyncio
import json
import mimetypes
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import assistant, config, db, incident, knowledge, llm, transcribe, voice
from .schemas import ChatRequest


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    cards, scenarios = db.rebuild(conn)
    conn.close()
    print(f"Loaded {cards} protocol cards and {scenarios} benchmark scenarios.")
    if not config.WILDMED_CASES.exists():
        print("wildmed-kb case studies not found: run  python wildmed-kb/scripts/build.py")
    voice.start_loading()
    warm_up = asyncio.create_task(assistant.warm_up())
    yield
    warm_up.cancel()


app = FastAPI(title="Bonsai Mate", lifespan=lifespan)


@app.get("/api/health")
async def health():
    conn = db.connect()
    try:
        count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return {
            "model": await llm.model_id(),
            "cards": count("cards"),
            "scenarios": count("scenarios"),
            "cases": count("cases"),
            "voice": voice.status,
        }
    finally:
        conn.close()


@app.get("/api/cards")
def cards(q: str = ""):
    conn = db.connect()
    try:
        return knowledge.search(conn, q, k=10) if q.strip() else db.all_cards(conn)
    finally:
        conn.close()


@app.get("/api/scenarios")
def scenarios():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT scenario_id, variation_of, organization, source_type, license_verified, clinical_review "
            "FROM scenarios ORDER BY scenario_id"
        )
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/chat")
async def chat(req: ChatRequest):
    messages = [m.model_dump() for m in req.messages]

    async def events():
        conn = db.connect()
        try:
            async for event in assistant.stream_reply(conn, messages, req.thinking, req.incident_id, req.plan_now):
                yield json.dumps(event) + "\n"
        finally:
            conn.close()

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.get("/api/incidents/{incident_id}")
def incident_record(incident_id: str):
    """Every question asked and answer given in one incident (for a handover note)."""
    conn = db.connect()
    try:
        record = incident.record(conn, incident_id)
    finally:
        conn.close()
    if record is None:
        raise HTTPException(404, "No such incident.")
    return record


# ---------- voice ----------


@app.post("/api/transcribe")
async def transcribe_audio(request: Request):
    """Transcribe an uploaded recording (raw body bytes) with Argmax Pro Tiny."""
    content_type = request.headers.get("content-type", "audio/wav")
    data = await request.body()
    try:
        text = await transcribe.transcribe_bytes(data, content_type)
    except transcribe.TranscriptionError as err:
        raise HTTPException(422, str(err))
    except Exception as err:  # keep the app up on unexpected failures
        raise HTTPException(503, f"Transcription failed: {err}")
    return {"text": text}


# Windows does not know these types, and the landing page's images and font need them.
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("font/woff2", ".woff2")

# Last, so /api routes win. The landing page is frontend/landing/, served at /landing/.
app.mount("/", StaticFiles(directory=config.FRONTEND, html=True), name="frontend")
