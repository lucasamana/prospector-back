from datetime import datetime
from pydantic import BaseModel, Field


class ExtraerZonaRequest(BaseModel):
    rubro: str | None = Field(
        default=None,
        description="Rubro opcional para filtrar en Google Maps (ej: 'barberia', 'cafe', 'gimnasio')",
    )
    max_results: int = Field(default=40, ge=1, le=200)
    radius_km: float = Field(default=0.2, ge=0.05, le=1.0)


class ExtraerZonaResponse(BaseModel):
    h3_index: str
    estado: str
    mensaje: str


class ZonaEstadoResponse(BaseModel):
    h3_index: str
    barrio_id: int
    lat_centro: float
    lng_centro: float
    estado: str
    ultimo_scraping: datetime | None
    leads_count: int
