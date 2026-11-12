"""Minimal SoSoValue OpenAPI v1 client (stdlib only).

Live mode: needs SOSO_API_KEY. Fixture mode: reads JSON files from a directory, one file per
request, so the whole pipeline runs (and is tested) without a key.

Every call is recorded in `self.calls` (path, params, time, ok) so each number in a signal can be
traced back to the exact request that produced it.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://openapi.sosovalue.com/openapi/v1"
MIN_INTERVAL_S = 3.2  # 20 requests/minute per key


class SosoError(RuntimeError):
    def __init__(self, path: str, code, message: str):
        super().__init__(f"{path}: {code} {message}")
        self.path, self.code, self.message = path, code, message


def fixture_name(path: str, params: dict | None) -> str:
    """Stable file name for a request: /etfs/summary-history?symbol=BTC -> etfs_summary-history__symbol-BTC.json"""
    name = path.strip("/").replace("/", "_")
    keep = {k: v for k, v in (params or {}).items() if k not in ("start_time", "end_time", "limit", "page_size", "page")}
    if keep:
        name += "__" + "_".join(f"{k}-{v}" for k, v in sorted(keep.items()))
    return re.sub(r"[^A-Za-z0-9_.-]", "-", name) + ".json"


class Client:
    def __init__(self, api_key: str | None = None, fixtures: str | Path | None = None, base: str = BASE):
        self.api_key = api_key if api_key is not None else os.environ.get("SOSO_API_KEY", "").strip()
        self.fixtures = Path(fixtures) if fixtures else None
        self.base = base
        self.calls: list[dict] = []
        self._last = 0.0
        if not self.fixtures and not self.api_key:
            raise SystemExit("No SOSO_API_KEY and no --fixtures directory: nothing to read from.")

    @property
    def mode(self) -> str:
        return "fixtures" if self.fixtures else "live"

    def get(self, path: str, params: dict | None = None):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        rec = {"path": path, "params": params, "at": int(time.time() * 1000)}
        try:
            data = self._fixture(path, params) if self.fixtures else self._live(path, params)
            rec["ok"] = True
            return data
        except Exception as e:
            rec["ok"] = False
            rec["error"] = str(e)[:200]
            raise
        finally:
            self.calls.append(rec)

    def _fixture(self, path, params):
        f = self.fixtures / fixture_name(path, params)
        if not f.exists():
            raise SosoError(path, 404, f"no fixture {f.name}")
        body = json.loads(f.read_text(encoding="utf-8"))
        return self._unwrap(path, body)

    def _live(self, path, params, retries: int = 3):
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"x-soso-api-key": self.api_key, "Accept": "application/json",
                                                   "User-Agent": "sosodesk/0.1"})
        for attempt in range(retries):
            self._last = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return self._unwrap(path, json.loads(r.read()))
            except urllib.error.HTTPError as e:
                body = {}
                try:
                    body = json.loads(e.read())
                except Exception:
                    pass
                if e.code == 429 and attempt < retries - 1:
                    time.sleep(float((body.get("details") or {}).get("retry_after", 30)))
                    continue
                raise SosoError(path, body.get("code", e.code), body.get("message", e.reason)) from None
        raise SosoError(path, 429, "rate limited")

    @staticmethod
    def _unwrap(path, body):
        if isinstance(body, dict) and "code" in body:
            if body["code"] != 0:
                raise SosoError(path, body["code"], body.get("message", ""))
            return body.get("data")
        return body
