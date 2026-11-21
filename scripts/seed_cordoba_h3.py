import asyncio
import json
from pathlib import Path

import h3
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal, init_db
from app.models import Barrio, ZonaH3

H3_RESOLUTION = 9
GEOJSON_PATH = Path("data/barrios_cordoba.geojson")

# Polígonos aproximados en formato GeoJSON ([lng, lat]) para arrancar ya mismo
BARRIOS_STARTER: dict[str, dict] = {
    "Nueva Córdoba": {
        "type": "Polygon",
        "coordinates": [[
            [-64.1920, -31.4210],
            [-64.1795, -31.4210],
            [-64.1795, -31.4345],
            [-64.1920, -31.4345],
            [-64.1920, -31.4210],
        ]],
    },
    "Centro": {
        "type": "Polygon",
        "coordinates": [[
            [-64.1920, -31.4090],
            [-64.1755, -31.4090],
            [-64.1755, -31.4210],
            [-64.1920, -31.4210],
            [-64.1920, -31.4090],
        ]],
    },
    "Güemes": {
        "type": "Polygon",
        "coordinates": [[
            [-64.2035, -31.4200],
            [-64.1920, -31.4200],
            [-64.1920, -31.4325],
            [-64.2035, -31.4325],
            [-64.2035, -31.4200],
        ]],
    },
    "General Paz": {
        "type": "Polygon",
        "coordinates": [[
            [-64.1735, -31.4070],
            [-64.1550, -31.4070],
            [-64.1550, -31.4205],
            [-64.1735, -31.4205],
            [-64.1735, -31.4070],
        ]],
    },
    "Cerro de las Rosas": {
        "type": "Polygon",
        "coordinates": [[
            [-64.2480, -31.3620],
            [-64.2220, -31.3620],
            [-64.2220, -31.3850],
            [-64.2480, -31.3850],
            [-64.2480, -31.3620],
        ]],
    },
    "Ituzaingó": {
        "type": "Polygon",
        "coordinates": [[
            [-64.1120, -31.4450],
            [-64.0860, -31.4450],
            [-64.0860, -31.4620],
            [-64.1120, -31.4620],
            [-64.1120, -31.4450],
        ]],
    },
    "Ferreyra": {
        "type": "Polygon",
        "coordinates": [[
            [-64.1290, -31.4620],
            [-64.1040, -31.4620],
            [-64.1040, -31.4790],
            [-64.1290, -31.4790],
            [-64.1290, -31.4620],
        ]],
    },
    "Los Fresnos": {
        "type": "Polygon",
        "coordinates": [[
            [-64.1420, -31.4540],
            [-64.1220, -31.4540],
            [-64.1220, -31.4680],
            [-64.1420, -31.4680],
            [-64.1420, -31.4540],
        ]],
    },
}


def load_barrios_source() -> list[tuple[str, dict]]:
    if GEOJSON_PATH.exists():
        raw = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))
        barrios = []
        for feat in raw.get("features", []):
            props = feat.get("properties") or {}
            nombre = (
                props.get("nombre")
                or props.get("NOMBRE")
                or props.get("barrio")
                or props.get("name")
            )
            geom = feat.get("geometry")
            if nombre and geom and geom.get("type") in ("Polygon", "MultiPolygon"):
                barrios.append((str(nombre).strip().title(), geom))
        print(f"Cargados {len(barrios)} barrios desde {GEOJSON_PATH}")
        return barrios

    print("No se encontró data/barrios_cordoba.geojson; usando los 5 barrios starter.")
    return list(BARRIOS_STARTER.items())


async def seed() -> None:
    await init_db()
    barrios_data = load_barrios_source()

    total_celdas = 0
    async with AsyncSessionLocal() as session:
        for nombre, geometry in barrios_data:
            # Upsert del barrio
            stmt_barrio = (
                pg_insert(Barrio)
                .values(nombre=nombre, geojson_poligono=geometry)
                .on_conflict_do_nothing(index_elements=["nombre"])
            )
            await session.execute(stmt_barrio)

            result = await session.execute(
                select(Barrio).where(Barrio.nombre == nombre)
            )
            barrio = result.scalar_one()

            # Generar hexágonos H3 (resolución 9: ~174m de arista)
            cells = h3.geo_to_cells(geometry, res=H3_RESOLUTION)
            if not cells:
                print(f"  [!] {nombre}: 0 celdas generadas (revisar geometría)")
                continue

            rows = []
            for h3_idx in cells:
                lat, lng = h3.cell_to_latlng(h3_idx)
                # cell_to_boundary devuelve tuplas (lat, lng) -> ideal para Leaflet
                boundary = [[pt[0], pt[1]] for pt in h3.cell_to_boundary(h3_idx)]
                rows.append(
                    {
                        "h3_index": h3_idx,
                        "barrio_id": barrio.id,
                        "lat_centro": lat,
                        "lng_centro": lng,
                        "poligono_json": boundary,
                        "estado": "pendiente",
                        "rubros_scrapeados": [],
                        "saturada": False,
                    }
                )

            stmt_cells = (
                pg_insert(ZonaH3)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["h3_index"])
            )
            await session.execute(stmt_cells)
            total_celdas += len(rows)
            print(f"  ✓ {nombre}: {len(rows)} hexágonos H3 procesados")

        await session.commit()

    print(f"\nListo. Total de celdas H3 procesadas: {total_celdas}")


if __name__ == "__main__":
    asyncio.run(seed())
