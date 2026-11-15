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

from . import assistant, config, db, knowledge, llm, voice, wildmed
from .schemas import ChatRequest


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    cards, scenarios = db.rebuild(conn)
    conn.close()
    print(f"Loaded {cards} protocol cards and {scenarios} benchmark scenarios.")
    if not wildmed.available():
        print("wildmed-kb index not found: run  python wildmed-kb/scripts/build.py")
    voice.start_loading()
    yield


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
            "cases": wildmed.available(),
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
            async for event in assistant.stream_reply(conn, messages, req.thinking):
                yield json.dumps(event) + "\n"
        finally:
            conn.close()

    return StreamingResponse(events(), media_type="application/x-ndjson")


# ---------- voice ----------


@app.websocket("/api/listen")
async def listen(ws: WebSocket):
    """Live speech-to-text. The browser sends {"sample_rate": N}, then raw float32
    mono audio frames, then {"stop": true}. The server sends {"confirmed", "hypothesis"}
    updates while the user talks, and {"final": text} at the end."""
    await ws.accept()
    if voice.status["listen"] != "ready" or not voice.listen_lock.acquire(blocking=False):
        await ws.send_json({"error": "Voice input is busy or not ready yet."})
        await ws.close()
        return

    loop = asyncio.get_running_loop()
    updates: asyncio.Queue = asyncio.Queue()
    continuation, session = voice.open_session()

    def read_results() -> None:  # session.results is a blocking iterator
        try:
            for result in session.results:
                loop.call_soon_threadsafe(updates.put_nowait, (result.text, result.hypothesis_text or ""))
        finally:
            loop.call_soon_threadsafe(updates.put_nowait, None)

    threading.Thread(target=read_results, daemon=True).start()

    async def send_updates() -> str:
        confirmed = ""
        while (update := await updates.get()) is not None:
            confirmed = f"{confirmed} {update[0]}".strip()
            await ws.send_json({"confirmed": confirmed, "hypothesis": update[1]})
        return confirmed

    sender = asyncio.create_task(send_updates())
    try:
        sample_rate = voice.SAMPLE_RATE
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                samples = np.frombuffer(message["bytes"], dtype=np.float32)
                continuation.yield_(voice.to_16k(samples, sample_rate))
            elif message.get("text"):
                control = json.loads(message["text"])
                sample_rate = int(control.get("sample_rate", sample_rate))
                if control.get("stop"):
                    break
        continuation.finish()  # the session confirms whatever is left, then ends
        await ws.send_json({"final": await sender})
        await ws.close()
    except (WebSocketDisconnect, RuntimeError):
        pass  # browser went away
    finally:
        continuation.finish()
        session.stop()
        sender.cancel()
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
