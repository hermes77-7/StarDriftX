"""Live astronomical catalog and name queries."""

import asyncio
import csv
import io
import math
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Literal
from urllib.parse import quote
from xml.etree import ElementTree
from xml.sax import SAXException

import httpx
from astropy.io.votable import parse

from app.shared_work import SharedWork

Catalog = Literal["gaia", "wise", "simbad"]
GAIA_VIZIER = "https://vizier.cfa.harvard.edu/viz-bin/asu-tsv"
IRSA_GATOR = "https://irsa.ipac.caltech.edu/cgi-bin/Gator/nph-query"
SIMBAD_TAP = "https://simbad.cfa.harvard.edu/simbad/sim-tap/sync"
SIMBAD_MIRROR = "https://simbad.idia.ac.za/simbad/sim-tap/sync"
SESAME_MIRRORS = (
    "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-ox/",
    "https://vizier.idia.ac.za/viz-bin/nph-sesame/-ox/",
)
MESSIER = re.compile(r"(?:M|MESSIER)\s*0*([1-9][0-9]*)", re.IGNORECASE)
ANDROMEDA = re.compile(r"NGC\s*0*224|ANDROMEDA GALAXY", re.IGNORECASE)
# Use the existing SIMBAD coordinates so all M31 aliases share the same cached views.
M31 = {"name": "M 31", "ra": 10.684708333333333, "dec": 41.268750000000004, "resolver": "SIMBAD"}
MAX_SOURCES = 800
MAX_BYTES = 512 * 1024


class CatalogUnavailable(RuntimeError):
    pass


