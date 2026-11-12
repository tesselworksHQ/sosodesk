# sosodesk: a one-person crypto desk that shows its work

**Live page:** https://tesselworksHQ.github.io/sosodesk/ · built on the [SoSoValue](https://sosovalue.com) OpenAPI

A solo trader or a small fund has two problems that dashboards don't solve:

1. **"Why did we take this position?"** A week later nobody remembers which flow print or headline drove a call.
2. **"Is the track record real?"** Signal sellers quietly drop their bad calls. A buyer can't check.

sosodesk is a daily desk process that answers both. Once a day it reads SoSoValue data, makes one
risk call on BTC (risk-on / neutral / risk-off plus a position size), runs it through hard risk limits on a paper
book, and publishes a page where **every number behind the call is listed with the SoSoValue endpoint it came
from**. Every call gets a **receipt**: a SHA-256 of the call and its evidence, kept in git with the raw API
request log, and **anchored on chain** the same day (the receipt goes into a transaction's calldata), so the
track record can't be quietly rewritten or backdated. Anyone can check a call with `python -m desk verify`.

No server and no database: one Python command is the desk, git is the ledger, a public chain is the clock, and
GitHub Pages is the report.

## What it reads (SoSoValue OpenAPI v1)

| Input | Endpoint | Used for |
|---|---|---|
| US spot ETF flows (BTC, ETH, SOL) | `GET /etfs/summary-history` | 5-day net inflow vs. the 20-day typical day; flow streak |
| BTC daily klines | `GET /currencies` → `GET /currencies/{id}/klines` | trend (close vs. 20/50-day average); paper fills; scoring |
| SoSoValue indices | `GET /indices` → `GET /indices/{t}/market-snapshot` | breadth: how many sector indices are up over 7 days |
| Hot news + BTC news | `GET /news/hot`, `GET /news?currency_id=…` | headline tone over 24 h; every counted headline is linked |
| Fear & Greed | `GET /analyses/fgi_indicator` | contrarian trim at extremes |
| Macro calendar | `GET /macro/events` | event-risk gate (CPI, FOMC, payrolls, PCE, GDP) |
| Market overview | `GET /market/overview` | context on the page |

About 25 requests a day, far below the 20/min and 100k/month limits. The client spaces calls at 3.2 s and
honours `retry_after` on HTTP 429.

## The rules (all in `desk/signal.py` and `desk/book.py`, short enough to read)

- Composite = weighted mean of five scores in [-1, 1]: ETF flows 0.35, trend 0.25, index breadth 0.15, news 0.15,
  Fear & Greed 0.10. Risk-on at ≥ +0.20, risk-off at ≤ −0.20.
- Target position = composite clipped to [0, 1] × 50% cap. Long-only spot, paper money.
- **Gates:** ETF flows and trend strongly disagree → half size. A high-impact macro event today or tomorrow → half
  size. ETF or price data missing → hold, no new risk.
- **Book limits:** at most 20% of equity traded per day; 10 bp fee per trade; **kill-switch** at 10% below peak
  equity → flat for 3 days.
- **No look-ahead:** a call for day D only uses ETF days and closes before D, and fills at D's close.
- The first run replays the last ~20 trading days the API still serves, with the same rules, so the page starts
  with a track record. Replayed days are labelled and run without news and index breadth (not served historically).

## Run it

```bash
python -m unittest discover -s tests            # no key, no network
python tools/make_fixtures.py                    # synthetic SoSoValue-shaped data
python -m desk run --fixtures fixtures           # demo run → site/index.html
SOSO_API_KEY=... python -m desk run              # live run → data/ (commit it) + site/
```

Deploy your own: fork, add the repository secret `SOSO_API_KEY`, enable Pages (source: GitHub Actions), and run
the `desk` workflow. Without the secret the workflow publishes a clearly labelled demo page.

Check a receipt: `python -c "import json,sys; from desk.book import receipt_id; r=json.load(open(sys.argv[1])); print(receipt_id(r['signal'])==r['receipt'])" data/receipts/<date>.json`

Python 3.10+, standard library only (anchoring also needs Foundry's `cast`).

## On-chain receipts: check a call yourself

```bash
python -m desk anchor --key-file my-anchor.key --cast cast   # after a run: anchor the latest call (Sepolia)
python -m desk verify                                        # every day: recompute + check the chain
python -m desk verify --date 2026-11-12                      # one day
```

`anchor` sends one zero-value transaction from the anchoring account to itself. Its calldata is the ASCII tag
`sosodesk` followed by the 32-byte receipt. `data/anchors.json` maps each date to its transaction. `verify` needs
no key: it recomputes the receipt from `data/receipts/<date>.json`, fetches the transaction from a public RPC,
checks that the calldata carries that receipt, and prints the block time. An edited receipt, a swapped
transaction or a backdated call all fail. Gas is ~21.6k per day.

## How this repo runs

The live desk runs on the operator's machine: `python -m desk run` with `SOSO_API_KEY`, then `anchor`, then
`git push` of `data/` only. The API key and the anchoring key never leave that machine. The workflow then renders
the page from the committed `data/`. Forks can instead set the `SOSO_API_KEY` repository secret, and the workflow
runs the desk itself. With neither, it publishes a clearly labelled demo page.

## Changelog

- **Wave 3 (Nov 2026):** first version. SoSoValue client with fixture mode, five-input signal with per-datapoint
  evidence, paper book with turnover cap and kill-switch, receipts, replayed track record, scorecard (next-day and
  7-day hit rate vs. buy & hold), static page and daily GitHub Action. On-chain anchoring of each receipt
  (Ethereum Sepolia) with a keyless `verify` command.

## Not investment advice

Paper trading only. The page shows what a simple, disciplined rule set would have done, with its misses.

Built by Tessel Works (tesselworks@proton.me). MIT licence.
