"""Global city resolution with verified coordinates, country and IANA timezone.

Geographical data: GeoNames via https://open-meteo.com/en/docs/geocoding-api
No weather, prices or transport availability are inferred from geography.
"""

from __future__ import annotations

import re
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx


def normalize(value):
    return re.sub(
        r"[^\w]+",
        " ",
        unicodedata.normalize("NFKC", value).casefold().replace("ё", "е"),
    ).strip()


QUERY_ALIASES = {"шармэшшейх": "Sharm el Sheikh", "шармэльшейх": "Sharm el Sheikh"}


class PlaceResolver:
    def __init__(self, catalog, settings, client=None):
        self.catalog, self.settings, self.client = catalog, settings, client

    async def search(self, query, limit=3, country_code=None, register=True):
        owned = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds, follow_redirects=False
        )
        try:
            city_name, separator, country_name = query.partition(",")
            alias = QUERY_ALIASES.get(normalize(city_name).replace(" ", ""))
            lookup = (
                alias + ("," + country_name if separator else "") if alias else query
            )
            params = {"name": lookup, "count": 10, "language": "ru", "format": "json"}
            if country_code:
                params["countryCode"] = country_code
            response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search", params=params
            )
            response.raise_for_status()
            body = response.json()
            if (
                not isinstance(body, dict)
                or body.get("error")
                or not isinstance(body.get("results", []), list)
            ):
                raise ValueError("Invalid geography response")
            values = []
            for item in body.get("results", []):
                if not isinstance(item, dict):
                    continue
                try:
                    if (
                        not isinstance(item.get("name"), str)
                        or not item["name"].strip()
                    ):
                        continue
                    if item.get("feature_code") not in {
                        "PPL",
                        "PPLA",
                        "PPLA2",
                        "PPLA3",
                        "PPLA4",
                        "PPLC",
                    }:
                        continue
                    ZoneInfo(item["timezone"])
                    latitude, longitude = (
                        float(item["latitude"]),
                        float(item["longitude"]),
                    )
                    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                        continue
                    if not re.fullmatch(r"[A-Z]{2}", item["country_code"]):
                        continue
                    if type(item["id"]) is not int or item["id"] <= 0:
                        continue
                    values.append(
                        {
                            "id": f"place_geonames_{item['id']}",
                            "name": item["name"],
                            "country": item["country"],
                            "country_code": item["country_code"],
                            "region": item.get("admin1", ""),
                            "timezone": item["timezone"],
                            "latitude": latitude,
                            "longitude": longitude,
                            "population": int(item.get("population", 0)),
                            "aliases": [query],
                            "geography_source": "GeoNames / Open-Meteo",
                            "component_coverage": {
                                "hotel": "full",
                                "transport": "full",
                                "transfer": "unsupported",
                            },
                        }
                    )
                except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
                    continue
            exact = [
                value
                for value in values
                if normalize(value["name"]) == normalize(query.split(",")[0])
            ]
            if exact:
                values = exact
            values.sort(key=lambda value: -value["population"])
            # A capital/large city should not become ambiguous because of a tiny namesake.
            if (
                len(values) > 1
                and values[0]["population"] >= max(1, values[1]["population"]) * 10
            ):
                values = values[:1]
            for value in values[:limit] if register else []:
                if len(self.catalog.destinations) >= 2048:
                    oldest = next(
                        (
                            key
                            for key in self.catalog.destinations
                            if key.startswith("place_geonames_")
                        ),
                        None,
                    )
                    if oldest:
                        del self.catalog.destinations[oldest]
                self.catalog.destinations[value["id"]] = value
            return values[:limit]
        finally:
            if owned:
                await client.aclose()

    async def coordinates(self, destination):
        if "latitude" in destination and "longitude" in destination:
            return destination
        values = await self.search(
            destination["name"], 1, destination.get("country_code"), register=False
        )
        if values:
            for key in ("latitude", "longitude", "geography_source"):
                destination[key] = values[0][key]
        return destination
