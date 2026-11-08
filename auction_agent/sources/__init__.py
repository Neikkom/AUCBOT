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


# ---------------------------------------------------------------------------
# Automatic: any auction-data provider with a JSON HTTP API
# ---------------------------------------------------------------------------

def _dig(obj, path: str | None):
    """Follow a dotted path ("data.items", "photos.0.url") through dicts/lists."""
    if not path:
        return obj
    for part in path.split("."):
        if obj is None:
            return None
        if isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


_NUMERIC = {"year", "odometer", "current_bid", "buy_now", "est_retail_value"}


class HttpApiSource:
    """Polls a provider's REST API and maps its JSON into `Lot`s.

    Configured entirely from sources.yaml, so switching providers means editing
    the field map, not the code:

        - kind: api
          name: copart                       # becomes Lot.source
          url: https://provider.example/v1/lots?site=copart&updated_after={since}
          method: GET                        # or POST with `body:`
          headers: {Authorization: "Bearer ${AUCTION_API_KEY}"}
          items_path: data                   # where the list of lots is in the response
          next_path: links.next              # optional: URL of the next page
          max_pages: 10
          fields:                            # Lot field -> provider field (dotted path)
            lot_id: lot_number
            year: year
            ...

    `${ENV_VAR}` is expanded from the environment, so keys stay out of the file.
    `{since}` is replaced with the time of the last successful poll (ISO 8601).
    """

    def __init__(self, cfg: dict, store=None):
        self.cfg = cfg
        self.name = cfg.get("name", "api")
        self.store = store
        self.fields: dict[str, str] = cfg.get("fields", {})
        self.constants: dict = cfg.get("constants", {})   # fixed values, e.g. {source: copart}

    def _since(self) -> str:
        last = self.store.get_meta(f"since:{self.name}") if self.store else None
        return last or self.cfg.get("initial_since", "1970-01-01T00:00:00Z")

    def _request(self, url: str) -> dict | list:
        import os
        import urllib.request
        headers = {k: os.path.expandvars(str(v)) for k, v in self.cfg.get("headers", {}).items()}
        headers.setdefault("Accept", "application/json")
        body = self.cfg.get("body")
        data = None
        if body is not None:
            data = os.path.expandvars(json.dumps(body).replace("{since}", self._since())).encode()
            headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=headers, method=self.cfg.get("method", "GET"))
        with urllib.request.urlopen(req, timeout=int(self.cfg.get("timeout", 30))) as r:
            return json.loads(r.read())

    def to_lot(self, item: dict) -> Lot:
        data: dict = {"source": self.name, **self.constants}
        for field, path in self.fields.items():
            v = _dig(item, path)
            if v in (None, ""):
                continue
            if field in _NUMERIC:
                v = _num(str(v))
                if v is None:
                    continue
                if field in ("year", "odometer"):
                    v = int(v)
            elif field == "auction_date" and isinstance(v, str):
                v = _date(v) or v       # let pydantic parse ISO strings
            elif field == "images" and isinstance(v, str):
                v = [v]
            elif field == "lot_id":
                v = str(v)
            data[field] = v
        if not data.get("description"):
            data["description"] = "; ".join(f"{k}: {v}" for k, v in item.items()
                                            if isinstance(v, (str, int, float)) and v != "")[:4000]
        return Lot.model_validate(data)

    def fetch(self) -> Iterable[Lot]:
        import os
        from datetime import datetime, timezone
        started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = os.path.expandvars(self.cfg["url"]).replace("{since}", self._since())
        lots: list[Lot] = []
        for _ in range(int(self.cfg.get("max_pages", 10))):
            resp = self._request(url)
            items = _dig(resp, self.cfg.get("items_path")) or []
            for item in items:
                try:
                    lots.append(self.to_lot(item))
                except Exception as e:
                    print(f"[{self.name}] skipped an item: {e}")
            url = _dig(resp, self.cfg.get("next_path")) if self.cfg.get("next_path") else None
            if not url or not items:
                break
        if self.store:   # only advance the cursor after a fully successful poll
            self.store.set_meta(f"since:{self.name}", started)
        yield from lots


def load_sources(path: str | Path, store=None) -> list[LotSource]:
    """Build every source listed in a sources.yaml file."""
    import os
    import yaml
    cfg = yaml.safe_load(Path(path).read_text()) or {}
    out: list[LotSource] = []
    for s in cfg.get("sources", []):
        if s.get("enabled", True) is False:
            continue
        kind = s["kind"]
        if kind == "api":
            out.append(HttpApiSource(s, store))
        else:
            out.append(build_source(kind, os.path.expanduser(s["path"])))
    return out
