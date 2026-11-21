from datetime import date, datetime
from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func

from app.database import Base


class Barrio(Base):
    __tablename__ = "barrios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    geojson_poligono: Mapped[dict] = mapped_column(JSONB, nullable=False)

    celdas: Mapped[list["ZonaH3"]] = relationship(
        back_populates="barrio", cascade="all, delete-orphan"
    )
    leads: Mapped[list["Lead"]] = relationship(back_populates="barrio")

class ZonaH3(Base):
    __tablename__ = "zonas_h3"

    h3_index: Mapped[str] = mapped_column(String(20), primary_key=True)
    barrio_id: Mapped[int] = mapped_column(
        ForeignKey("barrios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lat_centro: Mapped[float] = mapped_column(Float, nullable=False)
    lng_centro: Mapped[float] = mapped_column(Float, nullable=False)
    # Guardamos [[lat, lng], ...] listo para Leaflet
    poligono_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    # pendiente | procesando | scrapeado | error
    estado: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pendiente", index=True
    )
    ultimo_scraping: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    rubros_scrapeados: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    saturada: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    barrio: Mapped["Barrio"] = relationship(back_populates="celdas")
    leads: Mapped[list["Lead"]] = relationship(back_populates="zona")

    


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    google_place_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    zona_h3: Mapped[str | None] = mapped_column(
        ForeignKey("zonas_h3.h3_index", ondelete="SET NULL"), nullable=True, index=True
    )
    barrio_id: Mapped[int | None] = mapped_column(
        ForeignKey("barrios.id", ondelete="SET NULL"), nullable=True, index=True
    )

    nombre: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    rubro: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    direccion: Mapped[str | None] = mapped_column(String(300), nullable=True)

    telefono_raw: Mapped[str | None] = mapped_column(String(80), nullable=True)
    telefono_normalizado: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # whatsapp_verificado | celular | fijo | sin_telefono
    tipo_telefono: Mapped[str] = mapped_column(
        String(30), nullable=False, default="sin_telefono", index=True
    )
    whatsapp_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    instagram: Mapped[str | None] = mapped_column(String(255), nullable=True)

    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviews_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    horarios_json: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)

    # pendiente | llamado | seguimiento | interesado | cliente | descartado
    estado_crm: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pendiente", index=True
    )
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    proximo_contacto: Mapped[date | None] = mapped_column(Date, nullable=True)

    referido_por_lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    barrio: Mapped["Barrio | None"] = relationship(back_populates="leads")
    zona: Mapped["ZonaH3 | None"] = relationship(back_populates="leads")
    referido_por: Mapped["Lead | None"] = relationship(
        remote_side=[id], backref="referidos"
    )
