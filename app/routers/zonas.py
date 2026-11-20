from datetime import datetime, timezone
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.models import Lead, ZonaH3
from app.schemas import ExtraerZonaRequest, ExtraerZonaResponse, ZonaEstadoResponse
from app.services.apify_client import fetch_places_from_apify
from app.services.enricher import enrich_leads_batch
from app.services.phone_parser import parse_cordoba_phone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/zonas", tags=["Zonas"])


def _map_apify_item_to_lead(item: dict, h3_index: str, barrio_id: int) -> dict | None:
    place_id = item.get("placeId")
    nombre = item.get("title")
    if not place_id or not nombre:
        return None

    telefono_raw = item.get("phoneUnformatted") or item.get("phone")
    parsed = parse_cordoba_phone(telefono_raw)

    rubro = item.get("categoryName")
    if not rubro and isinstance(item.get("categories"), list) and item["categories"]:
        rubro = item["categories"][0]

    return {
        "google_place_id": str(place_id),
        "zona_h3": h3_index,
        "barrio_id": barrio_id,
        "nombre": str(nombre).strip()[:255],
        "rubro": str(rubro).strip()[:150] if rubro else None,
        "direccion": str(item.get("address") or "").strip()[:300] or None,
        "telefono_raw": str(telefono_raw).strip()[:80] if telefono_raw else None,
        "telefono_normalizado": parsed.telefono_normalizado,
        "tipo_telefono": parsed.tipo_telefono,
        "whatsapp_url": parsed.whatsapp_url,
        "website": item.get("website"),
        "instagram": None,
        "rating": item.get("totalScore"),
        "reviews_count": item.get("reviewsCount"),
        "horarios_json": item.get("openingHours"),
        "estado_crm": "pendiente",
    }


async def _ejecutar_extraccion_zona(
    h3_index: str,
    barrio_id: int,
    lat: float,
    lng: float,
    rubro: str | None,
    max_results: int,
    radius_km: float,
) -> None:
    try:
        raw_places = await fetch_places_from_apify(
            lat=lat,
            lng=lng,
            rubro=rubro,
            max_results=max_results,
            radius_km=radius_km,
        )

        # Mapear y deduplicar por google_place_id antes de enriquecer
        deduped: dict[str, dict] = {}
        for item in raw_places:
            mapped = _map_apify_item_to_lead(item, h3_index, barrio_id)
            if mapped and mapped["google_place_id"] not in deduped:
                deduped[mapped["google_place_id"]] = mapped

        leads_list = await enrich_leads_batch(list(deduped.values()))

        async with AsyncSessionLocal() as session:
            if leads_list:
                stmt = (
                    pg_insert(Lead)
                    .values(leads_list)
                    .on_conflict_do_nothing(index_elements=["google_place_id"])
                )
                await session.execute(stmt)

            zona = await session.get(ZonaH3, h3_index)
            if zona:
                zona.estado = "scrapeado"
                zona.ultimo_scraping = datetime.now(timezone.utc)

            await session.commit()
            logger.info("Celda %s scrapeada: %d lugares procesados", h3_index, len(leads_list))

    except Exception:
        logger.exception("Error extrayendo celda %s", h3_index)
        async with AsyncSessionLocal() as session:
            zona = await session.get(ZonaH3, h3_index)
            if zona:
                zona.estado = "error"
                await session.commit()


@router.get("/{h3_index}", response_model=ZonaEstadoResponse)
async def obtener_estado_zona(h3_index: str, db: AsyncSession = Depends(get_db)):
    zona = await db.get(ZonaH3, h3_index)
    if not zona:
        raise HTTPException(status_code=404, detail="Celda H3 no encontrada")

    leads_count = await db.scalar(
        select(func.count(Lead.id)).where(Lead.zona_h3 == h3_index)
    )
    return ZonaEstadoResponse(
        h3_index=zona.h3_index,
        barrio_id=zona.barrio_id,
        lat_centro=zona.lat_centro,
        lng_centro=zona.lng_centro,
        estado=zona.estado,
        ultimo_scraping=zona.ultimo_scraping,
        leads_count=leads_count or 0,
    )


@router.post(
    "/{h3_index}/extraer",
    response_model=ExtraerZonaResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def extraer_zona(
    h3_index: str,
    background_tasks: BackgroundTasks,
    body: ExtraerZonaRequest = ExtraerZonaRequest(),
    db: AsyncSession = Depends(get_db),
):
    zona = await db.get(ZonaH3, h3_index)
    if not zona:
        raise HTTPException(status_code=404, detail="Celda H3 no encontrada")

    if zona.estado == "procesando":
        raise HTTPException(
            status_code=409,
            detail="Esta celda ya está siendo procesada en este momento",
        )

    zona.estado = "procesando"
    await db.commit()

    background_tasks.add_task(
        _ejecutar_extraccion_zona,
        h3_index=zona.h3_index,
        barrio_id=zona.barrio_id,
        lat=zona.lat_centro,
        lng=zona.lng_centro,
        rubro=body.rubro,
        max_results=body.max_results,
        radius_km=body.radius_km,
    )

    return ExtraerZonaResponse(
        h3_index=zona.h3_index,
        estado="procesando",
        mensaje="Extracción iniciada en segundo plano",
    )
