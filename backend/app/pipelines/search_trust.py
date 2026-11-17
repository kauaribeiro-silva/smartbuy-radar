from __future__ import annotations

import asyncio
import json
import logging
import re
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

STORE_DOMAIN_MAP: dict[str, str] = {
    "amazon": "amazon.com.br",
    "amazon brasil": "amazon.com.br",
    "mercado livre": "mercadolivre.com.br",
    "mercadolivre": "mercadolivre.com.br",
    "kabum!": "kabum.com.br",
    "kabum": "kabum.com.br",
    "magazine luiza": "magazineluiza.com.br",
    "magalu": "magazineluiza.com.br",
    "fast shop": "fastshop.com.br",
    "fastshop": "fastshop.com.br",
    "carrefour": "carrefour.com.br",
    "casas bahia": "casasbahia.com.br",
    "ponto": "pontofrio.com.br",
    "extra": "extra.com.br",
    "shopee": "shopee.com.br",
    "dell": "dell.com",
    "samsung": "samsung.com",
    "apple": "apple.com",
    "lg": "lg.com",
    "girafa": "girafa.com.br",
    "pichau": "pichau.com.br",
    "terabyteshop": "terabyteshop.com.br",
}


def _normalize_store(merchant_name: str, fallback_url: str = "") -> tuple[str, str]:
    clean = (merchant_name or "").strip()
    lower = clean.lower()
    for key, dom in STORE_DOMAIN_MAP.items():
        if key == lower or key in lower:
            canonical = {
                "amazon.com.br": "Amazon Brasil",
                "mercadolivre.com.br": "Mercado Livre",
                "kabum.com.br": "KaBuM!",
                "magazineluiza.com.br": "Magazine Luiza",
                "fastshop.com.br": "Fast Shop",
                "carrefour.com.br": "Carrefour",
                "casasbahia.com.br": "Casas Bahia",
            }.get(dom, clean)
            return canonical, dom
    if fallback_url:
        return match_known_store(fallback_url)
    return clean or "Loja Parceira", "zoom.com.br"


