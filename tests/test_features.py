import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from auction_agent.agent import AuctionAgent
from auction_agent.evaluator import HeuristicEvaluator
from auction_agent.models import BuyBox
from auction_agent.notify import MultiNotifier, NtfyNotifier, TelegramNotifier
from auction_agent.sources import HttpApiSource, JsonFileSource
from auction_agent.store import Store
from auction_agent.web import create_app

BOX = BuyBox(year_min=2016, max_miles=120000, conditions=["run and drive", "run & drive"],
             must_haves=["Airbags not deployed"], deal_breakers=["Frame damage"], auto_watch_score=70)


@pytest.fixture
def fake_server():
    """Tiny HTTP server that records requests and returns canned JSON."""
    calls, pages = [], {}

    class H(BaseHTTPRequestHandler):
        def _go(self):
            n = int(self.headers.get("content-length") or 0)
            calls.append({"path": self.path, "headers": dict(self.headers),
                          "body": self.rfile.read(n).decode() if n else ""})
            body = json.dumps(pages.get(self.path.split("?")[0], {"ok": True})).encode()
            self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
            self.wfile.write(body)
        do_GET = do_POST = _go
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", calls, pages
    srv.shutdown()


def test_http_api_source_maps_paginates_and_advances_cursor(fake_server, tmp_path, monkeypatch):
    base, calls, pages = fake_server
    monkeypatch.setenv("AUCTION_API_KEY", "secret123")
    pages["/lots"] = {"data": [{"lot_number": 111, "year": "2019", "make": "Toyota", "model": "Camry",
                                "odometer": {"value": "68,412"}, "highlights": "Run and Drive",
                                "sale_date": "2026-11-20T15:00:00Z", "photos": ["a.jpg"]}],
                      "next": f"{base}/lots2"}
    pages["/lots2"] = {"data": [{"lot_number": 222, "year": 2021, "make": "Honda", "odometer": {"value": 41200}}],
                       "next": None}
    store = Store(tmp_path / "a.db")
    cfg = {"name": "copart", "url": f"{base}/lots?since={{since}}", "items_path": "data", "next_path": "next",
           "headers": {"Authorization": "Bearer ${AUCTION_API_KEY}"}, "initial_since": "2026-11-01T00:00:00Z",
           "fields": {"lot_id": "lot_number", "year": "year", "make": "make", "model": "model",
                      "odometer": "odometer.value", "condition": "highlights", "auction_date": "sale_date",
                      "images": "photos"}}
    lots = list(HttpApiSource(cfg, store).fetch())
    assert [l.lot_id for l in lots] == ["111", "222"]
    assert lots[0].odometer == 68412 and lots[0].year == 2019 and lots[0].images == ["a.jpg"]
    assert lots[0].source == "copart" and lots[0].auction_date.year == 2026
    assert calls[0]["headers"]["Authorization"] == "Bearer secret123"
    assert "since=2026-11-01" in calls[0]["path"]
    # second poll uses the advanced cursor
    list(HttpApiSource(cfg, store).fetch())
    assert "since=2026-11-01" not in calls[2]["path"]


def test_ntfy_and_telegram_payloads(fake_server):
    base, calls, _ = fake_server
    NtfyNotifier("my-topic", server=base).send("Auction in <1h: Camry", "body", url="http://x/lot", priority="urgent")
    assert calls[-1]["path"] == "/my-topic"
    h = calls[-1]["headers"]
    assert h["Priority"] == "5" and h["Click"] == "http://x/lot" and "Camry" in h["Title"]
    t = TelegramNotifier("TOKEN", "42"); t.endpoint = f"{base}/tg"
    t.send("New match", "body", url="http://x", priority="high")
    payload = json.loads(calls[-1]["body"])
    assert payload["chat_id"] == "42" and "New match" in payload["text"] and "http://x" in payload["text"]


def test_multinotifier_survives_broken_channel():
    class Boom:
        def send(self, *a, **k): raise RuntimeError("down")
    got = []
    class Ok:
        def send(self, s, b, url=None, priority="default"): got.append(s)
    MultiNotifier([Boom(), Ok()]).send("hi", "there")
    assert got == ["hi"]


def test_dashboard_api(tmp_path):
    store = Store(tmp_path / "a.db")
    class Quiet:
        channels = ["Console"]
        def send(self, *a, **k): pass
    agent = AuctionAgent(store, HeuristicEvaluator(), Quiet(), BOX)
    agent.run_once([JsonFileSource("data/sample_lots.json")])
    c = TestClient(create_app(agent, token="pw"))

    assert c.get("/api/watchlist").status_code == 401
    H = {"x-token": "pw"}
    assert "AUCBOT" in c.get("/").text
    st = c.get("/api/state", headers=H).json()
    assert st["lots_seen"] == 5 and st["last_cycle"]["new_lots"] == 5
    watch = c.get("/api/watchlist", headers=H).json()
    assert {w["key"] for w in watch} == {"copart:41234567", "iaa:38811220"}
    matches = c.get("/api/matches", headers=H).json()
    malibu = next(m for m in matches if m["key"] == "iaa:38815555")
    assert not malibu["watched"]
    assert c.post("/api/watch", json={"key": malibu["key"]}, headers=H).json()["ok"]
    r = c.post("/api/bid", json={"key": "copart:41234567", "amount": 9000}, headers=H).json()
    assert "$9,000" in r["message"]
    lot = c.get("/api/lot", params={"key": "copart:41234567"}, headers=H).json()
    assert lot["watch"]["bid_status"] == "approved" and lot["watch"]["approved_amount"] == 9000
    c.post("/api/placed", json={"key": "copart:41234567"}, headers=H)
    assert c.get("/api/lot", params={"key": "copart:41234567"}, headers=H).json()["watch"]["bid_status"] == "placed"
    assert c.post("/api/bid", json={"key": "nope:1"}, headers=H).status_code == 400
    assert c.post("/api/unwatch", json={"key": malibu["key"]}, headers=H).json()["ok"]


def test_source_failure_is_recorded_not_fatal(tmp_path):
    class Broken:
        name = "badfeed"
        def fetch(self): raise ConnectionError("provider down")
    store = Store(tmp_path / "a.db")
    agent = AuctionAgent(store, HeuristicEvaluator(), MultiNotifier([]), BOX)
    r = agent.run_once([Broken(), JsonFileSource("data/sample_lots.json")])
    assert r["new_lots"] == 5
    assert json.loads(store.get_meta("source:badfeed"))["ok"] is False
