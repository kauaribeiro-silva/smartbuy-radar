from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

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

UNRELATED_NICHE_KEYWORDS = (
    "auto partes",
    "autopeças",
    "veicpromo",
    "construção",
    "indústria",
    "snacks",
    "supermercado",
    "fralda",
    "baby",
    "bebê",
    "beleza",
    "perfume",
    "promosaude",
    "cuidados e saúde",
    "moda",
    "vestuário",
    "calçado",
    "pneu",
    "ração",
    "pet ",
    "pets",
    "malaprime",
    "para sua casa",
    "casatech",
    "casaninja",
    "gta vi",
    "vemgta",
)


def _is_stale_dated_coupon(code: str) -> bool:
    """
    Detect single-day promotional coupons with embedded DDMM dates (e.g. CUP0N0M1A1111PB
    for 11/11, CUP0N0M1A2810PB for 28/10, CUP0N0M1A1010PB for 10/10, CUP0N0M1A2309PB for 23/09).
    If a valid DDMM date pattern is embedded and does not match today's DDMM, reject it.
    """
    today_ddmm = datetime.now().strftime("%d%m")
    for m in re.finditer(r"(\d{4})", code):
        digits = m.group(1)
        if digits in ("1000", "2000", "2024", "2025", "2026", today_ddmm):
            continue
        dd = int(digits[:2])
        mm = int(digits[2:])
        if 1 <= dd <= 31 and 1 <= mm <= 12:
            return True
    return False

CUPONOMIA_SLUGS: dict[str, str] = {
    "Mercado Livre": "mercado-livre",
    "Amazon Brasil": "amazon",
    "KaBuM!": "kabum",
    "Magazine Luiza": "magazine-luiza",
    "Fast Shop": "fast-shop",
    "Carrefour": "carrefour",
    "Casas Bahia": "casas-bahia",
    "Shopee Oficial": "shopee",
}


async def _scrape_cuponomia_store(
    store_name: str, product: IdentifiedProduct
) -> list[CouponInfo]:
    """Fetch live verified coupon codes from Cuponomia for a given Brazilian store."""
    slug = CUPONOMIA_SLUGS.get(store_name)
    if not slug:
        return []

    url = f"https://www.cuponomia.com.br/desconto/{slug}"
    coupons: list[CouponInfo] = []
    prod_lower = f"{product.exact_model} {product.brand} {product.category}".lower()

    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=8.0
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return coupons
            soup = BeautifulSoup(resp.text, "lxml")

            scored_coupons: list[tuple[int, CouponInfo]] = []
            prod_tokens = [
                t for t in re.split(r"[^a-z0-9]+", prod_lower) if len(t) >= 4
            ]

            for li in soup.select("li.item-code, li.item-promo")[:14]:
                li_text_lower = li.get_text(" ", strip=True).lower()
                if "verificado" not in li_text_lower:
                    continue

                code_el = li.select_one(".item-code-link span, .coupon-code, button span")
                raw_code = code_el.get_text(strip=True) if code_el else ""
                code = re.sub(r"^Ver\s*Cupom\s*", "", raw_code, flags=re.IGNORECASE).strip().upper()
                if not code or len(code) < 4 or " " in code or "ATIVE" in code or "ATIVAR" in code:
                    continue
                if _is_stale_dated_coupon(code):
                    continue

                title_el = li.select_one("h3, .item-title")
                desc_el = li.select_one("p, .item-desc")
                title_txt = title_el.get_text(" ", strip=True) if title_el else ""
                desc_txt = desc_el.get_text(" ", strip=True) if desc_el else ""
                combined = f"{title_txt} — {desc_txt}".strip(" —")
                comb_lower = combined.lower()
                full_check = f"{code.lower()} {comb_lower}"

                if any(m in full_check for m in DEAD_COUPON_MARKERS):
                    continue
                if any(k in full_check for k in UNRELATED_NICHE_KEYWORDS):
                    continue
                # Brand-specific coupon check (e.g. ACERNOKABUM or JBL30OFF when product is AOC)
                specific_brands = (
                    "acer", "jbl", "samsung", "dell", "asus", "lenovo",
                    "lg", "philips", "apple", "motorola", "xiaomi", "playstation", "nintendo",
                )
                if any(
                    b in full_check and b not in prod_lower
                    for b in specific_brands
                ):
                    continue

                # Category/product-type mismatch check
                category_specific_words = (
                    "impressora", "ar-condicionado", "geladeira", "fogão", "lavadora",
                    "colchão", "cadeira", "mochila", "mala", "ventilador", "air fryer",
                    "monitor", "notebook", "smartphone", "celular", "console", "placa de vídeo",
                )
                if any(
                    w in full_check and w not in prod_lower
                    for w in category_specific_words
                ):
                    continue

                # Parse discount percent or fixed BRL
                pct_match = re.search(r"(\d{1,2})\s*%\s*(?:off|de\s*desconto|de\s*economia|em\s*suas)", comb_lower)
                brl_match = re.search(r"r\$\s*(\d{1,4})(?:,\d{2})?\s*(?:off|de\s*desconto)", comb_lower)
                min_match = re.search(r"acima\s+de\s+r\$\s*(\d{1,5})", comb_lower)
                max_match = re.search(r"(?:limitado\s+a|máximo\s+de)\s+r\$\s*(\d{1,5})", comb_lower)

                if pct_match:
                    dtype = "percent"
                    dval = float(pct_match.group(1))
                    min_spend = float(min_match.group(1)) if min_match else None
                elif brl_match:
                    dtype = "fixed"
                    dval = float(brl_match.group(1))
                    # Fixed BRL coupons in Brazil require at least 5x the coupon value as minimum spend
                    parsed_min = float(min_match.group(1)) if min_match else 0.0
                    min_spend = max(parsed_min, dval * 5.0)
                else:
                    continue

                max_disc = float(max_match.group(1)) if max_match else None
                relevance = sum(25 for tok in prod_tokens if tok in full_check)

                scored_coupons.append(
                    (
                        relevance,
                        CouponInfo(
                            code=code,
                            store_name=store_name,
                            discount_type=dtype,  # type: ignore[arg-type]
                            discount_value=dval,
                            max_discount_brl=max_disc,
                            min_spend_brl=min_spend,
                            status="TESTAR_NO_CHECKOUT",
                            rule_description=combined[:160],
                            source="Cuponomia (Verificado hoje)",
                        ),
                    )
                )

            scored_coupons.sort(key=lambda pair: pair[0], reverse=True)
            coupons = [c for _, c in scored_coupons]
    except Exception as exc:
        logger.debug("Cuponomia scrape skipped for %s: %s", store_name, exc)

    return coupons[:3]


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
    Pipeline 3: Hunt and validate live, active coupons for the target stores.
    Combines direct Cuponomia live scraping with Gemini validation.
    """
    if not offers:
        return {}

    unique_stores = list(dict.fromkeys(o.store_name for o in offers))
    coupons_by_store: dict[str, list[CouponInfo]] = {s: [] for s in unique_stores}

    scraped_lists = await asyncio.gather(
        *[_scrape_cuponomia_store(s, product) for s in unique_stores],
        return_exceptions=True,
    )
    for idx, res in enumerate(scraped_lists):
        if isinstance(res, list) and res:
            coupons_by_store[unique_stores[idx]].extend(res)

    return coupons_by_store
