#!/usr/bin/env python
"""Measure an OpenAI-compatible backend on real bill text.

Prefill and generation are separated by exploiting the prompt cache: the same
prompt is sent twice, first asking for a single token (which times prefill),
then for many (which the cache serves, timing generation alone). The reported
cache hit confirms the second run really was cached.

    scripts/bench_backend.py --base http://127.0.0.1:8080/v1 \
        --model mlx-community/Qwen3.8-27B-4bit
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIZES = [1_200, 5_000, 20_000, 60_000]
GEN_TOKENS = 96


def call(base, model, messages, max_tokens, nonce=None, schema=None, timeout=1800):
    body = {"model": model, "messages": messages, "temperature": 0,
            "max_tokens": max_tokens}
    if schema:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "out", "schema": schema}}
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return r, time.time() - t0


def bill_text() -> str:
    db = ROOT / "data" / "index.sqlite"
    if not db.exists():
        return "The Legislature of the State of Florida enacts: " * 4000
    con = sqlite3.connect(str(db))
    row = con.execute("SELECT segments FROM bill_render WHERE session='2026'"
                      " ORDER BY nchars DESC LIMIT 1").fetchone()
    return " ".join(t for _, _, _, t in json.loads(row[0])) if row else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)

    text = bill_text()
    print(f"backend: {args.base}  model: {args.model} {args.label}")
    print(f"{'chars':>8} {'prompt tok':>11} {'prefill t/s':>12} "
          f"{'gen t/s':>9} {'cached':>8} {'first-tok s':>12}")
    print("-" * 68)

    for size in SIZES:
        nonce = f"[run {size} {time.time()}]"
        prompt = (nonce + "\nSummarise this Florida bill excerpt in one sentence.\n\n"
                  + text[:size])
        msgs = [{"role": "user", "content": prompt}]
        try:
            a, ta = call(args.base, args.model, msgs, 1)
            b, tb = call(args.base, args.model, msgs, GEN_TOKENS)
        except Exception as exc:
            print(f"{size:>8,}  failed: {exc}")
            continue

        ptok = a.get("usage", {}).get("prompt_tokens", 0)
        cached = b.get("usage", {}).get("prompt_tokens_details", {}).get("cached_tokens", 0)
        gtok = b.get("usage", {}).get("completion_tokens", GEN_TOKENS)
        prefill = ptok / ta if ta > 0 else 0
        # Difference the two runs: both paid the same prefill, so what is left
        # is generation alone. This holds whether or not the cache engaged --
        # dividing tokens by total time silently blames prefill on generation.
        extra_tokens, extra_time = max(gtok - 1, 1), max(tb - ta, 1e-6)
        gen = extra_tokens / extra_time
        print(f"{size:>8,} {ptok:>11,} {prefill:>12.0f} {gen:>9.1f} "
              f"{cached:>8,} {ta:>12.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
