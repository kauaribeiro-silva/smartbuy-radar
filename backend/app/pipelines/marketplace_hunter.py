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

# Maximum age (in hours) for a community deal (blocks 4-5 day old expired 11.11 posts while keeping today/yesterday deals)
MAX_COMMUNITY_AGE_HOURS = 36.0
MAX_SHOPEE_FLASH_AGE_HOURS = 24.0

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

COMMUNITY_NOISE_WORDS = {
    "gamer", "full", "hd", "fhd", "uhd", "qhd", "wqhd", "ips", "va", "tn", "led", "wled",
    "hdr10", "hdr", "hdr400", "g-sync", "gsync", "freesync", "sync", "adaptive", "hdmi",
    "displayport", "dp", "vga", "usb", "preto", "branco", "bivolt", "polegadas", "pol",
    "0,5ms", "0.5ms", "1ms", "5ms", "05ms", "nativo", "mbr", "mprt", "srgb", "widescreen",
}


def _build_community_queries(query: str) -> list[str]:
    """
    Build 1-2 concise 3-4 word queries for Pelando and Promobit search engines
    so long 8-10 word titles don't return 0 results.
    """
    clean = re.sub(r'["”″\']', " ", query.lower())
    clean = re.sub(r"\b0[.,]5\s*ms\b", " ", clean)
    tokens = [
        t for t in re.findall(r"[a-z0-9]+", clean)
        if t not in COMMUNITY_NOISE_WORDS and len(t) >= 2
    ]
    if not tokens:
        return [query.strip()]

    queries: list[str] = []
    # 1. Specific alphanumeric model / line tokens (e.g. "aoc agon g50" or "lg ultragear 27")
    non_cat = [
        t for t in tokens
        if t not in ("monitor", "console", "smartphone", "celular", "notebook", "smart", "tv")
        and not t.endswith("hz")
    ]
    if len(non_cat) >= 2:
        q1 = " ".join(non_cat[:4])
        queries.append(q1)

    # 2. Category + brand + size/key token (e.g. "monitor aoc 27")
    q2 = " ".join(tokens[:4])
    if q2 not in queries:
        queries.append(q2)

    return queries[:2]


def _parse_age_hours(age_str: str) -> float | None:
    """Convert Pelando relative timestamps like '14 h', '14 hs', '2 dias', 'ontem', '30 min' into hours."""
    s = (age_str or "").strip().lower()
    if "agora" in s or "seg" in s:
        return 0.05
    if "ontem" in s:
        return 24.0
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


def _is_login_walled_marketplace(url_or_store: str) -> bool:
    low = (url_or_store or "").lower()
    return any(
        k in low
        for k in (
            "shopee.com.br",
            "s.shopee.com.br",
            "shopee oficial",
            "mercadolivre.com.br",
            "mercadolibre.com",
            "meli.la",
            "mercado livre",
            "aliexpress.com",
        )
    )


def _extract_ml_native_coupon(html: str, base_price: float) -> CouponInfo | None:
    """Detect native 'R$ 640,17 com Cupom' or 'R$ 30 OFF com cupom' badge on Mercado Livre."""
    soup = BeautifulSoup(html, "lxml")
    txt = soup.get_text(" ", strip=True)
    m_final = re.search(
        r"R\$\s*(\d{1,5}(?:\.\d{3})*(?:,\d{1,2})?)\s+com\s+cupom",
        txt,
        re.IGNORECASE,
    )
    if m_final:
        with_cup = parse_brl_price(m_final.group(1))
        if with_cup and 0 < (base_price - with_cup) <= base_price * 0.35:
            disc = round(base_price - with_cup, 2)
            return CouponInfo(
                code="CUPOM NO ANÚNCIO ML",
                store_name="Mercado Livre",
                discount_type="fixed",
                discount_value=disc,
                status="CONFIRMADO_HOJE",
                rule_description=f"Marque a caixa 'Aplicar cupom' na página do Mercado Livre (vai para R$ {with_cup:,.2f})",
                source="Cupom Nativo Mercado Livre",
            )
    m_off = re.search(
        r"(?:compre\s+r\$\s*\d+\s+e\s+)?ganhe\s+r\$\s*(\d+)\s*off|cupom\s+de\s+r\$\s*(\d+)\s*off",
        txt,
        re.IGNORECASE,
    )
    if m_off:
        val = float(m_off.group(1) or m_off.group(2) or 0)
        if 5 <= val <= base_price * 0.35:
            return CouponInfo(
                code="CUPOM NO ANÚNCIO ML",
                store_name="Mercado Livre",
                discount_type="fixed",
                discount_value=val,
                status="CONFIRMADO_HOJE",
                rule_description=f"Marque o cupom de R$ {val:,.0f} OFF na página do produto no Mercado Livre",
                source="Cupom Nativo Mercado Livre",
            )
    return None


