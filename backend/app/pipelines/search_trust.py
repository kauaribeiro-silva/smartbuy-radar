from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import uuid
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

from app.config import settings
from app.models import (
    BlockedGroup,
    ChannelStatus,
    CouponInfo,
    IdentifiedProduct,
    SpecContract,
    StoreOffer,
)
from app.pipelines.marketplace_hunter import hunt_pelando_live, hunt_promobit_live
from app.pipelines.spec_validator import (
    apply_price_floor,
    apply_spec_filter,
    heuristic_contract,
    semantic_double_check,
)
from app.utils.browser import DEFAULT_HEADERS
from app.utils.parsers import (
    evaluate_offer_trust,
    extract_json_from_llm,
    match_known_store,
    parse_brl_price,
)

logger = logging.getLogger(__name__)

STORE_DOMAIN_MAP: dict[str, str] = {
    "amazon": "amazon.com.br",
    "amazon br": "amazon.com.br",
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
    """
    Strip noisy marketing tokens ONLY when a specific alphanumeric model code (like 24G50F)
    is present. For general searches (like 'Monitor 27 polegadas 144hz' or 'PlayStation 5'),
    preserve size and refresh rate so stores return matching products.
    """
    cleaned = re.sub(r'["”″\']', " ", query)
    cleaned = re.sub(r"\bpolegadas\b", " ", cleaned, flags=re.IGNORECASE)
    has_model_code = bool(
        re.search(
            r"\b(?:[a-z]+\d+[a-z0-9]{2,}|\d+[a-z]+\d+[a-z0-9]*)\b",
            cleaned,
            flags=re.IGNORECASE,
        )
    )
    if has_model_code:
        noise = (
            r"\b(144hz|165hz|180hz|240hz|60hz|75hz|120hz|1ms|0[.,]5ms|5ms|hdr10|hdr|"
            r"g-sync|gsync|freesync|amd|nvidia|full\s*hd|fhd|1920x1080|hdmi|displayport|"
            r"nativo|mbr|wled|led|ips|va|tn|preto|branco)\b"
        )
        cleaned = re.sub(noise, " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b\d{2}(?:[.,]\d)?\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) >= 4 else query.strip()


def _expand_search_queries(product: IdentifiedProduct) -> list[str]:
    """
    Generate up to 2 complementary queries:
    1. Clean commercial query
    2. Short Brand + Model Code / SKU query (or 'PlayStation 5 Slim' synonym)
    """
    primary = _compact_search_query(product.search_query)
    queries = [primary]

    low = primary.lower()
    if ("playstation 5" in low or "ps5" in low) and "slim" not in low and "pro" not in low:
        queries.append("Console PlayStation 5 Slim")
        return queries

    codes = re.findall(
        r"\b(?:[a-z]+\d+[a-z0-9]{2,}|\d+[a-z]+\d+[a-z0-9]*)\b",
        f"{product.exact_model} {product.sku_part_number} {primary}",
        flags=re.IGNORECASE,
    )
    if codes:
        words = [
            w
            for w in primary.split()
            if len(w) >= 2 and w.lower() not in ("gamer", "para", "com")
        ]
        brand_prefix = " ".join(words[:2]) if len(words) >= 2 else (product.brand or words[0])
        short_q = f"{brand_prefix} {codes[-1]}".strip()
        if short_q.lower() != primary.lower() and len(short_q) >= 4:
            queries.append(short_q)

    return queries[:2]


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


def _decode_bing_aclick_url(href: str) -> str:
    """Decode Base64 target URL from Bing Shopping aclick links."""
    try:
        u_param = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u_param.startswith("a1"):
            u_param = u_param[2:]
        if u_param:
            pad = "=" * (-len(u_param) % 4)
            decoded = unquote(
                base64.urlsafe_b64decode(u_param + pad).decode("utf-8", errors="ignore")
            )
            if decoded.startswith("http") and "doubleclick.net" not in decoded:
                # Strip tracking query params on ML/Amazon for clean URLs
                if "mercadolivre.com.br" in decoded or "amazon.com.br" in decoded:
                    return decoded.split("?")[0]
                return decoded
    except Exception:
        pass
    return href


async def _search_bing_shopping_live(query: str) -> list[dict[str, Any]]:
    """
    Query Bing Shopping Brazil for real-time offers across Amazon Brasil, Mercado Livre,
    KaBuM!, Magazine Luiza, Fast Shop, Carrefour, and Casas Bahia.
    """
    results: list[dict[str, Any]] = []
    compact = _compact_search_query(query)
    queries_to_run = [compact]
    # Also build a concise brand + model_code query (e.g. "AOC 24G50F Preto") to surface primary ML/Amazon listings
    codes = re.findall(r"\b(?:[a-z]+\d+[a-z0-9]{2,}|\d+[a-z]+\d+[a-z0-9]*)\b", compact, flags=re.IGNORECASE)
    words = [w for w in compact.split() if len(w) >= 2 and w.lower() not in ("gamer", "para", "com")]
    if codes and len(words) >= 2:
        short_q = f"{words[0]} {words[1]} {codes[-1]} Preto"
        if short_q.lower() != compact.lower():
            queries_to_run.append(short_q)
    elif compact.lower() != query.strip().lower():
        queries_to_run.append(query.strip())

    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=10.0
        ) as client:
            responses = await asyncio.gather(
                *[
                    client.get(
                        f"https://www.bing.com/shop?q={quote_plus(q)}&cc=br&setlang=pt-br"
                    )
                    for q in queries_to_run[:2]
                ],
                return_exceptions=True,
            )

            for resp in responses:
                if not isinstance(resp, httpx.Response) or resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "lxml")
                for a_el in soup.select("a.br-offLink[href]"):
                    ttl_el = a_el.select_one("span[title]") or a_el.select_one(".br-offTtl, .br-title")
                    title = (
                        ttl_el.get("title") or ttl_el.get_text(" ", strip=True)
                        if ttl_el
                        else a_el.get_text(" ", strip=True)
                    )
                    if not title:
                        continue

                    price_el = a_el.select_one(".br-price, .br-offPrice, .pd-price")
                    pix = parse_brl_price(
                        price_el.get_text(" ", strip=True) if price_el else ""
                    )
                    if not pix:
                        continue

                    slr_el = a_el.select_one(".br-offSlrTxt, .br-seller, .br-offSlr")
                    raw_seller = slr_el.get_text(" ", strip=True) if slr_el else ""
                    direct_url = _decode_bing_aclick_url(a_el["href"])
                    store_name, store_domain = _normalize_store(raw_seller, direct_url)

                    # Only accept known trusted Brazilian retailers from Shopping feed
                    if store_domain == "zoom.com.br" and store_name not in STORE_DOMAIN_MAP.values():
                        if not any(k in raw_seller.lower() for k in STORE_DOMAIN_MAP):
                            continue

                    # Standard interest-free installments on Amazon BR / Mercado Livre / KaBuM!
                    inst_count = 10 if pix >= 200 else 6
                    results.append(
                        {
                            "store_name": store_name,
                            "store_domain": store_domain,
                            "product_title": title.strip(),
                            "product_url": direct_url,
                            "price_pix": pix,
                            "price_installment_total": pix,
                            "installments_count": inst_count,
                            "interest_free": True,
                            "seller_name": f"{store_name} Oficial",
                            "seller_rating": 4.9,
                            "is_official_store": True,
                            "in_stock": True,
                        }
                    )
    except Exception as exc:
        logger.debug("Bing Shopping live search failed: %s", exc)
    return results


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
                if not name:
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
                        "price_installment_total": max(pix, card),
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