class SkyCatalogs:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
        self.work = SharedWork[dict]()
        self.slots = asyncio.Semaphore(6)
        self.resolve_slots = asyncio.Semaphore(4)

    async def _cached(self, key: tuple, ttl: int, load: Callable[[], Awaitable[dict]]) -> dict:
        entry = self.cache.get(key)
        if entry and time.monotonic() < entry[0]:
            self.cache.move_to_end(key)
            return entry[1]

        async def fill():
            result = await load()
            self.cache[key] = (time.monotonic() + (ttl if result else 60), result)
            self.cache.move_to_end(key)
            if len(self.cache) > 256:
                self.cache.popitem(last=False)
            return result

        return await self.work.run(key, fill)

    async def _read(
        self, url: str, params: dict | None, budget: float, slots: asyncio.Semaphore,
    ) -> bytes:
        async with asyncio.timeout(budget), slots, self.http.stream(
            "GET", url, params=params, timeout=budget,
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_BYTES:
                    raise CatalogUnavailable("The catalog response is too large.")
                body.extend(chunk)
            return bytes(body)

    async def _get(
        self, endpoint: str, params: dict, alternate: str | None = None, *, resolve: bool = False,
    ) -> bytes:
        endpoints = (endpoint, alternate or endpoint)
        budgets = (3, 3) if resolve else (12, 8)
        slots = self.resolve_slots if resolve else self.slots
        try:
            async with asyncio.timeout(sum(budgets) + 1):
                for index, (url, budget) in enumerate(zip(endpoints, budgets, strict=True)):
                    try:
                        return await self._read(url, params, budget, slots)
                    except httpx.HTTPStatusError as error:
                        status = error.response.status_code
                        if index or (status not in (408, 500, 502, 503, 504)
                                     and not (status == 429 and alternate)):
                            raise
                    except (TimeoutError, httpx.TransportError, httpx.DecodingError):
                        if index:
                            raise
                    if not alternate:
                        await asyncio.sleep(0.3)
        except (TimeoutError, httpx.HTTPError) as error:
            raise CatalogUnavailable("The upstream catalog is temporarily unavailable.") from error
        raise AssertionError("Catalog request exited without a result.")

    async def _tap(
        self, endpoint: str, query: str, required: set[str], *, resolve: bool = False,
    ) -> list[dict]:
        params = {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query}
        alternate = SIMBAD_MIRROR if endpoint == SIMBAD_TAP else None
        raw = await self._get(endpoint, params, alternate, resolve=resolve)
        try:
            rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
            if not required.issubset(rows.fieldnames or []):
                raise CatalogUnavailable("The catalog returned an unexpected table.")
            return list(rows)
        except (csv.Error, UnicodeError) as error:
            raise CatalogUnavailable("The catalog returned an unreadable table.") from error

    async def _wise(self, ra: float, dec: float, radius: float, magnitude: float | None) -> list:
        params = {
            "catalog": "allwise_p3as_psd", "spatial": "Cone", "radius": radius,
            "radunits": "deg", "objstr": f"{ra:.12f} {dec:+.12f}", "outfmt": 3,
            "selcols": "designation,ra,dec,w1mpro", "order": "w1mpro", "outrows": MAX_SOURCES + 1,
            "constraints": "w1mpro IS NOT NULL" if magnitude is None else f"w1mpro <= {magnitude}",
        }
        raw = await self._get(IRSA_GATOR, params)
        try:
            table = await asyncio.to_thread(_wise_table, raw)
            return [
                {"object_id": str(row["designation"]), "ra": str(row["ra"]),
                 "dec": str(row["dec"]), "magnitude": str(row["w1mpro"])}
                for row in table
            ]
        except (OSError, ValueError, KeyError, IndexError, SAXException) as error:
            raise CatalogUnavailable("IRSA returned an unreadable AllWISE table.") from error

    async def _gaia(
        self, ra: float, dec: float, radius: float, magnitude: float | None,
    ) -> list:
        params = {
            "-source": "I/355/gaiadr3", "-c": f"{ra:.12f} {dec:+.12f}", "-c.r": radius * 60,
            "-out": "Source,RA_ICRS,DE_ICRS,Gmag", "-sort": "Gmag", "-out.max": MAX_SOURCES + 1,
        }
        if magnitude is not None:
            params["Gmag"] = f"<={magnitude}"
        raw = await self._get(GAIA_VIZIER, params)
        try:
            lines = raw.decode("utf-8-sig").splitlines()
            if not any(line.startswith("#Name: I/355/gaiadr3") for line in lines):
                raise CatalogUnavailable("VizieR did not return the Gaia DR3 catalog.")
            if any(line.startswith("#INFO") and "error" in line.split("=", 1)[0].lower()
                   for line in lines):
                raise CatalogUnavailable("VizieR could not complete the Gaia cone search.")
            values = [line for line in lines if line.strip() and not line.startswith("#")]
            rows = csv.DictReader(values, delimiter="\t")
            return [
                {"object_id": row["Source"], "ra": row["RA_ICRS"],
                 "dec": row["DE_ICRS"], "magnitude": row["Gmag"]}
                for row in rows
            ]
        except (UnicodeError, csv.Error, KeyError) as error:
            raise CatalogUnavailable("VizieR returned an unreadable Gaia table.") from error

    async def cone(
        self, catalog: Catalog, ra: float, dec: float, radius: float, magnitude: float | None,
    ) -> dict:
        async def load() -> dict:
            band = {"gaia": "G", "wise": "W1", "simbad": "V"}[catalog]
            if catalog == "gaia":
                rows = await self._gaia(ra, dec, radius, magnitude)
            elif catalog == "wise":
                rows = await self._wise(ra, dec, radius, magnitude)
            else:
                mag_filter = f" AND f.V <= {magnitude:.10g}" if magnitude is not None else ""
                circle = f"CIRCLE('ICRS',{ra:.12f},{dec:.12f},{radius:.12f})"
                query = (
                    f"SELECT TOP {MAX_SOURCES + 1} b.main_id AS object_id, b.ra, b.dec, "
                    "f.V AS magnitude, b.otype FROM basic AS b "
                    "LEFT JOIN allfluxes AS f ON b.oid = f.oidref "
                    f"WHERE 1 = CONTAINS(POINT('ICRS',b.ra,b.dec),{circle})"
                    f"{mag_filter} ORDER BY magnitude, object_id"
                )
                rows = await self._tap(SIMBAD_TAP, query, {"object_id", "ra", "dec", "magnitude"})
            sources = []
            for row in rows:
                position = _position(row)
                if not position or not row["object_id"]:
                    continue
                identifier = row["object_id"].strip()
                value = _number(row["magnitude"])
                if magnitude is not None and (value is None or value > magnitude):
                    continue
                display = {"gaia": f"Gaia DR3 {identifier}", "wise": f"AllWISE {identifier}"}
                source = {"id": identifier, "name": display.get(catalog, identifier),
                          **position, "catalog": catalog}
                if value is not None:
                    source.update(magnitude=value, mag_band=band)
                if row.get("otype"):
                    source["details"] = {"object_type": row["otype"].strip()}
                sources.append(source)
            return {"catalog": catalog, "count": min(len(sources), MAX_SOURCES),
                    "sources": sources[:MAX_SOURCES], "truncated": len(sources) > MAX_SOURCES}

        return await self._cached(("cone", catalog, ra, dec, radius, magnitude), 3600, load)

    async def resolve(self, name: str) -> dict:
        name = " ".join(name.split())
        if match := MESSIER.fullmatch(name):
            name = f"M {match[1]}"
        if name == "M 31" or ANDROMEDA.fullmatch(name):
            return M31.copy()

        async def load() -> dict:
            # SIMBAD catalog designations use a space between the prefix and number.
            spaced = re.sub(r"^([A-Za-z]+)(?=\d)", r"\1 ", name)
            aliases = sorted({name, spaced, spaced.upper(), name.title()})
            terms = ", ".join("'" + alias.replace("'", "''") + "'" for alias in aliases)
            query = (
                "SELECT TOP 5 b.main_id, b.ra, b.dec, i.id AS matched_id "
                "FROM ident AS i JOIN basic AS b ON i.oidref = b.oid "
                f"WHERE i.id IN ({terms})"
            )
            try:
                rows = await self._tap(
                    SIMBAD_TAP, query, {"main_id", "ra", "dec", "matched_id"}, resolve=True,
                )
            except CatalogUnavailable:
                return await self._sesame(name)
            rows.sort(key=lambda row: row["matched_id"] != name)
            for row in rows:
                if position := _position(row):
                    return {"name": row["main_id"].strip(), **position, "resolver": "SIMBAD"}
            try:
                return await self._sesame(name)
            except CatalogUnavailable:
                return {}

        return await self._cached(("resolve", name), 3600, load)

    async def _sesame(self, name: str) -> dict:
        for endpoint in SESAME_MIRRORS:
            try:
                raw = await self._read(f"{endpoint}?{quote(name, safe='')}", None, 3,
                                       self.resolve_slots)
                root = ElementTree.fromstring(raw)
                target = root.find(".//{*}Target")
                if target is None:
                    continue
                for resolver in target.findall("{*}Resolver"):
                    position = _position({
                        "ra": resolver.findtext("{*}jradeg"),
                        "dec": resolver.findtext("{*}jdedeg"),
                    })
                    if position:
                        source = resolver.get("name", "")[:1].upper()
                        return {
                            "name": resolver.findtext("{*}oname") or name, **position,
                            "resolver": {"S": "SIMBAD", "N": "NED", "V": "VizieR"}.get(
                                source, "CDS Sesame",
                            ),
                        }
                info = " ".join(node.text or "" for node in target.findall(".//{*}INFO")).casefold()
                if any(message in info for message in ("nothing found", "not found", "no object")):
                    return {}
            except (TimeoutError, httpx.HTTPError, ElementTree.ParseError, CatalogUnavailable):
                continue
        raise CatalogUnavailable("The name resolver is temporarily unavailable.")


def _wise_table(raw: bytes):
    return parse(io.BytesIO(raw)).get_first_table().to_table(use_names_over_ids=True)


def _number(value: str | None) -> float | None:
    try:
        result = float(value) if value is not None else math.nan
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _position(row: dict) -> dict | None:
    ra, dec = _number(row["ra"]), _number(row["dec"])
    if ra is not None and dec is not None and 0 <= ra < 360 and -90 <= dec <= 90:
        return {"ra": ra, "dec": dec}
    return None
