"""Command line interface.

    python -m auction_agent run      --buybox buybox.yaml --source json:data/sample_lots.json
    python -m auction_agent watch    --buybox buybox.yaml --sources sources.yaml --every 30
    python -m auction_agent serve    --buybox buybox.yaml --sources sources.yaml --every 15   # dashboard + poller
    python -m auction_agent watchlist
    python -m auction_agent show copart:12345678
    python -m auction_agent approve-bid copart:12345678 [--amount 6500]
    python -m auction_agent mark-placed copart:12345678
    python -m auction_agent remind
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .agent import AuctionAgent, format_lot
from .bidding import ManualBidPlacer, approve
from .evaluator import ClaudeEvaluator, HeuristicEvaluator
from .models import BuyBox
from .notify import from_env
from .sources import build_source, load_sources
from .store import Store


def load_buybox(path: str) -> BuyBox:
    text = Path(path).read_text()
    if path.endswith((".yaml", ".yml")):
        import yaml
        return BuyBox.model_validate(yaml.safe_load(text))
    return BuyBox.model_validate_json(text)


def make_agent(args, store: Store) -> AuctionAgent:
    box = load_buybox(args.buybox) if getattr(args, "buybox", None) else BuyBox()
    use_llm = not getattr(args, "offline", False) and (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
    evaluator = ClaudeEvaluator(model=os.getenv("AGENT_MODEL", "claude-opus-5-5")) if use_llm else HeuristicEvaluator()
    if not use_llm:
        print("[agent] Using offline heuristic evaluator (set ANTHROPIC_API_KEY for Claude).")
    return AuctionAgent(store, evaluator, from_env(), box)


def sources_from(args, store: Store):
    out = list(load_sources(args.sources, store)) if getattr(args, "sources", None) else []
    for spec in args.source or []:
        kind, _, path = spec.partition(":")
        out.append(build_source(kind, os.path.expanduser(path)))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="auction_agent")
    p.add_argument("--db", default="data/agent.db")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("run", "watch", "serve"):
        s = sub.add_parser(name)
        s.add_argument("--buybox", required=True)
        s.add_argument("--sources", help="sources.yaml listing every feed (API, CSV, JSON)")
        s.add_argument("--source", action="append", help="kind:path, kind = json | copart-csv | iaa-csv")
        s.add_argument("--offline", action="store_true", help="skip Claude; use keyword heuristics")
        if name != "run":
            s.add_argument("--every", type=float, default=30 if name == "watch" else 15,
                           help="minutes between polls")
        if name == "serve":
            s.add_argument("--host", default="0.0.0.0")
            s.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))

    sub.add_parser("watchlist")
    s = sub.add_parser("show"); s.add_argument("key")
    s = sub.add_parser("approve-bid"); s.add_argument("key"); s.add_argument("--amount", type=float)
    s = sub.add_parser("mark-placed"); s.add_argument("key")
    s = sub.add_parser("unwatch"); s.add_argument("key")
    s = sub.add_parser("add"); s.add_argument("key"); s.add_argument("--max-bid", type=float)
    s = sub.add_parser("remind"); s.add_argument("--buybox")

    args = p.parse_args(argv)
    try:
        _dispatch(args, Store(args.db))
    except (KeyError, ValueError, FileNotFoundError) as e:
        raise SystemExit(f"error: {e.args[0] if e.args else e}")


def _dispatch(args, store: Store) -> None:

    if args.cmd == "serve":
        from .web import serve
        agent = make_agent(args, store)
        if not (args.sources or args.source):
            raise ValueError("pass --sources sources.yaml (or --source kind:path)")
        # Rebuilt every cycle, so edits to sources.yaml apply without a restart.
        serve(agent, lambda: sources_from(args, store), args.every, args.host, args.port)
    elif args.cmd in ("run", "watch"):
        agent = make_agent(args, store)
        while True:
            print(f"[agent] cycle: {json.dumps(agent.run_once(sources_from(args, store)))}")
            if args.cmd == "run":
                break
            time.sleep(args.every * 60)
    elif args.cmd == "watchlist":
        rows = store.watchlist()
        if not rows:
            print("Watchlist is empty.")
        for r in rows:
            lot = store.get_lot(r["key"])
            when = f"{lot.auction_date:%b %d %H:%M}" if lot and lot.auction_date else "date TBD"
            cap = r["approved_amount"] or r["max_bid"] or 0
            print(f"{r['key']:<22} {lot.title_line if lot else '':<32} {when:<14} cap ${cap:>8,.0f}  {r['bid_status']}")
    elif args.cmd == "show":
        lot, d = store.get_lot(args.key), store.get_decision(args.key)
        if not lot:
            raise SystemExit(f"unknown lot {args.key}")
        print(format_lot(lot, d) if d else lot.model_dump_json(indent=2))
        if d and d.max_bid_breakdown:
            print("Price-cap breakdown:", json.dumps(d.max_bid_breakdown, indent=2))
    elif args.cmd == "approve-bid":
        print(approve(store, args.key, args.amount, ManualBidPlacer()))
    elif args.cmd == "mark-placed":
        store.set_bid(args.key, "placed"); print(f"{args.key} marked as placed.")
    elif args.cmd == "unwatch":
        store.unwatch(args.key); print(f"Removed {args.key}.")
    elif args.cmd == "add":
        d = store.get_decision(args.key)
        store.watch(args.key, "manual", args.max_bid or (d.max_bid if d else None)); print(f"Added {args.key}.")
    elif args.cmd == "remind":
        box = load_buybox(args.buybox) if args.buybox else BuyBox()
        n = AuctionAgent(store, HeuristicEvaluator(), from_env(), box).remind()
        print(f"{n} reminder(s) sent.")


if __name__ == "__main__":
    main()
