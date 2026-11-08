"""Bid caps ("pre-bids" / "proxy bids" / "max bids").

Copart and IAA both let a member enter a maximum bid ahead of the sale; the
site then bids for you up to that amount. This module never bids on its own.
It turns a suggestion into an *approved* cap only after you confirm it, then
hands it to a `BidPlacer`.

`ManualBidPlacer` (the default) prints the lot link and amount so you enter
the pre-bid yourself in your account. Automating sign-in and bidding on the
auction sites is against their member terms and can get the account
suspended, so no such placer is included. If you buy through a broker or a
data/bidding provider with an API that permits it, implement `BidPlacer`
for that API.
"""
from __future__ import annotations

from typing import Protocol

from .store import Store


class BidPlacer(Protocol):
    def place(self, lot_key: str, url: str | None, amount: float) -> str: ...


class ManualBidPlacer:
    def place(self, lot_key: str, url: str | None, amount: float) -> str:
        return (f"Enter a pre-bid / max bid of ${amount:,.0f} for {lot_key}"
                f"{' at ' + url if url else ''}, then run `mark-placed {lot_key}`.")


def approve(store: Store, lot_key: str, amount: float | None, placer: BidPlacer) -> str:
    row = next((r for r in store.watchlist() if r["key"] == lot_key), None)
    if row is None:
        raise KeyError(f"{lot_key} is not on the watchlist")
    amount = amount if amount is not None else row["max_bid"]
    if not amount or amount <= 0:
        raise ValueError("no positive bid amount; pass --amount")
    if row["max_bid"] and amount > row["max_bid"]:
        print(f"Warning: ${amount:,.0f} is above the suggested cap of ${row['max_bid']:,.0f}.")
    store.set_bid(lot_key, "approved", amount)
    lot = store.get_lot(lot_key)
    return placer.place(lot_key, lot.url if lot else None, amount)
