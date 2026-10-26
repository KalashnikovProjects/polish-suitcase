"""Каталог и покрытие: загружает versioned-фикстуры, считает покрытие
относительно обязательных компонентов конкретной поездки."""
from __future__ import annotations

import json
from pathlib import Path

from data_layer.contracts import Coverage, CoverageLevel, TripComponent

# фикстуры лежат рядом с пакетом: data_layer/fixtures/catalog
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "catalog"

_RANK = {
    CoverageLevel.UNSUPPORTED: 0,
    CoverageLevel.SPARSE: 1,
    CoverageLevel.PARTIAL: 2,
    CoverageLevel.FULL: 3,
}
_COMPONENT_BASE = {
    TripComponent.TRANSPORT_OUT: "transport",
    TripComponent.TRANSPORT_BACK: "transport",
    TripComponent.HOTEL: "hotel",
    TripComponent.TRANSFER: "transfer",
}


class Catalog:
    def __init__(self, fixtures_dir: Path = FIXTURES_DIR):
        self._dir = fixtures_dir
        self.destinations: dict[str, dict] = {
            d["id"]: d for d in self._load("destinations.json")["destinations"]
        }
        self.hotels: list[dict] = self._load("hotels.json")["hotels"]
        self.routes: list[dict] = self._load("transport.json")["routes"]
        from data_layer.places import enrich_catalog
        enrich_catalog(self.destinations)

    def _load(self, name: str) -> dict:
        return json.loads((self._dir / name).read_text(encoding="utf-8"))

    def destination(self, destination_id: str) -> dict | None:
        return self.destinations.get(destination_id)

    def hotels_for(self, destination_id: str) -> list[dict]:
        return [h for h in self.hotels if h["destination_id"] == destination_id]

    def routes_between(self, origin_id: str, destination_id: str) -> list[dict]:
        return [r for r in self.routes
                if r["origin_id"] == origin_id and r["destination_id"] == destination_id]

    def coverage_for(self, destination_ids: list[str],
                     required: list[TripComponent]) -> Coverage:
        missing: list[str] = []
        worst = CoverageLevel.FULL
        known = False
        for dest_id in destination_ids:
            dest = self.destinations.get(dest_id)
            if dest is None:
                missing.append(f"{dest_id}: неизвестное направление")
                worst = CoverageLevel.UNSUPPORTED
                continue
            known = True
            comp_cov = dest.get("component_coverage", {})
            for comp in required:
                base = _COMPONENT_BASE[comp]
                level = CoverageLevel(comp_cov.get(base, "unsupported"))
                if _RANK[level] < _RANK[worst]:
                    worst = level
                if _RANK[level] <= 1:
                    missing.append(f"{dest_id}:{comp.value}:{level.value}")
        if not known and destination_ids:
            missing = missing or ["unknown_destination"]
        recommendation = "warn_user" if worst in (CoverageLevel.SPARSE, CoverageLevel.UNSUPPORTED) else None
        return Coverage(
            destination_id=destination_ids[0] if len(destination_ids) == 1 else None,
            level=worst,
            required_components=list(required),
            missing=missing,
            recommendation=recommendation,
        )
