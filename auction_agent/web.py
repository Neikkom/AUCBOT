"""Web dashboard + background poller.

    python -m auction_agent serve --buybox buybox.yaml --sources sources.yaml --every 15

Then open http://localhost:8000 (on your phone too, if it's on the same network
or you host it somewhere). Set DASHBOARD_TOKEN to require a password, which you
should always do if the dashboard is reachable from the internet.
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .agent import AuctionAgent
from .bidding import ManualBidPlacer, approve

STATIC = Path(__file__).parent / "static"


class Poller:
    """Runs agent.run_once on an interval in a background thread."""

    def __init__(self, agent: AuctionAgent, sources_factory: Callable[[], list], every_min: float):
        self.agent, self.sources_factory, self.every = agent, sources_factory, every_min * 60
        self.next_run: Optional[float] = None
        self.running = False
        self._wake = threading.Event()
        self._lock = threading.Lock()

    def cycle(self) -> dict:
        with self._lock:              # never two cycles at once
            self.running = True
            try:
                return self.agent.run_once(self.sources_factory())
            except Exception as e:
                print(f"[poller] cycle failed: {e}")
                return {"error": str(e)}
            finally:
                self.running = False

    def loop(self) -> None:
        while True:
            self.cycle()
            self.next_run = time.time() + self.every
            self._wake.wait(self.every)
            self._wake.clear()

    def start(self) -> None:
        threading.Thread(target=self.loop, daemon=True, name="poller").start()

    def run_now(self) -> None:
        self._wake.set()


class KeyBody(BaseModel):
    key: str


class BidBody(BaseModel):
    key: str
    amount: Optional[float] = None


def create_app(agent: AuctionAgent, poller: Optional[Poller] = None, token: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="AUCBOT", docs_url=None, redoc_url=None)
    store = agent.store
    token = token if token is not None else os.getenv("DASHBOARD_TOKEN")

    def auth(request: Request) -> None:
        if not token:
            return
        given = request.headers.get("x-token") or request.cookies.get("aucbot_token") or ""
        if not hmac.compare_digest(given, token):
            raise HTTPException(401, "token required")

    def lot_json(key: str, row=None) -> dict:
        lot = store.get_lot(key)
        d = store.get_decision(key)
        out = {"key": key, "lot": lot.model_dump(mode="json") if lot else None,
               "title": lot.title_line if lot else key,
               "decision": d.model_dump(mode="json") if d else None}
        if row is not None:
            out["watch"] = dict(row)
        return out

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "dashboard.html").read_text()

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        sources = {}
        for row in store._q("SELECT k, v FROM meta WHERE k LIKE 'source:%'"):
            sources[row["k"].split(":", 1)[1]] = json.loads(row["v"])
        last = store.get_meta("last_cycle")
        return {
            "buybox": agent.box.model_dump(),
            "last_cycle": json.loads(last) if last else None,
            "sources": sources,
            "channels": getattr(agent.notifier, "channels", []),
            "evaluator": type(agent.evaluator).__name__,
            "next_run": datetime.fromtimestamp(poller.next_run).isoformat(timespec="seconds")
                        if poller and poller.next_run else None,
            "running": bool(poller and poller.running),
            "lots_seen": store.count_lots(),
            "watching": len(store.watchlist()),
        }

    @app.get("/api/watchlist", dependencies=[Depends(auth)])
    def watchlist():
        return [lot_json(r["key"], r) for r in store.watchlist()]

    @app.get("/api/matches", dependencies=[Depends(auth)])
    def matches(limit: int = 100):
        watched = {r["key"] for r in store.watchlist()}
        return [{"key": lot.key, "title": lot.title_line, "lot": lot.model_dump(mode="json"),
                 "decision": d.model_dump(mode="json"), "screened": created, "watched": lot.key in watched}
                for lot, d, created in store.matches(limit)]

    @app.get("/api/lot", dependencies=[Depends(auth)])
    def lot(key: str):
        row = next((r for r in store.watchlist() if r["key"] == key), None)
        if not store.get_lot(key):
            raise HTTPException(404, "unknown lot")
        return lot_json(key, row)

    @app.post("/api/watch", dependencies=[Depends(auth)])
    def watch(b: KeyBody):
        if not store.get_lot(b.key):
            raise HTTPException(404, "unknown lot")
        d = store.get_decision(b.key)
        store.watch(b.key, "manual (dashboard)", d.max_bid if d else None)
        return {"ok": True}

    @app.post("/api/unwatch", dependencies=[Depends(auth)])
    def unwatch(b: KeyBody):
        store.unwatch(b.key)
        return {"ok": True}

    @app.post("/api/bid", dependencies=[Depends(auth)])
    def bid(b: BidBody):
        try:
            msg = approve(store, b.key, b.amount, ManualBidPlacer())
        except (KeyError, ValueError) as e:
            raise HTTPException(400, e.args[0] if e.args else str(e))
        return {"ok": True, "message": msg}

    @app.post("/api/placed", dependencies=[Depends(auth)])
    def placed(b: KeyBody):
        store.set_bid(b.key, "placed")
        return {"ok": True}

    @app.post("/api/run", dependencies=[Depends(auth)])
    def run_now():
        if not poller:
            raise HTTPException(400, "no poller running")
        poller.run_now()
        return {"ok": True}

    @app.post("/api/test-alert", dependencies=[Depends(auth)])
    def test_alert():
        agent.notifier.send("AUCBOT test alert", "If you can read this on your phone, alerts work.",
                            url=os.getenv("DASHBOARD_URL"), priority="high")
        return {"ok": True, "channels": getattr(agent.notifier, "channels", [])}

    return app


def serve(agent: AuctionAgent, sources_factory: Callable[[], list], every_min: float,
          host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn
    poller = Poller(agent, sources_factory, every_min)
    poller.start()
    if not os.getenv("DASHBOARD_TOKEN"):
        print("[web] WARNING: DASHBOARD_TOKEN is not set; anyone who can reach this port can approve bids.")
    print(f"[web] dashboard on http://{host}:{port}  (polling every {every_min:g} min)")
    uvicorn.run(create_app(agent, poller), host=host, port=port, log_level="warning")
