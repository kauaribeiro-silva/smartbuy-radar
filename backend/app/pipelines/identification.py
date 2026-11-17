from __future__ import annotations

import json
import logging
from typing import Optional
from google import genai
from google.genai import types

from app.config import settings
from app.models import IdentifiedProduct
from app.utils.browser import fetch_page_smart
from app.utils.parsers import extract_json_from_llm, extract_product_metadata, parse_brl_price

logger = logging.getLogger(__name__)


def _get_client() -> genai.Client:
    if not settings.gemini_api_key:
        raise ValueError(
            "GEMINI_API_KEY não configurada. Defina a variável no arquivo .env."
        )
    return genai.Client(api_key=settings.gemini_api_key)


async def identify_from_image(
    image_bytes: bytes,
    mime_type: str,
    user_hint: str = "",
) -> IdentifiedProduct:
    """Pipeline 1A: Identify exact product specs from an uploaded/pasted image using Gemini Vision."""
    client = _get_client()

    prompt = (
        "Você é um especialista em identificação de produtos de e-commerce no Brasil.\n"
        "Analise a imagem enviada e identifique o produto com precisão máxima.\n"
        f"Dica adicional do usuário (se houver): '{user_hint}'.\n\n"
        "Retorne EXCLUSIVAMENTE um JSON válido com os campos:\n"
        "{\n"
        '  "brand": "Marca",\n'
        '  "line": "Linha do produto",\n'
        '  "exact_model": "Nome e modelo exato (ex: iPhone 15 Pro 256GB Titânio Natural)",\n'
        '  "color": "Cor identificada ou vazia",\n'
        '  "capacity_or_voltage": "Capacidade/Armazenamento/Voltagem (ex: 256GB, 110V, Bivolt)",\n'
        '  "ean_gtin": "EAN/GTIN se visível ou conhecido, senão vazio",\n'
        '  "sku_part_number": "SKU/Part Number se visível ou conhecido, senão vazio",\n'
        '  "category": "Categoria (ex: Smartphones, Notebooks, Eletrodomésticos, Áudio, Games)",\n'
        '  "search_query": "Termo ideal de busca no Google/varejistas BR",\n'
        '  "is_ambiguous": true/false (true se não der para saber capacidade/voltagem/variação exata pela foto),\n'
        '  "ambiguity_options": ["opção 1", "opção 2"] (lista de variações possíveis caso is_ambiguous=true, ex: ["128GB", "256GB", "512GB"]),\n'
        '  "confidence": 0.0 a 1.0\n'
        "}"
    )

    response = await client.aio.models.generate_content(
        model=settings.gemini_model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
        ),
    )

    parsed = extract_json_from_llm(response.text or "")
    if not isinstance(parsed, dict) or not parsed.get("exact_model"):
        raise ValueError("Não foi possível identificar o produto na imagem.")

    return IdentifiedProduct(
        brand=parsed.get("brand", ""),
        line=parsed.get("line", ""),
        exact_model=parsed["exact_model"],
        color=parsed.get("color", ""),
        capacity_or_voltage=parsed.get("capacity_or_voltage", ""),
        ean_gtin=parsed.get("ean_gtin", ""),
        sku_part_number=parsed.get("sku_part_number", ""),
        category=parsed.get("category", "Eletrônicos"),
        search_query=parsed.get("search_query") or parsed["exact_model"],
        is_ambiguous=bool(parsed.get("is_ambiguous", False)),
        ambiguity_options=parsed.get("ambiguity_options") or [],
        confidence=float(parsed.get("confidence", 0.85)),
    )


def _title_from_url_slug(url: str) -> str:
    """Extract a human-readable product title from a Brazilian e-commerce URL slug."""
    from urllib.parse import urlparse
    import re

    path = urlparse(url).path.strip("/")
    segments = [s for s in path.split("/") if s and s not in ("p", "dp", "produto", "produtos")]
    # Pick the longest descriptive slug segment
    best = max(segments, key=len, default="")
    # Strip trailing Mercado Livre / Amazon IDs like MLB12345 or B0XYZ
    best = re.sub(r"\b(MLB\d+|B0[A-Z0-9]{8})\b", "", best, flags=re.IGNORECASE)
    cleaned = re.sub(r"[-_+]+", " ", best).strip()
    return cleaned[:120] if len(cleaned) >= 4 else url


