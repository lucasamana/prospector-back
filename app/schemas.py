from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EstadoCRM = Literal[
    "pendiente", "llamado", "seguimiento", "interesado", "cliente", "descartado"
]
TipoTelefono = Literal["whatsapp_verificado", "celular", "fijo", "sin_telefono"]


# --- Zonas y Extracción ---


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


# --- Barrios y Mapa ---


class BarrioAvanceOut(BaseModel):
    id: int
    nombre: str
    celdas_totales: int
    celdas_scrapeadas: int
    celdas_procesando: int
    porcentaje_relevado: float
    leads_totales: int
    leads_contactados: int
    porcentaje_contactado: float


class CeldaMapaOut(BaseModel):
    h3_index: str
    barrio_id: int
    lat_centro: float
    lng_centro: float
    poligono_json: list[list[float]]
    estado: str
    ultimo_scraping: datetime | None
    leads_count: int


# --- Leads y CRM ---


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    google_place_id: str | None
    zona_h3: str | None
    barrio_id: int | None
    barrio_nombre: str | None = None

    nombre: str
    rubro: str | None
    direccion: str | None

    telefono_raw: str | None
    telefono_normalizado: str | None
    tipo_telefono: str
    whatsapp_url: str | None
    website: str | None
    instagram: str | None

    rating: float | None
    reviews_count: int | None
    horarios_json: dict | list | None

    estado_crm: str
    notas: str | None
    proximo_contacto: date | None
    referido_por_lead_id: int | None

    created_at: datetime
    updated_at: datetime


class LeadListResponse(BaseModel):
    items: list[LeadOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class LeadUpdate(BaseModel):
    estado_crm: EstadoCRM | None = None
    notas: str | None = None
    proximo_contacto: date | None = None
    rubro: str | None = None
    telefono_raw: str | None = None
    instagram: str | None = None
    website: str | None = None


class ReferidoCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=255)
    rubro: str | None = Field(default=None, max_length=150)
    direccion: str | None = Field(default=None, max_length=300)
    telefono_raw: str | None = Field(default=None, max_length=80)
    instagram: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=500)
    barrio_id: int | None = None
    notas: str | None = None
    proximo_contacto: date | None = None