from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator, Optional

from app.config import settings
from app.db import (
    get_product_by_key,
    history as db_history,
    make_product_key,
    record_price,
    save_recommendations,
    upsert_tracked_after_search,
)
from app.models import SSEEvent
from app.pipelines.benefits_intel import analyze_points_and_cashback
from app.pipelines.coupon_hunter import hunt_live_coupons
from app.pipelines.financial_engine import compute_financial_verdicts
from app.pipelines.identification import (
    identify_from_image,
    identify_from_text,
    identify_from_url,
)
from app.pipelines.recommendations import build_recommendations, compute_timing_verdict
from app.pipelines.search_trust import search_and_filter_offers_v2
from app.pipelines.spec_validator import build_spec_contract

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
    """Orchestrate the 5 SmartBuy Radar v2.0 pipelines and stream live SSE progress events."""
    try:
        # ── PIPELINE 1: IDENTIFICAÇÃO EXATA + TRAVA A (SPEC LOCK) ───────────────
        if image_bytes and image_mime and not variation_override:
            yield SSEEvent(
                step=1,
                stage="identification",
                status="running",
                message="Analisando imagem via Gemini Vision para extrair marca, modelo, cor e SKU...",
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
                message=f"Resolvendo especificações exatas e construindo Trava Anti-Downgrade para '{base_q}'...",
                progress_pct=12,
            )
            product = await identify_from_text(base_q, variation_override=variation_override)

        spec = await build_spec_contract(product.search_query, product.exact_model)
        ref_txt = (
            f" | Preço referência: R$ {product.reference_price_brl:,.2f}"
            if product.reference_price_brl
            else ""
        )
        mand_txt = ", ".join(spec.mandatory_tokens[:5]) or "padrão"
        forb_sample = ", ".join(spec.forbidden_tokens[:6])
        yield SSEEvent(
            step=1,
            stage="identification",
            status="done",
            message=(
                f"Trava A ativa para '{spec.canonical_name}'{ref_txt} "
                f"[Obrigatórios: {mand_txt} | Bloqueados: {forb_sample}...]"
            ),
            progress_pct=25,
            data={"product": product.model_dump(), "spec": spec.model_dump()},
        )

        # ── PIPELINE 2: MARKETPLACE DEEP HUNTER + TRAVAS A, B e C ───────────────
        yield SSEEvent(
            step=2,
            stage="search_trust",
            status="running",
            message="Varrendo Varejistas + Comunidades 72h (Pelando, Promobit, Shopee, ML, Amazon, KaBuM!, Magalu) com Travas A/B/C...",
            progress_pct=45,
        )
        search_data = await search_and_filter_offers_v2(product, spec=spec)
        offers = search_data["offers"]
        rejected_count = search_data["rejected_count"]
        rejected_reasons = search_data["rejected_reasons"]
        blocked_groups = search_data["blocked_groups"]
        channels = search_data["channels"]
        price_floor_brl = search_data["price_floor_brl"]
        semantic_check = search_data["semantic_check"]
        community_coupons = search_data["community_coupons"]

        if not offers:
            yield SSEEvent(
                step=2,
                stage="search_trust",
                status="warning",
                message=(
                    f"Nenhuma oferta passou nas 3 travas estritas para '{spec.canonical_name}' "
                    f"({rejected_count} anúncios de versões inferiores/acessórios bloqueados)."
                ),
                progress_pct=62,
            )
        else:
            blocked_summary = (
                ", ".join(f"{g.count} {g.label}" for g in blocked_groups[:3])
                if blocked_groups
                else "nenhum downgrade detectado"
            )
            yield SSEEvent(
                step=2,
                stage="search_trust",
                status="done",
                message=(
                    f"{len(offers)} ofertas exatas aprovadas | Ignorados: {rejected_count} "
                    f"({blocked_summary})."
                ),
                progress_pct=60,
            )

        # ── PIPELINE 3 & 4: CUPONS VIVOS + PONTOS/CASHBACK + RECOMENDAÇÕES ──────
        yield SSEEvent(
            step=3,
            stage="coupons_benefits",
            status="running",
            message="Validando cupons vivos (Cuponomia/Pelando/Promobit), paridade Livelo/Esfera e recomendações de ecossistema...",
            progress_pct=76,
        )

        best_raw_pix = min((o.price_pix for o in offers), default=product.reference_price_brl)
        coupons_by_store, benefits_by_store, recommendations = await asyncio.gather(
            hunt_live_coupons(product, offers),
            analyze_points_and_cashback(product, offers, mile_value_brl=mile_value_brl),
            build_recommendations(product, spec, best_raw_pix),
        )

        # Merge community coupons discovered by Promobit/Pelando
        for cc in community_coupons:
            store_list = coupons_by_store.setdefault(cc.store_name, [])
            if not any(existing.code == cc.code for existing in store_list):
                store_list.insert(0, cc)

        total_coupons = sum(len(v) for v in coupons_by_store.values())
        total_benefits = sum(len(v) for v in benefits_by_store.values())
        yield SSEEvent(
            step=4,
            stage="coupons_benefits",
            status="done",
            message=(
                f"Encontrados {total_coupons} cupons ativos, {total_benefits} opções de pontos/cashback "
                f"e {len(recommendations)} recomendações de ecossistema."
            ),
            progress_pct=88,
        )

        # ── PIPELINE 5: COMPARADOR FINANCEIRO + MEMÓRIA SQLITE + TERMÔMETRO ─────
        yield SSEEvent(
            step=5,
            stage="financial_engine",
            status="running",
            message="Calculando Custo Efetivo Real, Termômetro de Timing e salvando no banco de memória SQLite...",
            progress_pct=95,
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

        # Check previous history before inserting current datapoint so Timing Verdict compares fairly
        prod_key = make_product_key(spec.canonical_name, spec.mandatory_tokens)
        existing_tracked = get_product_by_key(prod_key)
        prev_lowest = existing_tracked.get("lowest_seen_price") if existing_tracked else None
        prev_history = db_history(existing_tracked["id"]) if existing_tracked else []

        timing = compute_timing_verdict(
            offers=result.offers,
            history_rows=prev_history,
            lowest_seen_db=prev_lowest,
        )

        # Persist to SQLite memory
        best_offer = result.offers[0] if result.offers else None
        tracked_id = upsert_tracked_after_search(
            canonical_name=spec.canonical_name,
            search_query=product.search_query,
            mandatory_tokens=spec.mandatory_tokens,
            forbidden_tokens=spec.forbidden_tokens,
            category=product.category,
            brand=product.brand,
            image_url=product.image_url,
            best_pix=best_offer.price_pix if best_offer else None,
            best_store=best_offer.store_name if best_offer else "",
            best_url=best_offer.product_url if best_offer else "",
            active_coupon=(
                best_offer.applied_coupon.code
                if (best_offer and best_offer.applied_coupon)
                else ""
            ),
        )
        if best_offer:
            record_price(
                tracked_id,
                price_pix=best_offer.price_pix,
                price_installments=best_offer.price_installment_total,
                effective_price=best_offer.effective_net_price,
                store=best_offer.store_name,
            )
        if recommendations:
            save_recommendations(tracked_id, [r.model_dump() for r in recommendations])

        result = result.model_copy(
            update={
                "spec": spec,
                "blocked_groups": blocked_groups,
                "channels": channels,
                "price_floor_brl": price_floor_brl,
                "semantic_check": semantic_check,
                "timing": timing,
                "recommendations": recommendations,
                "tracked_product_id": tracked_id,
            }
        )

        yield SSEEvent(
            step=5,
            stage="financial_engine",
            status="result",
            message="Plano de Compra Definitivo v2.0 pronto!",
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
