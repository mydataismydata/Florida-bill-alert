# Florida Bill Alert

A free, open-source legislation tracker for the Florida Legislature.

It follows bills as they are filed, shows clearly how far along the process each
one is, and publishes an AI-analyzed plain-English summary and provisions, a forward-looking read of how the bill's language could be used, and
an independent cost analysis cross-referenced against the sponsor's own fiscal
estimates.

**Who it's for:** legislators who have to vote on bills they haven't had time to
read, local county and school board directors looking to keep abreast of pending impacts, concerned citizens and anyone trying to follow what
is actually happening in Tallahassee.

## How it's built

Analysis uses a local language model (LLM) with strict guide rails around verifying facts. The LLM may make mistakes, which is why the actual published text of the bill is always directly linked from all LLM claims.

The public site is **static files pushed to a public host**. There is no inference endpoint, no model, and no
pipeline code on the public server. It cannot reach back into the LLM, by design.

```
  private AI box                        public host
  ─────────────────                     ─────────────
  scrape flsenate.gov                   static site
  deterministic parsing        push      pre-rendered bill pages
  local LLM analysis          ──────▶    subscribe / unsubscribe
  citation verification      (one-way)   email queue
  render static bundle                   subscriber list
```

Non-analyzed text is directly extracted, not processed with an LLM. Bill
stage, what text a bill adds and deletes, which statutes it touches, committee
votes, and the sponsor's own claims and summary are all extracted directly
and carry no hallucination risk. The LLM is used only for genuine
summarization and analysis, and every claim it makes must cite a verbatim span
of the bill text that is checked against the source before publication.

The local LLM is pluggable, including local models or cloud API keys. Model profiles let the pipeline run
on small models so contributors aren't required to own a large machine.

## Technical notes

[docs/ANALYSIS-NOTES.md](docs/ANALYSIS-NOTES.md).

See [docs/FEASIBILITY.md](docs/FEASIBILITY.md) for the plan and
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

Full instructions, including a local LLM setup using MLX, are in
[docs/SETUP.md](docs/SETUP.md).

## License

MIT — see [LICENSE](LICENSE).
