"""Rule-based risk signal. Every component returns a score in [-1, 1] plus the evidence behind it.

Nothing here is a model or a black box: the rules are short enough to read, and each evidence row names
the SoSoValue endpoint and the exact data point it came from.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

WEIGHTS = {"etf_flows": 0.35, "trend": 0.25, "breadth": 0.15, "news": 0.15, "sentiment": 0.10}
RISK_ON, RISK_OFF = 0.20, -0.20
MACRO_WORDS = re.compile(r"\b(cpi|ppi|pce|fomc|fed|interest rate|nonfarm|payroll|gdp|unemployment|jobless)\b", re.I)
NEG = re.compile(r"\b(hack(ed)?|exploit(ed)?|drain(ed)?|lawsuit|sues?|charged|ban(s|ned)?|outflows?|liquidat\w*|"
                 r"crash(es)?|plunge[sd]?|delist\w*|insolven\w*|bankrupt\w*|halt(s|ed)?|sell-?off|fraud|probe)\b", re.I)
POS = re.compile(r"\b(approv\w*|inflows?|record high|all-time high|ath|adopt\w*|surge[sd]?|rall(y|ies|ied)|"
                 r"reserve|accumulat\w*|upgrade[sd]?|partnership)\b", re.I)


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def fnum(x) -> float:
    return float(x) if x is not None else 0.0


def ev(component, label, value, source, link=None, ref=None):
    row = {"component": component, "label": label, "value": value, "source": source}
    if link:
        row["link"] = link
    if ref:
        row["ref"] = ref
    return row


def usd(x: float) -> str:
    a = abs(x)
    body = f"${a / 1e9:.2f}B" if a >= 1e9 else f"${a / 1e6:.0f}M" if a >= 1e6 else f"${a:,.0f}"
    return ("-" if x < 0 else "+") + body


# ---- components ---------------------------------------------------------------------------------------------

def etf_flows(snap, as_of: str):
    """Last-5-day US spot ETF net inflow for the asset, scaled by the 20-day typical day."""
    rows = [r for r in (snap.get("etf") or {}).get(snap["asset"]) or [] if r["date"] < as_of]  # a flow day counts once it has closed
    rows.sort(key=lambda r: r["date"], reverse=True)
    if len(rows) < 6:
        return None, [], "fewer than 6 ETF days"
    src = f"GET /etfs/summary-history?symbol={snap['asset']}&country_code=US"
    flows = [fnum(r["total_net_inflow"]) for r in rows]
    f5 = sum(flows[:5])
    typical = sum(abs(x) for x in flows[:20]) / min(20, len(flows)) or 1.0
    score = clamp(f5 / (typical * 5) * 1.5)
    streak = 0
    for x in flows:
        if streak >= 0 and x > 0:
            streak += 1
        elif streak <= 0 and x < 0:
            streak -= 1
        else:
            break
    evid = [ev("etf_flows", f"Net inflow {r['date']}", round(fnum(r["total_net_inflow"])), src, ref=r["date"])
            for r in rows[:5]]
    evid.append(ev("etf_flows", "5-day net inflow (USD)", round(f5), src))
    evid.append(ev("etf_flows", "20-day typical |daily flow| (USD)", round(typical), src))
    evid.append(ev("etf_flows", "Flow streak (days, +in/-out)", streak, src))
    others = {s: rows_[0] for s, rows_ in (snap.get("etf") or {}).items() if s != snap["asset"] and rows_}
    for s, r in others.items():
        evid.append(ev("etf_flows", f"Context: {s} ETF net inflow {r['date']}", round(fnum(r["total_net_inflow"])),
                       f"GET /etfs/summary-history?symbol={s}&country_code=US", ref=r["date"]))
    return score, evid, f"5-day net {usd(f5)} vs a typical day of {usd(typical)[1:]}; streak {streak:+d} days"


def trend(snap, as_of: str):
    ks = [k for k in snap.get("klines") or [] if day(k["timestamp"]) < as_of]  # no look-ahead: the fill is at the as_of close
    ks.sort(key=lambda k: k["timestamp"])
    if len(ks) < 50:
        return None, [], "fewer than 50 daily klines"
    closes = [fnum(k["close"]) for k in ks]
    c, s20, s50 = closes[-1], sum(closes[-20:]) / 20, sum(closes[-50:]) / 50
    score = clamp(0.5 * (c / s20 - 1) / 0.05 + 0.5 * (c / s50 - 1) / 0.10)
    src = f"GET /currencies/{snap.get('currency_id')}/klines?interval=1d"
    return score, [ev("trend", f"{snap['asset']} close {day(ks[-1]['timestamp'])}", c, src, ref=day(ks[-1]["timestamp"])),
                   ev("trend", "20-day average close", round(s20, 2), src),
                   ev("trend", "50-day average close", round(s50, 2), src)],         f"close {c / s20 - 1:+.1%} vs 20-day average, {c / s50 - 1:+.1%} vs 50-day"


def breadth(snap, as_of: str, live: bool):
    if not live:
        return None, [], "index snapshots are point-in-time (not in backfill)"
    idx = {t: s for t, s in (snap.get("indices") or {}).items() if s and "roi_7d" in s}
    if len(idx) < 3:
        return None, [], "fewer than 3 SoSoValue indices"
    up = sum(1 for s in idx.values() if fnum(s["roi_7d"]) > 0)
    score = clamp(2 * up / len(idx) - 1)
    evid = [ev("breadth", f"{t} 7-day return", round(fnum(s["roi_7d"]), 4), f"GET /indices/{t}/market-snapshot")
            for t, s in sorted(idx.items())]
    evid.append(ev("breadth", "Indices up over 7 days", f"{up}/{len(idx)}", "GET /indices"))
    return score, evid, f"{up} of {len(idx)} SoSoValue sector indices up over 7 days"


def news(snap, as_of_ms: int):
    lo = as_of_ms - 24 * 3600 * 1000
    seen, pos, neg, evid = set(), 0, 0, []
    for key, src in (("hot_news", "GET /news/hot"), ("asset_news", "GET /news?category=1")):
        for n in snap.get(key) or []:
            t = n.get("release_time") or 0
            title = (n.get("title") or "").strip()
            if not (lo <= t <= as_of_ms) or not title or title.lower() in seen:
                continue
            seen.add(title.lower())
            p, q = bool(POS.search(title)), bool(NEG.search(title))
            if p == q:
                continue  # neutral or mixed headline: not counted
            pos, neg = pos + p, neg + q
            evid.append(ev("news", ("+ " if p else "- ") + title[:140], 1 if p else -1, src,
                           link=n.get("source_link"), ref=day(t)))
    if not seen:
        return None, [], "no headlines in the last 24 h"
    score = clamp((pos - neg) / max(5, pos + neg))
    evid.append(ev("news", "Headlines read / positive / negative (24 h)", f"{len(seen)} / {pos} / {neg}",
                   "GET /news/hot + GET /news"))
    return score, evid, f"{pos} positive and {neg} negative of {len(seen)} headlines in 24 h"


def sentiment(snap, as_of_ms: int):
    """Contrarian: extreme greed trims risk, extreme fear adds a little."""
    rows = []
    for r in snap.get("fgi") or []:
        t = int(r.get("timestamp", 0))
        vals = [v for k, v in r.items() if k != "timestamp" and isinstance(v, (int, float))]
        if vals and t <= as_of_ms:
            rows.append((t, vals[0]))
    if not rows:
        return None, [], "no Fear & Greed reading"
    t, v = max(rows)
    score = clamp((50 - v) / 50) * (1 if v <= 25 or v >= 75 else 0.4)
    tone = "extreme fear: adds a little" if v <= 25 else "extreme greed: trims" if v >= 75 else "no extreme: light weight"
    return score, [ev("sentiment", f"Crypto Fear & Greed {day(t)}", v, "GET /analyses/fgi_indicator", ref=day(t))],         f"index {v}, {tone}"


# ---- gates and the combined call --------------------------------------------------------------------------

def macro_gate(snap, as_of: str):
    d0 = datetime.strptime(as_of, "%Y-%m-%d")
    window = {(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in (0, 1)}
    hits = []
    for row in snap.get("macro") or []:
        if row.get("date") in window:
            hits += [f"{row['date']}: {e}" for e in row.get("events", []) if MACRO_WORDS.search(e)]
    return hits


def compute(snap: dict, as_of_ms: int, max_exposure: float = 0.5, live: bool = True) -> dict:
    as_of = day(as_of_ms)
    comps, evidence = {}, []
    parts = {
        "etf_flows": etf_flows(snap, as_of),
        "trend": trend(snap, as_of),
        "breadth": breadth(snap, as_of, live),
        "news": news(snap, as_of_ms) if live else (None, [], "headlines older than 7 days are not in the API"),
        "sentiment": sentiment(snap, as_of_ms),
    }
    for name, (score, evid, why) in parts.items():
        comps[name] = {"score": None if score is None else round(score, 3), "weight": WEIGHTS[name], "note": why}
        evidence += evid
    have = {k: v for k, v in comps.items() if v["score"] is not None}
    w = sum(v["weight"] for v in have.values())
    composite = sum(v["score"] * v["weight"] for v in have.values()) / w if w else 0.0

    gates, target = [], clamp(composite, 0, 1) * max_exposure
    if comps["etf_flows"]["score"] is None or comps["trend"]["score"] is None:
        gates.append("core data missing (ETF flows or price trend): hold, no new risk")
        target = None
    else:
        e, t = comps["etf_flows"]["score"], comps["trend"]["score"]
        if e * t < 0 and abs(e) > 0.25 and abs(t) > 0.25:
            gates.append("ETF flows and price trend disagree: size halved")
            target *= 0.5
    macro = macro_gate(snap, as_of)
    if macro and target:
        gates.append("high-impact macro event within 24 h (" + "; ".join(macro[:3]) + "): size halved")
        target *= 0.5
    stance = "risk-on" if composite >= RISK_ON else "risk-off" if composite <= RISK_OFF else "neutral"
    return {"as_of": as_of, "as_of_ms": as_of_ms, "asset": snap["asset"], "composite": round(composite, 3),
            "stance": stance, "target_exposure": None if target is None else round(target, 4),
            "max_exposure": max_exposure, "components": comps, "gates": gates, "evidence": evidence,
            "weights_used": round(w, 2)}
