"""Paper book: turns signals into a position under hard risk limits, and keeps an honest track record.

Limits (all enforced here, not in the signal):
  - long-only spot, exposure between 0 and `max_exposure` of equity;
  - at most `max_step` of equity traded per rebalance (no all-in flips on one noisy day);
  - kill-switch: equity `kill_drawdown` below its peak -> flat, and locked for `cooldown_days`;
  - fees of `fee_bps` on every traded notional.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

START_EQUITY = 10_000.0
LIMITS = {"max_step": 0.20, "kill_drawdown": 0.10, "cooldown_days": 3, "fee_bps": 10}
RULES_VERSION = "1.0"


def receipt_id(signal: dict) -> str:
    blob = json.dumps({"rules": RULES_VERSION, "signal": signal}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def close_on(klines: list, as_of: str):
    from .signal import day
    best = None
    for k in klines or []:
        d = day(k["timestamp"])
        if d <= as_of and (best is None or d > best[0]):
            best = (d, float(k["close"]))
    return best


def new_book() -> dict:
    return {"start_equity": START_EQUITY, "limits": LIMITS, "rules_version": RULES_VERSION, "entries": []}


def step(book: dict, signal: dict, klines: list, receipt: str) -> dict:
    """Apply one signal. Re-running the same date replaces that date's entry (idempotent)."""
    entries = book["entries"]
    if entries and entries[-1]["date"] >= signal["as_of"]:
        while entries and entries[-1]["date"] >= signal["as_of"]:
            entries.pop()
    prev = entries[-1]["state"] if entries else {"cash": START_EQUITY, "units": 0.0, "peak": START_EQUITY,
                                                  "locked_until": None, "first_price": None}
    px = close_on(klines, signal["as_of"])
    if px is None:
        raise ValueError(f"no {signal['asset']} close on or before {signal['as_of']}")
    price = px[1]
    cash, units = prev["cash"], prev["units"]
    equity = cash + units * price
    peak = max(prev["peak"], equity)
    first_price = prev["first_price"] or price
    weight_now = units * price / equity if equity else 0.0
    notes = []

    locked = prev["locked_until"]
    if locked and signal["as_of"] > locked:
        locked = None
        notes.append("kill-switch cooldown over")
    dd = 1 - equity / peak if peak else 0.0
    target = signal["target_exposure"]
    if dd >= LIMITS["kill_drawdown"] and not locked:
        locked = (datetime.strptime(signal["as_of"], "%Y-%m-%d")
                  + timedelta(days=LIMITS["cooldown_days"])).strftime("%Y-%m-%d")
        notes.append(f"KILL-SWITCH: drawdown {dd:.1%} >= {LIMITS['kill_drawdown']:.0%}; flat until {locked}")
    if locked:
        target = 0.0
    if target is None:
        target = weight_now
        notes.append("hold: signal had no target")
    step_cap = LIMITS["max_step"]
    if abs(target - weight_now) > step_cap and not locked:
        capped = weight_now + step_cap * (1 if target > weight_now else -1)
        notes.append(f"turnover cap: wanted {target:.0%}, moved to {capped:.0%}")
        target = capped

    trade_notional = (target - weight_now) * equity
    fee = abs(trade_notional) * LIMITS["fee_bps"] / 10_000
    units += trade_notional / price
    cash -= trade_notional + fee
    equity_after = cash + units * price
    entry = {
        "date": signal["as_of"], "receipt": receipt, "stance": signal["stance"], "composite": signal["composite"],
        "signal_target": signal["target_exposure"], "weight": round(target, 4), "price": price,
        "equity": round(equity_after, 2), "buy_hold": round(START_EQUITY * price / first_price, 2),
        "drawdown": round(dd, 4), "fee": round(fee, 2), "trade_notional": round(trade_notional, 2),
        "gates": signal["gates"], "notes": notes,
        "backfill": bool(signal.get("backfill")),
        "state": {"cash": cash, "units": units, "peak": max(peak, equity_after), "locked_until": locked,
                  "first_price": first_price},
    }
    entries.append(entry)
    return entry


def scorecard(book: dict, klines: list) -> dict:
    """Score each past call against what the asset did next (1 and 7 days later)."""
    from .signal import day
    closes = {day(k["timestamp"]): float(k["close"]) for k in klines or []}
    dates = sorted(closes)
    rows, hits, scored = [], 0, 0
    for e in book["entries"]:
        later = [d for d in dates if d > e["date"]]
        fwd = {}
        for n in (1, 7):
            if len(later) >= n:
                fwd[f"r{n}d"] = round(closes[later[n - 1]] / e["price"] - 1, 4)
        verdict = None
        if "r1d" in fwd and e["stance"] != "neutral":
            verdict = (e["stance"] == "risk-on") == (fwd["r1d"] > 0)
            scored += 1
            hits += verdict
        rows.append({"date": e["date"], "stance": e["stance"], **fwd, "right": verdict})
    ents = book["entries"]
    return {"rows": rows, "scored": scored, "hits": hits,
            "hit_rate": round(hits / scored, 3) if scored else None,
            "desk_return": round(ents[-1]["equity"] / START_EQUITY - 1, 4) if ents else None,
            "buy_hold_return": round(ents[-1]["buy_hold"] / START_EQUITY - 1, 4) if ents else None,
            "max_drawdown": round(max((e["drawdown"] for e in ents), default=0), 4)}
