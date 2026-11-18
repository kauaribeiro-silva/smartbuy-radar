from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

import httpx
from google import genai
from google.genai import types

from app.config import settings
from app.models import BenefitOption, IdentifiedProduct, StoreOffer
from app.pipelines.coupon_hunter import CUPONOMIA_SLUGS
from app.utils.browser import DEFAULT_HEADERS
from app.utils.parsers import extract_json_from_llm

logger = logging.getLogger(__name__)

# Baseline standard parities in Brazil when live AI grounding is rate-limited
BASELINE_STORE_BENEFITS: dict[str, list[dict[str, str | float]]] = {
    "Amazon Brasil": [
        {
            "platform": "Livelo",
            "benefit_type": "points",
            "rate_label": "1 pt/R$",
            "points_per_brl": 1.0,
            "cashback_percent": 0.0,
            "rule_summary": "Paridade base Livelo para Amazon Brasil via hotsite parceiro.",
            "portal_url": "https://www.livelo.com.br",
        },
        {
            "platform": "Méliuz",
            "benefit_type": "cashback",
            "rate_label": "2% cashback",
            "points_per_brl": 0.0,
            "cashback_percent": 2.0,
            "rule_summary": "Cashback em categorias selecionadas ativando o redirecionamento Méliuz.",
            "portal_url": "https://www.meliuz.com.br/desconto/cupom-amazon",
        },
    ],
    "KaBuM!": [
        {
            "platform": "Livelo",
            "benefit_type": "points",
            "rate_label": "1 pt/R$",
            "points_per_brl": 1.0,
            "cashback_percent": 0.0,
            "rule_summary": "Pontuação base Livelo em produtos vendidos e entregues por KaBuM!.",
            "portal_url": "https://www.livelo.com.br",
        },
    ],
    "Magazine Luiza": [
        {
            "platform": "Livelo",
            "benefit_type": "points",
            "rate_label": "2 pts/R$",
            "points_per_brl": 2.0,
            "cashback_percent": 0.0,
            "rule_summary": "Acúmulo em produtos vendidos e entregues por Magalu via hotsite Livelo.",
            "portal_url": "https://www.livelo.com.br",
        },
    ],
    "Fast Shop": [
        {
            "platform": "Esfera",
            "benefit_type": "points",
            "rate_label": "2 pts/R$",
            "points_per_brl": 2.0,
            "cashback_percent": 0.0,
            "rule_summary": "Acúmulo de pontos Esfera via link parceiro Fast Shop.",
            "portal_url": "https://www.esfera.com.vc",
        },
    ],
    "Mercado Livre": [
        {
            "platform": "Meli+ / Méliuz",
            "benefit_type": "cashback",
            "rate_label": "1.5% cashback",
            "points_per_brl": 0.0,
            "cashback_percent": 1.5,
            "rule_summary": "Benefício Meli+ / portal parceiro com carrinho limpo.",
            "portal_url": "https://www.mercadolivre.com.br",
        },
    ],
    "Shopee Oficial": [
        {
            "platform": "Cuponomia / Moedas Shopee",
            "benefit_type": "cashback",
            "rate_label": "2% cashback",
            "points_per_brl": 0.0,
            "cashback_percent": 2.0,
            "rule_summary": "Cashback em Lojas Oficiais (Shopee Mall) via portal parceiro.",
            "portal_url": "https://www.cuponomia.com.br/desconto/shopee",
        },
    ],
    "Carrefour": [
        {
            "platform": "Esfera",
            "benefit_type": "points",
            "rate_label": "1.5 pts/R$",
            "points_per_brl": 1.5,
            "cashback_percent": 0.0,
            "rule_summary": "Acúmulo de pontos Esfera na loja parceira Carrefour.",
            "portal_url": "https://www.esfera.com.vc",
        },
    ],
    "Casas Bahia": [
        {
            "platform": "Livelo",
            "benefit_type": "points",
            "rate_label": "1.5 pts/R$",
            "points_per_brl": 1.5,
            "cashback_percent": 0.0,
            "rule_summary": "Acúmulo de pontos Livelo em itens vendidos por Casas Bahia.",
            "portal_url": "https://www.livelo.com.br",
        },
    ],
}


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