async def _search_zoom_live(query: str, base_host: str = "https://www.zoom.com.br") -> list[dict[str, Any]]:
    """
    Query Zoom.com.br or Buscape.com.br __NEXT_DATA__ Redux state for real-time multi-store
    Brazilian offers, installment plans, and live cashback rates.
    """
    results: list[dict[str, Any]] = []
    compact = _compact_search_query(query)
    search_url = f"{base_host}/search?q={quote_plus(compact)}"
    portal_label = "Buscapé" if "buscape" in base_host else "Zoom"
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
            merchant_cb_rates = (redux.get("merchantCashbacks") or {}).get("rates") or {}
            if not isinstance(hits, list):
                return results

            product_page_urls: list[str] = []

            for h in hits[:12]:
                if not isinstance(h, dict):
                    continue
                name = str(h.get("name") or "").strip()
                if not name:
                    continue

                hit_type = h.get("type")
                raw_url = str(h.get("url") or "")
                full_url = (
                    f"{base_host}{raw_url}"
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

                cb_rate = float(
                    ((h.get("cashback") or {}).get("formula") or {}).get("cashbackRate")
                    or 0.0
                )
                if cb_rate <= 0:
                    m_id = str(h.get("merchantId") or (h.get("bestOffer") or {}).get("merchantId") or "")
                    cb_rate = float(merchant_cb_rates.get(m_id) or 0.0)
                live_cb_pct = round(cb_rate * 100.0, 2) if 0 < cb_rate < 1 else round(cb_rate, 2)

                inst = h.get("installments") or {}
                inst_months = int(inst.get("amount_months") or 10)
                inst_total = parse_brl_price(inst.get("total_value")) or pix
                if abs(inst_total - pix) < 1.0:
                    inst_total = pix
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
                        "source": portal_label,
                        "live_cashback_pct": live_cb_pct,
                    }
                )

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
                        elif abs(tot_p - pix_val) < 1.0:
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


