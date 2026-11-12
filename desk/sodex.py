"""Execution on SoDEX: price today's rebalance against the live SoDEX spot order book (ValueChain, on-chain CLOB).

The paper book fills at the daily close (so the replayed track record and the live one use the same rule). This
module answers the desk's next question, "what would it cost to do this for real right now?", with SoDEX's public
market data (no key): it walks the vBTC_vUSDC book at a known ValueChain block, and builds the exact spot order
the desk would sign: a LIMIT IOC at the worst level it needs, so it can never fill worse than the quote shows.
Nothing is signed or sent; the ticket is published next to the call.
"""
from __future__ import annotations

import json
import urllib.request
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

BASE = "https://mainnet-gw.sodex.dev/api/v1/spot"
SYMBOL = {"BTC": "vBTC_vUSDC"}
SLIPPAGE_LIMIT_BPS = 25  # the desk refuses to cross more than this from mid
BUY, SELL, LIMIT, IOC = 1, 2, 1, 3  # SoDEX OrderSideEnum / OrderTypeEnum / TimeInForceEnum


def _get(path: str, fixtures: str | None):
    if fixtures:
        name = "sodex_" + path.strip("/").replace("/", "_").replace("?", "__").replace("=", "-").replace("&", "_")
        return json.loads((Path(fixtures) / f"{name}.json").read_text(encoding="utf-8"))
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json", "User-Agent": "sosodesk"})
    with urllib.request.urlopen(req, timeout=20) as r:
        out = json.loads(r.read())
    if out.get("code") != 0:
        raise RuntimeError(f"SoDEX {path}: code {out.get('code')}")
    return out


def fetch(asset: str = "BTC", fixtures: str | None = None) -> dict:
    sym = SYMBOL[asset]
    meta = _get(f"/markets/symbols?symbol={sym}", fixtures)["data"][0]
    book = _get(f"/markets/{sym}/orderbook?limit=100", fixtures)["data"]
    return {"symbol": meta, "book": book, "requests": [f"GET {BASE}/markets/symbols?symbol={sym}",
                                                       f"GET {BASE}/markets/{sym}/orderbook?limit=100"]}


def ticket(market: dict, notional_usd: float, as_of: str) -> dict:
    """Walk the book for a buy (notional > 0) or sell (< 0) of |notional| USD. Pure function, no network."""
    meta, book = market["symbol"], market["book"]
    bids = [(Decimal(p), Decimal(q)) for p, q in book["bids"]]
    asks = [(Decimal(p), Decimal(q)) for p, q in book["asks"]]
    if not bids or not asks:
        return {"status": "no-book"}
    mid = (bids[0][0] + asks[0][0]) / 2
    out = {"symbol": meta["name"], "symbolID": meta["id"], "block_height": book.get("blockHeight"),
           "block_time_ms": book.get("blockTime"), "mid": float(mid),
           "spread_bps": float((asks[0][0] - bids[0][0]) / mid * 10_000), "requests": market.get("requests", [])}
    step = Decimal(meta["stepSize"])
    qty = (Decimal(str(abs(notional_usd))) / mid).quantize(step, rounding=ROUND_DOWN)
    if qty < Decimal(meta["minQuantity"]) or qty * mid < Decimal(meta["minNotional"]):
        out.update(status="no-trade", note="rebalance below SoDEX minimum order size" if notional_usd else
                   "no rebalance today")
        out["depth_25bp_usd"] = float(sum(p * q for p, q in asks if p <= mid * (1 + Decimal(SLIPPAGE_LIMIT_BPS) / 10_000))
                                      + sum(p * q for p, q in bids if p >= mid * (1 - Decimal(SLIPPAGE_LIMIT_BPS) / 10_000)))
        return out
    side = BUY if notional_usd > 0 else SELL
    levels = asks if side == BUY else bids
    left, cost, worst, used = qty, Decimal(0), None, 0
    for p, q in levels:
        take = min(left, q)
        cost += take * p
        left -= take
        worst, used = p, used + 1
        if left <= 0:
            break
    filled = qty - left
    vwap = cost / filled if filled else mid
    slip = float(abs(vwap - mid) / mid * 10_000)
    fee = float(cost * Decimal(meta["takerFee"]))
    out.update(status="ok" if left <= 0 and slip <= SLIPPAGE_LIMIT_BPS else "blocked",
               side="BUY" if side == BUY else "SELL", quantity=str(qty), filled_quantity=str(filled),
               levels=used, vwap=float(vwap), slippage_bps=round(slip, 2), taker_fee_usd=round(fee, 2),
               notional_usd=float(cost))
    if left > 0:
        out["note"] = "book too thin within 100 levels: order would only part-fill"
    elif slip > SLIPPAGE_LIMIT_BPS:
        out["note"] = f"slippage {slip:.1f} bp > {SLIPPAGE_LIMIT_BPS} bp limit: desk would not send this order"
    # The exact body for POST /api/v1/spot/trade/orders (signed with EIP-712, domain "spot"); accountID is the
    # operator's, so it is left as a placeholder. LIMIT IOC at the worst level walked caps the fill price.
    out["order"] = {"accountID": "<accountID>", "orders": [{
        "symbolID": meta["id"], "clOrdID": f"sosodesk-{as_of}", "side": side, "type": LIMIT, "timeInForce": IOC,
        "price": str(worst), "quantity": str(qty)}]}
    return out