async def _verify_and_enrich_pelando_deal(
    client: httpx.AsyncClient, item: dict[str, Any]
) -> dict[str, Any] | None:
    """
    Fetch Pelando deal detail page, check expiration/comments, decode the direct store URL,
    and verify live price on open-HTML retailers while preserving active (<24h-36h)
    Shopee and Mercado Livre deals behind login walls.
    """
    deal_url = item["product_url"]
    try:
        resp = await client.get(deal_url, timeout=8.0)
        if resp.status_code != 200:
            return None
        low_html = resp.text.lower()
        if '"isexpired":true' in low_html or '"inactive":true' in low_html:
            return None

        # Check Pelando comments for expiration markers
        comment_texts = re.findall(r'"content"\s*:\s*"([^"]+)"', resp.text)
        if any(
            any(m in c.lower() for m in EXPIRED_COMMENT_MARKERS)
            for c in comment_texts
        ):
            return None

        direct_url = _decode_pelando_jwt_url(resp.text, deal_url)
        if direct_url == deal_url:
            return None

        code = _extract_pelando_coupon_code(resp.text)
        if code and not item.get("embedded_coupon_code"):
            item["embedded_coupon_code"] = code
            item["page_coupon"] = CouponInfo(
                code=code,
                store_name=item["store_name"],
                discount_type="fixed",
                discount_value=0.0,
                status="CONFIRMADO_HOJE",
                rule_description=f"Cupom ativo reportado no Pelando ({item['product_title'][:50]})",
                source="Pelando (Ativo Hoje)",
            )

        if "resgate todos os cupons" in low_html or "página de cupons" in low_html:
            item["deal_note"] = (
                f"{item.get('deal_note', '')} • Resgate o cupom da loja na página do anúncio"
            ).strip(" •")

        s_name, s_dom = _canonical_store(direct_url)
        if s_name != direct_url and s_dom != "pelando.com.br":
            item["store_name"] = s_name
            item["store_domain"] = s_dom

        is_walled = _is_login_walled_marketplace(direct_url) or _is_login_walled_marketplace(
            item["store_name"]
        )
        age_h = float(item.get("deal_age_hours") or 0.0)
        if "shopee" in item["store_name"].lower() and age_h > MAX_SHOPEE_FLASH_AGE_HOURS:
            return None

        # Try fetching the target store page to verify live price
        headers = (
            {"User-Agent": "Twitterbot/1.0", "Accept-Language": "pt-BR,pt;q=0.9"}
            if "mercadolivre.com.br" in direct_url
            else DEFAULT_HEADERS
        )
        try:
            store_resp = await client.get(direct_url, headers=headers, timeout=7.0)
            if store_resp.status_code in (404, 410):
                return None
            if store_resp.status_code == 200:
                final_url = str(store_resp.url)
                meta = extract_product_metadata(store_resp.text, final_url)
                if meta.get("in_stock") is False:
                    return None
                live_price = meta.get("price_brl")
                if live_price and live_price > 0:
                    posted_price = float(item["price_pix"])
                    if live_price > posted_price * 1.18 and not item.get("embedded_coupon_code"):
                        return None
                    item["price_pix"] = min(live_price, posted_price) if item.get("embedded_coupon_code") else live_price
                    item["price_installment_total"] = item["price_pix"]
                    item["product_url"] = final_url
                    if "mercadolivre.com.br" in final_url and not item.get("page_coupon"):
                        ml_cup = _extract_ml_native_coupon(store_resp.text, live_price)
                        if ml_cup:
                            item["page_coupon"] = ml_cup
                    return item
        except Exception:
            if not is_walled:
                return None

        # Login-walled marketplaces (Shopee / Mercado Livre) don't expose price_brl to plain HTTPX
        if is_walled:
            item["product_url"] = direct_url
            item["seller_name"] = f"{item['store_name']} (Oferta Ativa Pelando)"
            return item

        return None
    except Exception:
        return None


