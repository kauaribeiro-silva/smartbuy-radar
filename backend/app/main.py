from __future__ import annotations

import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.db import (
    active_alert,
    delete_product,
    dismiss_alert,
    get_product,
    history as db_history,
    init_db,
    list_recent,
    list_refresh_candidates,
    load_recommendations,
    record_price,
    set_target_price,
    set_watching,
    touch_checked,
    update_after_refresh,
    get_telegram_config,
    set_setting,
    send_telegram_alert,
)
from app.models import IdentifiedProduct
from app.pipelines.coupon_hunter import hunt_live_coupons
from app.pipelines.orchestrator import run_radar_pipeline
from app.pipelines.search_trust import search_and_filter_offers_v2
from app.pipelines.spec_validator import spec_from_stored

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _periodic_monitor_loop() -> None:
    """24/7 Background Price & Coupon Monitor running every 30 minutes."""
    while True:
        await asyncio.sleep(1800)
        try:
            await _background_refresh_stale()
        except Exception as exc:
            logger.debug("Periodic monitor loop error: %s", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    monitor_task = asyncio.create_task(_periodic_monitor_loop())
    try:
        yield
    finally:
        monitor_task.cancel()


app = FastAPI(
    title="SmartBuy Radar API",
    description="Otimizador de Compra Perfeita v2.0 — StrictSpecValidator, MarketplaceDeepHunter, Memória SQLite e Recomendações.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _verify_auth(x_radar_key: Optional[str]) -> None:
    if settings.app_access_password:
        if not x_radar_key or not hmac.compare_digest(
            x_radar_key, settings.app_access_password
        ):
            raise HTTPException(status_code=401, detail="Acesso não autorizado.")


@app.get("/health")
async def health_check() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "version": "2.0.0",
        "gemini_configured": bool(settings.gemini_api_key),
        "model": settings.gemini_model,
    }


async def _refresh_single_tracked(product_id: int) -> dict[str, Any] | None:
    """Re-run strict search & coupon check for a tracked product and detect price drops or new coupons."""
    prod = get_product(product_id)
    if not prod:
        return None

    spec = spec_from_stored(
        prod["canonical_name"],
        prod.get("mandatory_tokens") or [],
        prod.get("forbidden_tokens") or [],
    )
    ident = IdentifiedProduct(
        brand=prod.get("brand") or "",
        exact_model=prod["canonical_name"],
        category=prod.get("category") or "Eletrônicos",
        search_query=prod["search_query"],
        reference_price_brl=prod.get("first_seen_price"),
    )

    search_data = await search_and_filter_offers_v2(ident, spec=spec)
    offers = search_data["offers"]
    if not offers:
        touch_checked(product_id)
        return get_product(product_id)

    coupons_by_store = await hunt_live_coupons(ident, offers[:4])
    for cc in search_data.get("community_coupons") or []:
        store_list = coupons_by_store.setdefault(cc.store_name, [])
        if not any(existing.code == cc.code for existing in store_list):
            store_list.insert(0, cc)

    best = min(offers, key=lambda o: o.price_pix)
    store_coupons = coupons_by_store.get(best.store_name, [])
    best_coupon_code = (
        store_coupons[0].code
        if store_coupons
        else (best.embedded_coupon_code or "")
    )

    prev_price = prod.get("last_checked_price") or prod.get("first_seen_price")
    prev_coupon = prod.get("active_coupon") or ""
    target_price = prod.get("target_price")

    alert_kind = ""
    alert_msg = ""
    alert_amount: float | None = None

    if prev_price and best.price_pix < (prev_price - 0.5):
        drop = round(prev_price - best.price_pix, 2)
        coupon_part = f" com o cupom {best_coupon_code}" if best_coupon_code else ""
        alert_kind = "queda"
        alert_amount = drop
        alert_msg = (
            f"🔥 Alerta de Oportunidade: O {prod['canonical_name']} que você pesquisou "
            f"caiu R$ {drop:,.2f} na {best.store_name} (agora R$ {best.price_pix:,.2f}){coupon_part}!"
        )
    elif target_price and best.price_pix <= target_price:
        alert_kind = "alvo"
        alert_amount = round(target_price - best.price_pix, 2)
        coupon_part = f" + cupom {best_coupon_code}" if best_coupon_code else ""
        alert_msg = (
            f"🎯 Preço Alvo Atingido: {prod['canonical_name']} chegou a R$ {best.price_pix:,.2f} "
            f"na {best.store_name}{coupon_part} (seu alvo: R$ {target_price:,.2f})!"
        )
    elif best_coupon_code and best_coupon_code != prev_coupon:
        alert_kind = "cupom"
        alert_msg = (
            f"🔥 Novo Cupom Ativo: {prod['canonical_name']} na {best.store_name} "
            f"(R$ {best.price_pix:,.2f}) ganhou o cupom {best_coupon_code}!"
        )

    update_after_refresh(
        product_id,
        new_price=best.price_pix,
        best_store=best.store_name,
        best_url=best.product_url,
        active_coupon=best_coupon_code,
        alert_kind=alert_kind,
        alert_message=alert_msg,
        alert_amount=alert_amount,
    )
    if alert_msg:
        asyncio.create_task(
            send_telegram_alert(f"{alert_msg}\n🔗 Oferta: {best.product_url}")
        )
    record_price(
        product_id,
        price_pix=best.price_pix,
        price_installments=best.price_installment_total,
        effective_price=best.effective_net_price,
        store=best.store_name,
    )
    return get_product(product_id)


async def _background_refresh_stale() -> None:
    """Refresh up to 3 tracked products whose last check was > 15 minutes ago."""
    try:
        candidates = list_refresh_candidates(limit=3)
        now = datetime.now(timezone.utc)
        for item in candidates:
            last_at = item.get("last_checked_at")
            if last_at:
                try:
                    dt = datetime.fromisoformat(last_at)
                    if now - dt < timedelta(minutes=15):
                        continue
                except ValueError:
                    pass
            await _refresh_single_tracked(int(item["id"]))
    except Exception as exc:
        logger.debug("Background radar refresh skipped: %s", exc)


@app.get("/api/memory/radar")
async def get_startup_radar(
    x_radar_key: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """
    Startup Promo Radar: returns the user's recently searched products, active price-drop
    or coupon alerts, price history sparkline points, and cached ecosystem recommendations,
    while kicking off a background refresh.
    """
    _verify_auth(x_radar_key)
    items = list_recent(limit=8)
    alerts: list[dict[str, Any]] = []
    latest_recs: list[dict[str, Any]] = []

    enriched_items: list[dict[str, Any]] = []
    for it in items:
        pid = int(it["id"])
        hist = db_history(pid, limit=20)
        it["history"] = hist
        al = active_alert(it)
        if al:
            alerts.append({**al, "canonical_name": it["canonical_name"], "best_url": it.get("best_url", ""), "best_store": it.get("best_store", "")})
        if not latest_recs:
            recs, _ = load_recommendations(pid, max_age_hours=48.0)
            if recs:
                latest_recs = recs
        enriched_items.append(it)

    # Trigger non-blocking background refresh
    asyncio.create_task(_background_refresh_stale())

    return {
        "items": enriched_items,
        "alerts": alerts,
        "recommendations": latest_recs,
    }


@app.post("/api/memory/refresh/{product_id}")
async def refresh_tracked_product(
    product_id: int,
    x_radar_key: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """1-click instant revalidation of a tracked product."""
    _verify_auth(x_radar_key)
    updated = await _refresh_single_tracked(product_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Produto monitorado não encontrado.")
    updated["history"] = db_history(product_id, limit=20)
    return {
        "item": updated,
        "alert": active_alert(updated),
    }


class TrackedPatchBody(BaseModel):
    target_price: float | None = None
    clear_target_price: bool = False
    is_watching: bool | None = None
    dismiss_alert: bool = False


@app.patch("/api/memory/products/{product_id}")
async def patch_tracked_product(
    product_id: int,
    body: TrackedPatchBody,
    x_radar_key: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _verify_auth(x_radar_key)
    prod = get_product(product_id)
    if not prod:
        raise HTTPException(status_code=404, detail="Produto não encontrado.")

    if body.clear_target_price:
        set_target_price(product_id, None)
    elif body.target_price is not None:
        set_target_price(product_id, round(float(body.target_price), 2))
        set_watching(product_id, True)

    if body.is_watching is not None:
        set_watching(product_id, body.is_watching)

    if body.dismiss_alert:
        dismiss_alert(product_id)

    updated = get_product(product_id) or prod
    updated["history"] = db_history(product_id, limit=20)
    return {"item": updated}


@app.delete("/api/memory/products/{product_id}")
async def remove_tracked_product(
    product_id: int,
    x_radar_key: Optional[str] = Header(default=None),
) -> dict[str, bool]:
    _verify_auth(x_radar_key)
    delete_product(product_id)
    return {"ok": True}


class TelegramConfigBody(BaseModel):
    bot_token: str = ""
    chat_id: str = ""
    send_test: bool = True


@app.get("/api/memory/telegram")
async def read_telegram_config(
    x_radar_key: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _verify_auth(x_radar_key)
    cfg = get_telegram_config()
    masked_token = (
        f"{cfg['bot_token'][:8]}...{cfg['bot_token'][-4:]}"
        if len(cfg.get("bot_token", "")) > 12
        else ""
    )
    return {
        "configured": bool(cfg.get("bot_token") and cfg.get("chat_id")),
        "masked_token": masked_token,
        "chat_id": cfg.get("chat_id", ""),
    }


@app.post("/api/memory/telegram")
async def save_telegram_config(
    body: TelegramConfigBody,
    x_radar_key: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _verify_auth(x_radar_key)
    if body.bot_token.strip():
        set_setting("telegram_bot_token", body.bot_token.strip())
    if body.chat_id.strip():
        set_setting("telegram_chat_id", body.chat_id.strip())

    test_sent = False
    if body.send_test:
        test_sent = await send_telegram_alert(
            "✅ SmartBuy Radar v2.0 conectado! Você receberá alertas automáticos de queda de preço e novos cupons aqui."
        )
    cfg = get_telegram_config()
    return {
        "configured": bool(cfg.get("bot_token") and cfg.get("chat_id")),
        "test_sent": test_sent,
    }


@app.post("/api/radar/stream")
@app.post("/api/search")
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
    Available at both /api/radar/stream and /api/search.
    """
    _verify_auth(x_radar_key)

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
