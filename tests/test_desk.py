"""python -m unittest discover -s tests   (no network, no key)"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desk import book as bk  # noqa: E402
from desk.signal import NEG, POS, compute, macro_gate  # noqa: E402

NOW = 1794492000000  # 2026-11-12 14:00 UTC


def kl(closes, start_ms=NOW - 120 * 86_400_000):
    return [{"timestamp": start_ms + i * 86_400_000, "open": c, "high": c, "low": c, "close": c} for i, c in enumerate(closes)]


class Lexicon(unittest.TestCase):
    def test_words(self):
        self.assertTrue(NEG.search("Exchange hacked, $40M drained"))
        self.assertTrue(POS.search("Spot ETFs see record inflows"))
        self.assertFalse(POS.search("Bathtub sales"))  # 'ath' needs a word boundary
        self.assertFalse(NEG.search("Banner year for miners"))


class Book(unittest.TestCase):
    def sig(self, d, target, stance="risk-on"):
        return {"as_of": d, "asset": "BTC", "stance": stance, "composite": 0.5, "target_exposure": target, "gates": []}

    def test_turnover_cap_and_idempotent_rerun(self):
        k = kl([100.0] * 121)
        from desk.signal import day
        d1, d2 = day(k[-2]["timestamp"]), day(k[-1]["timestamp"])
        b = bk.new_book()
        e1 = bk.step(b, self.sig(d1, 0.5), k, "r1")
        self.assertAlmostEqual(e1["weight"], 0.20)
        bk.step(b, self.sig(d2, 0.5), k, "r2")
        bk.step(b, self.sig(d2, 0.5), k, "r2b")  # same date again replaces, does not stack
        self.assertEqual(len(b["entries"]), 2)
        self.assertAlmostEqual(b["entries"][-1]["weight"], 0.40)

    def test_kill_switch(self):
        closes = [100.0] * 115 + [100, 60, 60, 60, 60, 60]
        k = kl(closes)
        from desk.signal import day
        dates = [day(x["timestamp"]) for x in k[-6:]]
        b = bk.new_book()
        bk.step(b, self.sig(dates[0], 0.2), k, "a")  # 20% long at 100
        bk.step(b, self.sig(dates[1], 0.2), k, "b")  # price -40% -> equity -8%: no kill yet
        b["entries"][-1]["state"]["peak"] = 11_000  # force a 10%+ drawdown from peak
        e = bk.step(b, self.sig(dates[2], 0.5), k, "c")
        self.assertEqual(e["weight"], 0.0)
        self.assertTrue(any("KILL-SWITCH" in n for n in e["notes"]))
        e = bk.step(b, self.sig(dates[3], 0.5), k, "d")
        self.assertEqual(e["weight"], 0.0)  # still locked during cooldown


class Signal(unittest.TestCase):
    def test_missing_core_data_holds(self):
        s = compute({"asset": "BTC", "etf": {}, "klines": []}, NOW)
        self.assertIsNone(s["target_exposure"])
        self.assertTrue(s["gates"])

    def test_macro_gate(self):
        snap = {"macro": [{"date": "2026-11-13", "events": ["CPI", "Retail Sales"]}]}
        self.assertEqual(macro_gate(snap, "2026-11-12"), ["2026-11-13: CPI"])
        self.assertEqual(macro_gate(snap, "2026-11-10"), [])

    def test_no_lookahead(self):
        """Changing the fill day's own ETF row or close must not change that day's call."""
        closes = [100 + i for i in range(121)]
        etf = [{"date": f"2026-11-{d:02d}", "total_net_inflow": 1e8} for d in range(1, 12)]
        a = compute({"asset": "BTC", "etf": {"BTC": etf}, "klines": kl(closes), "currency_id": "x"}, NOW, live=False)
        etf2 = etf + [{"date": "2026-11-12", "total_net_inflow": -9e9}]
        closes2 = closes[:-1] + [1.0]
        b = compute({"asset": "BTC", "etf": {"BTC": etf2}, "klines": kl(closes2), "currency_id": "x"}, NOW, live=False)
        self.assertEqual(a["composite"], b["composite"])


class EndToEnd(unittest.TestCase):
    def test_fixture_run(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            run = lambda *a: subprocess.run([sys.executable, *a], cwd=ROOT, check=True, capture_output=True, text=True)  # noqa: E731
            run("tools/make_fixtures.py", "--out", str(tmp / "fx"))
            out = run("-m", "desk", "run", "--fixtures", str(tmp / "fx"), "--data", str(tmp / "data"),
                      "--site", str(tmp / "site"), "--now-ms", str(NOW))
            res = json.loads(out.stdout)
            self.assertIn(res["stance"], ("risk-on", "neutral", "risk-off"))
            html = (tmp / "site" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Demo data", html)
            latest = json.loads((tmp / "data" / "latest.json").read_text())
            rec = json.loads((tmp / "data" / "receipts" / f"{res['as_of']}.json").read_text())
            self.assertEqual(rec["receipt"], bk.receipt_id(rec["signal"]))  # receipt verifies
            self.assertEqual(latest["receipt"], rec["receipt"])
            self.assertTrue(rec["api_calls"])
            import xml.etree.ElementTree as ET
            atom = "{http://www.w3.org/2005/Atom}"
            root = ET.parse(tmp / "site" / "feed.xml").getroot()
            entries = root.findall(atom + "entry")
            self.assertTrue(entries)
            self.assertIn(rec["receipt"], entries[0].find(atom + "id").text)  # newest call first
            self.assertEqual(latest["execution"]["status"], "ok" if latest["entry"]["trade_notional"] else "no-trade")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Sodex(unittest.TestCase):
    MKT = {"symbol": {"id": 1, "name": "vBTC_vUSDC", "stepSize": "0.00001", "minQuantity": "0.00001",
                      "minNotional": "5", "takerFee": "0.00065"},
           "book": {"blockHeight": 7, "bids": [["99999", "0.01"], ["99990", "1"]],
                    "asks": [["100001", "0.01"], ["100010", "0.01"], ["101000", "5"]]}}

    def test_buy_walks_asks_and_caps_price(self):
        from desk.sodex import ticket
        t = ticket(self.MKT, 2_000.0, "2026-11-12")  # 0.02 BTC: two ask levels
        self.assertEqual(t["status"], "ok")
        self.assertEqual((t["side"], t["quantity"], t["levels"]), ("BUY", "0.02000", 2))
        self.assertEqual(t["order"]["orders"][0]["price"], "100010")  # IOC limit = worst level walked
        self.assertLess(t["slippage_bps"], 1)

    def test_slippage_limit_blocks(self):
        from desk.sodex import ticket
        t = ticket(self.MKT, 20_000.0, "2026-11-12")  # 0.2 BTC reaches the 101000 level: ~50 bp
        self.assertEqual(t["status"], "blocked")
        self.assertIn("slippage", t["note"])

    def test_no_trade_and_sell(self):
        from desk.sodex import ticket
        self.assertEqual(ticket(self.MKT, 0.0, "d")["status"], "no-trade")
        self.assertEqual(ticket(self.MKT, -500.0, "d")["side"], "SELL")


class Anchor(unittest.TestCase):
    def setUp(self):
        from desk import anchor as an
        self.an = an
        self.tmp = Path(tempfile.mkdtemp())
        sig = {"as_of": "2026-11-12", "asset": "BTC", "stance": "neutral", "composite": 0.0}
        self.rid = bk.receipt_id(sig)
        (self.tmp / "receipts").mkdir()
        (self.tmp / "receipts" / "2026-11-12.json").write_text(json.dumps({"receipt": self.rid, "signal": sig}))
        (self.tmp / "anchors.json").write_text(json.dumps({"2026-11-12": {"chain": "sepolia", "tx": "0xabc",
                                                                         "receipt": self.rid}}))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_chain(self, calldata):
        def call(url, method, params):
            if method == "eth_getTransactionByHash":
                return {"input": calldata, "blockNumber": "0x10"}
            return {"timestamp": hex(1794492000)}
        return call

    def test_calldata_format(self):
        self.assertEqual(self.an.calldata(self.rid), "0x736f736f6465736b" + self.rid)
        with self.assertRaises(ValueError):
            self.an.calldata("zz")

    def test_verify_ok(self):
        rep = self.an.verify(self.tmp, "2026-11-12", call=self.fake_chain(self.an.calldata(self.rid)))
        self.assertTrue(rep["anchored"])
        self.assertEqual(rep["anchored_at"], "2026-11-12 14:00:00 UTC")

    def test_verify_catches_wrong_tx_and_edited_receipt(self):
        with self.assertRaises(RuntimeError):
            self.an.verify(self.tmp, "2026-11-12", call=self.fake_chain(self.an.calldata("0" * 64)))
        p = self.tmp / "receipts" / "2026-11-12.json"
        rec = json.loads(p.read_text())
        rec["signal"]["stance"] = "risk-on"  # rewrite history
        p.write_text(json.dumps(rec))
        with self.assertRaises(RuntimeError):
            self.an.verify(self.tmp, "2026-11-12", call=self.fake_chain(self.an.calldata(self.rid)))


if __name__ == "__main__":
    unittest.main()
