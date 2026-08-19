# Florida Bill Alert

A free, open-source legislation tracker for the Florida Legislature.

It follows bills as they are filed, shows clearly how far along the process each
one is, and publishes a plain-English summary alongside the important
provisions, a forward-looking read of how the bill's language could be used, and
an independent cost analysis cross-referenced against the sponsor's own fiscal
estimates.

**Who it's for:** legislators who have to vote on bills they haven't had time to
read, activists petitioning those legislators, and anyone trying to follow what
is actually happening in Tallahassee.

## How it's built

Analysis runs on a **private machine**. The public site is **static files
pushed to a public host**. There is no inference endpoint, no model, and no
pipeline code on the public server — it cannot reach back into the private
machine, by design.

```
  private AI box                        public host
  ─────────────────                     ─────────────
  scrape flsenate.gov                   static site
  deterministic parsing        push      pre-rendered bill pages
  local LLM analysis          ──────▶    subscribe / unsubscribe
  citation verification      (one-way)   email queue
  render static bundle                   subscriber list
```

A deliberate design rule: **anything a parser can do, a parser does.** Bill
stage, what text a bill adds and deletes, which statutes it touches, committee
votes, and the sponsor's own fiscal claim are all extracted deterministically
and carry no hallucination risk. The LLM is used only for genuine
summarization and analysis, and every claim it makes must cite a verbatim span
of the bill text that is checked against the source before publication.

The local LLM is pluggable — llama.cpp, Ollama, MLX, or any OpenAI-compatible
endpoint, with an optional cloud API key. Model profiles let the pipeline run
on small models so contributors aren't required to own a large machine.

## Status

The deterministic layer is complete and the site builds. For the 2026 session:
1,897 bills ingested with zero fetch failures, and every bill rendered with its
pathway, the exact language it adds and deletes, and the statutes it changes.

| layer | validation |
|---|---|
| Additions and deletions | 99.0% agreement against identical Senate companions |
| Stage and outcome | 228/228 enacted bills, no misclassifications |
| Statute cross-references | 98.7% recall, no false positives |

No language model is involved in any of it. See
[docs/FEASIBILITY.md](docs/FEASIBILITY.md) for the plan and
[docs/SETUP.md](docs/SETUP.md) to run it.

## Data source

Everything comes from [flsenate.gov](https://www.flsenate.gov), which carries
bills, bill text, and committee staff analyses for **both** chambers. The
crawler identifies itself and honors the site's published one-second crawl
delay. Every fetched document is cached permanently, so re-analysis never
causes a re-scrape.

Florida bills and statutes are government edicts and are in the public domain.

## Usage

```
python3.11 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
export PYTHONPATH=src

./.venv/bin/python -m flba --session 2026 enumerate
./.venv/bin/python -m flba --session 2026 bills --limit 200 --order activity
./.venv/bin/python -m flba --session 2026 docs  --limit 500
./.venv/bin/python -m flba --session 2026 bill 797 --with-docs
./.venv/bin/python -m flba --session 2026 status
```

Every stage is resumable and skips anything already cached, so a backfill runs
in short chunks rather than one long session. Single bills can be pulled or
refreshed on demand at any time, including while a backfill is running.

Full instructions, including the MLX model setup, are in
[docs/SETUP.md](docs/SETUP.md).

## License

MIT — see [LICENSE](LICENSE).
