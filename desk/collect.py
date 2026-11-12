"""Pull one snapshot of everything the desk looks at from the SoSoValue API."""
from __future__ import annotations

from .client import Client, SosoError

ETF_SYMBOLS = ("BTC", "ETH", "SOL")
MAX_INDICES = 12
FGI_CHART = "fgi_indicator"


def _try(snap: dict, key: str, fn):
    try:
        snap[key] = fn()
    except SosoError as e:
        snap.setdefault("errors", []).append({"what": key, "error": str(e)})
        snap[key] = None


def collect(client: Client, asset: str = "BTC", now_ms: int | None = None) -> dict:
    snap: dict = {"asset": asset, "mode": client.mode}

    def currency_id():
        for c in client.get("/currencies") or []:
            if str(c.get("symbol", "")).upper() == asset:
                return c["currency_id"]
        raise SosoError("/currencies", 404, f"{asset} not listed")

    _try(snap, "currency_id", currency_id)
    if snap["currency_id"]:
        _try(snap, "klines", lambda: client.get(f"/currencies/{snap['currency_id']}/klines",
                                                {"interval": "1d", "limit": 120}))
    snap["etf"] = {}
    for sym in ETF_SYMBOLS:
        try:
            snap["etf"][sym] = client.get("/etfs/summary-history", {"symbol": sym, "country_code": "US", "limit": 40})
        except SosoError as e:
            snap.setdefault("errors", []).append({"what": f"etf.{sym}", "error": str(e)})

    snap["indices"] = {}
    try:
        tickers = (client.get("/indices") or [])[:MAX_INDICES]
    except SosoError as e:
        snap.setdefault("errors", []).append({"what": "indices", "error": str(e)})
        tickers = []
    for t in tickers:
        try:
            snap["indices"][t] = client.get(f"/indices/{t}/market-snapshot")
        except SosoError as e:
            snap.setdefault("errors", []).append({"what": f"index.{t}", "error": str(e)})

    _try(snap, "hot_news", lambda: (client.get("/news/hot", {"page_size": 50}) or {}).get("list", []))
    if snap.get("currency_id"):
        _try(snap, "asset_news", lambda: (client.get("/news", {"currency_id": snap["currency_id"], "category": 1,
                                                                "page_size": 50}) or {}).get("list", []))
    _try(snap, "macro", lambda: client.get("/macro/events"))
    _try(snap, "overview", lambda: client.get("/market/overview"))
    _try(snap, "fgi", lambda: client.get(f"/analyses/{FGI_CHART}", {"limit": 30}))
    snap["calls"] = client.calls
    return snap
