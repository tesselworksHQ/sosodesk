"""sosodesk: python -m desk run [--fixtures DIR] [--data data] [--site site] [--now-ms MS]"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import book as bk
from .client import Client
from .collect import collect
from .render import render
from .signal import compute, day

BACKFILL_DAYS = 20


def end_of(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000) + 86_399_000


def slim(snap: dict) -> dict:
    """Snapshot as stored in git: news bodies dropped (titles + links are enough to audit)."""
    out = dict(snap)
    for key in ("hot_news", "asset_news"):
        out[key] = [{k: n.get(k) for k in ("id", "title", "release_time", "source_link")} for n in snap.get(key) or []]
    return out


def save_receipt(data: Path, signal: dict, calls: list, mode: str) -> str:
    rid = bk.receipt_id(signal)
    rec = {"receipt": rid, "rules_version": bk.RULES_VERSION, "data_mode": mode, "signal": signal,
           "api_calls": calls}
    (data / "receipts").mkdir(parents=True, exist_ok=True)
    (data / "receipts" / f"{signal['as_of']}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rid


def run(args) -> dict:
    data, site = Path(args.data), Path(args.site)
    data.mkdir(parents=True, exist_ok=True)
    client = Client(fixtures=args.fixtures)
    now_ms = args.now_ms or int(time.time() * 1000)
    snap = collect(client, asset=args.asset)
    (data / "snapshot.json").write_text(json.dumps(slim(snap), indent=1), encoding="utf-8")

    bpath = data / "book.json"
    book = json.loads(bpath.read_text(encoding="utf-8")) if bpath.exists() else bk.new_book()
    if book.get("data_mode", client.mode) != client.mode:
        # never let a demo (fixture) track record carry over into a live one, or the reverse
        print(f"book in {bpath} is {book['data_mode']} data; starting a fresh {client.mode} book")
        for f in (data / "receipts").glob("*.json"):
            f.unlink()
        book = bk.new_book()
    today = day(now_ms)
    if not book["entries"]:
        # First run: replay the last trading days the API still serves, with the same rules, so the page starts
        # with a track record. Backfilled calls lack news and index breadth (not served historically): marked.
        etf_days = sorted({r["date"] for r in (snap.get("etf") or {}).get(args.asset) or []})
        days = [(datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") for d in etf_days]
        for d in [d for d in days if d < today][-BACKFILL_DAYS:]:
            sig = compute(snap, end_of(d), live=False)
            sig["backfill"] = True
            if sig["target_exposure"] is None and not book["entries"]:
                continue
            rid = save_receipt(data, sig, [], client.mode)
            bk.step(book, sig, snap.get("klines"), rid)
    sig = compute(snap, now_ms, live=True)
    rid = save_receipt(data, sig, client.calls, client.mode)
    entry = bk.step(book, sig, snap.get("klines"), rid)
    book["data_mode"] = client.mode
    book["updated_ms"] = now_ms
    bpath.write_text(json.dumps(book, indent=1), encoding="utf-8")
    card = bk.scorecard(book, snap.get("klines"))
    (data / "latest.json").write_text(json.dumps({"signal": sig, "receipt": rid, "entry": entry, "scorecard": card},
                                                 indent=1), encoding="utf-8")
    render(site, data, sig, rid, book, card, snap)
    return {"as_of": sig["as_of"], "stance": sig["stance"], "composite": sig["composite"],
            "weight": entry["weight"], "receipt": rid[:16], "errors": len(snap.get("errors") or [])}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="desk")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect, compute the signal, step the paper book, render the site")
    r.add_argument("--fixtures", help="read API responses from this directory instead of the live API")
    r.add_argument("--asset", default="BTC")
    r.add_argument("--data", default="data")
    r.add_argument("--site", default="site")
    r.add_argument("--now-ms", type=int, default=None)
    a = ap.parse_args(argv)
    print(json.dumps(run(a)))


if __name__ == "__main__":
    main()
