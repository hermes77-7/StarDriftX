import httpx
from fastapi.testclient import TestClient

from app.catalogs import SkyCatalogs
from app.main import app


def test_live_catalog_cache_magnitude_and_name_resolution(monkeypatch):
    monkeypatch.setenv("STARDRIFT_RELEASE_CHECK_SECONDS", "0")
    queries = []

    def archive(request):
        queries.append(request)
        if request.url.path.endswith("/asu-tsv"):
            assert request.url.params["-source"] == "I/355/gaiadr3"
            assert request.url.params["Gmag"] == "<=16.0"
            return httpx.Response(200, text=(
                "#Name: I/355/gaiadr3\nSource\tRA_ICRS\tDE_ICRS\tGmag\n"
                " \tdeg\tdeg\tmag\n----\t----\t----\t----\n"
                "381266926930344576\t10.70\t41.25\t14.26\n42\t10.71\t41.27\t17.1\n"
            ))
        if request.url.host == "irsa.ipac.caltech.edu":
            return httpx.Response(503)
        if request.url.host.startswith("vizier.") and "/nph-sesame/" in request.url.path:
            return httpx.Response(
                200, text="<Sesame><Target><INFO>Nothing found</INFO></Target></Sesame>",
            )
        if request.url.host == "simbad.cfa.harvard.edu" and "M 42" in request.url.params["QUERY"]:
            return httpx.Response(502)
        query = request.url.params["QUERY"]
        if "no-such-name" in query:
            return httpx.Response(200, text="main_id,ra,dec,matched_id\n")
        assert "'M 42'" in query
        return httpx.Response(200, text="main_id,ra,dec,matched_id\nM 42,83.82208,-5.39111,M 42\n")

    http = httpx.AsyncClient(transport=httpx.MockTransport(archive))
    with TestClient(app) as client:
        client.app.state.catalogs = SkyCatalogs(http)
        params = {"ra": 10.68, "dec": 41.27, "mag_limit": 16}
        result = client.get("/api/catalog", params=params)
        assert result.status_code == 200
        assert result.json()["count"] == 1
        assert result.json()["sources"][0]["id"] == "381266926930344576"
        assert client.get("/api/catalog", params=params).json() == result.json()
        assert len(queries) == 1
        assert client.get("/api/catalog", params={**params, "catalog": "wise"}).status_code == 503
        resolved = client.get("/api/resolve", params={"name": "M42"})
        assert resolved.status_code == 200
        assert resolved.json()["ra"] == 83.82208
        assert queries[-1].url.host == "simbad.idia.ac.za"
        request_count = len(queries)
        assert client.get("/api/resolve", params={"name": "messier  42"}).json() == resolved.json()
        assert len(queries) == request_count
        assert client.get("/api/resolve", params={"name": "no-such-name"}).status_code == 404


def test_resolver_uses_local_m31_aliases_and_recovers_from_mirror_failure(monkeypatch):
    monkeypatch.setenv("STARDRIFT_RELEASE_CHECK_SECONDS", "0")
    queries = []

    def archive(request):
        queries.append(request.url.host)
        if request.url.host.startswith("simbad."):
            return httpx.Response(503)
        if request.url.host == "vizier.cfa.harvard.edu":
            return httpx.Response(200, text="<html>Temporary service error</html>")
        if request.url.query == b"Vega":
            return httpx.Response(200, text=(
                '<Sesame><Target><Resolver name="Sc=Simbad"><oname>Vega</oname>'
                '<jradeg>279.23473</jradeg><jdedeg>38.78369</jdedeg>'
                '</Resolver></Target></Sesame>'
            ))
        return httpx.Response(503)

    with TestClient(app) as client:
        http = httpx.AsyncClient(transport=httpx.MockTransport(archive))
        client.app.state.catalogs = SkyCatalogs(http)
        for alias in ("M31", "m  31", "Messier 31", "NGC 0224", "Andromeda Galaxy"):
            result = client.get("/api/resolve", params={"name": alias}).json()
            assert (result["ra"], result["dec"]) == (10.684708333333333, 41.268750000000004)
        assert not queries
        assert client.get("/api/resolve", params={"name": "Vega"}).json()["ra"] == 279.23473
        assert queries[-1] == "vizier.idia.ac.za"
        assert client.get("/api/resolve", params={"name": "unavailable"}).status_code == 503
