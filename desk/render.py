"""Static site: one page that shows today's call, every input behind it, the limits, and the track record."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from html import escape as h
from pathlib import Path

from .book import LIMITS, START_EQUITY
from .signal import RISK_OFF, RISK_ON, WEIGHTS

NAMES = {"etf_flows": "US spot ETF flows", "trend": "Price trend", "breadth": "SoSoValue index breadth",
         "news": "News tone (24 h)", "sentiment": "Fear & Greed (contrarian)"}
CSS = """
:root{--bg:#0f1115;--card:#171a21;--ink:#e7e9ee;--mut:#9aa3b2;--on:#2fbf71;--off:#e5534b;--neu:#d4a72c;--line:#2a2f3a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px}h1{font-size:24px;margin:0}h2{font-size:17px;margin:28px 0 10px}
.sub{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.big{font-size:30px;font-weight:700}
.risk-on{color:var(--on)}.risk-off{color:var(--off)}.neutral{color:var(--neu)}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--mut);font-weight:600}.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{position:relative;height:10px;background:#232835;border-radius:5px;min-width:120px}.bar i{position:absolute;top:0;height:10px;border-radius:5px}
.mid{position:absolute;left:50%;top:-3px;width:1px;height:16px;background:var(--mut)}
code{background:#232835;padding:1px 5px;border-radius:4px;font-size:12.5px;color:#b9c3d6}a{color:#7fb0ff}
.banner{background:#3a2e0b;border:1px solid #7a5d12;color:#f2d47c;padding:10px 14px;border-radius:8px;margin:12px 0}
.gate{color:var(--neu)}details summary{cursor:pointer;color:var(--mut)}.ok{color:var(--on)}.bad{color:var(--off)}
"""


def pct(x, d=1):
    return "–" if x is None else f"{x * 100:+.{d}f}%"


def bar(score):
    if score is None:
        return '<span class="sub">n/a</span>'
    left, width = (50, score * 50) if score >= 0 else (50 + score * 50, -score * 50)
    col = "var(--on)" if score > 0 else "var(--off)"
    return f'<div class="bar"><span class="mid"></span><i style="left:{left:.1f}%;width:{width:.1f}%;background:{col}"></i></div>'


def chart(entries):
    if len(entries) < 2:
        return '<p class="sub">The chart appears after the second run.</p>'
    W, H, P = 960, 240, 30
    ys = [e["equity"] for e in entries] + [e["buy_hold"] for e in entries]
    lo, hi = min(ys), max(ys)
    hi = hi if hi > lo else lo + 1

    def pts(key):
        n = len(entries) - 1
        return " ".join(f"{P + i * (W - 2 * P) / n:.1f},{H - P - (e[key] - lo) / (hi - lo) * (H - 2 * P):.1f}"
                        for i, e in enumerate(entries))
    lab = (f'<text x="{P}" y="16" fill="#9aa3b2" font-size="12">${hi:,.0f}</text>'
           f'<text x="{P}" y="{H - 8}" fill="#9aa3b2" font-size="12">${lo:,.0f} · {h(entries[0]["date"])} → '
           f'{h(entries[-1]["date"])}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Desk equity vs buy and hold">{lab}'
            f'<polyline fill="none" stroke="#5b6475" stroke-width="2" stroke-dasharray="5 4" points="{pts("buy_hold")}"/>'
            f'<polyline fill="none" stroke="#7fb0ff" stroke-width="2.5" points="{pts("equity")}"/></svg>'
            '<p class="sub"><span style="color:#7fb0ff">━ desk (paper)</span> &nbsp; '
            '<span style="color:#5b6475">┅ buy &amp; hold</span></p>')


def render(site: Path, data_dir: Path, sig: dict, rid: str, book: dict, card: dict, snap: dict) -> None:
    site = Path(site)
    site.mkdir(parents=True, exist_ok=True)
    entries = book["entries"]
    last = entries[-1]
    mode = book.get("data_mode", "live")
    upd = datetime.fromtimestamp(book.get("updated_ms", 0) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>sosodesk · {h(sig['asset'])} {h(sig['stance'])}</title><style>{CSS}</style><main>",
           "<h1>sosodesk</h1><div class=sub>A one-person crypto desk that shows its work. Every call is built from "
           "SoSoValue data, every input is listed below with the endpoint it came from, and every call is kept "
           f"with a receipt hash so the track record can't be quietly edited. Updated {h(upd)}.</div>"]
    if mode != "live":
        out.append('<div class="banner"><b>Demo data.</b> This build ran on recorded test fixtures, not the live '
                   'SoSoValue API. The live run replaces this page.</div>')
    out.append('<div class="grid">')
    out.append(f'<div class="card"><div class="sub">Call for {h(sig["as_of"])}</div>'
               f'<div class="big {h(sig["stance"])}">{h(sig["stance"])}</div><div class="sub">composite '
               f'{sig["composite"]:+.2f} (on ≥ {RISK_ON:+.2f}, off ≤ {RISK_OFF:+.2f})</div></div>')
    out.append(f'<div class="card"><div class="sub">Paper position in {h(sig["asset"])}</div>'
               f'<div class="big">{last["weight"] * 100:.0f}%</div><div class="sub">of equity · cap '
               f'{sig["max_exposure"] * 100:.0f}% · signal wanted {pct(sig["target_exposure"], 0) if sig["target_exposure"] is not None else "hold"}</div></div>')
    out.append(f'<div class="card"><div class="sub">Paper equity (start ${START_EQUITY:,.0f})</div>'
               f'<div class="big">${last["equity"]:,.0f}</div><div class="sub">desk {pct(card["desk_return"])} · '
               f'buy &amp; hold {pct(card["buy_hold_return"])}</div></div>')
    hr = card["hit_rate"]
    out.append(f'<div class="card"><div class="sub">Calls right next day</div><div class="big">'
               f'{"–" if hr is None else f"{hr * 100:.0f}%"}</div><div class="sub">{card["hits"]} of {card["scored"]} '
               f'directional calls · max drawdown {card["max_drawdown"] * 100:.1f}%</div></div></div>')

    if sig["gates"] or last["notes"]:
        out.append('<div class="card"><b>Risk gates that fired</b><ul>' +
                   "".join(f'<li class="gate">{h(g)}</li>' for g in sig["gates"] + last["notes"]) + "</ul></div>")

    out.append("<h2>Why: the five inputs</h2><div class=card><table><tr><th>Input</th><th>Weight</th><th>Score</th>"
               "<th style='width:30%'></th><th>Note</th></tr>")
    for k, c in sig["components"].items():
        sc = "–" if c["score"] is None else f'{c["score"]:+.2f}'
        out.append(f'<tr><td>{h(NAMES[k])}</td><td class=num>{c["weight"]:.2f}</td><td class=num>{sc}</td>'
                   f'<td>{bar(c["score"])}</td><td class=sub>{h(c["note"] or "")}</td></tr>')
    out.append(f'</table><p class=sub>Composite = weighted mean of the available inputs '
               f'(weights used: {sig["weights_used"]:.2f} of 1.00). Target = composite clipped to [0, 1] × '
               f'{sig["max_exposure"] * 100:.0f}% cap, then the gates below.</p></div>')

    out.append("<h2>Evidence: every data point behind this call</h2><div class=card><table>"
               "<tr><th>Input</th><th>Data point</th><th class=num>Value</th><th>Source</th></tr>")
    for e in sig["evidence"]:
        v = e["value"]
        vs = f"{v:,}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)
        label = f'<a href="{h(e["link"])}" rel="noopener">{h(e["label"])}</a>' if e.get("link") else h(e["label"])
        out.append(f'<tr><td class=sub>{h(NAMES[e["component"]])}</td><td>{label}</td><td class=num>{h(vs)}</td>'
                   f'<td><code>{h(e["source"])}</code></td></tr>')
    out.append(f'</table><p class=sub>Receipt <code>{h(rid)}</code> = SHA-256 of this call and its evidence '
               f'(<a href="data/receipts/{h(sig["as_of"])}.json">raw receipt with every API request made</a>).</p></div>')

    out.append(f"<h2>Track record</h2><div class=card>{chart(entries)}<table><tr><th>Date</th><th>Call</th>"
               "<th class=num>Position</th><th class=num>Next day</th><th class=num>Next 7 days</th><th>Right?</th>"
               "<th>Receipt</th></tr>")
    rows = {r["date"]: r for r in card["rows"]}
    for e in reversed(entries[-30:]):
        r = rows.get(e["date"], {})
        right = {True: '<span class=ok>yes</span>', False: '<span class=bad>no</span>'}.get(r.get("right"), "–")
        bf = " <span class=sub>(replay)</span>" if e.get("backfill") else ""
        out.append(f'<tr><td>{h(e["date"])}{bf}</td><td class="{h(e["stance"])}">{h(e["stance"])}</td>'
                   f'<td class=num>{e["weight"] * 100:.0f}%</td><td class=num>{pct(r.get("r1d"))}</td>'
                   f'<td class=num>{pct(r.get("r7d"))}</td><td>{right}</td>'
                   f'<td><a href="data/receipts/{h(e["date"])}.json"><code>{h(e["receipt"][:12])}</code></a></td></tr>')
    out.append("</table></div>")

    out.append(f"<h2>Rules and limits</h2><div class=card><ul>"
               f"<li>Weights: " + ", ".join(f"{h(NAMES[k])} {v:.2f}" for k, v in WEIGHTS.items()) + "</li>"
               f"<li>Long-only spot {h(sig['asset'])}, never above {sig['max_exposure'] * 100:.0f}% of equity; "
               f"at most {LIMITS['max_step'] * 100:.0f}% of equity traded per day; {LIMITS['fee_bps']} bp fee per trade.</li>"
               f"<li>Kill-switch: {LIMITS['kill_drawdown'] * 100:.0f}% below peak equity → flat for "
               f"{LIMITS['cooldown_days']} days.</li>"
               "<li>ETF flows and price trend disagree strongly → half size. High-impact macro event (CPI, FOMC, "
               "payrolls, PCE, GDP) today or tomorrow → half size. Missing ETF or price data → hold, no new risk.</li>"
               "<li>No look-ahead: a call uses ETF days and closes strictly before its date and fills at that "
               "date's close. Replayed days (first run) use the same rules without news or index breadth, which "
               "the API does not serve historically.</li>"
               "<li>Paper trading only. Not investment advice.</li></ul></div>")
    errs = snap.get("errors") or []
    if errs:
        out.append("<details class=card><summary>" + f"{len(errs)} API call(s) failed this run</summary><ul>" +
                   "".join(f"<li><code>{h(e['what'])}</code>: {h(e['error'])}</li>" for e in errs) + "</ul></details>")
    out.append('<p class=sub>Data: <a href="https://sosovalue.com">SoSoValue</a> OpenAPI. Source: '
               '<a href="https://github.com/tesselworksHQ/sosodesk">github.com/tesselworksHQ/sosodesk</a> · '
               'built by Tessel Works.</p></main>')
    (site / "index.html").write_text("\n".join(out), encoding="utf-8")
    # ship the data next to the page so the receipt links resolve on GitHub Pages
    dst = site / "data"
    if dst.exists():
        shutil.rmtree(dst)
    src = Path(data_dir)
    if src.exists():
        shutil.copytree(src, dst)
    (site / "latest.json").write_text(json.dumps({"signal": sig, "receipt": rid, "position": last["weight"],
                                                  "scorecard": card}, indent=1), encoding="utf-8")
