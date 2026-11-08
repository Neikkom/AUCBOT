"""The agent loop: ingest -> filter -> evaluate -> price cap -> watchlist ->
alerts -> auction reminders -> bid plans."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Iterable, Optional

from .evaluator import Evaluator
from .filters import hard_filter
from .models import BuyBox, Decision, Lot
from .notify import Notifier
from .pricing import max_bid
from .sources import LotSource
from .store import Store

REMINDER_WINDOWS = [("24h", timedelta(hours=24)), ("1h", timedelta(hours=1))]


def format_lot(lot: Lot, d: Decision) -> str:
    lines = [f"{lot.title_line}  [{lot.source.upper()} #{lot.lot_id}]"]
    if lot.url:
        lines.append(lot.url)
    miles = f"{lot.odometer:,}" if lot.odometer is not None else "?"
    lines.append(f"{miles} mi | {lot.primary_damage or '?'} | {lot.title_type or '?'} title | "
                 f"{lot.condition or '?'} | {lot.location or '?'}")
    if lot.auction_date:
        lines.append(f"Auction: {lot.auction_date:%a %b %d %Y %H:%M}")
    ev = d.evaluation
    if ev:
        lines.append(f"Score: {ev.score}/100 — {ev.summary}")
        lines.append("Buy-box check:")
        mark = {"yes": "✔", "no": "✘", "unknown": "?"}
        for a in ev.answers:
            lines.append(f"  {mark[a.answer]} [{a.kind}] {a.question} — {a.evidence}")
        if ev.risk_flags:
            lines.append("Risks: " + "; ".join(ev.risk_flags))
        lines.append(f"Est. retail ${ev.est_retail_value:,.0f} | repairs ${ev.repair_low:,.0f}–${ev.repair_high:,.0f}")
    if d.max_bid is not None:
        lines.append(f"Suggested max bid (price cap): ${d.max_bid:,.0f}")
    return "\n".join(lines)


class AuctionAgent:
    def __init__(self, store: Store, evaluator: Evaluator, notifier: Notifier, box: BuyBox):
        self.store, self.evaluator, self.notifier, self.box = store, evaluator, notifier, box

    # 1. ingest ------------------------------------------------------------
    def ingest(self, sources: Iterable[LotSource]) -> list[Lot]:
        new: list[Lot] = []
        for src in sources:
            name = getattr(src, "name", type(src).__name__)
            try:
                count = 0
                for lot in src.fetch():
                    count += 1
                    if self.store.upsert_lot(lot):
                        new.append(lot)
                self.store.set_meta(f"source:{name}", json.dumps(
                    {"ok": True, "at": datetime.now().isoformat(timespec="seconds"), "lots": count}))
            except Exception as e:  # one broken feed shouldn't stop the cycle
                print(f"[ingest] {name} failed: {e}")
                self.store.set_meta(f"source:{name}", json.dumps(
                    {"ok": False, "at": datetime.now().isoformat(timespec="seconds"), "error": str(e)[:300]}))
        return new

    def link(self, lot: Lot) -> Optional[str]:
        """Where a tap on a phone alert should go: the dashboard if configured, else the listing."""
        base = os.getenv("DASHBOARD_URL")
        return f"{base.rstrip('/')}/#lot={lot.key}" if base else lot.url

    # 2-4. screen one lot ----------------------------------------------------
    def screen(self, lot: Lot) -> Decision:
        passed, reasons = hard_filter(lot, self.box)
        d = Decision(lot_key=lot.key, passed_filters=passed, filter_reasons=reasons)
        if passed:
            ev = self.evaluator.evaluate(lot, self.box)
            d.evaluation = ev
            d.max_bid, d.max_bid_breakdown = max_bid(ev, self.box)
        self.store.save_decision(d, self.box.name)
        return d

    def screen_new(self, lots: Iterable[Lot]) -> list[tuple[Lot, Decision]]:
        matches = []
        for lot in lots:
            if self.store.has_decision(lot.key, self.box.name):
                continue
            try:
                d = self.screen(lot)
            except Exception as e:
                print(f"[screen] {lot.key} failed: {e}")
                continue
            if d.passed_filters and d.evaluation:
                matches.append((lot, d))
                if d.evaluation.score >= self.box.auto_watch_score and d.max_bid:
                    self.store.watch(lot.key, f"auto: score {d.evaluation.score}", d.max_bid)
        return matches

    def alert_matches(self, matches: list[tuple[Lot, Decision]]) -> None:
        matches = sorted(matches, key=lambda m: m[1].evaluation.score, reverse=True)
        for lot, d in matches:
            if self.store.alert_sent(lot.key, "match"):
                continue
            watched = d.evaluation.score >= self.box.auto_watch_score
            tag = "Added to watchlist" if watched else "Possible match"
            self.notifier.send(f"{tag}: {lot.title_line} (score {d.evaluation.score})", format_lot(lot, d),
                               url=self.link(lot), priority="high" if watched else "default")
            self.store.mark_alert(lot.key, "match")

    # 5. auction reminders ----------------------------------------------------
    def remind(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now()
        sent = 0
        for row in self.store.watchlist():
            lot = self.store.get_lot(row["key"])
            if not lot or not lot.auction_date or lot.auction_date < now:
                continue
            due = [(k, w) for k, w in sorted(REMINDER_WINDOWS, key=lambda kw: kw[1])
                   if lot.auction_date - now <= w]
            if not due or self.store.alert_sent(lot.key, f"remind-{due[0][0]}"):
                continue
            kind = due[0][0]
            cap = row["approved_amount"] or row["max_bid"] or 0
            status = row["bid_status"]
            action = ("Your approved bid cap is set." if status in ("approved", "placed")
                      else "No bid cap approved yet. Approve one in the dashboard or you'll need to bid live.")
            approved = status in ("approved", "placed")
            prio = "default" if kind == "24h" else ("high" if approved else "urgent")
            self.notifier.send(
                f"Auction in <{kind}: {lot.title_line}",
                f"{lot.source.upper()} #{lot.lot_id} sells {lot.auction_date:%a %b %d %H:%M}.\n"
                f"Cap: ${cap:,.0f} ({status}). {action}\n{lot.url or ''}",
                url=self.link(lot), priority=prio)
            for k, _ in due:  # wider windows are now redundant
                self.store.mark_alert(lot.key, f"remind-{k}")
            sent += 1
        return sent

    # full cycle --------------------------------------------------------------
    def run_once(self, sources: Iterable[LotSource]) -> dict:
        new = self.ingest(sources)
        matches = self.screen_new(new)
        self.alert_matches(matches)
        reminders = self.remind()
        result = {"new_lots": len(new), "matches": len(matches), "reminders": reminders,
                  "at": datetime.now().isoformat(timespec="seconds")}
        self.store.set_meta("last_cycle", json.dumps(result))
        return result