def _compact_search_query(query: str) -> str:
    """Strip noisy spec tokens (Hz, ms, HDR10, FreeSync, etc.) so store search engines return more matches."""
    noise = (
        r"\b(144hz|165hz|180hz|240hz|60hz|75hz|120hz|1ms|0[.,]5ms|5ms|hdr10|hdr|"
        r"g-sync|gsync|freesync|amd|nvidia|full\s*hd|fhd|1920x1080|hdmi|displayport|"
        r"nativo|mbr|wled|led)\b"
    )
    cleaned = re.sub(noise, " ", query, flags=re.IGNORECASE)
    cleaned = re.sub(r'["”″\']', " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) >= 5 else query.strip()


def _matches_query_tokens(candidate_title: str, query: str) -> bool:
    """
    Ensure candidate product title matches key model codes / specs from the query
    (e.g., if query has '24G50F', reject '27G50F' or '25G4S').
    """
    cand_lower = candidate_title.lower()
    q_lower = query.lower()

    model_codes = re.findall(r"\b(?:[a-z]+\d+[a-z0-9]*|\d+[a-z]+\d*[a-z0-9]*)\b", q_lower)
    ignore_units = {
        "144hz", "165hz", "180hz", "240hz", "60hz", "75hz", "120hz",
        "1ms", "5ms", "05ms", "hdr10", "4k", "8k", "5g", "4g",
        "110v", "220v", "127v", "bivolt", "1920x1080",
    }
    strict_codes = [
        c for c in model_codes
        if len(c) >= 5 and c not in ignore_units and not c.endswith(("gb", "tb", "mhz", "mah", "pol"))
    ]
    if strict_codes:
        return all(code in cand_lower for code in strict_codes)

    words = [w for w in re.findall(r"[a-z0-9]{3,}", _compact_search_query(q_lower)) if w not in ignore_units]
    if not words:
        return True
    matched = sum(1 for w in words if w in cand_lower)
    return matched >= max(1, int(len(words) * 0.55))


async def _search_kabum_live(query: str) -> list[dict[str, Any]]:
    """Query KaBuM! catalog via __NEXT_DATA__ JSON for real-time Pix and card prices."""
    results: list[dict[str, Any]] = []
    compact = _compact_search_query(query)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", compact).strip("-")
    url = f"https://www.kabum.com.br/busca/{slug}"
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=10.0
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return results
            soup = BeautifulSoup(resp.text, "lxml")
            nd = soup.find("script", id="__NEXT_DATA__")
            if not nd or not nd.string:
                return results
            outer = json.loads(nd.string)
            raw_data = outer.get("props", {}).get("pageProps", {}).get("data", "")
            parsed = json.loads(raw_data) if isinstance(raw_data, str) and raw_data else raw_data
            items = (
                parsed.get("catalogServer", {}).get("data", [])
                if isinstance(parsed, dict)
                else []
            )
            for it in items[:10]:
                if not isinstance(it, dict) or not it.get("available", True):
                    continue
                name = str(it.get("name") or "").strip()
                if not name or not _matches_query_tokens(name, query):
                    continue
                pix = parse_brl_price(it.get("priceWithDiscount") or it.get("price"))
                card = parse_brl_price(it.get("price")) or pix
                if not pix:
                    continue
                code = it.get("code")
                prod_url = (
                    f"https://www.kabum.com.br/produto/{code}"
                    if code
                    else url
                )
                seller_name = str(it.get("sellerName") or "KaBuM!").strip()
                is_official = seller_name.lower() in ("kabum!", "kabum", "")
                results.append(
                    {
                        "store_name": "KaBuM!",
                        "store_domain": "kabum.com.br",
                        "product_title": name,
                        "product_url": prod_url,
                        "price_pix": pix,
                        "price_installment_total": card,
                        "installments_count": 10,
                        "interest_free": True,
                        "seller_name": seller_name or "KaBuM!",
                        "seller_rating": 4.9 if is_official else 4.7,
                        "is_official_store": is_official,
                        "in_stock": True,
                    }
                )
    except Exception as exc:
        logger.debug("KaBuM! live search failed: %s", exc)
    return results


async def _search_zoom_live(query: str) -> list[dict[str, Any]]:
    """
    Query Zoom.com.br __NEXT_DATA__ Redux state for real-time multi-store Brazilian offers,
    installment plans, and cashback rates.
    """
    results: list[dict[str, Any]] = []
    compact = _compact_search_query(query)
    search_url = f"https://www.zoom.com.br/search?q={quote_plus(compact)}"
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=10.0
        ) as client:
            resp = await client.get(search_url)
            if resp.status_code != 200:
                return results
            soup = BeautifulSoup(resp.text, "lxml")
            nd = soup.find("script", id="__NEXT_DATA__")
            if not nd or not nd.string:
                return results
            redux = (
                json.loads(nd.string)
                .get("props", {})
                .get("initialReduxState", {})
            )
            hits = redux.get("hits", {}).get("hits", [])
            if not isinstance(hits, list):
                return results

            product_page_urls: list[str] = []

            for h in hits[:12]:
                if not isinstance(h, dict):
                    continue
                name = str(h.get("name") or "").strip()
                if not name or not _matches_query_tokens(name, query):
                    continue

                hit_type = h.get("type")
                raw_url = str(h.get("url") or "")
                full_url = (
                    f"https://www.zoom.com.br{raw_url}"
                    if raw_url.startswith("/")
                    else (raw_url or search_url)
                )

                if hit_type == "product" and raw_url.startswith("/") and len(product_page_urls) < 2:
                    product_page_urls.append(full_url)

                merchant = (
                    h.get("merchantName")
                    or (h.get("bestOffer") or {}).get("merchantName")
                    or ""
                )
                if not merchant:
                    continue
                store_name, store_domain = _normalize_store(merchant)
                pix = parse_brl_price(h.get("price"))
                if not pix:
                    continue

                inst = h.get("installments") or {}
                inst_months = int(inst.get("amount_months") or 10)
                inst_total = parse_brl_price(inst.get("total_value")) or pix
                has_interest = bool(h.get("hasInterest", False)) and (inst_total > pix * 1.02)
                is_mkt = bool(h.get("marketplace", False))

                results.append(
                    {
                        "store_name": store_name,
                        "store_domain": store_domain,
                        "product_title": name,
                        "product_url": full_url,
                        "price_pix": pix,
                        "price_installment_total": inst_total,
                        "installments_count": max(1, inst_months),
                        "interest_free": not has_interest,
                        "seller_name": f"{store_name} Oficial" if not is_mkt else store_name,
                        "seller_rating": 4.9 if not is_mkt else 4.7,
                        "is_official_store": not is_mkt,
                        "in_stock": True,
                    }
                )

            # Expand top product page to capture all stores (Amazon, ML, Magalu, KaBuM, Fast Shop)
            for prod_url in product_page_urls[:1]:
                try:
                    p_resp = await client.get(prod_url)
                    if p_resp.status_code != 200:
                        continue
                    p_soup = BeautifulSoup(p_resp.text, "lxml")
                    p_nd = p_soup.find("script", id="__NEXT_DATA__")
                    if not p_nd or not p_nd.string:
                        continue
                    p_redux = (
                        json.loads(p_nd.string)
                        .get("props", {})
                        .get("initialReduxState", {})
                    )
                    offer_list = (
                        p_redux.get("offers", {}).get("offerList", [])
                        if isinstance(p_redux.get("offers"), dict)
                        else []
                    )
                    for o in offer_list[:10]:
                        if not isinstance(o, dict):
                            continue
                        o_name = str(o.get("name") or query).strip()
                        seller = str(o.get("sellerName") or "").strip()
                        pix_val = parse_brl_price(o.get("price"))
                        if not seller or not pix_val:
                            continue
                        s_name, s_domain = _normalize_store(seller)
                        num_p = int(o.get("numParcels") or 0)
                        tot_p = parse_brl_price(o.get("totalParceledValue"))
                        if num_p <= 0 or not tot_p:
                            num_p = 10
                            tot_p = pix_val
                        has_int = bool(o.get("hasInterest", False)) and (tot_p > pix_val * 1.02)
                        is_mkt = bool(o.get("sellerIsMarketplace", False))
                        oid = o.get("id")
                        lead_url = (
                            f"https://www.zoom.com.br/lead?oid={oid}"
                            if oid
                            else prod_url
                        )
                        results.append(
                            {
                                "store_name": s_name,
                                "store_domain": s_domain,
                                "product_title": o_name,
                                "product_url": lead_url,
                                "price_pix": pix_val,
                                "price_installment_total": tot_p,
                                "installments_count": num_p,
                                "interest_free": not has_int,
                                "seller_name": f"{s_name} Oficial" if not is_mkt else s_name,
                                "seller_rating": 4.9 if not is_mkt else 4.7,
                                "is_official_store": not is_mkt,
                                "in_stock": int(o.get("stock", 1)) > 0,
                            }
                        )
                except Exception as exc:
                    logger.debug("Zoom product page expansion failed: %s", exc)
    except Exception as exc:
        logger.debug("Zoom live search failed: %s", exc)
    return results


