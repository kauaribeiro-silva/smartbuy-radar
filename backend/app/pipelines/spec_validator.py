"""StrictSpecValidator — anti-downgrade guard for SmartBuy Radar v2.0.

Trava A: Spec contract (canonical name + mandatory tokens + forbidden tokens).
         Built with Gemini when available, always merged with a deterministic heuristic
         engine so the guard keeps working when the LLM quota is exhausted.
Trava B: Outlier price floor (< 55% of the median of the exact model).
Trava C: Semantic double-check of the 10 cheapest survivors.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import unicodedata
from collections import OrderedDict
from typing import Any, Iterable, Optional

from google import genai
from google.genai import types

from app.config import settings
from app.models import BlockedGroup, SpecContract, StoreOffer
from app.utils.parsers import extract_json_from_llm

logger = logging.getLogger(__name__)

PRICE_FLOOR_RATIO = 0.55

# --------------------------------------------------------------------------- normalization

UNITS = ("gb", "tb", "mb", "hz", "ms", "v", "w", "mah", "mp", "pol", "polegadas", "p")


def norm(text: str) -> str:
    s = unicodedata.normalize("NFKD", (text or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("”", '"').replace("″", '"')
    s = re.sub(r"play\s*station\s*(\d)", r"ps\1", s)
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)
    s = re.sub(r"(\d)\s+(gb|tb|mb|hz|ms|v|w|mah|mp)\b", r"\1\2", s)
    return s


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", norm(text))


def flat(text: str) -> str:
    return " " + " ".join(tokenize(text)) + " "


# --------------------------------------------------------------------------- lexicons

USED_TOKENS = (
    "usado", "usada", "seminovo", "seminova", "recondicionado", "recondicionada", "vitrine",
    "mostruario", "refurbished", "defeito", "defeituoso", "avariado", "sucata", "retirada de pecas",
    "open box", "caixa aberta", "embalagem aberta",
)

ACCESSORY_TOKENS = (
    "capa", "capinha", "pelicula", "case", "estojo", "bolsa", "protetor", "adesivo", "skin",
    "caixa vazia", "kit acessorios", "avulso", "avulsa", "aluguel", "conta", "gift card",
    "cartao presente", "suporte", "bateria avulsa", "manual", "carcaca", "grip",
)

EXTRA_ALWAYS_FORBIDDEN = ("v1", "v2")

# family -> (trigger tokens, mandatory anchor tokens, exclusive variants, forbidden phrases)
FAMILY_RULES: list[dict[str, Any]] = [
    {"name": "switch", "triggers": {"switch"}, "anchors": ["switch"],
     "variants": {"lite", "oled"}, "phrases": ["switch 2"]},
    {"name": "iphone", "triggers": {"iphone"}, "anchors": ["iphone"],
     "variants": {"pro", "max", "plus", "mini", "se", "air"}, "phrases": []},
    {"name": "galaxy", "triggers": {"galaxy"}, "anchors": ["galaxy"],
     "variants": {"ultra", "plus", "fe", "edge"}, "phrases": []},
    {"name": "playstation", "triggers": {"ps5", "ps4", "ps3"}, "anchors": [],
     "variants": {"digital", "slim", "pro"}, "phrases": []},
    {"name": "xbox", "triggers": {"xbox"}, "anchors": ["xbox"],
     "variants": {"series", "one"}, "phrases": []},
    {"name": "macbook", "triggers": {"macbook"}, "anchors": ["macbook"],
     "variants": {"air", "pro"}, "phrases": []},
    {"name": "ipad", "triggers": {"ipad"}, "anchors": ["ipad"],
     "variants": {"air", "pro", "mini"}, "phrases": []},
    {"name": "airpods", "triggers": {"airpods"}, "anchors": ["airpods"],
     "variants": {"pro", "max"}, "phrases": []},
    {"name": "applewatch", "triggers": {"watch"}, "anchors": [],
     "variants": {"se", "ultra"}, "phrases": []},
    {"name": "gpu", "triggers": {"rtx", "gtx", "rx"}, "anchors": [],
     "variants": {"ti", "super", "xt", "xtx"}, "phrases": []},
    {"name": "kindle", "triggers": {"kindle"}, "anchors": ["kindle"],
     "variants": {"paperwhite", "oasis", "scribe"}, "phrases": []},
]

GENERIC_VARIANTS = {"lite", "mini", "plus", "ultra", "max", "fe", "xl"}

ALL_VARIANT_WORDS: set[str] = set(GENERIC_VARIANTS)
for _fam in FAMILY_RULES:
    ALL_VARIANT_WORDS |= set(_fam["variants"])

FAMILY_ANCHORS = {
    "iphone", "galaxy", "redmi", "poco", "moto", "playstation", "ps", "xbox", "switch", "pixel",
    "note", "edge", "rtx", "gtx", "rx", "ryzen", "macbook", "ipad", "airpods", "watch", "kindle",
    "echo", "tab", "mi",
}

NOISE_TOKENS = {
    "hdr10", "hdr400", "hdr600", "hdr", "4k", "8k", "5g", "4g", "1080p", "1440p", "2160p", "720p",
    "fhd", "uhd", "qhd", "wqhd", "usb", "hdmi", "led", "ips", "va", "tn", "wifi", "nfc",
    "gsync", "freesync", "nvidia", "amd",
}

STOPWORDS = {
    "de", "da", "do", "das", "dos", "para", "com", "sem", "e", "o", "a", "os", "as", "em", "por",
    "novo", "nova", "original", "lacrado", "nacional", "garantia", "nota", "fiscal", "preto", "branco",
    "azul", "vermelho", "cinza", "verde", "rosa", "roxo", "dourado", "prata", "cor",
}

CATEGORY_WORDS = {
    "console", "video", "game", "videogame", "aparelho", "smartphone", "celular", "notebook",
    "monitor", "gamer", "tv", "smart", "tela", "polegadas",
}

UNIT_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)?(?:hz|ms|pol|polegadas|w|v|mp|p|mah)$")
CAPACITY_RE = re.compile(r"^\d+(?:gb|tb|mb)$")

USED_SET = {norm(t) for t in USED_TOKENS}
ACCESSORY_SET = {norm(t) for t in ACCESSORY_TOKENS}


def _token_present(token: str, tokens: set[str], flat_title: str) -> bool:
    t = norm(token).strip()
    if not t:
        return True
    if " " in t:
        return f" {' '.join(tokenize(t))} " in flat_title
    if t in tokens:
        return True
    if t.replace(".", "").isdigit():
        for tok in tokens:
            if tok.startswith(t) and tok[len(t):] in UNITS:
                return True
    if len(t) >= 4 and t in flat_title.replace(" ", ""):
        return True
    return False


def _forbidden_hit(token: str, tokens: set[str], flat_title: str) -> bool:
    t = norm(token).strip()
    if not t:
        return False
    if " " in t:
        return f" {' '.join(tokenize(t))} " in flat_title
    return t in tokens


# --------------------------------------------------------------------------- Trava A


def heuristic_contract(query: str, canonical_name: str | None = None) -> SpecContract:
    qt = tokenize(query)
    qset = set(qt)
    qflat = flat(query)
    mandatory: "OrderedDict[str, None]" = OrderedDict()

    for i, t in enumerate(qt):
        if t in NOISE_TOKENS or t in STOPWORDS or t in CATEGORY_WORDS:
            continue
        if UNIT_TOKEN_RE.match(t):
            continue
        if CAPACITY_RE.match(t):
            mandatory[t] = None
            continue
        if t in ALL_VARIANT_WORDS:
            mandatory[t] = None
            continue
        if re.search(r"[a-z]", t) and re.search(r"\d", t) and len(t) >= 2:
            mandatory[t] = None
            continue
        if t.isdigit():
            prev = qt[i - 1] if i > 0 else ""
            if len(t) >= 3 or prev in FAMILY_ANCHORS:
                mandatory[t] = None

    forbidden: "OrderedDict[str, None]" = OrderedDict()
    for w in USED_SET | ACCESSORY_SET | set(EXTRA_ALWAYS_FORBIDDEN):
        if " " in w:
            if w not in qflat:
                forbidden[w] = None
        elif w not in qset:
            forbidden[w] = None

    matched_family = False
    for fam in FAMILY_RULES:
        if fam["triggers"] & qset:
            matched_family = True
            for a in fam["anchors"]:
                mandatory[a] = None
            for v in sorted(fam["variants"]):
                if v not in qset:
                    forbidden[v] = None
            for p in fam["phrases"]:
                if p not in qflat:
                    forbidden[p] = None
            break
    if not matched_family:
        for v in sorted(GENERIC_VARIANTS):
            if v not in qset:
                forbidden[v] = None

    # Never forbid something the user asked for
    for tok in list(forbidden):
        if tok in qset or tok in mandatory:
            forbidden.pop(tok, None)

    if not mandatory:
        meaningful = [
            t for t in qt
            if t not in STOPWORDS and t not in CATEGORY_WORDS and t not in NOISE_TOKENS and len(t) >= 3
        ]
        for t in sorted(meaningful, key=len, reverse=True)[:2]:
            mandatory[t] = None

    return SpecContract(
        canonical_name=(canonical_name or query).strip(),
        mandatory_tokens=list(mandatory.keys()),
        forbidden_tokens=list(forbidden.keys()),
        source="heurística",
    )


async def build_spec_contract(query: str, canonical_name: str | None = None) -> SpecContract:
    """Trava A. Gemini decomposes the query; result is merged with the heuristic contract."""
    base = heuristic_contract(query, canonical_name)
    if not settings.gemini_api_key:
        return base

    prompt = (
        "Você é um validador estrito de catálogo de e-commerce brasileiro.\n"
        f"Termo buscado pelo usuário: '{query}'.\n"
        "Decomponha em um contrato de especificação. Retorne EXCLUSIVAMENTE JSON:\n"
        '{"canonical_name": "nome exato e completo do produto",\n'
        ' "mandatory_tokens": ["palavras/códigos que OBRIGATORIAMENTE precisam estar no título do anúncio"],\n'
        ' "forbidden_tokens": ["variações inferiores, gerações antigas, acessórios e condições a descartar"]}\n'
        "Regras: mandatory_tokens só pode conter termos que o usuário escreveu (ex: para 'Nintendo Switch OLED' => ['oled']; "
        "para 'iPhone 15 Pro Max 256GB' => ['pro','max','256gb']). forbidden_tokens deve incluir versões diferentes do mesmo "
        "produto (ex: 'lite' para Switch OLED), 'usado', 'recondicionado', 'defeito', 'caixa vazia', 'película', 'case', 'capa', "
        "'estojo', 'aluguel', 'conta' e acessórios avulsos. Use minúsculas."
    )
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        resp = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"),
        )
        parsed = extract_json_from_llm(resp.text or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini indisponível na Trava A (%s); usando contrato heurístico.", str(exc)[:120])
        return base

    if not isinstance(parsed, dict):
        return base

    qset = set(tokenize(query))
    mandatory = list(base.mandatory_tokens)
    for tok in parsed.get("mandatory_tokens") or []:
        n = norm(str(tok)).strip()
        # Only accept tokens grounded in what the user actually typed
        if n and all(part in qset or any(q.startswith(part) for q in qset) for part in tokenize(n)):
            if n not in mandatory:
                mandatory.append(n)

    forbidden = list(base.forbidden_tokens)
    for tok in parsed.get("forbidden_tokens") or []:
        n = norm(str(tok)).strip()
        if not n or n in mandatory:
            continue
        if any(part in qset for part in tokenize(n)):
            continue
        if n not in forbidden:
            forbidden.append(n)

    name = str(parsed.get("canonical_name") or "").strip() or base.canonical_name
    return SpecContract(
        canonical_name=name,
        mandatory_tokens=mandatory,
        forbidden_tokens=forbidden,
        source="gemini + heurística",
    )


def spec_from_stored(canonical_name: str, mandatory: list[str], forbidden: list[str]) -> SpecContract:
    return SpecContract(
        canonical_name=canonical_name,
        mandatory_tokens=mandatory,
        forbidden_tokens=forbidden,
        source="memória",
    )


# --------------------------------------------------------------------------- spec check


def classify_forbidden(token: str) -> tuple[str, str]:
    """Returns (category, human label) for a forbidden token that matched."""
    t = norm(token)
    if t in USED_SET:
        return "usado", f"Usado/recondicionado ('{t}')"
    if t in ACCESSORY_SET:
        return "acessório", f"Acessório/serviço ('{t}')"
    return "variante", f"Versão diferente ('{t}')"


def check_title(title: str, spec: SpecContract, query_has_para: bool = False) -> Optional[tuple[str, str]]:
    """Returns None when the title passes, otherwise (category, label) explaining the block."""
    tokens = set(tokenize(title))
    ft = flat(title)

    for tok in spec.forbidden_tokens:
        if _forbidden_hit(tok, tokens, ft):
            return classify_forbidden(tok)

    # accessory-for pattern: "<something> para Nintendo Switch ..."
    if not query_has_para:
        anchor_tokens = [t for t in tokenize(spec.canonical_name) if len(t) >= 4 and t not in STOPWORDS][:2]
        for a in anchor_tokens:
            if re.search(rf"\b(?:para|p/|compativel com)\s+(?:o\s+|a\s+)?(?:\w+\s+){{0,2}}{re.escape(a)}\b", norm(title)):
                return "acessório", "Acessório 'para <produto>'"

    for tok in spec.mandatory_tokens:
        if not _token_present(tok, tokens, ft):
            return "especificação", f"Especificação divergente (sem '{norm(tok)}')"
    return None


def apply_spec_filter(
    raw_offers: list[dict[str, Any]], spec: SpecContract
) -> tuple[list[dict[str, Any]], list[BlockedGroup]]:
    query_has_para = " para " in flat(spec.canonical_name)
    kept: list[dict[str, Any]] = []
    groups: "OrderedDict[tuple[str, str], BlockedGroup]" = OrderedDict()
    for item in raw_offers:
        verdict = check_title(item.get("product_title", ""), spec, query_has_para)
        if verdict is None:
            kept.append(item)
            continue
        category, label = verdict
        g = groups.setdefault((category, label), BlockedGroup(category=category, label=label))
        g.count += 1
        if len(g.examples) < 3:
            g.examples.append(str(item.get("product_title", ""))[:90])
    ordered = sorted(groups.values(), key=lambda g: -g.count)
    return kept, ordered


# --------------------------------------------------------------------------- Trava B


def apply_price_floor(
    items: list[dict[str, Any]],
    reference_price: float | None = None,
) -> tuple[list[dict[str, Any]], list[BlockedGroup], float | None]:
    """Drop anything below 55% of the exact-model median."""
    prices = [float(i["price_pix"]) for i in items if i.get("price_pix")]
    if not prices:
        return items, [], None
    if len(prices) >= 3:
        base = statistics.median(prices)
    elif reference_price:
        base = reference_price
    else:
        return items, [], None
    floor = round(base * PRICE_FLOOR_RATIO, 2)

    kept: list[dict[str, Any]] = []
    blocked = BlockedGroup(
        category="preço suspeito",
        label=f"Preço abaixo do piso de sanidade (< R$ {floor:,.2f} = 55% da mediana)",
    )
    for i in items:
        if float(i["price_pix"]) < floor:
            blocked.count += 1
            if len(blocked.examples) < 3:
                blocked.examples.append(f"{str(i.get('product_title', ''))[:70]} — R$ {float(i['price_pix']):,.2f}")
        else:
            kept.append(i)
    return kept, ([blocked] if blocked.count else []), floor


# --------------------------------------------------------------------------- Trava C


async def semantic_double_check(
    spec: SpecContract, offers: list[StoreOffer], top_n: int = 10
) -> tuple[list[StoreOffer], list[BlockedGroup], str]:
    """Ask Gemini Flash to eliminate any of the N cheapest offers that is not the exact requested version."""
    if not offers:
        return offers, [], "sem ofertas para validar"
    ordered = sorted(offers, key=lambda o: o.price_pix)
    top = ordered[:top_n]
    if not settings.gemini_api_key:
        return offers, [], "validação determinística (Gemini não configurado)"

    listing = [
        {"id": o.id, "titulo": o.product_title, "loja": o.store_name, "preco": o.price_pix}
        for o in top
    ]
    prompt = (
        "Você é um auditor de catálogo. O usuário quer EXATAMENTE este produto/versão:\n"
        f"'{spec.canonical_name}' (tokens obrigatórios: {spec.mandatory_tokens}).\n"
        "Abaixo estão os 10 menores preços encontrados. Elimine qualquer item que NÃO seja estritamente a mesma "
        "versão/modelo (versão inferior, geração diferente, acessório, bundle de outro produto, usado).\n"
        f"Itens: {json.dumps(listing, ensure_ascii=False)}\n"
        'Responda EXCLUSIVAMENTE JSON: {"rejected": [{"id": "...", "motivo": "..."}]} '
        "(lista vazia se todos forem o modelo exato)."
    )
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        resp = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"),
        )
        parsed = extract_json_from_llm(resp.text or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini indisponível na Trava C (%s).", str(exc)[:120])
        return offers, [], "validação determinística (Gemini indisponível/cota)"

    if not isinstance(parsed, dict):
        return offers, [], "validação determinística (resposta inválida do Gemini)"

    rejected = {str(r.get("id")): str(r.get("motivo") or "") for r in (parsed.get("rejected") or []) if isinstance(r, dict)}
    valid_ids = {o.id for o in top}
    rejected = {k: v for k, v in rejected.items() if k in valid_ids}
    if not rejected:
        return offers, [], "Gemini confirmou: todos os 10 menores preços são o modelo exato"

    group = BlockedGroup(category="verificação IA", label="Eliminado na dupla checagem semântica (Gemini)")
    kept: list[StoreOffer] = []
    for o in offers:
        if o.id in rejected:
            group.count += 1
            if len(group.examples) < 3:
                group.examples.append(f"{o.product_title[:70]} — {rejected[o.id][:60]}")
        else:
            kept.append(o)
    return kept, [group], f"Gemini eliminou {group.count} item(ns) que não eram o modelo exato"
