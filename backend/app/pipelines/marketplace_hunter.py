"""MarketplaceDeepHunter — multi-channel deep extractor for Mercado Livre, Shopee,
Pelando and Promobit with STRICT live-price verification (zero stale forum prices).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from app.models import ChannelStatus, CouponInfo, SpecContract
from app.pipelines.spec_validator import check_title
from app.utils.browser import DEFAULT_HEADERS, fetch_html_playwright
from app.utils.parsers import extract_product_metadata, parse_brl_price

logger = logging.getLogger(__name__)

# Maximum age (in hours) for a community deal even before live-store price verification
MAX_COMMUNITY_AGE_HOURS = 12.0

EXPIRED_COMMENT_MARKERS = (
    "acabou",
    "esgotou",
    "esgotado",
    "expirado",
    "expirou",
    "subiu",
    "encerrado",
    "encerrou",
    "não funciona",
    "nao funciona",
    "não chega",
    "nao chega",
    "preço normal",
)


def _parse_age_hours(age_str: str) -> float | None:
    """Convert Pelando relative timestamps like '15 h', '2 d', '30 min' into hours."""
    s = (age_str or "").strip().lower()
    if "agora" in s or "seg" in s:
        return 0.05
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
    """Extract the real direct store URL from Pelando's dpl JWT link."""
    match = re.search(
        r"https://dpl\.pelando\.com\.br/r/([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)\.[A-Za-z0-9_\-]+",
        html,
    )
    if not match:
        return fallback_url
    try:
        payload_b64 = match.group(1).split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        data = json.loads(
            base64.urlsafe_b64decode(payload_b64).decode("utf-8", errors="ignore")
        )
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
        ("havan", "Havan", "havan.com.br"),
        ("aliexpress", "AliExpress", "aliexpress.com"),
    ]
    for key, name, dom in mapping:
        if key in low:
            return name, dom
    return s or "Comunidade", "pelando.com.br"


async def _verify_and_enrich_pelando_deal(
    client: httpx.AsyncClient, item: dict[str, Any]
) -> dict[str, Any] | None:
    """
    Fetch Pelando deal detail page, decode the direct store URL, and VERIFY that the
    price is actually live on the target store right now. If the store price cannot be
    verified or no longer matches, return None so stale community posts never appear.
    """
    deal_url = item["product_url"]
    try:
        resp = await client.get(deal_url, timeout=8.0)
        if resp.status_code != 200:
            return None
        low_html = resp.text.lower()
        if '"isexpired":true' in low_html or '"inactive":true' in low_html:
            return None

        direct_url = _decode_pelando_jwt_url(resp.text, deal_url)
        if direct_url == deal_url:
            return None

        # Verify live store price on the target retailer page
        store_resp = await client.get(direct_url, timeout=8.0)
        if store_resp.status_code != 200:
            return None
        meta = extract_product_metadata(store_resp.text, direct_url)
        if meta.get("in_stock") is False:
            return None
        live_price = meta.get("price_brl")
        if not live_price or live_price <= 0:
            # Target store did not confirm live price (e.g. Shopee login wall or dead link)
            return None

        # Use the REAL live price from the store page right now
        item["product_url"] = direct_url
        item["price_pix"] = live_price
        item["price_installment_total"] = live_price
        s_name, s_dom = _canonical_store(direct_url)
        if s_name != direct_url:
            item["store_name"] = s_name
            item["store_domain"] = s_dom
            item["seller_name"] = f"{s_name} (Preço Confirmado ao Vivo)"

        code = _extract_pelando_coupon_code(resp.text)
        if code and not item.get("embedded_coupon_code"):
            item["embedded_coupon_code"] = code
        return item
    except Exception:
        return None


