from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup


OFFICIAL_RETAILER_DOMAINS: dict[str, str] = {
    "amazon.com.br": "Amazon Brasil",
    "mercadolivre.com.br": "Mercado Livre",
    "kabum.com.br": "KaBuM!",
    "magazineluiza.com.br": "Magazine Luiza",
    "magalu.com": "Magazine Luiza",
    "fastshop.com.br": "Fast Shop",
    "shopee.com.br": "Shopee Oficial",
    "carrefour.com.br": "Carrefour",
    "casasbahia.com.br": "Casas Bahia",
    "pontofrio.com.br": "Ponto",
    "extra.com.br": "Extra",
    "samsung.com": "Samsung Oficial",
    "apple.com": "Apple Oficial",
    "dell.com": "Dell Oficial",
    "sony.com.br": "Sony Oficial",
    "lg.com": "LG Oficial",
    "lenovo.com": "Lenovo Oficial",
    "pichau.com.br": "Pichau",
    "terabyteshop.com.br": "Terabyteshop",
    "kalunga.com.br": "Kalunga",
    "girafa.com.br": "Girafa",
}

REFURBISHED_KEYWORDS = (
    "recondicionado",
    "usado",
    "seminovo",
    "semi-novo",
    "vitrine",
    "mostruário",
    "mostruario",
    "refurbished",
    "open box",
    "openbox",
    "caixa aberta",
    "embalagem aberta",
    "avaria",
    "peça de reposição",
    "para retirada de peças",
)

ACCESSORY_BAIT_KEYWORDS = (
    "capa ",
    "capinha",
    "película",
    "pelicula",
    "cabo ",
    "adaptador",
    "suporte ",
    "carregador avulso",
    "controle remoto",
    "bateria avulsa",
    "caixa vazia",
    "manual ",
    "estojo ",
    "case ",
)


