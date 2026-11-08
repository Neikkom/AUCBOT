# Auction Agent

Watches salvage/insurance auction lots (Copart, IAA, ...), screens new lots against
your **buy box**, turns each listing into buy-box yes/no questions, suggests a
**price cap**, keeps a **watchlist**, reminds you before sales, and tracks
**pre-bid (bid cap)** approvals.

```
sources ─► ingest (dedupe) ─► hard filters ─► Claude evaluation ─► price cap ─► watchlist ─► alerts
                                (free)         buy-box Q&A, risks,    (deterministic      │
                                               value & repair est.)    math)              └► reminders (24h / 1h)
                                                                                           └► bid-cap approval
```

## Quick start

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...            # optional; without it an offline keyword evaluator is used
python -m auction_agent run --buybox buybox.yaml --source json:data/sample_lots.json
python -m auction_agent watchlist
python -m auction_agent show copart:41234567
python -m auction_agent approve-bid copart:41234567 --amount 9500
python -m auction_agent mark-placed copart:41234567
```

Run continuously (polls every 30 min, sends reminders on each cycle):

```bash
python -m auction_agent watch --buybox buybox.yaml --source copart-csv:~/Downloads/salesdata.csv --source iaa-csv:~/Downloads/iaa.csv --every 30
```

## Buy box

`buybox.yaml` has two parts:

- **Hard filters:** year, miles, make/model, title, condition ("run and drive"),
  excluded damage, keys, location, budget. Free and instant. Unknown values pass
  through so the evaluator can judge them from the text.
- **Rules:** `must_haves`, `nice_to_haves`, `deal_breakers` in plain English.
  Claude rewrites each rule as a yes/no question about the specific lot and answers
  it with evidence from the listing, or "unknown" if the listing doesn't say.

## Price cap

Claude estimates retail value and a low/high repair range. `pricing.py` then
does the math in code: `retail − high repair − transport − misc − target profit`,
minus buyer fees. **The fee table in `pricing.py` is approximate. Replace it with the
current one from your account.**

## Data sources

Shipped adapters read data you're entitled to:

- `json:` any JSON array of lots (see `data/sample_lots.json`)
- `copart-csv:` / `iaa-csv:` the sales-list CSV exports available to members.
  Column maps are in `auction_agent/sources/__init__.py`; check them against a
  real export.

To pull lots automatically, add a `LotSource` that calls a licensed auction-data
provider's API. Scraping Copart/IAA directly is against their terms of use, and
they actively block it.

## Bid caps

Both sites support a pre-bid / max bid that bids for you up to a limit. The agent
**never bids by itself**: `approve-bid` records your approved cap, and the
default `ManualBidPlacer` tells you what to enter. Bots that log in and bid on
your account break the member agreement and risk a ban. If your broker has a
bidding API, implement `BidPlacer` for it.

## Notifications

Console output is always on. Optional channels:

- `ALERT_WEBHOOK_URL`: Slack or Discord webhook
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO`: email

## Config

- `AGENT_MODEL`: defaults to `claude-opus-5-5`. Evaluation uses structured output,
  a cached system prompt, and server-side refusal fallback.

## Tests

```bash
python -m pytest -q
```
