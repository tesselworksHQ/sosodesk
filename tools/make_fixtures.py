"""Write SYNTHETIC SoSoValue-shaped responses to fixtures/ so the desk runs and is tested without an API key.

The numbers are a seeded random walk, not market data. Shapes follow the SoSoValue OpenAPI v1 docs.
Usage: python tools/make_fixtures.py [--now 2026-11-12] [--seed 7] [--out fixtures]
"""
import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desk.client import fixture_name  # noqa: E402

BTC_ID = "1673723677362319866"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", default="2026-11-12")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="fixtures")
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.strptime(a.now, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    ms = lambda d: int(d.timestamp() * 1000)  # noqa: E731

    def put(path, data, params=None):
        (out / fixture_name(path, params)).write_text(
            json.dumps({"code": 0, "message": "success", "data": data}, indent=1), encoding="utf-8")

    put("/currencies", [{"currency_id": BTC_ID, "symbol": "btc", "name": "Bitcoin"},
                        {"currency_id": "1673723677362319867", "symbol": "eth", "name": "Ethereum"}])
    px, kl = 98_000.0, []
    for i in range(120, -1, -1):
        d = now - timedelta(days=i)
        o = px
        px *= 1 + rnd.gauss(0.0008, 0.022)
        kl.append({"timestamp": ms(d), "open": round(o, 2), "high": round(max(o, px) * 1.01, 2),
                   "low": round(min(o, px) * 0.99, 2), "close": round(px, 2), "volume": rnd.randint(2, 6) * 10**10})
    put(f"/currencies/{BTC_ID}/klines", kl, {"interval": "1d"})

    for sym, scale in (("BTC", 3e8), ("ETH", 8e7), ("SOL", 1e7)):
        rows, cum = [], 40e9
        for i in range(1, 45):
            d = now - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            f = rnd.gauss(0.2, 1.0) * scale
            cum += f
            rows.append({"date": d.strftime("%Y-%m-%d"), "total_net_inflow": round(f),
                         "total_value_traded": round(abs(f) * 12 + 2e9), "total_net_assets": round(cum * 2.4),
                         "cum_net_inflow": round(cum)})
        put("/etfs/summary-history", rows, {"symbol": sym, "country_code": "US"})

    tickers = ["ssimag7", "ssilayer1", "ssilayer2", "ssidefi", "ssimeme", "ssiai", "ssigamefi", "ssirwa"]
    put("/indices", tickers)
    for t in tickers:
        put(f"/indices/{t}/market-snapshot", {"price": round(rnd.uniform(5, 40), 2),
                                              "change_pct_24h": round(rnd.gauss(0, 0.02), 4),
                                              "roi_7d": round(rnd.gauss(0.01, 0.05), 4),
                                              "roi_1m": round(rnd.gauss(0.02, 0.1), 4)})
    heads = ["Spot bitcoin ETFs log fifth day of inflows", "Exchange hacked, $40M drained from hot wallet",
             "Major bank expands crypto custody", "SEC delays decision on altcoin ETF",
             "Bitcoin rallies as dollar weakens", "Lending protocol paused after exploit",
             "State pension fund adds bitcoin reserve allocation", "Miners report record hashrate"]
    hot = [{"id": 900000 + i, "source_link": f"https://sosovalue.com/news/cluster/{900000 + i}",
            "release_time": ms(now - timedelta(hours=2 + 2.5 * i)), "title": t, "content": "<p>synthetic</p>"}
           for i, t in enumerate(heads)]
    put("/news/hot", {"page": 1, "page_size": 50, "total": len(hot), "list": hot})
    put("/news", {"page": 1, "page_size": 50, "total": 0, "list": []}, {"currency_id": BTC_ID, "category": 1})
    put("/macro/events", [{"date": (now + timedelta(days=1)).strftime("%Y-%m-%d"), "events": ["CPI", "Core CPI"]},
                          {"date": (now + timedelta(days=6)).strftime("%Y-%m-%d"), "events": ["Retail Sales"]}])
    put("/market/overview", {"total_market_cap": "3280614829402.85", "total_market_cap_change_pct": "0.6",
                             "total_volume_24h": "90122576829.22", "as_of": ms(now)})
    put("/analyses/fgi_indicator", [{"timestamp": str(ms(now - timedelta(days=i))),
                                     "crypto_fear_&_greed_index": rnd.randint(20, 80)} for i in range(30)])

    # SoDEX public market data (shape of mainnet-gw.sodex.dev/api/v1/spot responses), book around the last close
    def sodex(name, data):
        (out / f"sodex_{name}.json").write_text(json.dumps({"code": 0, "timestamp": ms(now), "data": data}),
                                                encoding="utf-8")
    sodex("markets_symbols__symbol-vBTC_vUSDC", [{
        "id": 1, "name": "vBTC_vUSDC", "displayName": "BTC/USDC", "pricePrecision": 0, "tickSize": "1",
        "stepSize": "0.00001", "minQuantity": "0.00001", "minNotional": "5", "makerFee": "0.00035",
        "takerFee": "0.00065", "status": "TRADING"}])
    mid = round(px)
    sodex("markets_vBTC_vUSDC_orderbook__limit-100", {
        "blockTime": ms(now), "blockHeight": 291_000_000, "updateID": 1,
        "bids": [[str(mid - 1 - i), f"{rnd.uniform(0.01, 0.04):.5f}"] for i in range(100)],
        "asks": [[str(mid + i), f"{rnd.uniform(0.01, 0.04):.5f}"] for i in range(100)]})
    print(f"wrote synthetic fixtures to {out}/ (now={a.now}, seed={a.seed})")


if __name__ == "__main__":
    main()
