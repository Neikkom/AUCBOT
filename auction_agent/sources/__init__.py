"""Lot sources. Each source yields normalized `Lot` objects.

Shipped adapters read data you are entitled to: JSON feeds and the CSV
sales-list exports Copart and IAA members can download from their accounts.
Add a new adapter (e.g. a licensed data provider's API) by implementing
`LotSource.fetch()`.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol

from ..models import Lot


class LotSource(Protocol):
    name: str

    def fetch(self) -> Iterable[Lot]: ...


class JsonFileSource:
    """A JSON array of lot objects (fields as in `Lot`)."""

    def __init__(self, path: str | Path, name: str = "json"):
        self.path, self.name = Path(path), name

    def fetch(self) -> Iterable[Lot]:
        for item in json.loads(self.path.read_text()):
            item.setdefault("source", self.name)
            yield Lot.model_validate(item)


# Column names vary between exports and change over time. These maps are a
# starting point; check them against a file you've downloaded and adjust.
COPART_COLUMNS = {
    "lot_id": ["Lot number", "Lot #", "Lot Number"],
    "year": ["Year"], "make": ["Make"], "model": ["Model Group", "Model Detail", "Model"],
    "trim": ["Trim"], "vin": ["VIN"],
    "odometer": ["Odometer"], "odometer_status": ["Odometer Brand"],
    "primary_damage": ["Damage Description", "Primary Damage"],
    "secondary_damage": ["Secondary Damage"],
    "title_type": ["Sale Title Type", "Title Type", "Doc Type"],
    "condition": ["Runs/Drives", "Highlights", "Condition"],
    "has_keys": ["Has Keys-Yes or No", "Keys"],
    "location": ["Yard name", "Location state", "Location"],
    "zip_code": ["Location ZIP", "Zip"],
    "auction_date": ["Sale Date M/D/CY", "Sale Date", "Auction Date"],
    "current_bid": ["High Bid =non-vix,Sealed=Vix", "Current Bid"],
    "buy_now": ["Buy-It-Now Price", "Buy Now"],
    "est_retail_value": ["Est. Retail Value", "ACV"],
    "url": ["Image URL", "URL"],
}
IAA_COLUMNS = {
    "lot_id": ["Stock#", "Stock Number", "Item#"],
    "year": ["Year"], "make": ["Make"], "model": ["Model"], "trim": ["Series", "Trim"],
    "vin": ["VIN"], "odometer": ["Odometer", "Mileage"], "odometer_status": ["Odometer Status"],
    "primary_damage": ["Primary Damage", "Loss Type"], "secondary_damage": ["Secondary Damage"],
    "title_type": ["Title/Sale Doc", "Title"], "condition": ["Start Code", "Run & Drive", "Condition"],
    "has_keys": ["Key", "Keys"], "location": ["Branch", "Location"], "zip_code": ["Zip"],
    "auction_date": ["Auction Date", "Sale Date"], "current_bid": ["Current Bid"],
    "buy_now": ["Buy Now Price"], "est_retail_value": ["ACV", "Actual Cash Value"],
    "url": ["URL"],
}


def _num(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return float(str(v).replace("$", "").replace(",", "").split()[0])
    except ValueError:
        return None


def _date(v: str | None) -> datetime | None:
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(v.strip(), fmt)
        except ValueError:
            continue
    return None


class CsvExportSource:
    """Reads a sales-list / watchlist CSV you exported from your auction account."""

    def __init__(self, path: str | Path, name: str, columns: dict[str, list[str]]):
        self.path, self.name, self.columns = Path(path), name, columns

    def _get(self, row: dict, field: str) -> str | None:
        for col in self.columns.get(field, []):
            if row.get(col) not in (None, ""):
                return row[col]
        return None

    def fetch(self) -> Iterable[Lot]:
        with self.path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                lot_id = self._get(row, "lot_id")
                if not lot_id:
                    continue
                keys = (self._get(row, "has_keys") or "").strip().lower()
                odo = _num(self._get(row, "odometer"))
                yr = _num(self._get(row, "year"))
                yield Lot(
                    source=self.name, lot_id=lot_id.strip(),
                    url=self._get(row, "url"),
                    year=int(yr) if yr else None,
                    make=self._get(row, "make"), model=self._get(row, "model"),
                    trim=self._get(row, "trim"), vin=self._get(row, "vin"),
                    odometer=int(odo) if odo is not None else None,
                    odometer_status=self._get(row, "odometer_status"),
                    primary_damage=self._get(row, "primary_damage"),
                    secondary_damage=self._get(row, "secondary_damage"),
                    title_type=self._get(row, "title_type"),
                    condition=self._get(row, "condition"),
                    has_keys=True if keys in ("yes", "y", "present") else False if keys in ("no", "n", "missing") else None,
                    location=self._get(row, "location"), zip_code=self._get(row, "zip_code"),
                    auction_date=_date(self._get(row, "auction_date")),
                    current_bid=_num(self._get(row, "current_bid")),
                    buy_now=_num(self._get(row, "buy_now")),
                    est_retail_value=_num(self._get(row, "est_retail_value")),
                    description="; ".join(f"{k}: {v}" for k, v in row.items() if v),
                )


def build_source(kind: str, path: str) -> LotSource:
    kind = kind.lower()
    if kind == "json":
        return JsonFileSource(path)
    if kind == "copart-csv":
        return CsvExportSource(path, "copart", COPART_COLUMNS)
    if kind == "iaa-csv":
        return CsvExportSource(path, "iaa", IAA_COLUMNS)
    raise ValueError(f"unknown source kind: {kind}")