def parse_brl_price(text: str | float | int | None) -> Optional[float]:
    """Parse Brazilian Real price strings like 'R$ 4.299,90' or '4299.90'."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        val = float(text)
        return val if val > 0 else None

    cleaned = str(text).strip()
    match = re.search(
        r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d{1,3}(?:\.\d{3})+(?![\d,])|\d+(?:[.,]\d{1,2})?)",
        cleaned,
    )
    if not match:
        return None
    raw = match.group(1)
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        # Brazilian thousands separator without decimals, e.g. "1.655" -> 1655
        raw = raw.replace(".", "")
    try:
        val = float(raw)
        return round(val, 2) if val > 0 else None
    except ValueError:
        return None


def extract_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def match_known_store(url: str) -> tuple[str, str]:
    domain = extract_domain(url)
    for known_domain, store_name in OFFICIAL_RETAILER_DOMAINS.items():
        if domain == known_domain or domain.endswith(f".{known_domain}"):
            return store_name, known_domain
    return domain, domain


def extract_json_from_llm(text: str) -> Any:
    """Safely parse JSON from Gemini response text, stripping markdown code fences."""
    if not text:
        return None
    stripped = text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped)
    if fence_match:
        stripped = fence_match.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        # Try extracting outermost {} or []
        obj_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", stripped)
        if obj_match:
            try:
                return json.loads(obj_match.group(1))
            except json.JSONDecodeError:
                return None
    return None


def extract_product_metadata(html: str, url: str) -> dict[str, Any]:
    """Extract JSON-LD Product, OpenGraph tags, EAN/GTIN, SKU, and price from HTML."""
    soup = BeautifulSoup(html, "lxml")
    meta: dict[str, Any] = {
        "url": url,
        "title": "",
        "brand": "",
        "sku": "",
        "gtin": "",
        "price_brl": None,
        "image_url": "",
        "seller_name": "",
        "description": "",
        "in_stock": True,
    }

    # 1. OpenGraph & standard meta tags
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        meta["title"] = og_title["content"].strip()
    elif soup.title and soup.title.string:
        meta["title"] = soup.title.string.strip()

    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        meta["image_url"] = og_image["content"].strip()

    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        meta["description"] = og_desc["content"].strip()

    price_meta = soup.find("meta", property="product:price:amount") or soup.find(
        "meta", itemprop="price"
    )
    if price_meta and price_meta.get("content"):
        meta["price_brl"] = parse_brl_price(price_meta["content"])

    # 2. JSON-LD Product blocks
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        items = data if isinstance(data, list) else [data]
        # Handle @graph
        expanded: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    expanded.extend(g for g in item["@graph"] if isinstance(g, dict))
                else:
                    expanded.append(item)

        for obj in expanded:
            obj_type = obj.get("@type", "")
            if isinstance(obj_type, list):
                is_product = "Product" in obj_type
            else:
                is_product = obj_type == "Product"
            if not is_product:
                continue

            if obj.get("name"):
                meta["title"] = str(obj["name"]).strip()
            if obj.get("sku"):
                meta["sku"] = str(obj["sku"]).strip()
            for gtin_key in ("gtin13", "gtin14", "gtin12", "gtin8", "gtin", "ean"):
                if obj.get(gtin_key):
                    meta["gtin"] = str(obj[gtin_key]).strip()
                    break

            brand = obj.get("brand")
            if isinstance(brand, dict) and brand.get("name"):
                meta["brand"] = str(brand["name"]).strip()
            elif isinstance(brand, str):
                meta["brand"] = brand.strip()

            img = obj.get("image")
            if isinstance(img, list) and img:
                meta["image_url"] = str(img[0])
            elif isinstance(img, str):
                meta["image_url"] = img

            offers = obj.get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                if offers.get("price") and not meta["price_brl"]:
                    meta["price_brl"] = parse_brl_price(offers["price"])
                avail = str(offers.get("availability", ""))
                if "OutOfStock" in avail:
                    meta["in_stock"] = False
                seller = offers.get("seller")
                if isinstance(seller, dict) and seller.get("name"):
                    meta["seller_name"] = str(seller["name"]).strip()
            break

    return meta


def evaluate_offer_trust(
    product_title: str,
    target_query: str,
    store_name: str,
    seller_name: str,
    seller_rating: float | None,
    is_official_store: bool,
    price_pix: float,
    reference_price: float | None = None,
    allow_refurbished: bool = False,
) -> tuple[bool, int, str]:
    """
    Strict Anti-Scam & Seller Quality filter.
    Returns (passed, trust_score_0_to_100, reason).
    """
    title_lower = product_title.lower()
    target_lower = target_query.lower()

    # 1. Reject refurbished/used unless explicitly requested
    if not allow_refurbished and not any(
        kw in target_lower for kw in ("usado", "recondicionado", "seminovo")
    ):
        for kw in REFURBISHED_KEYWORDS:
            if kw in title_lower:
                return False, 10, f"Anúncio descartado: produto '{kw}' (não é novo lacrado)."

    # 2. Reject accessory bait when target is not an accessory
    target_is_accessory = any(kw.strip() in target_lower for kw in ACCESSORY_BAIT_KEYWORDS)
    if not target_is_accessory:
        for kw in ACCESSORY_BAIT_KEYWORDS:
            if kw in title_lower and kw.strip() not in target_lower:
                return False, 15, f"Anúncio descartado: parece acessório ('{kw.strip()}'), não o produto principal."

    # 3. Reject suspiciously low price (< 42% of reference price) - typical scam or wrong item
    if reference_price and reference_price > 100 and price_pix < (reference_price * 0.42):
        return (
            False,
            20,
            f"Preço suspeito (R$ {price_pix:.2f} vs referência R$ {reference_price:.2f}): possível golpe ou acessório.",
        )

    # 4. Seller reputation & official store checks
    seller_lower = (seller_name or "").lower()
    official_markers = (
        "loja oficial",
        "amazon.com.br",
        "magalu",
        "magazine luiza",
        "kabum",
        "fast shop",
        "carrefour",
        "casas bahia",
        "mercadolivre",
        "mercado livre",
        "samsung",
        "apple",
        "dell",
        "platinum",
    )
    inferred_official = is_official_store or any(m in seller_lower for m in official_markers)

    if inferred_official:
        score = 96
        if seller_rating and seller_rating >= 4.8:
            score = 99
        return True, score, "Vendido e entregue por loja oficial / vendedor Platinum verificado."

    if seller_rating is not None:
        if seller_rating >= 4.7:
            return True, int(80 + (seller_rating - 4.7) * 50), f"Vendedor bem avaliado ({seller_rating:.1f}★)."
        return False, 35, f"Vendedor com avaliação abaixo do mínimo ({seller_rating:.1f}★ < 4.7★)."

    # Known direct retailers (KaBuM, Fast Shop, Magalu, etc.) when 1P
    direct_retailers = {"KaBuM!", "Fast Shop", "Magazine Luiza", "Carrefour", "Casas Bahia", "Amazon Brasil"}
    if store_name in direct_retailers and not seller_name:
        return True, 90, f"Oferta direta em varejista confiável ({store_name})."

    # Marketplace 3P without rating or official badge -> reject
    return False, 30, "Vendedor de marketplace sem histórico ou selo oficial comprovado."
