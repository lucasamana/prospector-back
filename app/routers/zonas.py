from datetime import datetime, timezone
import logging

import h3
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.models import Barrio, Lead, ZonaH3
from app.schemas import (
    AgregarZonaRequest,
    BarrioAvanceOut,
    CeldaMapaOut,
    ExtraerZonaRequest,
    ExtraerZonaResponse,
    ZonaEstadoResponse,
)
from app.services.apify_client import fetch_places_from_apify
from app.services.enricher import enrich_leads_batch
from app.services.phone_parser import parse_cordoba_phone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Zonas y Barrios"])

# Límites geográficos de Córdoba Capital (anillo Circunvalación + barrios periféricos)
CORDOBA_LAT_MIN = -31.54
CORDOBA_LAT_MAX = -31.30
CORDOBA_LNG_MIN = -64.33
CORDOBA_LNG_MAX = -64.05

# 6 pasadas objetivo por hexágono para considerar 100% escaneado
RUBROS_OBJETIVO = [
    "general",
    "cafe",
    "barberia",
    "restaurante",
    "gimnasio",
    "kiosco",
]


def _latlng_to_h3(lat: float, lng: float, res: int = 9) -> str:
    if hasattr(h3, "latlng_to_cell"):
        return h3.latlng_to_cell(lat, lng, res)
    return h3.geo_to_h3(lat, lng, res)


def _h3_to_center_and_polygon(h3_index: str) -> tuple[float, float, list[list[float]]]:
    if hasattr(h3, "cell_to_latlng"):
        lat, lng = h3.cell_to_latlng(h3_index)
        boundary = h3.cell_to_boundary(h3_index)
    else:
        lat, lng = h3.h3_to_geo(h3_index)
        boundary = h3.h3_to_geo_boundary(h3_index)
    poligono = [[float(p[0]), float(p[1])] for p in boundary]
    return float(lat), float(lng), poligono


def _normalizar_rubro_key(rubro: str | None) -> str:
    if not rubro or not rubro.strip():
        return "general"
    return rubro.strip().lower()


def _calcular_porcentaje_escaneo(rubros_scrapeados: list[str] | None) -> float:
    if not rubros_scrapeados:
        return 0.0
    unicos = {r.strip().lower() for r in rubros_scrapeados if r and r.strip()}
    pct = (len(unicos) / len(RUBROS_OBJETIVO)) * 100
    return round(min(100.0, pct), 1)


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

        # Si Apify llegó al tope pedido, probablemente quedaron comercios afuera
        toco_techo = len(raw_places) >= max_results

        deduped: dict[str, dict] = {}
        for item in raw_places:
            mapped = _map_apify_item_to_lead(item, h3_index, barrio_id)
            if mapped and mapped["google_place_id"] not in deduped:
                deduped[mapped["google_place_id"]] = mapped

        leads_list = await enrich_leads_batch(list(deduped.values()))
        rubro_key = _normalizar_rubro_key(rubro)

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
                actuales = list(zona.rubros_scrapeados or [])
                if rubro_key not in actuales:
                    actuales.append(rubro_key)
                # Asignamos lista nueva para que SQLAlchemy marque la columna JSON como modificada
                zona.rubros_scrapeados = actuales
                zona.saturada = bool(zona.saturada or toco_techo)
                zona.estado = "scrapeado"
                zona.ultimo_scraping = datetime.now(timezone.utc)

            await session.commit()
            logger.info(
                "Celda %s scrapeada (rubro=%s): %d lugares, saturada=%s",
                h3_index,
                rubro_key,
                len(leads_list),
                toco_techo,
            )

    except Exception:
        logger.exception("Error extrayendo celda %s", h3_index)
        async with AsyncSessionLocal() as session:
            zona = await session.get(ZonaH3, h3_index)
            if zona:
                zona.estado = "error"
                await session.commit()


# --- Endpoints de Barrios ---


@router.get("/barrios", response_model=list[BarrioAvanceOut])
async def listar_barrios(db: AsyncSession = Depends(get_db)):
    barrios = (await db.execute(select(Barrio).order_by(Barrio.nombre))).scalars().all()

    celdas_rows = (
        await db.execute(
            select(
                ZonaH3.barrio_id,
                func.count(ZonaH3.h3_index).label("totales"),
                func.sum(case((ZonaH3.estado == "scrapeado", 1), else_=0)).label("scrapeadas"),
                func.sum(case((ZonaH3.estado == "procesando", 1), else_=0)).label("procesando"),
            ).group_by(ZonaH3.barrio_id)
        )
    ).all()
    celdas_map = {r.barrio_id: r for r in celdas_rows}

    leads_rows = (
        await db.execute(
            select(
                Lead.barrio_id,
                func.count(Lead.id).label("totales"),
                func.sum(case((Lead.estado_crm != "pendiente", 1), else_=0)).label("contactados"),
            )
            .where(Lead.barrio_id.is_not(None))
            .group_by(Lead.barrio_id)
        )
    ).all()
    leads_map = {r.barrio_id: r for r in leads_rows}

    respuesta: list[BarrioAvanceOut] = []
    for b in barrios:
        c = celdas_map.get(b.id)
        c_tot = int(c.totales) if c else 0
        c_scr = int(c.scrapeadas or 0) if c else 0
        c_proc = int(c.procesando or 0) if c else 0

        l = leads_map.get(b.id)
        l_tot = int(l.totales) if l else 0
        l_cont = int(l.contactados or 0) if l else 0

        respuesta.append(
            BarrioAvanceOut(
                id=b.id,
                nombre=b.nombre,
                celdas_totales=c_tot,
                celdas_scrapeadas=c_scr,
                celdas_procesando=c_proc,
                porcentaje_relevado=round((c_scr / c_tot) * 100, 1) if c_tot else 0.0,
                leads_totales=l_tot,
                leads_contactados=l_cont,
                porcentaje_contactado=round((l_cont / l_tot) * 100, 1) if l_tot else 0.0,
            )
        )

    return respuesta


