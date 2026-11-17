from __future__ import annotations

import logging
from datetime import datetime
from google import genai
from google.genai import types

from app.config import settings
from app.models import BenefitOption, IdentifiedProduct, StoreOffer
from app.utils.parsers import extract_json_from_llm

logger = logging.getLogger(__name__)


def calculate_benefit_value_brl(
    net_price_brl: float,
    points_per_brl: float,
    cashback_percent: float,
    mile_value_brl: float,
) -> tuple[int, float]:
    """
    Calculate (estimated_points, estimated_return_brl) for a benefit option.
    Points are valued at mile_value_brl per 1,000 points.
    """
    if points_per_brl > 0:
        pts = int(round(net_price_brl * points_per_brl))
        val = round((pts / 1000.0) * mile_value_brl, 2)
        return pts, val
    if cashback_percent > 0:
        val = round(net_price_brl * (cashback_percent / 100.0), 2)
        return 0, val
    return 0, 0.0


async def analyze_points_and_cashback(
    product: IdentifiedProduct,
    offers: list[StoreOffer],
    mile_value_brl: float = 30.0,
) -> dict[str, list[BenefitOption]]:
    """
    Pipeline 4: Cross-reference active Livelo, Esfera, Méliuz, Inter Shop, Cuponomia,
    Zoom/Buscapé, Nubank Shopping, and native store perks for every trusted store.
    """
    if not offers:
        return {}

    unique_stores = list(dict.fromkeys(o.store_name for o in offers))
    client = genai.Client(api_key=settings.gemini_api_key)
    today_str = datetime.now().strftime("%d/%m/%Y")

    prompt = (
        f"Hoje é {today_str}. Você é um especialista em Pontos, Milhas e Cashback no Brasil.\n"
        f"Produto: {product.exact_model} (Categoria: {product.category}).\n"
        f"Lojas encontradas: {', '.join(unique_stores)}.\n\n"
        "Use o Google Search para consultar as taxas ATIVAS HOJE de:\n"
        "1. Programas de Pontos: Livelo (pontos por real) e Esfera (pontos por real) para essas lojas.\n"
        "2. Cashback e Shoppings de Bancos: Méliuz, Cuponomia, Inter Shop, Zoom, Buscapé e Nubank Shopping.\n"
        "3. Vantagens Nativas: Meli+ (Mercado Livre), Amazon Prime / Cartão Amazon, MagaluPay, desconto App.\n\n"
        "Retorne apenas taxas reais verificadas hoje (não invente promoções de 10 pts/R$ se hoje estiver 1 pt/R$).\n"
        "Retorne EXCLUSIVAMENTE um JSON array:\n"
        "[\n"
        "  {\n"
        '    "platform": "Livelo",\n'
        '    "store_name": "Amazon Brasil",\n'
        '    "benefit_type": "points" | "cashback" | "native_perk",\n'
        '    "rate_label": "4 pts/R$" ou "5% cashback",\n'
        '    "points_per_brl": 4.0,\n'
        '    "cashback_percent": 0.0,\n'
        '    "rule_summary": "Válido para compras via hotsite Livelo em itens selecionados",\n'
        '    "portal_url": "https://www.livelo.com.br/..."\n'
        "  }\n"
        "]"
    )

    parsed = None
    try:
        response = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
            ),
        )
        parsed = extract_json_from_llm(response.text or "")
    except Exception as exc:
        logger.warning("Gemini Search falhou em analyze_points_and_cashback (%s); prosseguindo sem taxas via IA.", exc)

    benefits_by_store: dict[str, list[BenefitOption]] = {s: [] for s in unique_stores}

    if not isinstance(parsed, list):
        return benefits_by_store

    for item in parsed:
        if not isinstance(item, dict):
            continue
        store = str(item.get("store_name") or "").strip()
        platform = str(item.get("platform") or "").strip()
        if not store or not platform:
            continue

        matched_store = next(
            (s for s in unique_stores if s.lower() in store.lower() or store.lower() in s.lower()),
            store,
        )
        if matched_store not in benefits_by_store:
            benefits_by_store[matched_store] = []

        pts_per_brl = float(item.get("points_per_brl") or 0.0)
        cb_pct = float(item.get("cashback_percent") or 0.0)
        if pts_per_brl <= 0 and cb_pct <= 0:
            continue

        btype = item.get("benefit_type")
        if btype not in ("points", "cashback", "native_perk"):
            btype = "points" if pts_per_brl > 0 else "cashback"

        benefits_by_store[matched_store].append(
            BenefitOption(
                platform=platform,
                store_name=matched_store,
                benefit_type=btype,
                rate_label=str(
                    item.get("rate_label")
                    or (f"{pts_per_brl:g} pts/R$" if pts_per_brl > 0 else f"{cb_pct:g}% cashback")
                ),
                points_per_brl=pts_per_brl,
                cashback_percent=cb_pct,
                estimated_points=0,
                estimated_return_brl=0.0,
                rule_summary=str(item.get("rule_summary") or ""),
                portal_url=str(item.get("portal_url") or ""),
            )
        )

    return benefits_by_store