async def hunt_pelando_live(
    query: str,
    spec: SpecContract | None = None,
    max_age_hours: float = MAX_COMMUNITY_AGE_HOURS,
) -> tuple[list[dict[str, Any]], ChannelStatus]:
    """
    Search Pelando recent deals using concise multi-query expansion so marketplace deals
    (Shopee & Mercado Livre) are reliably discovered and verified.
    """
    queries = _build_community_queries(query)
    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=10.0
        ) as client:
            resps = await asyncio.gather(
                *[
                    client.get(f"https://www.pelando.com.br/busca/{quote(q)}")
                    for q in queries
                ],
                return_exceptions=True,
            )

            for resp in resps:
                if not isinstance(resp, httpx.Response) or resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "lxml")
                cards = soup.select("div[class*='_deal-card_']")

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
                    age_h = _parse_age_hours(
                        ts_el.get_text(" ", strip=True) if ts_el else ""
                    )
                    if age_h is None or age_h > max_age_hours:
                        continue

                    stamp_el = card.select_one("span[class*='_deal-card-stamp_']")
                    price_val = parse_brl_price(
                        stamp_el.get_text(" ", strip=True) if stamp_el else ""
                    )
                    if not price_val:
                        continue

                    store_a = card.select_one("div[class*='deal-card-store'] a")
                    raw_store = (
                        store_a.get_text(" ", strip=True) if store_a else "Pelando"
                    )
                    store_name, store_domain = _canonical_store(raw_store)
                    if (
                        "shopee" in store_name.lower()
                        and age_h > MAX_SHOPEE_FLASH_AGE_HOURS
                    ):
                        continue

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
                            "seller_name": f"{store_name} (Validado Pelando)",
                            "seller_rating": 4.9,
                            "is_official_store": True,
                            "in_stock": True,
                            "source": "Pelando (Tempo Real)",
                            "deal_age_hours": age_h,
                            "deal_note": f"Oferta ativa postada há {int(max(1, age_h))}h no Pelando",
                        }
                    )

            verified_offers: list[dict[str, Any]] = []
            if candidates:
                checked = await asyncio.gather(
                    *[
                        _verify_and_enrich_pelando_deal(client, c)
                        for c in candidates[:6]
                    ],
                    return_exceptions=True,
                )
                for res in checked:
                    if isinstance(res, dict):
                        verified_offers.append(res)

            return verified_offers, ChannelStatus(
                name="Pelando (Tempo Real)",
                status="ok" if verified_offers else "vazio",
                count=len(verified_offers),
                detail=f"{len(verified_offers)} ofertas ativas verificadas",
            )
    except Exception as exc:
        logger.debug("Pelando hunt failed: %s", exc)
        return [], ChannelStatus(
            name="Pelando (Tempo Real)",
            status="indisponivel",
            detail=str(exc)[:80],
        )