async def _search_mercadolivre_live(query: str) -> list[dict[str, Any]]:
    """Query Mercado Livre public search page for live offers and seller badges."""
    results: list[dict[str, Any]] = []
    compact = _compact_search_query(query)
    search_url = f"https://lista.mercadolivre.com.br/{quote_plus(compact).replace('+', '-')}"
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=10.0
        ) as client:
            resp = await client.get(search_url)
            if resp.status_code != 200:
                return results
            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.select("li.ui-search-layout__item, div.poly-card")[:8]
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
                title_txt = title_el.get_text(" ", strip=True)
                if not _matches_query_tokens(title_txt, query):
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
                        "product_title": title_txt,
                        "product_url": link_el.get("href", search_url),
                        "price_pix": price_val,
                        "price_installment_total": price_val,
                        "installments_count": 10,
                        "interest_free": True,
                        "seller_name": seller_text or "MercadoLíder Platinum",
                        "seller_rating": 4.9 if is_official else 4.8,
                        "is_official_store": True,
                        "in_stock": True,
                    }
                )
    except Exception as exc:
        logger.debug("Mercado Livre live search failed: %s", exc)
    return results


async def _search_via_gemini_grounding(product: IdentifiedProduct) -> list[dict[str, Any]]:
    """Use Gemini + Google Search Grounding to find live Brazilian store offers."""
    if not settings.gemini_api_key:
        return []

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
        "Retorne EXCLUSIVAMENTE um JSON array válido com até 10 ofertas:\n"
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

    try:
        response = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
            ),
        )
    except Exception as exc:
        logger.warning("Gemini Search Grounding indisponível em search_trust (%s); usando scrapers ao vivo.", exc)
        return []

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
        store_name, store_domain = _normalize_store(str(item.get("store_name") or ""), url)

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
                "seller_name": str(item.get("seller_name") or store_name),
                "seller_rating": (
                    float(item["seller_rating"])
                    if item.get("seller_rating") is not None
                    else 4.8
                ),
                "is_official_store": bool(item.get("is_official_store", True)),
                "in_stock": bool(item.get("in_stock", True)),
            }
        )
    return cleaned


async def search_and_filter_offers(
    product: IdentifiedProduct,
) -> tuple[list[StoreOffer], int, list[str]]:
    """
    Pipeline 2: Search across Brazilian retailers (Zoom/Buscapé live Redux state, KaBuM! catalog,
    Mercado Livre, and Gemini Search Grounding) and apply strict Trust Score & anti-scam filters.
    """
    results_tuple = await asyncio.gather(
        _search_zoom_live(product.search_query),
        _search_kabum_live(product.search_query),
        _search_mercadolivre_live(product.search_query),
        _search_via_gemini_grounding(product),
        return_exceptions=True,
    )

    raw_offers: list[dict[str, Any]] = []
    for res in results_tuple:
        if isinstance(res, list):
            raw_offers.extend(res)

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

        dedup_key = f"{item['store_name'].lower()}::{round(item['price_pix'], 0)}"
        if dedup_key in seen_keys:
            continue

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

        seen_keys.add(dedup_key)
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
