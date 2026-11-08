# AUCBOT

Watches salvage/insurance auction lots (Copart, IAA, ...), screens new lots against
your **buy box**, turns each listing into buy-box yes/no questions, suggests a
**price cap**, keeps a **watchlist**, pushes **phone alerts**, reminds you before
sales, and tracks your **pre-bid (bid cap)** approvals from a **web dashboard**.

```
sources.yaml ─► poll every N min ─► dedupe ─► hard filters ─► Claude evaluation ─► price cap ─► watchlist
 (provider API,                                  (free)       buy-box Q&A, risks,    (code math)       │
  CSV exports, JSON)                                          value & repairs                         ├► phone alerts (ntfy / Telegram)
                                                                                                      ├► reminders 24h / 1h before sale
                                                                                                      └► dashboard: approve bid caps
```

## Quick start (your computer)

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in what you have, then:
set -a; . ./.env; set +a
python -m auction_agent serve --buybox buybox.yaml --sources sources.yaml --every 15
```

Open http://localhost:8000. The sample lots in `data/sample_lots.json` show up right away.
Without `ANTHROPIC_API_KEY` a basic offline keyword checker is used instead of Claude.

## Dashboard

- **Watchlist:** saved lots with countdown to sale, score, and price cap. Ended auctions fade and sink to the bottom.
- **Matches:** every lot that passed your buy box. Tap one, then **Add to watchlist**.
- **Lot detail:** buy-box questions with evidence, risks, value/repair estimates, and the
  price-cap math. **Approve cap** records your max bid and tells you exactly what to
  enter on the auction site. **I entered it** marks it done.
- **Setup:** source health, active alert channels, a **Send test alert** button, and your buy box.
- **Check now** polls immediately instead of waiting for the next cycle.

Set `DASHBOARD_TOKEN` to require a password. Do this before the dashboard is reachable
from the internet, since it controls bid approvals.

## Phone alerts

You get a push for each new match (high priority if it was auto-added to the watchlist), a reminder
24h before a watched lot sells, and another 1h before. The 1h reminder is urgent if you haven't
approved a cap yet. Tapping an alert opens the lot in the dashboard (set `DASHBOARD_URL`), or the listing otherwise.

**ntfy (easiest, free, no account):**
1. Install the **ntfy** app (iOS / Android).
2. Subscribe to a topic name nobody would guess, e.g. `aucbot-7f3k9q`. Anyone who knows the name can read the alerts.
3. Set `NTFY_TOPIC=aucbot-7f3k9q` and restart. Hit **Send test alert** on the Setup tab.

**Telegram:** message @BotFather, create a bot, and copy its token into `TELEGRAM_BOT_TOKEN`.
Send your bot any message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates`
and copy `chat.id` into `TELEGRAM_CHAT_ID`.

Slack/Discord webhooks (`ALERT_WEBHOOK_URL`) and email (`SMTP_*`) also work.

## Automatic lot feed

Copart and IAA have no public API, and scraping them breaks their terms. For hands-off
operation, subscribe to an auction-data provider that offers Copart/IAA lots over a JSON
API and is licensed to resell that data. Then in `sources.yaml`:

1. Set the provider's URL and put your key in `AUCTION_API_KEY` (the file reads `${AUCTION_API_KEY}`).
2. Point `items_path` at the list of lots in their response, and `next_path` at the next-page link.
3. Map each of our fields to theirs under `fields:` (dotted paths like `odometer.value` work).
4. Set `enabled: true`.

`{since}` in the URL is replaced with the time of the last successful poll, so each cycle
only asks for new or updated lots. `sources.yaml` is re-read every cycle, so edits apply
without a restart. CSV exports (`copart-csv`, `iaa-csv`) can run alongside.

## Running 24/7

Alerts only fire while the agent is running. Options:

- **An always-on computer** (or a Raspberry Pi) running the `serve` command above.
- **A small cloud host** (Railway, Render, Fly.io, a $5 VPS): deploy the included `Dockerfile`,
  set the variables from `.env.example` in the host's settings, and **attach a persistent
  volume at `/app/data`** so the watchlist survives redeploys. Put it behind HTTPS and set `DASHBOARD_TOKEN`.

```bash
docker build -t aucbot .
docker run -d --env-file .env -p 8000:8000 -v aucbot-data:/app/data aucbot
```

## Buy box

`buybox.yaml` has two parts:

- **Hard filters:** year, miles, make/model, title, condition ("run and drive"),
  excluded damage, keys, location, budget. Free and instant. Unknown values pass
  through so the evaluator can judge them from the text.
- **Rules:** `must_haves`, `nice_to_haves`, `deal_breakers` in plain English.
  Claude rewrites each as a yes/no question about the specific lot and answers it with
  evidence from the listing, or "unknown" if the listing doesn't say.

## Price cap

Claude estimates retail value and a low/high repair range. `pricing.py` then does the
math in code: `retail − high repair − transport − misc − target profit`, minus buyer fees.
**The fee table in `pricing.py` is approximate. Replace it with the current one from your account.**

## Bid caps

Both sites support a pre-bid / max bid that bids for you up to a limit. AUCBOT **never bids
by itself**: you approve a cap, it tells you what to enter, and you enter it. Bots that log in
and bid on your account break the member agreement and risk a ban. If your broker has a
bidding API, implement `BidPlacer` in `bidding.py` for it.

## Command line

```bash
python -m auction_agent run --buybox buybox.yaml --sources sources.yaml   # one cycle
python -m auction_agent watchlist
python -m auction_agent show copart:41234567
python -m auction_agent approve-bid copart:41234567 --amount 9500
python -m auction_agent mark-placed copart:41234567
```

## Config

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude evaluates listings (otherwise offline keyword mode) |
| `AGENT_MODEL` | defaults to `claude-opus-5-5` |
| `DASHBOARD_TOKEN` | dashboard password |
| `DASHBOARD_URL` | public dashboard address, used in alert links |
| `POLL_MINUTES` | poll interval in Docker (default 15) |
| `NTFY_TOPIC`, `NTFY_SERVER`, `NTFY_TOKEN` | ntfy push |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram push |
| `ALERT_WEBHOOK_URL` | Slack / Discord |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` | email |
| `AUCTION_API_KEY` | your data provider key, referenced from `sources.yaml` |

## Tests

```bash
python -m pytest -q
```
