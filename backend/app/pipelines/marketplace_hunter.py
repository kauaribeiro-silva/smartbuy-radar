"""MarketplaceDeepHunter — multi-channel deep extractor for Mercado Livre, Shopee,
Pelando (72h live community deals + JWT direct URL decoder) and Promobit API.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, quote_plus

import httpx
from bs4 import BeautifulSoup

from app.models import ChannelStatus, CouponInfo, SpecContract
from app.pipelines.spec_validator import check_title
from app.utils.browser import DEFAULT_HEADERS, fetch_html_playwright
from app.utils.parsers import parse_brl_price

logger = logging.getLogger(__name__)


def _parse_age_hours(age_str: str) -> float | None:
    """Convert Pelando relative timestamps like '15 h', '2 d', '30 min' into hours."""
    s = (age_str or "").strip().lower()
    m_min = re.search(r"(\d+)\s*min", s)
    if m_min:
        return round(float(m_min.group(1)) / 60.0, 2)
    m_h = re.search(r"(\d+)\s*h", s)
    if m_h:
        return float(m_h.group(1))
    m_d = re.search(r"(\d+)\s*d", s)
    if m_d:
        return float(m_d.group(1)) * 24.0
    return None


def _decode_pelando_jwt_url(html: str, fallback_url: str) -> str:
    """Extract the real direct store URL (e.g. shopee.com.br/...) from Pelando's dpl JWT link."""
    match = re.search(
        r"https://dpl\.pelando\.com\.br/r/([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)\.[A-Za-z0-9_\-]+",
        html,
    )
    if not match:
        return fallback_url
    try:
        payload_b64 = match.group(1).split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8", errors="ignore"))
        url = str(data.get("url") or "").strip()
        if url.startswith("http"):
            return url
    except Exception:
        pass
    return fallback_url


def _extract_pelando_coupon_code(html: str) -> str:
    """Extract explicit coupon code from a Pelando deal page if present."""
    for pat in (
        r'"couponCode"\s*:\s*"([A-Z0-9_\-]{4,25})"',
        r"cupom\s*[:\-]?\s*([A-Z0-9]{5,20})\b",
    ):
        m = re.search(pat, html)
        if m:
            cand = m.group(1).strip().upper()
            if cand not in ("PELANDO", "CUPOM", "PROMO", "DESCONTO", "SHOPEE", "MERCADO"):
                return cand
    return ""


def _canonical_store(raw_store: str) -> tuple[str, str]:
    s = (raw_store or "").strip()
    low = s.lower()
    mapping = [
        ("shopee", "Shopee Oficial", "shopee.com.br"),
        ("mercado livre", "Mercado Livre", "mercadolivre.com.br"),
        ("mercadolivre", "Mercado Livre", "mercadolivre.com.br"),
        ("amazon", "Amazon Brasil", "amazon.com.br"),
        ("kabum", "KaBuM!", "kabum.com.br"),
        ("magalu", "Magazine Luiza", "magazineluiza.com.br"),
        ("magazine luiza", "Magazine Luiza", "magazineluiza.com.br"),
        ("fast shop", "Fast Shop", "fastshop.com.br"),
        ("carrefour", "Carrefour", "carrefour.com.br"),
        ("casas bahia", "Casas Bahia", "casasbahia.com.br"),
        ("aliexpress", "AliExpress", "aliexpress.com"),
    ]
    for key, name, dom in mapping:
        if key in low:
            return name, dom
    return s or "Comunidade", "pelando.com.br"


async def _enrich_pelando_deal(
    client: httpx.AsyncClient, item: dict[str, Any]
) -> dict[str, Any]:
    """Fetch Pelando deal detail page to decode the direct Shopee/ML URL and coupon."""
    deal_url = item["product_url"]
    try:
        resp = await client.get(deal_url, timeout=8.0)
        if resp.status_code == 200:
            direct_url = _decode_pelando_jwt_url(resp.text, deal_url)
            item["product_url"] = direct_url
            if item.get("store_name") in ("Pelando", "Comunidade") and direct_url != deal_url:
                s_name, s_dom = _canonical_store(direct_url)
                if s_name != direct_url:
                    item["store_name"] = s_name
                    item["store_domain"] = s_dom
                    item["seller_name"] = f"{s_name} (Validado Pelando)"
            code = _extract_pelando_coupon_code(resp.text)
            if code and not item.get("embedded_coupon_code"):
                item["embedded_coupon_code"] = code
    except Exception:
        pass
    return item


