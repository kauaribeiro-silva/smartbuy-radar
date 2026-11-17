from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

from app.config import settings
from app.models import IdentifiedProduct, StoreOffer
from app.utils.browser import DEFAULT_HEADERS, fetch_page_smart
from app.utils.parsers import (
    evaluate_offer_trust,
    extract_json_from_llm,
    extract_product_metadata,
    match_known_store,
    parse_brl_price,
)

logger = logging.getLogger(__name__)


async def _search_mercadolivre_live(query: str) -> list[dict[str, Any]]:
    """Query Mercado Livre public search page/API for live offers and seller badges."""
    results: list[dict[str, Any]] = []
    search_url = f"https://lista.mercadolivre.com.br/{quote_plus(query).replace('+', '-')}"
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=10.0
        ) as client:
            resp = await client.get(search_url)
            if resp.status_code != 200:
                return results
            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.select("li.ui-search-layout__item, div.poly-card")[:6]
            for card in cards:
                title_el = card.select_one("a.poly-component__title, h2.ui-search-item__title")
                link_el = card.select_one("a.poly-component__title, a.ui-search-link")
                price_el = card.select_one(
                    "div.poly-price__current span.andes-money-amount__fraction, span.andes-money-amount__fraction"
                )
                seller_el = card.select_one(
                    "span.poly-component__seller, p.ui-search-official-store-label"
                )
                if not (title_el and link_el and price_el):
                    continue
                price_val = parse_brl_price(price_el.get_text(strip=True))
                if not price_val:
                    continue
                seller_text = seller_el.get_text(" ", strip=True) if seller_el else ""
                is_official = "oficial" in seller_text.lower() or "loja oficial" in seller_text.lower()
                results.append(
                    {
                        "store_name": "Mercado Livre",
                        "store_domain": "mercadolivre.com.br",
                        "product_title": title_el.get_text(" ", strip=True),
                        "product_url": link_el.get("href", search_url),
                        "price_pix": price_val,
                        "price_installment_total": price_val,
                        "installments_count": 10,
                        "interest_free": True,
                        "seller_name": seller_text or "MercadoLíder",
                        "seller_rating": 4.8 if is_official else 4.7,
                        "is_official_store": is_official,
                        "in_stock": True,
                    }
                )
    except Exception as exc:
        logger.debug("Mercado Livre live search failed: %s", exc)
    return results


