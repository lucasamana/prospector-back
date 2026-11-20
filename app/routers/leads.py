import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Barrio, Lead
from app.schemas import LeadListResponse, LeadOut, LeadUpdate, ReferidoCreate
from app.services.phone_parser import parse_cordoba_phone

router = APIRouter(prefix="/api/leads", tags=["Leads CRM"])


def _to_lead_out(lead: Lead, barrio_nombre: str | None) -> LeadOut:
    data = LeadOut.model_validate(lead)
    data.barrio_nombre = barrio_nombre
    return data


@router.get("", response_model=LeadListResponse)
async def listar_leads(
    barrio_id: int | None = None,
    zona_h3: str | None = None,
    estado_crm: str | None = None,
    tipo_telefono: str | None = None,
    rubro: str | None = None,
    busqueda: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    filters = []

    if barrio_id is not None:
        filters.append(Lead.barrio_id == barrio_id)
    if zona_h3:
        filters.append(Lead.zona_h3 == zona_h3)
    if estado_crm:
        filters.append(Lead.estado_crm == estado_crm)
    if tipo_telefono:
        filters.append(Lead.tipo_telefono == tipo_telefono)
    if rubro:
        filters.append(Lead.rubro.ilike(f"%{rubro.strip()}%"))
    if busqueda:
        term = f"%{busqueda.strip()}%"
        filters.append(
            or_(
                Lead.nombre.ilike(term),
                Lead.rubro.ilike(term),
                Lead.direccion.ilike(term),
                Lead.telefono_normalizado.ilike(term),
                Lead.notas.ilike(term),
            )
        )

    total = await db.scalar(select(func.count(Lead.id)).where(*filters)) or 0
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1

    stmt = (
        select(Lead, Barrio.nombre.label("barrio_nombre"))
        .outerjoin(Barrio, Lead.barrio_id == Barrio.id)
        .where(*filters)
        .order_by(Lead.proximo_contacto.asc().nullslast(), Lead.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).all()

    return LeadListResponse(
        items=[_to_lead_out(lead, b_nombre) for lead, b_nombre in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{lead_id}", response_model=LeadOut)
async def obtener_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            select(Lead, Barrio.nombre.label("barrio_nombre"))
            .outerjoin(Barrio, Lead.barrio_id == Barrio.id)
            .where(Lead.id == lead_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lead no encontrado")

    lead, barrio_nombre = row
    return _to_lead_out(lead, barrio_nombre)


@router.patch("/{lead_id}", response_model=LeadOut)
async def actualizar_lead(
    lead_id: int,
    body: LeadUpdate,
    db: AsyncSession = Depends(get_db),
):
    lead = await db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead no encontrado")

    updates = body.model_dump(exclude_unset=True)

    # Si el vendedor edita el teléfono manualmente, re-normalizamos con reglas Córdoba
    if "telefono_raw" in updates:
        parsed = parse_cordoba_phone(updates["telefono_raw"])
        lead.telefono_raw = updates["telefono_raw"]
        lead.telefono_normalizado = parsed.telefono_normalizado
        lead.tipo_telefono = parsed.tipo_telefono
        lead.whatsapp_url = parsed.whatsapp_url
        del updates["telefono_raw"]

    for field, value in updates.items():
        setattr(lead, field, value)

    await db.commit()
    await db.refresh(lead)

    barrio_nombre = None
    if lead.barrio_id:
        barrio = await db.get(Barrio, lead.barrio_id)
        barrio_nombre = barrio.nombre if barrio else None

    return _to_lead_out(lead, barrio_nombre)


@router.post(
    "/{lead_id}/referidos",
    response_model=LeadOut,
    status_code=status.HTTP_201_CREATED,
)
async def crear_referido(
    lead_id: int,
    body: ReferidoCreate,
    db: AsyncSession = Depends(get_db),
):
    origen = await db.get(Lead, lead_id)
    if not origen:
        raise HTTPException(status_code=404, detail="Lead de origen no encontrado")

    barrio_id = body.barrio_id if body.barrio_id is not None else origen.barrio_id
    parsed = parse_cordoba_phone(body.telefono_raw)

    referido = Lead(
        google_place_id=None,
        zona_h3=None,
        barrio_id=barrio_id,
        nombre=body.nombre.strip(),
        rubro=body.rubro.strip() if body.rubro else None,
        direccion=body.direccion.strip() if body.direccion else None,
        telefono_raw=body.telefono_raw.strip() if body.telefono_raw else None,
        telefono_normalizado=parsed.telefono_normalizado,
        tipo_telefono=parsed.tipo_telefono,
        whatsapp_url=parsed.whatsapp_url,
        instagram=body.instagram.strip() if body.instagram else None,
        website=body.website.strip() if body.website else None,
        estado_crm="pendiente",
        notas=body.notas,
        proximo_contacto=body.proximo_contacto,
        referido_por_lead_id=origen.id,
    )
    db.add(referido)
    await db.commit()
    await db.refresh(referido)

    barrio_nombre = None
    if referido.barrio_id:
        barrio = await db.get(Barrio, referido.barrio_id)
        barrio_nombre = barrio.nombre if barrio else None

    return _to_lead_out(referido, barrio_nombre)
