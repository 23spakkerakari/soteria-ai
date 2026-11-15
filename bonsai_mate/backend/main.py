"""Bonsai Mate API + static frontend.  Run:  uvicorn backend.main:app --port 8000"""
import asyncio
import json
import threading
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import assistant, config, db, incident, knowledge, llm, voice
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

MAX_UTTERANCE_SECONDS = 90  # then the recording is sent as if the user had tapped stop


@app.websocket("/api/listen")
async def listen(ws: WebSocket):
    """Live speech-to-text. The browser sends {"sample_rate": N}, then raw float32
    mono audio frames, then {"stop": true}. While the user talks the server sends
    {"preview": text} from the real-time session; at the end it sends {"final": text}
    from one clean pass over the whole utterance."""
    await ws.accept()
    if voice.status["listen"] != "ready" or not voice.listen_lock.acquire(blocking=False):
        await ws.send_json({"error": "Voice input is busy or not ready yet."})
        await ws.close()
        return

    loop = asyncio.get_running_loop()
    updates: asyncio.Queue = asyncio.Queue()
    mic_audio, session = voice.open_session()

    def read_results() -> None:  # session.results is a blocking iterator
        for result in session.results:
            loop.call_soon_threadsafe(updates.put_nowait, (result.text, result.hypothesis_text or ""))

    threading.Thread(target=read_results, daemon=True).start()

    async def send_previews() -> None:
        confirmed = ""
        while True:
            newly_confirmed, hypothesis = await updates.get()
            confirmed = f"{confirmed} {newly_confirmed}".strip()
            await ws.send_json({"preview": f"{confirmed} {hypothesis}".strip()})

    sender = asyncio.create_task(send_previews())
    recorded: list[np.ndarray] = []
    try:
        sample_rate = voice.SAMPLE_RATE
        seconds = 0.0
        while seconds < MAX_UTTERANCE_SECONDS:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes"):
                samples = voice.to_16k(np.frombuffer(message["bytes"], dtype=np.float32), sample_rate)
                recorded.append(samples)
                seconds += len(samples) / voice.SAMPLE_RATE
                mic_audio.yield_(samples)
            elif message.get("text"):
                control = json.loads(message["text"])
                sample_rate = int(control.get("sample_rate", sample_rate))
                if control.get("stop"):
                    break
        audio = np.concatenate(recorded) if recorded else np.zeros(0, np.float32)
        final = await asyncio.to_thread(voice.transcribe, audio)
        sender.cancel()
        await ws.send_json({"final": final})
        await ws.close()
    except (WebSocketDisconnect, RuntimeError):
        pass  # browser went away
    finally:
        sender.cancel()
        mic_audio.finish()  # only now, so the session's own last pass does not compete with the final one
        session.stop()
        voice.listen_lock.release()


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)


@app.post("/api/speak")
async def speak(req: SpeakRequest):
    if voice.status["speak"] != "ready":
        raise HTTPException(503, "Server text-to-speech is not available.")
    wav = await asyncio.to_thread(voice.speak, req.text)
    return Response(wav, media_type="audio/wav")


# Last, so /api routes win.
app.mount("/", StaticFiles(directory=config.FRONTEND, html=True), name="frontend")
