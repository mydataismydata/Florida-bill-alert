#!/usr/bin/env python3
"""A chat prompt for the local model. No quoting, no flags, keeps context.

    scripts/chat.py

Type and press enter. The conversation accumulates, so follow-ups work the way
they do anywhere else. Commands start with a slash:

    /reset      forget the conversation and start again
    /system …   set the standing instruction, and reset
    /undo       drop the last exchange
    /save FILE  write the transcript to a file
    /tokens     roughly how much context is in play
    /exit       leave (ctrl-D does the same)

Nothing here verifies anything against a bill. This is the raw model -- the
pipeline's quote checking, markers and guards are not in the loop, and it will
answer confidently about Florida procedure it has no way to know.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

URL = "http://localhost:8080/v1/chat/completions"
MODEL = "mlx-community/Qwen3.8-27B-4bit"
MAX_TOKENS = 1200

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"


def models() -> list[str]:
    try:
        with urllib.request.urlopen("http://localhost:8080/v1/models", timeout=5) as r:
            return [m["id"] for m in json.load(r).get("data", [])]
    except OSError:
        return []


def stream(messages: list[dict], model: str) -> str:
    """Print the reply as it arrives; return the whole of it."""
    body = json.dumps({"model": model, "messages": messages,
                       "max_tokens": MAX_TOKENS, "stream": True}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
    out: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                piece = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content")
                if piece:
                    out.append(piece)
                    sys.stdout.write(piece)
                    sys.stdout.flush()
    except urllib.error.URLError as exc:
        print(f"\n{DIM}-- no answer: {exc}{OFF}")
        return ""
    except KeyboardInterrupt:
        print(f"\n{DIM}-- stopped{OFF}")
    print()
    return "".join(out)


def main() -> int:
    available = models()
    if not available:
        print("The model server is not answering on port 8080.\n"
              "Start it with:  python -m mlx_vlm.server --model "
              f"{MODEL} --port 8080 --max-tokens 2048", file=sys.stderr)
        return 1
    model = MODEL if MODEL in available else available[0]

    system = ""
    history: list[dict] = []
    print(f"{BOLD}{model}{OFF}  {DIM}/reset /system /undo /save /tokens /exit{OFF}")

    while True:
        try:
            line = input(f"{BOLD}› {OFF}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue

        if line.startswith("/"):
            cmd, _, rest = line.partition(" ")
            cmd = cmd.lower()
            if cmd in ("/exit", "/quit", "/q"):
                return 0
            if cmd == "/reset":
                history.clear()
                print(f"{DIM}-- context cleared{OFF}")
            elif cmd == "/system":
                system = rest.strip()
                history.clear()
                print(f"{DIM}-- system set, context cleared{OFF}")
            elif cmd == "/undo":
                del history[-2:]
                print(f"{DIM}-- dropped, {len(history) // 2} exchanges left{OFF}")
            elif cmd == "/save":
                path = rest.strip() or "chat.md"
                with open(path, "w", encoding="utf-8") as fh:
                    for m in history:
                        fh.write(f"## {m['role']}\n\n{m['content']}\n\n")
                print(f"{DIM}-- written to {path}{OFF}")
            elif cmd == "/tokens":
                chars = sum(len(m["content"]) for m in history) + len(system)
                print(f"{DIM}-- {len(history) // 2} exchanges, ~{chars // 4:,} tokens{OFF}")
            else:
                print(f"{DIM}-- no such command{OFF}")
            continue

        history.append({"role": "user", "content": line})
        messages = ([{"role": "system", "content": system}] if system else []) + history
        reply = stream(messages, model)
        if reply:
            history.append({"role": "assistant", "content": reply})
        else:
            history.pop()          # keep the turn count honest


if __name__ == "__main__":
    raise SystemExit(main())
