from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

from app.config import settings
from app.models import CouponInfo, IdentifiedProduct, StoreOffer
from app.utils.browser import DEFAULT_HEADERS
from app.utils.parsers import extract_json_from_llm

logger = logging.getLogger(__name__)

DEAD_COUPON_MARKERS = (
    "esgotado",
    "expirado",
    "acabou",
    "encerrou",
    "encerrado",
    "limite atingido",
    "inválido",
    "inativo",
)


async def _scrape_pelando_recent(store_name: str) -> list[str]:
    """Fetch recent active deal/coupon snippets from Pelando search."""
    snippets: list[str] = []
    url = f"https://www.pelando.com.br/busca/{quote_plus('cupom ' + store_name)}"
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=8.0
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return snippets
            soup = BeautifulSoup(resp.text, "lxml")
            for el in soup.select("a, h3, span")[:60]:
                txt = el.get_text(" ", strip=True)
                if "cupom" in txt.lower() and 10 < len(txt) < 160:
                    if not any(m in txt.lower() for m in DEAD_COUPON_MARKERS):
                        snippets.append(txt)
    except Exception as exc:
        logger.debug("Pelando scrape skipped for %s: %s", store_name, exc)
    return snippets[:6]


def compute_coupon_discount(coupon: CouponInfo, base_price: float) -> float:
    """Compute BRL discount for a coupon against a given base price."""
    if coupon.min_spend_brl and base_price < coupon.min_spend_brl:
        return 0.0
    if coupon.discount_type == "percent":
        disc = base_price * (coupon.discount_value / 100.0)
        if coupon.max_discount_brl:
            disc = min(disc, coupon.max_discount_brl)
        return round(max(0.0, disc), 2)
    return round(min(base_price, max(0.0, coupon.discount_value)), 2)


async def hunt_live_coupons(
    product: IdentifiedProduct,
    offers: list[StoreOffer],
) -> dict[str, list[CouponInfo]]:
    """
    Pipeline 3: Hunt and validate live, active coupons in the last 24h-48h for the target stores.
    Returns mapping of store_name -> list[CouponInfo].
    """
    if not offers:
        return {}

    unique_stores = list(dict.fromkeys(o.store_name for o in offers))
    pelando_results = await asyncio.gather(
        *[_scrape_pelando_recent(s) for s in unique_stores[:5]],
        return_exceptions=True,
    )
    pelando_context: dict[str, list[str]] = {}
    for idx, res in enumerate(pelando_results):
        if isinstance(res, list) and res:
            pelando_context[unique_stores[idx]] = res

    client = genai.Client(api_key=settings.gemini_api_key)
    today_str = datetime.now().strftime("%d/%m/%Y")

    prompt = (
        f"Hoje é {today_str}. Você é um Caçador de Cupons Vivos no e-commerce brasileiro.\n"
        f"Produto alvo: {product.exact_model} (Categoria: {product.category}).\n"
        f"Lojas encontradas: {', '.join(unique_stores)}.\n"
        f"Sinais recentes do Pelando (se houver): {pelando_context}\n\n"
        "Use o Google Search para buscar cupons ATIVOS NAS ÚLTIMAS 24h/48h (Pelando, Promobit, Cuponomia, "
        "páginas oficiais de cupons do Mercado Livre, Amazon, KaBuM!, Magalu, Shopee, Fast Shop) "
        "que se apliquem a essas lojas ou à categoria do produto.\n\n"
        "REGRAS RÍGIDAS:\n"
        "1. PROIBIDO inventar cupons genéricos mortos. Só inclua cupons com evidência real recente.\n"
        "2. Descarte IMEDIATAMENTE qualquer cupom marcado como esgotado, expirado ou limite atingido.\n"
        "3. Classifique status como:\n"
        '   - "CONFIRMADO_HOJE" se reportado ativo hoje/ontem ou cupom direto da página oficial da loja.\n'
        '   - "TESTAR_NO_CHECKOUT" se for cupom de categoria, app, primeira compra ou cartão da loja.\n\n'
        "Retorne EXCLUSIVAMENTE um JSON array:\n"
        "[\n"
        "  {\n"
        '    "code": "CODIGO10",\n'
        '    "store_name": "Mercado Livre",\n'
        '    "discount_type": "percent" ou "fixed",\n'
        '    "discount_value": 10.0,\n'
        '    "max_discount_brl": 200.0 ou null,\n'
        '    "min_spend_brl": 500.0 ou null,\n'
        '    "status": "CONFIRMADO_HOJE" ou "TESTAR_NO_CHECKOUT",\n'
        '    "rule_description": "10% OFF limitado a R$ 200 em compras acima de R$ 500 no App",\n'
        '    "source": "Pelando / Página Oficial ML"\n'
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
    coupons_by_store: dict[str, list[CouponInfo]] = {s: [] for s in unique_stores}

    if not isinstance(parsed, list):
        return coupons_by_store

    for item in parsed:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip().upper()
        store = str(item.get("store_name") or "").strip()
        rule = str(item.get("rule_description") or "")
        if not code or len(code) < 3:
            continue
        if any(m in f"{code} {rule}".lower() for m in DEAD_COUPON_MARKERS):
            continue

        # Match store name flexibly
        matched_store = next(
            (s for s in unique_stores if s.lower() in store.lower() or store.lower() in s.lower()),
            store,
        )
        if matched_store not in coupons_by_store:
            coupons_by_store[matched_store] = []

        dtype = "percent" if item.get("discount_type") == "percent" else "fixed"
        dval = float(item.get("discount_value") or 0.0)
        if dval <= 0:
            continue

        status = (
            "CONFIRMADO_HOJE"
            if item.get("status") == "CONFIRMADO_HOJE"
            else "TESTAR_NO_CHECKOUT"
        )

        coupons_by_store[matched_store].append(
            CouponInfo(
                code=code,
                store_name=matched_store,
                discount_type=dtype,
                discount_value=dval,
                max_discount_brl=(
                    float(item["max_discount_brl"])
                    if item.get("max_discount_brl") is not None
                    else None
                ),
                min_spend_brl=(
                    float(item["min_spend_brl"])
                    if item.get("min_spend_brl") is not None
                    else None
                ),
                status=status,
                rule_description=rule,
                source=str(item.get("source") or "Busca ao vivo"),
            )
        )

    return coupons_by_store
