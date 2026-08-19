"""Polite HTTP fetching with a permanent on-disk raw cache.

flsenate.gov/robots.txt is `User-agent: *` / `crawl-delay: 1` with no Disallow
rules, so we crawl at one request per second from a single connection and
identify ourselves honestly.

Everything fetched is written to disk and never deleted. Re-analysis must never
require a re-scrape -- that is the whole point of the raw cache.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import requests

USER_AGENT = (
    "FloridaBillAlert/0.1 "
    "(+https://github.com/mydataismydata/Florida-bill-alert; "
    "open-source civic transparency crawler)"
)
CRAWL_DELAY = 1.0
BASE = "https://www.flsenate.gov"


@dataclass
class FetchResult:
    url: str
    path: Path
    status: int
    content_type: str
    nbytes: int
    sha256: str
    from_cache: bool

    @property
    def ok(self) -> bool:
        return self.status == 200

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8", errors="replace")


def _safe_segment(seg: str) -> str:
    """Make one URL path segment safe for the local filesystem."""
    out = []
    for ch in seg:
        out.append(ch if (ch.isalnum() or ch in "._-~ ()@,+") else "_")
    return "".join(out).strip() or "_"


class PoliteFetcher:
    def __init__(self, cache_root: Path, delay: float = CRAWL_DELAY, timeout: int = 90):
        self.cache_root = Path(cache_root)
        self.delay = delay
        self.timeout = timeout
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.session.headers["Accept-Encoding"] = "gzip, deflate"

    def local_path(self, url: str) -> Path:
        """Mirror the URL path on disk so the cache is browsable and debuggable."""
        parts = urlsplit(url)
        segs = [_safe_segment(s) for s in parts.path.strip("/").split("/") if s]
        if not segs:
            segs = ["index"]
        if parts.query:
            digest = hashlib.sha256(parts.query.encode()).hexdigest()[:10]
            segs[-1] = f"{segs[-1]}~{digest}"
        if "." not in segs[-1]:
            segs[-1] += ".html"
        return self.cache_root.joinpath(*segs)

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.monotonic()

    def fetch(self, url: str, force: bool = False, retries: int = 4) -> FetchResult:
        url = urljoin(BASE, url)
        path = self.local_path(url)

        if path.exists() and not force:
            raw = path.read_bytes()
            return FetchResult(url, path, 200, "", len(raw),
                               hashlib.sha256(raw).hexdigest(), True)

        # Vote-record URLs contain literal spaces; quote the path, leave query alone.
        parts = urlsplit(url)
        safe_url = f"{parts.scheme}://{parts.netloc}{quote(parts.path)}"
        if parts.query:
            safe_url += "?" + parts.query

        last_err = None
        for attempt in range(retries):
            self._wait()
            try:
                resp = self.session.get(safe_url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_err = exc
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(resp.content)
                return FetchResult(
                    url, path, 200,
                    resp.headers.get("Content-Type", ""),
                    len(resp.content),
                    hashlib.sha256(resp.content).hexdigest(),
                    False,
                )
            if resp.status_code in (404, 403, 410):
                return FetchResult(url, path, resp.status_code,
                                   resp.headers.get("Content-Type", ""), 0, "", False)
            last_err = f"HTTP {resp.status_code}"
            time.sleep(2 ** attempt)

        return FetchResult(url, path, 0, f"error: {last_err}", 0, "", False)
