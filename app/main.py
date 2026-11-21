import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

from app.database import AsyncSessionLocal, init_db
from app.models import ZonaH3
from app.routers import leads, zonas


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ZonaH3)
            .where(ZonaH3.estado == "procesando")
            .values(estado="error")
        )
        await session.commit()
    yield

app = FastAPI(
    title="Prospector Córdoba API",
    version="0.1.0",
    lifespan=lifespan,
)

cors_origins_raw = os.getenv("CORS_ORIGINS", "http://localhost:3000")
cors_origins = [origin.strip() for origin in cors_origins_raw.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(zonas.router)
app.include_router(leads.router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}