async def search_and_filter_offers_v2(
    product: IdentifiedProduct,
    spec: SpecContract | None = None,
) -> dict[str, Any]:
    """
    Pipeline 2 (v2.0): Multi-channel deep search (KaBuM!, Zoom/Buscapé, Bing Shopping BR,
    Pelando 72h, Promobit API, Gemini Grounding) + StrictSpecValidator (Travas A, B, C).
    """
    active_spec = spec or heuristic_contract(product.search_query, product.exact_model)
    expanded_queries = _expand_search_queries(product)
    primary_q = expanded_queries[0]
    secondary_q = expanded_queries[1] if len(expanded_queries) > 1 else None

    kabum_task = _search_kabum_live(primary_q)
    zoom_task = _search_zoom_live(primary_q, base_host="https://www.zoom.com.br")
    buscape_task = _search_zoom_live(
        secondary_q or primary_q, base_host="https://www.buscape.com.br"
    )
    bing_task = _search_bing_shopping_live(primary_q)
    pelando_task = hunt_pelando_live(primary_q, spec=active_spec)
    promobit_task = hunt_promobit_live(primary_q, spec=active_spec)
    gemini_task = _search_via_gemini_grounding(product)

    (
        kabum_res,
        zoom_res,
        buscape_res,
        bing_res,
        pelando_res,
        promobit_res,
        gemini_res,
    ) = await asyncio.gather(
        kabum_task,
        zoom_task,
        buscape_task,
        bing_task,
        pelando_task,
        promobit_task,
        gemini_task,
        return_exceptions=True,
    )

    raw_offers: list[dict[str, Any]] = []
    channels: list[ChannelStatus] = []
    community_coupons: list[CouponInfo] = []

    if isinstance(kabum_res, list):
        for o in kabum_res:
            o.setdefault("source", "KaBuM! Direto")
        raw_offers.extend(kabum_res)
        channels.append(
            ChannelStatus(
                name="KaBuM! Catálogo",
                status="ok" if kabum_res else "vazio",
                count=len(kabum_res),
            )
        )
    if isinstance(zoom_res, list):
        for o in zoom_res:
            o.setdefault("source", "Zoom")
        raw_offers.extend(zoom_res)
        channels.append(
            ChannelStatus(
                name="Zoom",
                status="ok" if zoom_res else "vazio",
                count=len(zoom_res),
            )
        )
    if isinstance(buscape_res, list):
        for o in buscape_res:
            o.setdefault("source", "Buscapé")
        raw_offers.extend(buscape_res)
        channels.append(
            ChannelStatus(
                name="Buscapé",
                status="ok" if buscape_res else "vazio",
                count=len(buscape_res),
            )
        )
    if isinstance(bing_res, list):
        for o in bing_res:
            o.setdefault("source", "Shopping SERP (Amazon/ML)")
        raw_offers.extend(bing_res)
        channels.append(
            ChannelStatus(
                name="Shopping SERP (Amazon/ML)",
                status="ok" if bing_res else "vazio",
                count=len(bing_res),
            )
        )
    if isinstance(pelando_res, tuple):
        pel_offers, pel_status = pelando_res
        raw_offers.extend(pel_offers)
        channels.append(pel_status)
    if isinstance(promobit_res, tuple):
        pro_offers, pro_coupons, pro_status = promobit_res
        raw_offers.extend(pro_offers)
        community_coupons.extend(pro_coupons)
        channels.append(pro_status)
    if isinstance(gemini_res, list) and gemini_res:
        for o in gemini_res:
            o.setdefault("source", "Gemini Grounding")
        raw_offers.extend(gemini_res)
        channels.append(
            ChannelStatus(name="Gemini Grounding", status="ok", count=len(gemini_res))
        )

    # ── TRAVA A: Spec Contract Filter (mandatory & forbidden tokens) ──────────
    spec_kept, blocked_spec_groups = apply_spec_filter(raw_offers, active_spec)

    # ── TRAVA B: Outlier Price Floor (< 55% of exact-model median) ────────────
    floor_kept, blocked_floor_groups, price_floor_brl = apply_price_floor(
        spec_kept, reference_price=product.reference_price_brl
    )

    blocked_groups: list[BlockedGroup] = blocked_spec_groups + blocked_floor_groups
    rejected_reasons: list[str] = [
        f"{g.label}: {g.count} anúncio(s) bloqueado(s)" for g in blocked_groups
    ]

    ref_price = product.reference_price_brl
    if not ref_price and floor_kept:
        prices = sorted(o["price_pix"] for o in floor_kept if o.get("price_pix"))
        if prices:
            ref_price = prices[len(prices) // 2]

    trusted_offers: list[StoreOffer] = []
    seen_keys: set[str] = set()

    for item in floor_kept:
        if not item.get("in_stock", True):
            rejected_reasons.append(f"{item['store_name']}: Produto sem estoque.")
            continue

        dedup_key = f"{item['store_name'].lower()}::{round(item['price_pix'], 0)}"
        if dedup_key in seen_keys:
            continue

        passed, score, reason = evaluate_offer_trust(
            product_title=item["product_title"],
            target_query=active_spec.canonical_name,
            store_name=item["store_name"],
            seller_name=item.get("seller_name", ""),
            seller_rating=item.get("seller_rating"),
            is_official_store=item.get("is_official_store", False),
            price_pix=item["price_pix"],
            reference_price=ref_price,
        )

        if not passed:
            rejected_reasons.append(
                f"{item['store_name']} ({item.get('seller_name') or '3P'}): {reason}"
            )
            continue

        seen_keys.add(dedup_key)
        inst_count = max(1, int(item.get("installments_count") or 10))
        inst_total = float(item.get("price_installment_total") or item["price_pix"])
        if abs(inst_total - float(item["price_pix"])) < 1.0:
            inst_total = float(item["price_pix"])
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
                live_cashback_pct=float(item.get("live_cashback_pct") or 0.0),
                source=str(item.get("source") or ""),
                deal_age_hours=item.get("deal_age_hours"),
                deal_note=str(item.get("deal_note") or ""),
                embedded_coupon_code=str(item.get("embedded_coupon_code") or ""),
                page_coupon=item.get("page_coupon"),
            )
        )

    # ── TRAVA C: Semantic Double-Check on Top 10 Cheapest ────────────────────
    checked_offers, sem_groups, sem_msg = await semantic_double_check(
        active_spec, trusted_offers
    )
    if sem_groups:
        blocked_groups.extend(sem_groups)
        for g in sem_groups:
            rejected_reasons.append(f"{g.label}: {g.count} item(ns)")

    checked_offers.sort(key=lambda o: (o.price_pix, -o.installments_count))
    total_blocked = sum(g.count for g in blocked_groups)

    return {
        "offers": checked_offers,
        "rejected_count": max(total_blocked, len(rejected_reasons)),
        "rejected_reasons": rejected_reasons,
        "blocked_groups": blocked_groups,
        "channels": channels,
        "price_floor_brl": price_floor_brl,
        "semantic_check": sem_msg,
        "community_coupons": community_coupons,
    }


async def search_and_filter_offers(
    product: IdentifiedProduct,
) -> tuple[list[StoreOffer], int, list[str]]:
    """Backward-compatible wrapper returning (trusted_offers, rejected_count, rejected_reasons)."""
    res = await search_and_filter_offers_v2(product)
    return res["offers"], res["rejected_count"], res["rejected_reasons"]