@router.get("/barrios/{barrio_id}/celdas", response_model=list[CeldaMapaOut])
async def listar_celdas_barrio(barrio_id: int, db: AsyncSession = Depends(get_db)):
    barrio = await db.get(Barrio, barrio_id)
    if not barrio:
        raise HTTPException(status_code=404, detail="Barrio no encontrado")

    stmt = (
        select(
            ZonaH3,
            func.count(Lead.id).label("leads_count"),
        )
        .outerjoin(Lead, Lead.zona_h3 == ZonaH3.h3_index)
        .where(ZonaH3.barrio_id == barrio_id)
        .group_by(ZonaH3.h3_index)
        .order_by(ZonaH3.h3_index)
    )
    rows = (await db.execute(stmt)).all()

    return [
        CeldaMapaOut(
            h3_index=zona.h3_index,
            barrio_id=zona.barrio_id,
            lat_centro=zona.lat_centro,
            lng_centro=zona.lng_centro,
            poligono_json=zona.poligono_json,
            estado=zona.estado,
            ultimo_scraping=zona.ultimo_scraping,
            leads_count=int(leads_count or 0),
            rubros_scrapeados=list(zona.rubros_scrapeados or []),
            porcentaje_escaneo=_calcular_porcentaje_escaneo(zona.rubros_scrapeados),
            saturada=bool(zona.saturada),
        )
        for zona, leads_count in rows
    ]


# --- Endpoints de Celdas H3 ---


@router.post(
    "/zonas/agregar",
    response_model=CeldaMapaOut,
    status_code=status.HTTP_201_CREATED,
)
async def agregar_zona(
    body: AgregarZonaRequest,
    db: AsyncSession = Depends(get_db),
):
    if not (
        CORDOBA_LAT_MIN <= body.lat <= CORDOBA_LAT_MAX
        and CORDOBA_LNG_MIN <= body.lng <= CORDOBA_LNG_MAX
    ):
        raise HTTPException(
            status_code=400,
            detail="Las coordenadas seleccionadas están fuera de Córdoba Capital",
        )

    h3_index = _latlng_to_h3(body.lat, body.lng, res=9)
    existente = await db.get(ZonaH3, h3_index)
    if existente:
        raise HTTPException(
            status_code=409,
            detail=f"El hexágono {h3_index} ya existe en el mapa",
        )

    # Si mandan barrio_id lo validamos; si no, usamos/creamos "Otras Zonas"
    if body.barrio_id is not None:
        barrio = await db.get(Barrio, body.barrio_id)
        if not barrio:
            raise HTTPException(status_code=404, detail="Barrio no encontrado")
    else:
        barrio = (
            await db.execute(select(Barrio).where(Barrio.nombre == "Otras Zonas"))
        ).scalar_one_or_none()
        if not barrio:
            barrio = Barrio(nombre="Otras Zonas", geojson_poligono={})
            db.add(barrio)
            await db.flush()

    lat_c, lng_c, poligono = _h3_to_center_and_polygon(h3_index)

    nueva_zona = ZonaH3(
        h3_index=h3_index,
        barrio_id=barrio.id,
        lat_centro=lat_c,
        lng_centro=lng_c,
        poligono_json=poligono,
        estado="pendiente",
        rubros_scrapeados=[],
        saturada=False,
    )
    db.add(nueva_zona)
    await db.commit()
    await db.refresh(nueva_zona)

    return CeldaMapaOut(
        h3_index=nueva_zona.h3_index,
        barrio_id=nueva_zona.barrio_id,
        lat_centro=nueva_zona.lat_centro,
        lng_centro=nueva_zona.lng_centro,
        poligono_json=nueva_zona.poligono_json,
        estado=nueva_zona.estado,
        ultimo_scraping=nueva_zona.ultimo_scraping,
        leads_count=0,
        rubros_scrapeados=[],
        porcentaje_escaneo=0.0,
        saturada=False,
    )


@router.get("/zonas/{h3_index}", response_model=ZonaEstadoResponse)
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
        rubros_scrapeados=list(zona.rubros_scrapeados or []),
        porcentaje_escaneo=_calcular_porcentaje_escaneo(zona.rubros_scrapeados),
        saturada=bool(zona.saturada),
    )


@router.post(
    "/zonas/{h3_index}/extraer",
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