async def _search_via_gemini_grounding(product: IdentifiedProduct) -> list[dict[str, Any]]:
    """Use Gemini 2.5 Flash + Google Search Grounding to find live Brazilian store offers."""
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY não configurada.")

    client = genai.Client(api_key=settings.gemini_api_key)
    ean_part = f" (EAN/GTIN: {product.ean_gtin})" if product.ean_gtin else ""
    sku_part = f" (SKU: {product.sku_part_number})" if product.sku_part_number else ""

    prompt = (
        f"Pesquise ofertas REAIS e ATUAIS hoje no Brasil para o produto exato:\n"
        f"Produto: {product.exact_model}{ean_part}{sku_part}\n"
        f"Termo de busca: {product.search_query}\n\n"
        "Busque exclusivamente em grandes varejistas e lojas oficiais confiáveis do Brasil:\n"
        "Amazon Brasil (amazon.com.br), Mercado Livre (mercadolivre.com.br), KaBuM! (kabum.com.br), "
        "Magazine Luiza (magazineluiza.com.br), Fast Shop (fastshop.com.br), Shopee Oficial (shopee.com.br), "
        "Carrefour (carrefour.com.br), Casas Bahia (casasbahia.com.br) e loja oficial da marca.\n\n"
        "REGRAS ESTRITAS:\n"
        "1. Retorne apenas produtos NOVOS (descarte usados, recondicionados, vitrine ou acessórios).\n"
        "2. Informe o preço À VISTA (Pix/Boleto), o preço TOTAL PARCELADO, número de parcelas sem juros, "
        "nome do vendedor (ex: 'Vendido e entregue por Amazon.com.br' ou 'Loja Oficial Samsung') e URL real.\n"
        "3. Retorne EXCLUSIVAMENTE um JSON array válido com até 10 ofertas:\n"
        "[\n"
        "  {\n"
        '    "store_name": "Amazon Brasil",\n'
        '    "product_title": "Título exato do anúncio",\n'
        '    "product_url": "https://...",\n'
        '    "price_pix": 4299.00,\n'
        '    "price_installment_total": 4599.00,\n'
        '    "installments_count": 10,\n'
        '    "interest_free": true,\n'
        '    "seller_name": "Amazon.com.br",\n'
        '    "seller_rating": 4.9,\n'
        '    "is_official_store": true,\n'
        '    "in_stock": true\n'
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

    # Collect grounding URLs if available to enrich/verify links
    grounding_urls: list[str] = []
    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            gm = getattr(candidates[0], "grounding_metadata", None)
            chunks = getattr(gm, "grounding_chunks", None) or []
            for ch in chunks:
                web = getattr(ch, "web", None)
                if web and getattr(web, "uri", None):
                    grounding_urls.append(web.uri)
    except Exception:
        pass

    parsed = extract_json_from_llm(response.text or "")
    if not isinstance(parsed, list):
        return []

    cleaned: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        pix = parse_brl_price(item.get("price_pix"))
        inst_total = parse_brl_price(item.get("price_installment_total")) or pix
        if not pix:
            continue
        url = str(item.get("product_url") or "").strip()
        if not url.startswith("http"):
            continue
        store_name, store_domain = match_known_store(url)
        if item.get("store_name") and store_name == store_domain:
            store_name = str(item["store_name"])

        cleaned.append(
            {
                "store_name": store_name,
                "store_domain": store_domain,
                "product_title": str(item.get("product_title") or product.exact_model),
                "product_url": url,
                "price_pix": pix,
                "price_installment_total": inst_total or pix,
                "installments_count": int(item.get("installments_count") or 10),
                "interest_free": bool(item.get("interest_free", True)),
                "seller_name": str(item.get("seller_name") or ""),
                "seller_rating": (
                    float(item["seller_rating"])
                    if item.get("seller_rating") is not None
                    else None
                ),
                "is_official_store": bool(item.get("is_official_store", False)),
                "in_stock": bool(item.get("in_stock", True)),
            }
        )
    return cleaned


async def _verify_live_price_sample(offer: dict[str, Any]) -> dict[str, Any]:
    """Attempt live HTML verification on a candidate offer URL to confirm price & stock."""
    url = offer["product_url"]
    # Skip redirect/grounding proxy URLs from live HTML scrape
    if "vertexaisearch.cloud.google.com" in url:
        return offer
    try:
        html = await fetch_page_smart(url)
        if not html:
            return offer
        meta = extract_product_metadata(html, url)
        if meta.get("price_brl"):
            live_price = meta["price_brl"]
            # Update if reasonable
            if live_price > 10:
                offer["price_pix"] = min(offer["price_pix"], live_price) if offer["price_pix"] else live_price
        if meta.get("in_stock") is False:
            offer["in_stock"] = False
        if meta.get("seller_name") and not offer.get("seller_name"):
            offer["seller_name"] = meta["seller_name"]
    except Exception:
        pass
    return offer


async def search_and_filter_offers(
    product: IdentifiedProduct,
) -> tuple[list[StoreOffer], int, list[str]]:
    """
    Pipeline 2: Search across Brazilian retailers and apply strict Trust Score & anti-scam filters.
    Returns (trusted_offers, rejected_count, rejected_reasons).
    """
    gemini_task = _search_via_gemini_grounding(product)
    ml_task = _search_mercadolivre_live(product.search_query)

    gemini_raw, ml_raw = await asyncio.gather(gemini_task, ml_task, return_exceptions=True)
    raw_offers: list[dict[str, Any]] = []
    if isinstance(gemini_raw, list):
        raw_offers.extend(gemini_raw)
    if isinstance(ml_raw, list):
        raw_offers.extend(ml_raw)

    # Live-verify top 4 non-ML offers in parallel (ML was already fetched live)
    to_verify = [o for o in raw_offers if o.get("store_domain") != "mercadolivre.com.br"][:4]
    if to_verify:
        verified = await asyncio.gather(
            *[_verify_live_price_sample(o) for o in to_verify],
            return_exceptions=True,
        )
        for idx, res in enumerate(verified):
            if isinstance(res, dict):
                to_verify[idx].update(res)

    # Reference price for anomaly detection
    ref_price = product.reference_price_brl
    if not ref_price and raw_offers:
        prices = sorted(o["price_pix"] for o in raw_offers if o.get("price_pix"))
        if prices:
            ref_price = prices[len(prices) // 2]

    trusted_offers: list[StoreOffer] = []
    rejected_reasons: list[str] = []
    seen_keys: set[str] = set()

    for item in raw_offers:
        if not item.get("in_stock", True):
            rejected_reasons.append(f"{item['store_name']}: Produto sem estoque.")
            continue

        dedup_key = f"{item['store_domain']}::{round(item['price_pix'], 0)}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        passed, score, reason = evaluate_offer_trust(
            product_title=item["product_title"],
            target_query=product.exact_model,
            store_name=item["store_name"],
            seller_name=item.get("seller_name", ""),
            seller_rating=item.get("seller_rating"),
            is_official_store=item.get("is_official_store", False),
            price_pix=item["price_pix"],
            reference_price=ref_price,
        )

        if not passed:
            rejected_reasons.append(f"{item['store_name']} ({item.get('seller_name') or '3P'}): {reason}")
            continue

        inst_count = max(1, int(item.get("installments_count") or 10))
        inst_total = float(item.get("price_installment_total") or item["price_pix"])
        inst_val = round(inst_total / inst_count, 2)

        trusted_offers.append(
            StoreOffer(
                id=str(uuid.uuid4())[:8],
                store_name=item["store_name"],
                store_domain=item["store_domain"],
                seller_name=item.get("seller_name") or item["store_name"],
                seller_rating=item.get("seller_rating"),
                is_official_store=item.get("is_official_store", False),
                trust_score=score,
                trust_reason=reason,
                product_title=item["product_title"],
                product_url=item["product_url"],
                in_stock=True,
                price_pix=round(float(item["price_pix"]), 2),
                price_installment_total=round(inst_total, 2),
                installments_count=inst_count,
                installment_value=inst_val,
                interest_free=bool(item.get("interest_free", True)),
                effective_net_price=round(float(item["price_pix"]), 2),
            )
        )

    trusted_offers.sort(key=lambda o: o.price_pix)
    return trusted_offers, len(rejected_reasons), rejected_reasons