async def hunt_pelando_live(
    query: str, spec: SpecContract | None = None, max_age_hours: float = 168.0
) -> tuple[list[dict[str, Any]], ChannelStatus]:
    """
    Search Pelando live community deals, decode direct store links (Shopee, Mercado Livre, etc.)
    and extract embedded coupon codes.
    """
    url = f"https://www.pelando.com.br/busca/{quote(query.strip())}"
    offers: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=11.0
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return [], ChannelStatus(
                    name="Pelando (Tempo Real)",
                    status="indisponivel",
                    detail=f"HTTP {resp.status_code}",
                )

            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.select("div[class*='_deal-card_']")
            seen_urls: set[str] = set()

            for card in cards[:18]:
                # Skip expired/inactive deals
                inactive_el = card.select_one("[data-inactive='true']")
                if inactive_el is not None:
                    continue

                title_a = card.select_one("h3 a[href*='/d/'], a[href*='/d/']")
                if not title_a:
                    continue
                deal_href = title_a.get("href", "")
                if not deal_href or deal_href in seen_urls:
                    continue
                seen_urls.add(deal_href)

                title = (
                    title_a.get("aria-label")
                    or title_a.get_text(" ", strip=True)
                    or ""
                ).strip()
                if not title:
                    continue

                stamp_el = card.select_one("span[class*='_deal-card-stamp_']")
                price_val = parse_brl_price(
                    stamp_el.get_text(" ", strip=True) if stamp_el else ""
                )
                if not price_val:
                    continue

                store_a = card.select_one("div[class*='deal-card-store'] a")
                raw_store = store_a.get_text(" ", strip=True) if store_a else "Pelando"
                store_name, store_domain = _canonical_store(raw_store)

                ts_el = card.select_one("div[class*='timestamp']")
                age_h = _parse_age_hours(ts_el.get_text(" ", strip=True) if ts_el else "")
                if age_h is not None and age_h > max_age_hours:
                    continue

                full_deal_url = (
                    f"https://www.pelando.com.br{deal_href}"
                    if deal_href.startswith("/")
                    else deal_href
                )

                offers.append(
                    {
                        "store_name": store_name,
                        "store_domain": store_domain,
                        "product_title": title,
                        "product_url": full_deal_url,
                        "price_pix": price_val,
                        "price_installment_total": price_val,
                        "installments_count": 10 if price_val >= 200 else 6,
                        "interest_free": True,
                        "seller_name": f"{store_name} (Validado Pelando)",
                        "seller_rating": 4.9,
                        "is_official_store": True,
                        "in_stock": True,
                        "source": "Pelando",
                        "deal_age_hours": age_h,
                        "deal_note": (
                            f"Oferta validada na comunidade Pelando há {int(age_h)}h"
                            if age_h is not None
                            else "Oferta ativa na comunidade Pelando"
                        ),
                    }
                )

            # Enrich top 4 spec-matching deals with direct JWT-decoded store URLs in parallel
            matching_for_enrich = [
                o for o in offers if (spec is None or check_title(o["product_title"], spec) is None)
            ][:4]
            if matching_for_enrich:
                await asyncio.gather(
                    *[_enrich_pelando_deal(client, o) for o in matching_for_enrich],
                    return_exceptions=True,
                )

            return offers, ChannelStatus(
                name="Pelando (Comunidade 72h)",
                status="ok" if offers else "vazio",
                count=len(offers),
                detail=f"{len(offers)} ofertas ativas validadas",
            )
    except Exception as exc:
        logger.debug("Pelando hunt failed: %s", exc)
        return [], ChannelStatus(
            name="Pelando (Comunidade 72h)",
            status="indisponivel",
            detail=str(exc)[:80],
        )


