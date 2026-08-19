# Setup

Two independent halves. The **ingest** side needs only Python and works today.
The **analysis** side needs a local model and is where MLX comes in.

## 1. Ingest (works now)

```bash
python3.11 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

```bash
export PYTHONPATH=src
P=./.venv/bin/python

$P -m flba --session 2026 enumerate          # walk the bill list
$P -m flba --session 2026 bills              # bill pages
$P -m flba --session 2026 docs               # text + staff analyses
$P -m flba --session 2026 status             # what we have
```

### Run the backfill in chunks, not marathons

Nothing needs a days-long run. Every stage is resumable and skips what is
already cached, so `--limit` is a clean stopping point:

```bash
$P -m flba --session 2026 bills --limit 200            # ~3.5 minutes
$P -m flba --session 2026 docs  --limit 500 --order activity
```

Repeat whenever convenient. `--order` decides what gets done first:

| `--order` | meaning |
|---|---|
| `number` | bill number (default) |
| `live` | bills still alive first, dead ones last |
| `activity` | most-worked bills first — versions + analyses + votes |

`activity` is the one to use for a backfill. It front-loads the bills that
actually moved, which are both the most useful and the most expensive to
analyse. In 2026, 1,233 of 1,897 bills never got past their filed version.

### Pull a single bill on demand

```bash
$P -m flba --session 2026 bill 797
$P -m flba --session 2026 bill 797 "HB 33" 1452 --with-docs
$P -m flba --session 2026 bill 797 --cached     # reparse, no network
```

This is safe to run while a backfill is going. Two things make that true, and
both were bugs first:

- The one-request-per-second crawl delay is held in a **lock file shared across
  processes**, not a per-process timer, so two jobs together still total 1 req/s.
- SQLite runs in WAL + autocommit with short atomic writes, so a long backfill
  never holds the write lock against an ad-hoc pull.

## 2. Analysis (MLX)

### Why MLX rather than Ollama

Ollama wraps llama.cpp and is the easier on-ramp, but MLX is Apple's own array
framework and gets first-class treatment on Apple Silicon — better memory
behaviour on unified RAM and faster support for new architectures. For a
pipeline that will re-read a 1,900-bill corpus every time a prompt changes,
that difference compounds.

Keep Ollama installed anyway. It is a useful second backend for cross-checking
that a prompt is not overfitted to one runtime.

### Install

`Qwen3.8-27B` is a **vision-language** model, so it needs `mlx-vlm`, not
`mlx-lm`. That trips people up.

```bash
./.venv/bin/pip install -U mlx-vlm      # vision-language models (Qwen3.8-27B)
./.venv/bin/pip install -U mlx-lm       # text-only models, and the server
```

### Model

Default: **`mlx-community/Qwen3.8-27B-4bit`** — dense, Apache-2.0, 16.1 GB on
disk. On 64 GB of unified memory that leaves ample room for a large KV cache,
which is what actually constrains long-bill work.

```bash
./.venv/bin/python -m mlx_vlm.generate \
  --model mlx-community/Qwen3.8-27B-4bit \
  --max-tokens 256 --temperature 0.0 \
  --prompt "Summarise this section of a Florida bill in plain English."
```

An 8-bit build exists if quality ever looks marginal; it roughly doubles the
footprint. Serve an OpenAI-compatible endpoint so the pipeline talks to one
interface regardless of backend:

```bash
./.venv/bin/python -m mlx_lm.server --model mlx-community/Qwen3.8-27B-4bit --port 8080
```

### Two things worth knowing about this model

**It is dense, not mixture-of-experts.** Every token reads all ~16 GB of
weights, so generation is bandwidth-bound and slower than an MoE of comparable
quality. If throughput becomes the constraint before quality does, an MoE such
as the Qwen3.6-35B-A3B line activates roughly 3B parameters per token and will
be several times faster at similar size. Benchmark both before committing.

**It has vision, and this project has two problems that are secretly vision
problems.** House bill text is PDF-only, and the fiscal tables inside committee
staff analyses are PDFs whose layout resists text extraction. A VLM can read a
rendered page directly. Use it as a *fallback and cross-check*, never as the
primary path — the geometric PDF parser (see [INGEST-NOTES.md](INGEST-NOTES.md))
is deterministic and cheap, and determinism is the whole point of the diff
layer. Vision is for the cases geometry cannot reach.

### Portability

The analysis box may not stay this machine. Everything model-specific therefore
sits behind one interface — `analyze(bill) -> AnalysisBundle` — with named
profiles (`tiny-3b`, `local-27b`, `cloud`) selecting model, chunk size, and
pass count. Moving to a weaker box should mean changing a profile name, not
touching the pipeline. Cloud API access is just another profile.

## 3. Publishing

Not built yet. The analysis box renders a static bundle and pushes it one-way
to the public host over SSH; the public server runs no model and never
initiates a connection inward. See [FEASIBILITY.md](FEASIBILITY.md) §3.