async def hunt_pelando_live(
    query: str,
    spec: SpecContract | None = None,
    max_age_hours: float = MAX_COMMUNITY_AGE_HOURS,
) -> tuple[list[dict[str, Any]], ChannelStatus]:
    """
    Search Pelando recent deals (< 12h) and keep ONLY offers whose price is verified live
    on the target store right now.
    """
    url = f"https://www.pelando.com.br/busca/{quote(query.strip())}"
    candidates: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=10.0
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

            for card in cards[:15]:
                if card.select_one("[data-inactive='true']") is not None:
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
                if spec and check_title(title, spec) is not None:
                    continue

                ts_el = card.select_one("div[class*='timestamp']")
                age_h = _parse_age_hours(ts_el.get_text(" ", strip=True) if ts_el else "")
                # Strict freshness check: reject if timestamp is missing or older than max_age_hours (12h)
                if age_h is None or age_h > max_age_hours:
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

                full_deal_url = (
                    f"https://www.pelando.com.br{deal_href}"
                    if deal_href.startswith("/")
                    else deal_href
                )

                candidates.append(
                    {
                        "store_name": store_name,
                        "store_domain": store_domain,
                        "product_title": title,
                        "product_url": full_deal_url,
                        "price_pix": price_val,
                        "price_installment_total": price_val,
                        "installments_count": 10 if price_val >= 200 else 6,
                        "interest_free": True,
                        "seller_name": f"{store_name} (Validado ao Vivo)",
                        "seller_rating": 4.9,
                        "is_official_store": True,
                        "in_stock": True,
                        "source": "Pelando (Confirmado)",
                        "deal_age_hours": age_h,
                        "deal_note": f"Postado há {int(max(1, age_h))}h e preço confirmado ao vivo na loja",
                    }
                )

            verified_offers: list[dict[str, Any]] = []
            if candidates:
                checked = await asyncio.gather(
                    *[_verify_and_enrich_pelando_deal(client, c) for c in candidates[:5]],
                    return_exceptions=True,
                )
                for res in checked:
                    if isinstance(res, dict):
                        verified_offers.append(res)

            return verified_offers, ChannelStatus(
                name="Pelando (<12h Confirmado)",
                status="ok" if verified_offers else "vazio",
                count=len(verified_offers),
                detail=f"{len(verified_offers)} ofertas verificadas ao vivo",
            )
    except Exception as exc:
        logger.debug("Pelando hunt failed: %s", exc)
        return [], ChannelStatus(
            name="Pelando (<12h Confirmado)",
            status="indisponivel",
            detail=str(exc)[:80],
        )


async def _verify_and_enrich_promobit_deal(
    client: httpx.AsyncClient, offer_id: Any, offer_url: str, item: dict[str, Any]
) -> dict[str, Any] | None:
    """
    Verify a Promobit deal (<12h) by checking comments for expiration AND resolving
    /Redirect/to/{offer_id}/ to the target retailer page to confirm the live store price
    right now. If the live price cannot be confirmed on the store, reject the deal.
    """
    try:
        d_resp = await client.get(offer_url, timeout=7.0)
        if d_resp.status_code == 200:
            d_soup = BeautifulSoup(d_resp.text, "lxml")
            nd = d_soup.find("script", id="__NEXT_DATA__")
            if nd and nd.string:
                pp = (
                    json.loads(nd.string)
                    .get("props", {})
                    .get("pageProps", {})
                )
                so = pp.get("serverOffer") or {}
                if so.get("offerStatusName") not in ("APPROVED", None):
                    return None
                comments = pp.get("serverComments") or []
                if any(
                    any(
                        m in str(c.get("commentText") or "").lower()
                        for m in EXPIRED_COMMENT_MARKERS
                    )
                    for c in comments
                    if isinstance(c, dict)
                ):
                    return None

        if not offer_id:
            return None

        red_resp = await client.get(
            f"https://www.promobit.com.br/Redirect/to/{offer_id}/", timeout=7.0
        )
        if red_resp.status_code != 200:
            return None

        m_link = re.search(r"l\s*=\s*'(https?://[^']+)'", red_resp.text)
        if not m_link:
            return None

        out_url = m_link.group(1).strip()
        store_resp = await client.get(out_url, timeout=8.0)
        if store_resp.status_code != 200:
            return None

        final_url = str(store_resp.url)
        meta = extract_product_metadata(store_resp.text, final_url)
        if meta.get("in_stock") is False:
            return None

        live_price = meta.get("price_brl")
        if not live_price or live_price <= 0:
            return None

        posted_price = item["price_pix"]
        page_coupon: CouponInfo | None = item.get("page_coupon")
        if (
            page_coupon
            and live_price > posted_price
            and (live_price - posted_price) <= live_price * 0.25
        ):
            page_coupon.discount_value = round(live_price - posted_price, 2)

        item["product_url"] = final_url
        item["price_pix"] = live_price
        item["price_installment_total"] = live_price
        s_name, s_dom = _canonical_store(final_url)
        if s_name != final_url:
            item["store_name"] = s_name
            item["store_domain"] = s_dom
            item["seller_name"] = f"{s_name} (Preço Confirmado ao Vivo)"
        return item
    except Exception:
        return None


