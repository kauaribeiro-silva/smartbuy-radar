from __future__ import annotations

import hmac
import json
import logging
from typing import Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.pipelines.orchestrator import run_radar_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="SmartBuy Radar API",
    description="Otimizador de Compra Perfeita — Busca Multimodal, Trust Score, Cupons Vivos e Pontos/Cashback.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "gemini_configured": bool(settings.gemini_api_key),
        "model": settings.gemini_model,
    }


@app.post("/api/radar/stream")
async def radar_stream(
    url: Optional[str] = Form(default=None),
    query: Optional[str] = Form(default=None),
    variation_override: Optional[str] = Form(default=None),
    mile_value_brl: float = Form(default=settings.default_mile_value_brl),
    skip_ambiguity_prompt: bool = Form(default=False),
    image: Optional[UploadFile] = File(default=None),
    x_radar_key: Optional[str] = Header(default=None),
) -> EventSourceResponse:
    """
    Unified SSE streaming endpoint accepting a product URL, text query, or uploaded/pasted image.
    """
    if settings.app_access_password:
        if not x_radar_key or not hmac.compare_digest(
            x_radar_key, settings.app_access_password
        ):
            raise HTTPException(
                status_code=401,
                detail="Acesso não autorizado.",
            )
    clean_url = url.strip() if url and url.strip() else None
    clean_query = query.strip() if query and query.strip() else None
    clean_variation = (
        variation_override.strip()
        if variation_override and variation_override.strip()
        else None
    )

    image_bytes: Optional[bytes] = None
    image_mime: Optional[str] = None
    if image is not None:
        image_bytes = await image.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Imagem enviada está vazia.")
        image_mime = image.content_type or "image/png"

    if not any([clean_url, clean_query, image_bytes]):
        raise HTTPException(
            status_code=400,
            detail="Envie um link de produto, termo de busca ou imagem.",
        )

    async def event_generator():
        async for event in run_radar_pipeline(
            url=clean_url,
            query=clean_query,
            image_bytes=image_bytes,
            image_mime=image_mime,
            variation_override=clean_variation,
            mile_value_brl=mile_value_brl,
            skip_ambiguity_prompt=skip_ambiguity_prompt,
        ):
            yield {
                "event": event.status,
                "data": json.dumps(event.model_dump(), ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())
