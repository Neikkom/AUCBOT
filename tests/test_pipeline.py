from datetime import datetime, timedelta

from auction_agent.agent import AuctionAgent
from auction_agent.evaluator import HeuristicEvaluator
from auction_agent.filters import hard_filter
from auction_agent.models import BuyBox, Lot, LotEvaluation
from auction_agent.pricing import buyer_fee, max_bid
from auction_agent.sources import JsonFileSource
from auction_agent.store import Store

BOX = BuyBox(year_min=2016, max_miles=120000, conditions=["run and drive"],
             excluded_damage=["flood"], must_haves=["Airbags not deployed"],
             deal_breakers=["Frame damage"], max_budget=15000)


class Collect:
    def __init__(self): self.msgs = []
    def send(self, subject, body, url=None, priority="default"): self.msgs.append(subject)


def lot(**kw):
    base = dict(source="t", lot_id="1", year=2019, odometer=50000, condition="Run and Drive")
    return Lot(**{**base, **kw})


def test_hard_filter():
    assert hard_filter(lot(), BOX)[0]
    assert not hard_filter(lot(year=2014), BOX)[0]
    assert not hard_filter(lot(odometer=130000), BOX)[0]
    assert not hard_filter(lot(condition="Stationary"), BOX)[0]
    assert not hard_filter(lot(primary_damage="Water/Flood"), BOX)[0]
    assert hard_filter(lot(odometer=None), BOX)[0]  # unknown passes to the LLM


def test_heuristic_negation():
    ev = HeuristicEvaluator().evaluate(lot(description="Airbags intact. No frame damage noted."), BOX)
    by = {a.kind: a.answer for a in ev.answers}
    assert by == {"must_have": "yes", "deal_breaker": "no"}
    ev = HeuristicEvaluator().evaluate(lot(description="Airbags deployed, frame damage."), BOX)
    assert ev.score <= 20


def test_max_bid_leaves_profit():
    ev = LotEvaluation(answers=[], risk_flags=[], est_retail_value=20000, repair_low=2000,
                       repair_high=4000, score=80, summary="")
    bid, b = max_bid(ev, BOX)
    total = bid + buyer_fee(bid) + 4000 + BOX.transport_cost + BOX.misc_costs
    assert 20000 - total >= BOX.target_profit
    assert bid % 25 == 0 and 0 < bid <= BOX.max_budget


def test_end_to_end(tmp_path):
    n = Collect()
    agent = AuctionAgent(Store(tmp_path / "a.db"), HeuristicEvaluator(), n, BOX)
    src = JsonFileSource("data/sample_lots.json")
    r1 = agent.run_once([src])
    assert r1["new_lots"] == 5 and r1["matches"] >= 1
    r2 = agent.run_once([src])               # second poll: nothing new, no duplicate alerts
    assert r2["new_lots"] == 0 and r2["matches"] == 0


def test_reminder_picks_tightest_window(tmp_path):
    store, n = Store(tmp_path / "a.db"), Collect()
    l = lot(auction_date=datetime.now() + timedelta(minutes=30))
    store.upsert_lot(l); store.watch(l.key, "t", 5000)
    agent = AuctionAgent(store, HeuristicEvaluator(), n, BOX)
    assert agent.remind() == 1 and "<1h" in n.msgs[0]
    assert agent.remind() == 0
