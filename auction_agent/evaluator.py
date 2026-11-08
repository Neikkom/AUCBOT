"""Turns a lot description + buy box into buy-box questions, answers them,
and estimates value/repair cost. Two implementations:

- ClaudeEvaluator: uses the Claude API with structured output.
- HeuristicEvaluator: keyword-based, no API key needed (for demos/tests).
"""
from __future__ import annotations

import json
import re
from typing import Protocol

from .models import BuyBox, BuyBoxAnswer, Lot, LotEvaluation

SYSTEM_PROMPT = """You are an analyst for a buyer of salvage and insurance-auction vehicles \
(Copart, IAA and similar). You get one lot listing and the buyer's buy box.

1. Rephrase every must-have, nice-to-have and deal-breaker rule as a yes/no question about \
this specific lot, and answer it from the listing only. Answer "unknown" when the listing \
doesn't say; never assume. Cite the field or phrase you used as evidence.
2. List risk flags a careful buyer would check (e.g. "Run and Drive" only means it moved at \
check-in, airbag deployment, frame/structural damage, flood signs, odometer status, title \
brand, missing keys, high current bid relative to value).
3. Estimate the vehicle's retail value once repaired, and a low/high repair cost range, in USD. \
Use the published ACV/ERV when present as an anchor. Be conservative on repairs: hidden \
damage is common.
4. Score 0-100: how well it fits the buy box and how good the deal could be. Any deal-breaker \
answered "yes" caps the score at 20. Must-haves answered "no" cap it at 40."""


class Evaluator(Protocol):
    def evaluate(self, lot: Lot, box: BuyBox) -> LotEvaluation: ...


def _lot_payload(lot: Lot, box: BuyBox) -> str:
    lot_dict = lot.model_dump(mode="json", exclude={"images"})
    box_dict = box.model_dump(include={"must_haves", "nice_to_haves", "deal_breakers",
                                       "year_min", "max_miles", "conditions", "title_types"})
    return (f"<buy_box>\n{json.dumps(box_dict, indent=2)}\n</buy_box>\n\n"
            f"<lot>\n{json.dumps(lot_dict, indent=2)}\n</lot>")


class ClaudeEvaluator:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium"):
        import anthropic  # imported lazily so the heuristic mode needs no SDK
        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort

    def evaluate(self, lot: Lot, box: BuyBox) -> LotEvaluation:
        resp = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            # On a safety decline, the API retries on a fallback model in-call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.effort},
            # Static system prompt first so it's cached across every lot.
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": _lot_payload(lot, box)}],
            output_format=LotEvaluation,
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"Evaluation declined for {lot.key}")
        if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
            raise RuntimeError(f"Evaluation incomplete for {lot.key} ({resp.stop_reason})")
        return resp.parsed_output