async def _scrape_live_cuponomia_cashback(store_name: str) -> BenefitOption | None:
    """Scrape today's real cashback percentage directly from Cuponomia store page."""
    slug = CUPONOMIA_SLUGS.get(store_name)
    if not slug:
        return None
    url = f"https://www.cuponomia.com.br/desconto/{slug}"
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=7.5
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            # Look for explicit cashback rate like "2,1% de cashback" or "até 5% de cashback (era 1,5%)"
            m_std = re.search(
                r"\(era\s+(\d+(?:,\d+)?)%\)|(\d+(?:,\d+)?)%\s*de\s*cashback",
                resp.text,
                re.IGNORECASE,
            )
            if not m_std:
                return None
            raw_pct = (m_std.group(1) or m_std.group(2) or "0").replace(",", ".")
            pct = float(raw_pct)
            if pct <= 0 or pct > 25:
                return None
            return BenefitOption(
                platform="Cuponomia",
                store_name=store_name,
                benefit_type="cashback",
                rate_label=f"{pct:g}% cashback (Ao Vivo)",
                points_per_brl=0.0,
                cashback_percent=pct,
                rule_summary=f"Taxa de cashback verificada hoje no Cuponomia para {store_name}.",
                portal_url=url,
            )
    except Exception:
        return None


async def analyze_points_and_cashback(
    product: IdentifiedProduct,
    offers: list[StoreOffer],
    mile_value_brl: float = 30.0,
) -> dict[str, list[BenefitOption]]:
    """
    Pipeline 4: Cross-reference active Livelo, Esfera, Méliuz, Inter Shop, Cuponomia (live HTTPX),
    Zoom/Buscapé live Redux rates, and native store perks for every trusted store.
    """
    if not offers:
        return {}

    unique_stores = list(dict.fromkeys(o.store_name for o in offers))
    benefits_by_store: dict[str, list[BenefitOption]] = {s: [] for s in unique_stores}

    # 1. Inject live cashback rates extracted directly from Zoom / Buscapé __NEXT_DATA__
    for o in offers:
        if o.live_cashback_pct and o.live_cashback_pct > 0:
            existing = benefits_by_store.get(o.store_name, [])
            if not any(b.platform.startswith("Zoom") for b in existing):
                existing.append(
                    BenefitOption(
                        platform="Zoom / Buscapé",
                        store_name=o.store_name,
                        benefit_type="cashback",
                        rate_label=f"{o.live_cashback_pct:g}% cashback (Ao Vivo)",
                        points_per_brl=0.0,
                        cashback_percent=o.live_cashback_pct,
                        rule_summary="Cashback real extraído da oferta no comparador Zoom/Buscapé.",
                        portal_url="https://www.zoom.com.br",
                    )
                )

    # 2. Scrape live Cuponomia cashback percentages in parallel
    live_cuponomia = await asyncio.gather(
        *[_scrape_live_cuponomia_cashback(s) for s in unique_stores],
        return_exceptions=True,
    )
    for res in live_cuponomia:
        if isinstance(res, BenefitOption):
            benefits_by_store[res.store_name].append(res)

    # 3. Try Gemini Search Grounding for promotional Livelo/Esfera campaigns (e.g. 5 to 10 pts/R$)
    parsed = None
    if settings.gemini_api_key:
        try:
            client = genai.Client(api_key=settings.gemini_api_key)
            today_str = datetime.now().strftime("%d/%m/%Y")
            prompt = (
                f"Hoje é {today_str}. Você é um especialista em Pontos, Milhas e Cashback no Brasil.\n"
                f"Produto: {product.exact_model} (Categoria: {product.category}).\n"
                f"Lojas encontradas: {', '.join(unique_stores)}.\n\n"
                "Use o Google Search para consultar as taxas ATIVAS HOJE de Livelo, Esfera, Méliuz e Inter Shop.\n"
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
            logger.warning(
                "Gemini Search falhou em analyze_points_and_cashback (%s); usando taxas ao vivo + tabela base.",
                str(exc)[:100],
            )

    if isinstance(parsed, list) and parsed:
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

    # 4. Merge baseline Livelo/Esfera/Méliuz options so points vs cashback comparison is always complete
    for store in unique_stores:
        base_list = BASELINE_STORE_BENEFITS.get(
            store,
            [
                {
                    "platform": "Zoom / Buscapé",
                    "benefit_type": "cashback",
                    "rate_label": "1% cashback",
                    "points_per_brl": 0.0,
                    "cashback_percent": 1.0,
                    "rule_summary": "Cashback padrão via comparador oficial.",
                    "portal_url": "https://www.zoom.com.br",
                }
            ],
        )
        existing_platforms = {b.platform.lower() for b in benefits_by_store[store]}
        for b in base_list:
            if str(b["platform"]).lower() not in existing_platforms:
                benefits_by_store[store].append(
                    BenefitOption(
                        platform=str(b["platform"]),
                        store_name=store,
                        benefit_type=str(b["benefit_type"]),  # type: ignore[arg-type]
                        rate_label=str(b["rate_label"]),
                        points_per_brl=float(b["points_per_brl"]),
                        cashback_percent=float(b["cashback_percent"]),
                        rule_summary=str(b["rule_summary"]),
                        portal_url=str(b["portal_url"]),
                    )
                )

    return benefits_by_store
