"""Client for the local OpenAI-compatible model server (llama-server or mlx_vlm.server)."""
import json
import re
from collections.abc import AsyncIterator

import httpx

from . import config

# Sampling presets from the Bonsai 2 model cards (same as ../bonsai-models/shared/index.html).
# Neither server reads these from the model file, so send them on every request.
PRESETS = {
    True: dict(temperature=1.0, top_p=0.95, top_k=20, min_p=0, presence_penalty=1.0, repetition_penalty=1.0, max_tokens=8192),
    False: dict(temperature=0.7, top_p=0.80, top_k=20, min_p=0, presence_penalty=1.5, repetition_penalty=1.0, max_tokens=1024),
}


class LLMUnavailable(Exception):
    pass


async def model_id() -> str | None:
    """Name of the loaded model, or None if no server is reachable."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            data = (await client.get(f"{config.LLM_URL}/v1/models")).json()["data"]
        model = next((m for m in data if m.get("loaded")), data[0])
        return model["id"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return None


def _body(messages: list[dict], model: str, thinking: bool, **extra) -> dict:
    return {
        "model": model,
        "messages": messages,
        "chat_template_kwargs": {"enable_thinking": thinking},
        **PRESETS[thinking],
        **extra,
    }


async def stream_chat(messages: list[dict], thinking: bool = False) -> AsyncIterator[tuple[str, str]]:
    """Yield ("reasoning" | "delta", text) pieces as the model produces them."""
    model = await model_id()
    if model is None:
        raise LLMUnavailable(config.LLM_URL)
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as client:
        async with client.stream(
            "POST",
            f"{config.LLM_URL}/v1/chat/completions",
            json=_body(messages, model, thinking, stream=True),
        ) as res:
            if res.status_code != 200:
                raise RuntimeError(f"Model server error {res.status_code}: {(await res.aread()).decode()[:300]}")
            async for line in res.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                choices = json.loads(data).get("choices") or [{}]
                delta = choices[0].get("delta") or {}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    yield "reasoning", reasoning
                if delta.get("content"):
                    yield "delta", delta["content"]


async def complete(messages: list[dict], json_mode: bool = False, max_tokens: int = 1024) -> str:
    """One full reply, without streaming. Used by the pipeline."""
    model = await model_id()
    if model is None:
        raise LLMUnavailable(config.LLM_URL)
    extra: dict = {"max_tokens": max_tokens}
    if json_mode:
        extra["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as client:
        res = await client.post(f"{config.LLM_URL}/v1/chat/completions", json=_body(messages, model, False, **extra))
        res.raise_for_status()
    content = res.json()["choices"][0]["message"]["content"] or ""
    return re.sub(r"^\s*<think>.*?</think>", "", content, flags=re.S).strip()