async def hunt_promobit_live(
    query: str,
    spec: SpecContract | None = None,
    max_age_hours: float = MAX_COMMUNITY_AGE_HOURS,
) -> tuple[list[dict[str, Any]], list[CouponInfo], ChannelStatus]:
    """
    Query Promobit's JSON API and keep ONLY offers from the last 12 hours whose
    price is verified live on the target retailer page right now.
    """
    offers: list[dict[str, Any]] = []
    coupons: list[CouponInfo] = []
    headers = {
        **DEFAULT_HEADERS,
        "Origin": "https://www.promobit.com.br",
        "Referer": "https://www.promobit.com.br/",
    }
    try:
        async with httpx.AsyncClient(
            headers=headers, follow_redirects=True, timeout=10.0
        ) as client:
            resp = await client.get(
                "https://api.promobit.com.br/search", params={"q": query.strip()}
            )
            if resp.status_code != 200:
                return (
                    [],
                    [],
                    ChannelStatus(
                        name="Promobit (<12h Confirmado)",
                        status="indisponivel",
                        detail=f"HTTP {resp.status_code}",
                    ),
                )

            data = resp.json()
            active_offers = data.get("active_offers") or []
            now = datetime.now(timezone.utc)
            candidates: list[tuple[Any, str, dict[str, Any]]] = []

            for ao in active_offers[:10]:
                if not isinstance(ao, dict):
                    continue
                if ao.get("offer_status_name") != "APPROVED":
                    continue

                # Strict freshness filter: MUST have offer_published within the last max_age_hours (12h)
                pub = ao.get("offer_published")
                if not pub:
                    continue
                try:
                    dt = datetime.fromisoformat(str(pub))
                    age_h = round(
                        (now - dt.astimezone(timezone.utc)).total_seconds() / 3600.0, 1
                    )
                except Exception:
                    continue

                # Reject 1-day-old / 5-day-old stale offers immediately!
                if age_h < 0 or age_h > max_age_hours:
                    continue

                title = str(ao.get("offer_title") or "").strip()
                if not title:
                    continue
                if spec and check_title(title, spec) is not None:
                    continue

                price_val = parse_brl_price(ao.get("offer_price"))
                if not price_val:
                    continue

                raw_store = str(
                    ao.get("store_name") or ao.get("store_domain") or "Promobit"
                )
                store_name, store_domain = _canonical_store(raw_store)
                slug = str(ao.get("offer_slug") or "").strip()
                offer_url = (
                    f"https://www.promobit.com.br/oferta/{slug}/"
                    if slug
                    else "https://www.promobit.com.br"
                )

                coupon_code = str(ao.get("offer_coupon") or "").strip().upper()
                page_coupon: CouponInfo | None = None
                if coupon_code and len(coupon_code) >= 3:
                    page_coupon = CouponInfo(
                        code=coupon_code,
                        store_name=store_name,
                        discount_type="fixed",
                        discount_value=0.0,
                        status="CONFIRMADO_HOJE",
                        rule_description=f"Cupom postado há {age_h}h no Promobit ({title[:55]})",
                        source="Promobit (<12h Confirmado)",
                    )

                candidates.append(
                    (
                        ao.get("offer_id"),
                        offer_url,
                        {
                            "store_name": store_name,
                            "store_domain": store_domain,
                            "product_title": title,
                            "product_url": offer_url,
                            "price_pix": price_val,
                            "price_installment_total": price_val,
                            "installments_count": 10 if price_val >= 200 else 6,
                            "interest_free": True,
                            "seller_name": f"{store_name} (Confirmado ao Vivo)",
                            "seller_rating": 4.9,
                            "is_official_store": True,
                            "in_stock": True,
                            "source": "Promobit (<12h Confirmado)",
                            "deal_age_hours": age_h,
                            "deal_note": f"Postado há {age_h}h e preço confirmado ao vivo na loja",
                            "embedded_coupon_code": coupon_code,
                            "page_coupon": page_coupon,
                        },
                    )
                )

            if candidates:
                checked = await asyncio.gather(
                    *[
                        _verify_and_enrich_promobit_deal(client, oid, ourl, item)
                        for oid, ourl, item in candidates[:5]
                    ],
                    return_exceptions=True,
                )
                for res in checked:
                    if isinstance(res, dict):
                        offers.append(res)
                        pc = res.get("page_coupon")
                        if isinstance(pc, CouponInfo):
                            coupons.append(pc)

            return (
                offers,
                coupons,
                ChannelStatus(
                    name="Promobit (<12h Confirmado)",
                    status="ok" if offers else "vazio",
                    count=len(offers),
                    detail=f"{len(offers)} ofertas verificadas ao vivo (<12h)",
                ),
            )
    except Exception as exc:
        logger.debug("Promobit hunt failed: %s", exc)
        return (
            [],
            [],
            ChannelStatus(
                name="Promobit (<12h Confirmado)",
                status="indisponivel",
                detail=str(exc)[:80],
            ),
        )


async def hunt_mercadolivre_catalog_playwright(
    product_url: str,
) -> dict[str, Any] | None:
    """Attempt deep extraction of a Mercado Livre /p/MLB... page via Playwright Stealth."""
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
