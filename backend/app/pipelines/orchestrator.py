from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator, Optional

from app.config import settings
from app.models import IdentifiedProduct, SSEEvent
from app.pipelines.benefits_intel import analyze_points_and_cashback
from app.pipelines.coupon_hunter import hunt_live_coupons
from app.pipelines.financial_engine import compute_financial_verdicts
from app.pipelines.identification import (
    identify_from_image,
    identify_from_text,
    identify_from_url,
)
from app.pipelines.search_trust import search_and_filter_offers

logger = logging.getLogger(__name__)


async def run_radar_pipeline(
    *,
    url: Optional[str] = None,
    query: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    image_mime: Optional[str] = None,
    variation_override: Optional[str] = None,
    mile_value_brl: float = settings.default_mile_value_brl,
    skip_ambiguity_prompt: bool = False,
) -> AsyncGenerator[SSEEvent, None]:
    """Orchestrate the 5 SmartBuy Radar pipelines and stream live SSE progress events."""
    try:
        # ── PIPELINE 1: IDENTIFICAÇÃO EXATA ─────────────────────────────────────
        if image_bytes and image_mime and not variation_override:
            yield SSEEvent(
                step=1,
                stage="identification",
                status="running",
                message="Analisando imagem via Gemini 2.5 Flash Vision para extrair marca, modelo, cor e SKU...",
                progress_pct=10,
            )
            product = await identify_from_image(
                image_bytes=image_bytes,
                mime_type=image_mime,
                user_hint=query or "",
            )
            if product.is_ambiguous and product.ambiguity_options and not skip_ambiguity_prompt:
                yield SSEEvent(
                    step=1,
                    stage="identification",
                    status="ambiguity",
                    message=(
                        f"Produto identificado como {product.exact_model}, "
                        "mas há variações possíveis. Confirme a variação exata:"
                    ),
                    progress_pct=20,
                    data={"product": product.model_dump()},
                )
                return
        elif url:
            yield SSEEvent(
                step=1,
                stage="identification",
                status="running",
                message="Extraindo metadados JSON-LD, EAN/SKU e preço de referência do link informado...",
                progress_pct=12,
            )
            product = await identify_from_url(url)
        else:
            base_q = query or ""
            yield SSEEvent(
                step=1,
                stage="identification",
                status="running",
                message=f"Resolvendo especificações exatas e EAN/SKU para '{base_q}'...",
                progress_pct=12,
            )
            product = await identify_from_text(base_q, variation_override=variation_override)

        ref_txt = (
            f" | Preço referência: R$ {product.reference_price_brl:,.2f}"
            if product.reference_price_brl
            else ""
        )
        yield SSEEvent(
            step=1,
            stage="identification",
            status="done",
            message=f"Produto identificado: {product.exact_model}{ref_txt}",
            progress_pct=25,
            data={"product": product.model_dump()},
        )

        # ── PIPELINE 2: BUSCA E FILTRO DE CONFIABILIDADE ────────────────────────
        yield SSEEvent(
            step=2,
            stage="search_trust",
            status="running",
            message="Varrendo varejistas confiáveis (Amazon, ML, KaBuM!, Magalu, Fast Shop, Carrefour) e aplicando trava anti-golpe...",
            progress_pct=42,
        )
        offers, rejected_count, rejected_reasons = await search_and_filter_offers(product)

        if not offers:
            yield SSEEvent(
                step=2,
                stage="search_trust",
                status="warning",
                message="Nenhuma oferta confiável com estoque encontrada para este modelo exato.",
                progress_pct=60,
            )
        else:
            yield SSEEvent(
                step=2,
                stage="search_trust",
                status="done",
                message=(
                    f"{len(offers)} ofertas confiáveis aprovadas pelo Trust Score "
                    f"({rejected_count} anúncios suspeitos/não-oficiais descartados)."
                ),
                progress_pct=58,
            )

        # ── PIPELINE 3 & 4: CUPONS VIVOS + PONTOS/CASHBACK (EM PARALELO) ────────
        yield SSEEvent(
            step=3,
            stage="coupons_benefits",
            status="running",
            message="Caçando cupons ativos nas últimas 24h (Pelando/Promobit/lojas) e calculando paridade Livelo, Esfera, Méliuz e Inter Shop...",
            progress_pct=74,
        )

        coupons_task = hunt_live_coupons(product, offers)
        benefits_task = analyze_points_and_cashback(
            product, offers, mile_value_brl=mile_value_brl
        )
        coupons_by_store, benefits_by_store = await asyncio.gather(
            coupons_task, benefits_task
        )

        total_coupons = sum(len(v) for v in coupons_by_store.values())
        total_benefits = sum(len(v) for v in benefits_by_store.values())
        yield SSEEvent(
            step=4,
            stage="coupons_benefits",
            status="done",
            message=(
                f"Encontrados {total_coupons} cupons ativos e {total_benefits} "
                f"oportunidades de pontos/cashback (milheiro base: R$ {mile_value_brl:,.2f})."
            ),
            progress_pct=88,
        )

        # ── PIPELINE 5: COMPARADOR FINANCEIRO ───────────────────────────────────
        yield SSEEvent(
            step=5,
            stage="financial_engine",
            status="running",
            message="Calculando Custo Efetivo Real (À Vista vs. Parcelado vs. Smart Hack)...",
            progress_pct=94,
        )

        result = compute_financial_verdicts(
            product=product,
            offers=offers,
            coupons_by_store=coupons_by_store,
            benefits_by_store=benefits_by_store,
            mile_value_brl=mile_value_brl,
            rejected_count=rejected_count,
            rejected_reasons=rejected_reasons,
        )

        yield SSEEvent(
            step=5,
            stage="financial_engine",
            status="result",
            message="Plano de Compra Definitivo pronto!",
            progress_pct=100,
            data={"result": result.model_dump()},
        )

    except Exception as exc:
        logger.exception("Erro no pipeline SmartBuy Radar")
        yield SSEEvent(
            step=0,
            stage="error",
            status="error",
            message=f"Erro ao processar busca: {exc}",
            progress_pct=100,
        )
