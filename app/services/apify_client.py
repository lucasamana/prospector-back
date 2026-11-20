import os
import httpx

APIFY_ACTOR_ID = "compass~crawler-google-places"
APIFY_ENDPOINT = (
    f"https://api.apify.com/v2/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items"
)


async def fetch_places_from_apify(
    lat: float,
    lng: float,
    rubro: str | None = None,
    max_results: int = 40,
    radius_km: float = 0.2,
) -> list[dict]:
    token = os.getenv("APIFY_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Falta configurar APIFY_API_TOKEN en las variables de entorno")

    payload: dict = {
        "customGeolocation": {
            "type": "Point",
            "coordinates": [lng, lat],  # Orden GeoJSON: [longitud, latitud]
            "radiusKm": radius_km,
        },
        "maxCrawledPlacesPerSearch": max_results,
        "language": "es",
        "deeperCityScrape": False,
        "includeWebResults": False,
    }
    if rubro and rubro.strip():
        payload["searchStringsArray"] = [rubro.strip()]
    else:
        # Búsqueda amplia si no se especificó rubro
        payload["searchStringsArray"] = ["comercio", "local"]

    async with httpx.AsyncClient(timeout=httpx.Timeout(240.0)) as client:
        resp = await client.post(
            APIFY_ENDPOINT,
            params={"token": token},
            json=payload,
        )
        resp.raise_for_status()
        items = resp.json()

    if not isinstance(items, list):
        return []

    return items