class HeuristicEvaluator:
    """Offline stand-in. Matches buy-box rules against the listing text with
    keywords. Crude, but lets you run the whole pipeline without an API key."""

    RISK_WORDS = {
        "flood": "Possible flood / water damage",
        "water": "Possible flood / water damage",
        "fire": "Fire damage",
        "burn": "Fire damage",
        "frame": "Possible frame / structural damage",
        "airbag": "Airbags may be deployed",
        "not actual": "Odometer not actual",
        "exempt": "Odometer exempt",
        "biohazard": "Biohazard",
        "stripped": "Parts missing / stripped",
        "engine": "Possible engine issue",
        "transmission": "Possible transmission issue",
    }
    DAMAGE_REPAIR = {  # rough (low, high) USD
        "front end": (2500, 6000), "rear end": (1500, 4500), "side": (1500, 4000),
        "minor dent": (300, 1200), "scratches": (200, 900), "hail": (800, 3500),
        "mechanical": (1000, 5000), "all over": (4000, 10000), "rollover": (6000, 12000),
        "water/flood": (4000, 10000), "flood": (4000, 10000), "burn": (5000, 15000),
        "vandalism": (500, 2500), "normal wear": (200, 800), "undercarriage": (1000, 4000),
    }

    GENERIC = {"vehicle", "damage", "reading", "problems", "problem", "issues", "only", "compared",
               "with", "lot", "the", "and", "has", "have", "low", "high", "value", "current"}
    NEG = re.compile(r"\b(no|not|non|without|none|intact|never)\b")

    @staticmethod
    def _stem(w: str) -> str:
        return w[:-1] if len(w) > 4 and w.endswith("s") else w

    def _text(self, lot: Lot) -> str:
        return " ".join(str(v) for v in lot.model_dump(exclude={"images"}).values() if v).lower()

    def _sentences(self, lot: Lot) -> list[str]:
        parts = [lot.description, lot.condition, lot.primary_damage, lot.secondary_damage,
                 f"odometer {lot.odometer_status}" if lot.odometer_status else None]
        text = ". ".join(p for p in parts if p).lower().replace("&", "and")
        return [x.strip() for x in re.split(r"[.;\n|]", text) if x.strip()]

    def _answer(self, rule: str, sentences: list[str], kind: str) -> BuyBoxAnswer:
        q = f"Does this lot have: {rule}?" if kind == "deal_breaker" else f"Does this lot satisfy: {rule}?"
        rule_l = rule.lower()
        rule_neg = bool(self.NEG.search(rule_l))
        words = {self._stem(w) for w in re.findall(r"[a-z]+", rule_l)
                 if len(w) > 3 and w not in self.GENERIC and not self.NEG.fullmatch(w)}
        if not words:
            return BuyBoxAnswer(question=q, answer="unknown", evidence="rule too vague for offline check", kind=kind)
        for sent in sentences:
            sw = {self._stem(w) for w in re.findall(r"[a-z]+", sent)}
            if words & sw:
                present = not self.NEG.search(sent)        # listing says the property is there
                ok = present if kind == "deal_breaker" else (present != rule_neg)
                return BuyBoxAnswer(question=q, answer="yes" if ok else "no",
                                    evidence=f'listing: "{sent}"', kind=kind)
        return BuyBoxAnswer(question=q, answer="unknown", evidence="not mentioned in listing", kind=kind)

    def evaluate(self, lot: Lot, box: BuyBox) -> LotEvaluation:
        sents = self._sentences(lot)
        answers = ([self._answer(r, sents, "must_have") for r in box.must_haves]
                   + [self._answer(r, sents, "nice_to_have") for r in box.nice_to_haves]
                   + [self._answer(r, sents, "deal_breaker") for r in box.deal_breakers])
        risks = sorted({msg for w, msg in self.RISK_WORDS.items()
                        if any(w in x and not self.NEG.search(x) for x in sents)})
        dmg = (lot.primary_damage or "").lower()
        low, high = next((v for k, v in self.DAMAGE_REPAIR.items() if k in dmg), (1500, 5000))
        retail = lot.est_retail_value or 12000.0

        score = 60
        score += 8 * sum(a.answer == "yes" for a in answers if a.kind == "must_have")
        score += 4 * sum(a.answer == "yes" for a in answers if a.kind == "nice_to_have")
        score -= 5 * len(risks)
        if lot.condition and "run" in lot.condition.lower():
            score += 10
        if any(a.answer == "no" for a in answers if a.kind == "must_have"):
            score = min(score, 40)
        if any(a.answer == "yes" for a in answers if a.kind == "deal_breaker"):
            score = min(score, 20)
        score = max(0, min(100, score))
        return LotEvaluation(
            answers=answers, risk_flags=risks, est_retail_value=retail,
            repair_low=low, repair_high=high, score=score,
            summary=f"{lot.title_line}, {lot.odometer or '?'} mi, {lot.primary_damage or 'unknown damage'}, "
                    f"{lot.condition or 'condition unknown'}. Heuristic estimate (no LLM).",
        )