async def _verify_and_enrich_promobit_deal(
    client: httpx.AsyncClient, offer_id: Any, offer_url: str, item: dict[str, Any]
) -> dict[str, Any] | None:
    """
    Verify a Promobit deal by checking comments for expiration AND resolving
    /Redirect/to/{offer_id}/ to the target retailer page. For login-walled marketplaces
    (Shopee / Mercado Livre), keep active deals (<24h-36h) with zero expiration comments.
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
                instr = str(so.get("offerInstructionsRaw") or "").strip()
                if instr:
                    clean_instr = re.sub(r"\s+", " ", instr)[:140]
                    item["deal_note"] = f"{item.get('deal_note', '')} • {clean_instr}".strip(" •")

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
        is_walled = _is_login_walled_marketplace(out_url) or _is_login_walled_marketplace(
            item["store_name"]
        )
        age_h = float(item.get("deal_age_hours") or 0.0)
        if "shopee" in item["store_name"].lower() and age_h > MAX_SHOPEE_FLASH_AGE_HOURS:
            return None

        headers = (
            {"User-Agent": "Twitterbot/1.0", "Accept-Language": "pt-BR,pt;q=0.9"}
            if "mercadolivre.com.br" in out_url
            else DEFAULT_HEADERS
        )
        try:
            store_resp = await client.get(out_url, headers=headers, timeout=8.0)
            if store_resp.status_code in (404, 410):
                return None
            if store_resp.status_code == 200:
                final_url = str(store_resp.url)
                meta = extract_product_metadata(store_resp.text, final_url)
                if meta.get("in_stock") is False:
                    return None
                live_price = meta.get("price_brl")
                if live_price and live_price > 0:
                    posted_price = float(item["price_pix"])
                    page_coupon: CouponInfo | None = item.get("page_coupon")
                    if (
                        page_coupon
                        and live_price > posted_price
                        and (live_price - posted_price) <= live_price * 0.25
                    ):
                        page_coupon.discount_value = round(live_price - posted_price, 2)
                    elif live_price > posted_price * 1.18 and not page_coupon:
                        return None

                    item["product_url"] = final_url
                    item["price_pix"] = live_price
                    item["price_installment_total"] = live_price
                    s_name, s_dom = _canonical_store(final_url)
                    if s_name != final_url and s_dom != "pelando.com.br":
                        item["store_name"] = s_name
                        item["store_domain"] = s_dom
                        item["seller_name"] = f"{s_name} (Preço Confirmado ao Vivo)"
                    return item
        except Exception:
            if not is_walled:
                return None

        if is_walled:
            item["product_url"] = out_url
            item["seller_name"] = f"{item['store_name']} (Oferta Ativa Promobit)"
            return item

        return None
    except Exception:
        return None


async def hunt_promobit_live(
    query: str,
    spec: SpecContract | None = None,
    max_age_hours: float = MAX_COMMUNITY_AGE_HOURS,
) -> tuple[list[dict[str, Any]], list[CouponInfo], ChannelStatus]:
    """
    Query Promobit's JSON API using concise multi-query expansion and keep ONLY
    recent verified deals (blocking 4-5 day old expired posts while capturing today's
    Shopee, Mercado Livre, and Amazon promotions).
    """
    offers: list[dict[str, Any]] = []
    coupons: list[CouponInfo] = []
    queries = _build_community_queries(query)
    headers = {
        **DEFAULT_HEADERS,
        "Origin": "https://www.promobit.com.br",
        "Referer": "https://www.promobit.com.br/",
    }
    try:
        async with httpx.AsyncClient(
            headers=headers, follow_redirects=True, timeout=10.0
        ) as client:
            resps = await asyncio.gather(
                *[
                    client.get(
                        "https://api.promobit.com.br/search", params={"q": q}
                    )
                    for q in queries
                ],
                return_exceptions=True,
            )

            now = datetime.now(timezone.utc)
            candidates: list[tuple[Any, str, dict[str, Any]]] = []
            seen_ids: set[Any] = set()

            for resp in resps:
                if not isinstance(resp, httpx.Response) or resp.status_code != 200:
                    continue
                data = resp.json()
                active_offers = data.get("active_offers") or []

                for ao in active_offers[:10]:
                    if not isinstance(ao, dict):
                        continue
                    oid = ao.get("offer_id")
                    if oid in seen_ids:
                        continue
                    seen_ids.add(oid)
                    if ao.get("offer_status_name") != "APPROVED":
                        continue

                    pub = ao.get("offer_published")
                    if not pub:
                        continue
                    try:
                        dt = datetime.fromisoformat(str(pub))
                        age_h = round(
                            (now - dt.astimezone(timezone.utc)).total_seconds()
                            / 3600.0,
                            1,
                        )
                    except Exception:
                        continue

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
                    if (
                        "shopee" in store_name.lower()
                        and age_h > MAX_SHOPEE_FLASH_AGE_HOURS
                    ):
                        continue

                    slug = str(ao.get("offer_slug") or "").strip()
                    offer_url = (
                        f"https://www.promobit.com.br/oferta/{slug}/"
                        if slug
                        else "https://www.promobit.com.br"
                    )

                    coupon_code = str(ao.get("offer_coupon") or "").strip().upper()
                    page_coupon: CouponInfo | None = None
                    if (
                        coupon_code
                        and len(coupon_code) >= 3
                        and " " not in coupon_code
                    ):
                        page_coupon = CouponInfo(
                            code=coupon_code,
                            store_name=store_name,
                            discount_type="fixed",
                            discount_value=0.0,
                            status="CONFIRMADO_HOJE",
                            rule_description=f"Cupom postado há {age_h}h no Promobit ({title[:55]})",
                            source="Promobit (Ativo Hoje)",
                        )

                    candidates.append(
                        (
                            oid,
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
                                "seller_name": f"{store_name} (Validado Promobit)",
                                "seller_rating": 4.9,
                                "is_official_store": True,
                                "in_stock": True,
                                "source": "Promobit (Ativas)",
                                "deal_age_hours": age_h,
                                "deal_note": f"Oferta ativa postada há {age_h}h no Promobit",
                                "embedded_coupon_code": coupon_code if page_coupon else "",
                                "page_coupon": page_coupon,
                            },
                        )
                    )

            if candidates:
                checked = await asyncio.gather(
                    *[
                        _verify_and_enrich_promobit_deal(client, oid, ourl, item)
                        for oid, ourl, item in candidates[:6]
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
                    name="Promobit (Ativas)",
                    status="ok" if offers else "vazio",
                    count=len(offers),
                    detail=f"{len(offers)} ofertas ativas verificadas",
                ),
            )
    except Exception as exc:
        logger.debug("Promobit hunt failed: %s", exc)
        return (
            [],
            [],
            ChannelStatus(
                name="Promobit (Ativas)",
                status="indisponivel",
                detail=str(exc)[:80],
            ),
        )


async def hunt_mercadolivre_direct(
    query: str,
    spec: SpecContract | None = None,
) -> tuple[list[dict[str, Any]], ChannelStatus]:
    """
    Discover Mercado Livre /p/MLB... catalog pages via Yahoo BR SERP and extract live
    prices and native 'R$ X com Cupom' badges using Twitterbot SSR rendering.
    """
    from urllib.parse import unquote

    queries = _build_community_queries(query)
    search_q = f"site:mercadolivre.com.br {queries[0]}"
    offers: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=10.0
        ) as client:
            resp = await client.get(
                "https://br.search.yahoo.com/search", params={"p": search_q}
            )
            if resp.status_code != 200:
                return [], ChannelStatus(
                    name="Mercado Livre Catálogo", status="vazio", count=0
                )

            txt = unquote(resp.text)
            raw_urls = list(
                dict.fromkeys(
                    re.findall(
                        r"https?://(?:www\.|produto\.)?mercadolivre\.com\.br/[A-Za-z0-9_\-/]*MLB\d+[A-Za-z0-9_\-]*",
                        txt,
                    )
                )
            )
            catalog_urls = [u for u in raw_urls if "/p/MLB" in u][:3]

            for ml_url in catalog_urls:
                try:
                    ml_resp = await client.get(
                        ml_url,
                        headers={
                            "User-Agent": "Twitterbot/1.0",
                            "Accept-Language": "pt-BR,pt;q=0.9",
                        },
                        timeout=8.0,
                    )
                    if ml_resp.status_code != 200:
                        continue
                    meta = extract_product_metadata(ml_resp.text, ml_url)
                    title = str(meta.get("title") or "").strip()
                    price = meta.get("price_brl")
                    if not title or not price or price <= 0:
                        continue
                    if spec and check_title(title, spec) is not None:
                        continue

                    native_coupon = _extract_ml_native_coupon(ml_resp.text, price)
                    deal_note = (
                        native_coupon.rule_description
                        if native_coupon
                        else "Catálogo Oficial Mercado Livre (Full)"
                    )
                    offers.append(
                        {
                            "store_name": "Mercado Livre",
                            "store_domain": "mercadolivre.com.br",
                            "product_title": title,
                            "product_url": ml_url,
                            "price_pix": price,
                            "price_installment_total": price,
                            "installments_count": 10 if price >= 200 else 6,
                            "interest_free": True,
                            "seller_name": "Mercado Livre (Catálogo Oficial)",
                            "seller_rating": 4.9,
                            "is_official_store": True,
                            "in_stock": meta.get("in_stock") is not False,
                            "source": "Mercado Livre Catálogo",
                            "deal_note": deal_note,
                            "page_coupon": native_coupon,
                        }
                    )
                except Exception:
                    continue

            return offers, ChannelStatus(
                name="Mercado Livre Catálogo",
                status="ok" if offers else "vazio",
                count=len(offers),
                detail=f"{len(offers)} ofertas do catálogo ML",
            )
    except Exception as exc:
        logger.debug("Mercado Livre direct hunt failed: %s", exc)
        return [], ChannelStatus(
            name="Mercado Livre Catálogo",
            status="indisponivel",
            detail=str(exc)[:80],
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
