"""On-chain receipts: put each call's receipt hash into a transaction, so a public chain timestamps the track record.

A git history can be rewritten and a server can be edited; a transaction can't. Once a day the desk sends one
zero-value transaction to its own address whose calldata is the tag "sosodesk" followed by the 32-byte receipt
(SHA-256 of the call and its evidence). Anyone can then check, with nothing but a public RPC:

    python -m desk verify --date 2026-11-12

which recomputes the receipt from data/receipts/<date>.json and compares it with the calldata and block time of the
transaction listed in data/anchors.json.

Signing uses Foundry's `cast` with a key file you control. The key is never written to data/, logs or the page.
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .book import receipt_id

TAG = b"sosodesk".hex()  # 736f736f6465736b
CHAINS = {
    "sepolia": {"chain_id": 11155111, "rpc": "https://ethereum-sepolia-rpc.publicnode.com",
                "explorer": "https://sepolia.etherscan.io/tx/"},
}


def calldata(receipt: str) -> str:
    if len(receipt) != 64 or any(c not in "0123456789abcdef" for c in receipt):
        raise ValueError("receipt must be 64 lowercase hex chars (SHA-256)")
    return "0x" + TAG + receipt


def load_anchors(data: Path) -> dict:
    p = Path(data) / "anchors.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def rpc_call(url: str, method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                          "User-Agent": "sosodesk"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    if out.get("error"):
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"]


def anchor(data: Path, date: str, key_file: Path, cast: str = "cast", chain: str = "sepolia",
           rpc: str | None = None) -> dict:
    """Send the anchoring transaction for one day's receipt and record it in data/anchors.json."""
    data = Path(data)
    anchors = load_anchors(data)
    if date in anchors:
        return anchors[date]
    rec = json.loads((data / "receipts" / f"{date}.json").read_text(encoding="utf-8"))
    rid = rec["receipt"]
    if receipt_id(rec["signal"]) != rid:
        raise RuntimeError(f"receipt for {date} does not match its contents; refusing to anchor")
    cfg = CHAINS[chain]
    url = rpc or cfg["rpc"]
    key = Path(key_file).read_text(encoding="utf-8").strip()
    key = key if key.startswith("0x") else "0x" + key
    addr = subprocess.run([cast, "wallet", "address", "--private-key", key], capture_output=True, text=True,
                          timeout=60).stdout.strip()
    if not addr.startswith("0x"):
        raise RuntimeError("cast could not derive the anchoring address")
    r = subprocess.run([cast, "send", addr, calldata(rid), "--value", "0", "--rpc-url", url,
                        "--private-key", key, "--json"], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError("cast send failed: " + (r.stdout + r.stderr).replace(key, "<key>")[-800:])
    tx = json.loads(r.stdout)
    entry = {"receipt": rid, "chain": chain, "chain_id": cfg["chain_id"], "from": addr,
             "tx": tx["transactionHash"], "block": int(tx["blockNumber"], 16),
             "status": int(tx["status"], 16), "data_mode": rec.get("data_mode", "live")}
    anchors[date] = entry
    (data / "anchors.json").write_text(json.dumps(anchors, indent=1, sort_keys=True), encoding="utf-8")
    return entry


def verify(data: Path, date: str, rpc: str | None = None, call=rpc_call) -> dict:
    """Recompute the receipt, then check the anchoring transaction on chain. Returns a report; raises on mismatch."""
    data = Path(data)
    rec = json.loads((data / "receipts" / f"{date}.json").read_text(encoding="utf-8"))
    out = {"date": date, "receipt": rec["receipt"], "content_matches": receipt_id(rec["signal"]) == rec["receipt"]}
    if not out["content_matches"]:
        raise RuntimeError(f"{date}: receipt file was edited (hash of contents != stored receipt)")
    a = load_anchors(data).get(date)
    if not a:
        out["anchored"] = False
        return out
    url = rpc or CHAINS[a["chain"]]["rpc"]
    tx = call(url, "eth_getTransactionByHash", [a["tx"]])
    if not tx or tx["input"].lower() != calldata(rec["receipt"]):
        raise RuntimeError(f"{date}: transaction {a['tx']} does not carry this receipt")
    blk = call(url, "eth_getBlockByNumber", [tx["blockNumber"], False])
    ts = int(blk["timestamp"], 16)
    out.update(anchored=True, chain=a["chain"], tx=a["tx"], block=int(tx["blockNumber"], 16),
               anchored_at=datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    return out