async def identify_from_url(url: str) -> IdentifiedProduct:
    """Pipeline 1B: Extract JSON-LD, og:title, EAN/SKU and reference price from product URL."""
    html = await fetch_page_smart(url)
    meta = extract_product_metadata(html, url) if html else {"url": url, "title": ""}
    slug_title = _title_from_url_slug(url)
    best_raw_title = meta.get("title") or slug_title

    client = _get_client()
    prompt = (
        "Você é um especialista em normalização de catálogo de e-commerce brasileiro.\n"
        "Abaixo estão os metadados extraídos da URL de referência do usuário:\n"
        f"- URL: {url}\n"
        f"- Título extraído: {best_raw_title}\n"
        f"- Marca extraída: {meta.get('brand', '')}\n"
        f"- SKU extraído: {meta.get('sku', '')}\n"
        f"- EAN/GTIN extraído: {meta.get('gtin', '')}\n"
        f"- Preço extraído: {meta.get('price_brl')}\n"
        f"- Descrição: {str(meta.get('description', ''))[:400]}\n\n"
        "Retorne EXCLUSIVAMENTE um JSON válido com:\n"
        "{\n"
        '  "brand": "Marca",\n'
        '  "line": "Linha",\n'
        '  "exact_model": "Modelo exato completo",\n'
        '  "color": "Cor",\n'
        '  "capacity_or_voltage": "Capacidade/Voltagem",\n'
        '  "ean_gtin": "EAN/GTIN se encontrado",\n'
        '  "sku_part_number": "SKU/Part Number",\n'
        '  "category": "Categoria",\n'
        '  "search_query": "Termo limpo de busca no Brasil",\n'
        '  "reference_price_brl": numero_ou_null,\n'
        '  "confidence": 0.95\n'
        "}"
    )

    parsed = None
    try:
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
        logger.warning("Gemini Search Grounding indisponível em identify_from_url (%s); usando metadados reais da URL.", exc)

    if not isinstance(parsed, dict) or not parsed.get("exact_model"):
        return IdentifiedProduct(
            brand=meta.get("brand", ""),
            exact_model=best_raw_title[:120],
            ean_gtin=meta.get("gtin", ""),
            sku_part_number=meta.get("sku", ""),
            search_query=best_raw_title[:110],
            reference_price_brl=meta.get("price_brl"),
            reference_url=url,
            image_url=meta.get("image_url") or None,
            confidence=0.85,
        )

    ref_price = meta.get("price_brl") or parse_brl_price(parsed.get("reference_price_brl"))
    return IdentifiedProduct(
        brand=parsed.get("brand") or meta.get("brand", ""),
        line=parsed.get("line", ""),
        exact_model=parsed["exact_model"],
        color=parsed.get("color", ""),
        capacity_or_voltage=parsed.get("capacity_or_voltage", ""),
        ean_gtin=parsed.get("ean_gtin") or meta.get("gtin", ""),
        sku_part_number=parsed.get("sku_part_number") or meta.get("sku", ""),
        category=parsed.get("category", "Eletrônicos"),
        search_query=parsed.get("search_query") or parsed["exact_model"],
        reference_price_brl=ref_price,
        reference_url=url,
        image_url=meta.get("image_url") or None,
        is_ambiguous=False,
        confidence=float(parsed.get("confidence", 0.92)),
    )


async def identify_from_text(query: str, variation_override: Optional[str] = None) -> IdentifiedProduct:
    """Pipeline 1C: Resolve a text product query (or confirmed variation) into exact specs."""
    client = _get_client()
    full_query = f"{query} {variation_override}".strip() if variation_override else query.strip()

    prompt = (
        "Você é um especialista em catálogo de produtos no e-commerce brasileiro.\n"
        f"Para a busca: '{full_query}', identifique o produto exato no mercado brasileiro.\n"
        "Use o Google Search para confirmar modelo exato, EAN/GTIN ou Part Number e preço médio de referência no Brasil.\n"
        "Retorne EXCLUSIVAMENTE um JSON válido:\n"
        "{\n"
        '  "brand": "Marca",\n'
        '  "line": "Linha",\n'
        '  "exact_model": "Modelo exato",\n'
        '  "color": "Cor padrão se aplicável",\n'
        '  "capacity_or_voltage": "Capacidade/Voltagem se aplicável",\n'
        '  "ean_gtin": "EAN/GTIN se encontrado",\n'
        '  "sku_part_number": "SKU/Part Number",\n'
        '  "category": "Categoria",\n'
        '  "search_query": "Termo ideal de busca",\n'
        '  "reference_price_brl": numero_ou_null,\n'
        '  "confidence": 0.9\n'
        "}"
    )

    parsed = None
    try:
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
        logger.warning("Gemini Search Grounding indisponível em identify_from_text (%s); preservando termo exato do usuário.", exc)

    if not isinstance(parsed, dict) or not parsed.get("exact_model"):
        return IdentifiedProduct(
            exact_model=full_query,
            search_query=full_query,
            confidence=0.9,
        )

    return IdentifiedProduct(
        brand=parsed.get("brand", ""),
        line=parsed.get("line", ""),
        exact_model=parsed["exact_model"],
        color=parsed.get("color", ""),
        capacity_or_voltage=parsed.get("capacity_or_voltage", ""),
        ean_gtin=parsed.get("ean_gtin", ""),
        sku_part_number=parsed.get("sku_part_number", ""),
        category=parsed.get("category", "Eletrônicos"),
        search_query=parsed.get("search_query") or full_query,
        reference_price_brl=parse_brl_price(parsed.get("reference_price_brl")),
        is_ambiguous=False,
        confidence=float(parsed.get("confidence", 0.9)),
    )
