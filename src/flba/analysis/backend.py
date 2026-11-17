"""Talking to a model.

Everything speaks the OpenAI chat-completions shape, so one client covers the
local MLX server, Ollama, and any hosted API. Nothing else in the analysis
layer knows which is in use.

Two properties of the local server shape the design, both measured rather than
assumed (see scripts/bench_backend.py):

* **Schema-forced decoding.** `response_format` constrains generation at the
  decoder, so the model cannot emit JSON that violates the schema. This is what
  makes small models usable; without it they produce plausible-looking
  malformed output.
* **Prompt caching.** The first request carrying a given bill pays full
  prefill; later requests reusing that prefix are roughly three times cheaper.
  So several sharply-scoped passes cost far less than their number suggests,
  and focused prompts beat one omnibus prompt on quality.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Reply:
    text: str
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    @property
    def cache_hit(self) -> bool:
        return self.cached_tokens > self.prompt_tokens * 0.5

    def json(self):
        return json.loads(self.text)


# The analysis box is the Mac Studio, which serves GLM 5.3 Flash through
# oMLX on its own loopback port. oMLX refuses requests without its API key,
# which lives in .env as FLBA_API_KEY and never in the repository.
DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_MODEL = "GLM-5.3-Flash-oQ4e-mtp"


@dataclass
class Backend:
    """An OpenAI-compatible chat endpoint."""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str | None = None
    timeout: int = 1800
    temperature: float = 0.0
    max_tokens: int = 1200
    retries: int = 3
    stats: dict = field(default_factory=lambda: {
        "calls": 0, "cache_hits": 0, "prompt_tokens": 0,
        "completion_tokens": 0, "seconds": 0.0})

    def chat(self, messages, schema=None, max_tokens=None,
             temperature=None) -> Reply:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "analysis", "schema": schema,
                                "strict": True},
            }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last = None
        for attempt in range(self.retries):
            req = urllib.request.Request(
                self.base_url.rstrip("/") + "/chat/completions",
                data=json.dumps(body).encode(), headers=headers)
            t0 = time.time()
            try:
                raw = json.loads(urllib.request.urlopen(req, timeout=self.timeout).read())
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                time.sleep(2 ** attempt)
                continue

            usage = raw.get("usage") or {}
            reply = Reply(
                text=raw["choices"][0]["message"].get("content") or "",
                prompt_tokens=usage.get("prompt_tokens", 0),
                cached_tokens=(usage.get("prompt_tokens_details") or {})
                              .get("cached_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                seconds=time.time() - t0,
            )
            self.stats["calls"] += 1
            self.stats["cache_hits"] += int(reply.cache_hit)
            self.stats["prompt_tokens"] += reply.prompt_tokens
            self.stats["completion_tokens"] += reply.completion_tokens
            self.stats["seconds"] += reply.seconds
            return reply

        raise RuntimeError(f"backend unreachable after {self.retries} tries: {last}")

    def available(self) -> bool:
        # oMLX answers 401 to an unauthenticated /models, which used to read
        # as "no server" even with the server up.
        headers = ({"Authorization": f"Bearer {self.api_key}"}
                   if self.api_key else {})
        try:
            urllib.request.urlopen(urllib.request.Request(
                self.base_url.rstrip("/") + "/models", headers=headers),
                timeout=5).read()
            return True
        except Exception:
            return False