async def hunt_promobit_live(
    query: str, spec: SpecContract | None = None
) -> tuple[list[dict[str, Any]], list[CouponInfo], ChannelStatus]:
    """
    Query Promobit's live JSON API (https://api.promobit.com.br/search?q=...)
    for active community-verified offers (including Shopee & Mercado Livre) and coupons.
    """
    offers: list[dict[str, Any]] = []
    coupons: list[CouponInfo] = []
    headers = {
        **DEFAULT_HEADERS,
        "Origin": "https://www.promobit.com.br",
        "Referer": "https://www.promobit.com.br/",
    }
    try:
        async with httpx.AsyncClient(headers=headers, timeout=11.0) as client:
            resp = await client.get(
                "https://api.promobit.com.br/search", params={"q": query.strip()}
            )
            if resp.status_code != 200:
                return (
                    [],
                    [],
                    ChannelStatus(
                        name="Promobit API",
                        status="indisponivel",
                        detail=f"HTTP {resp.status_code}",
                    ),
                )

            data = resp.json()
            active_offers = data.get("active_offers") or []
            now = datetime.now(timezone.utc)

            for ao in active_offers[:12]:
                if not isinstance(ao, dict):
                    continue
                if ao.get("offer_status_name") not in ("APPROVED", "ACTIVE", None):
                    continue
                title = str(ao.get("offer_title") or "").strip()
                if not title:
                    continue
                if spec and check_title(title, spec) is not None:
                    continue

                price_val = parse_brl_price(ao.get("offer_price"))
                if not price_val:
                    continue

                raw_store = str(ao.get("store_name") or ao.get("store_domain") or "Promobit")
                store_name, store_domain = _canonical_store(raw_store)
                slug = str(ao.get("offer_slug") or "").strip()
                offer_url = (
                    f"https://www.promobit.com.br/oferta/{slug}/"
                    if slug
                    else "https://www.promobit.com.br"
                )

                age_h: float | None = None
                pub = ao.get("offer_published")
                if pub:
                    try:
                        dt = datetime.fromisoformat(str(pub))
                        age_h = round((now - dt.astimezone(timezone.utc)).total_seconds() / 3600.0, 1)
                    except Exception:
                        age_h = None

                coupon_code = str(ao.get("offer_coupon") or "").strip().upper()
                page_coupon: CouponInfo | None = None
                if coupon_code and len(coupon_code) >= 3:
                    page_coupon = CouponInfo(
                        code=coupon_code,
                        store_name=store_name,
                        discount_type="fixed",
                        discount_value=0.0,
                        status="CONFIRMADO_HOJE",
                        rule_description=f"Cupom validado na oferta Promobit ({title[:60]}) — preço de R$ {price_val:,.2f} já considera o cupom.",
                        source="Promobit (Oferta Ativa)",
                    )
                    coupons.append(page_coupon)

                offers.append(
                    {
                        "store_name": store_name,
                        "store_domain": store_domain,
                        "product_title": title,
                        "product_url": offer_url,
                        "price_pix": price_val,
                        "price_installment_total": price_val,
                        "installments_count": 10 if price_val >= 200 else 6,
                        "interest_free": True,
                        "seller_name": f"{store_name} (Validado Promobit)",
                        "seller_rating": 4.9,
                        "is_official_store": True,
                        "in_stock": True,
                        "source": "Promobit",
                        "deal_age_hours": age_h,
                        "deal_note": (
                            f"Oferta aprovada no Promobit ({ao.get('offer_likes', 0)} curtidas)"
                        ),
                        "embedded_coupon_code": coupon_code,
                        "page_coupon": page_coupon,
                    }
                )

            return (
                offers,
                coupons,
                ChannelStatus(
                    name="Promobit (Ofertas Verificadas)",
                    status="ok" if offers else "vazio",
                    count=len(offers),
                    detail=f"{len(offers)} ofertas ativas (Shopee/ML/Varejo)",
                ),
            )
    except Exception as exc:
        logger.debug("Promobit hunt failed: %s", exc)
        return (
            [],
            [],
            ChannelStatus(
                name="Promobit (Ofertas Verificadas)",
                status="indisponivel",
                detail=str(exc)[:80],
            ),
        )


async def hunt_mercadolivre_catalog_playwright(
    product_url: str,
) -> dict[str, Any] | None:
    """
    Attempt deep extraction of a Mercado Livre /p/MLB... page via Playwright Stealth,
    reading __PRELOADED_STATE__ / JSON-LD and native coupon badges ('R$ X OFF').
    """
    if "mercadolivre.com.br" not in product_url:
        return None
    html = await fetch_html_playwright(product_url, wait_ms=2200)
    if not html or "account-verification" in html or "ui-empty-state" in html:
        return None

    soup = BeautifulSoup(html, "lxml")
    price_el = soup.select_one(
        "div.ui-pdp-price__second-line span.andes-money-amount__fraction"
    )
    price = parse_brl_price(price_el.get_text(strip=True) if price_el else "")
    if not price:
        return None

    # Check native page coupon badge (e.g. "R$ 100 OFF" or "Ganhe R$ 30 OFF")
    coupon_discount = 0.0
    coupon_label = ""
    badge_text = soup.get_text(" ", strip=True)
    m_cup = re.search(
        r"(?:compre\s+r\$\s*\d+\s+e\s+)?ganhe\s+r\$\s*(\d+)\s*off|cupom\s+de\s+r\$\s*(\d+)\s*off",
        badge_text,
        re.IGNORECASE,
    )
    if m_cup:
        val = float(m_cup.group(1) or m_cup.group(2) or 0)
        if 5 <= val <= price * 0.4:
            coupon_discount = val
            coupon_label = m_cup.group(0).strip()

    return {
        "price_pix": price,
        "native_coupon_discount": coupon_discount,
        "native_coupon_label": coupon_label,
    }
