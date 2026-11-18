"""StrictSpecValidator — anti-downgrade guard for SmartBuy Radar v2.0.

Trava A: Spec contract (canonical name + mandatory tokens + forbidden tokens).
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
from typing import Any, Optional

from google import genai
from google.genai import types

from app.config import settings
from app.models import BlockedGroup, SpecContract, StoreOffer
from app.utils.parsers import extract_json_from_llm

logger = logging.getLogger(__name__)

PRICE_FLOOR_RATIO = 0.55

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


USED_TOKENS = (
    "usado", "usada", "seminovo", "seminova", "recondicionado", "recondicionada", "vitrine",
    "mostruario", "refurbished", "defeito", "defeituoso", "avariado", "sucata", "retirada de pecas",
    "open box", "caixa aberta", "embalagem aberta",
)

ACCESSORY_TOKENS = (
    "capa", "capinha", "pelicula", "case", "estojo", "bolsa", "protetor de tela", "adesivo", "skin",
    "caixa vazia", "kit acessorios", "avulso", "avulsa", "aluguel", "conta primaria", "conta secundaria",
    "gift card", "cartao presente", "suporte vertical", "suporte de parede", "base carregadora",
    "base de carregamento", "bateria avulsa", "manual", "carcaca", "grip", "tampa lateral", "faceplate",
    "cooler para", "unidade de disco", "leitor de disco para",
)

# Family rules only forbid TRUE downgrades or other generations — never standard revisions like PS5 Slim/Digital
FAMILY_RULES: list[dict[str, Any]] = [
    {
        "name": "switch",
        "triggers": {"switch"},
        "anchors": ["switch"],
        # "lite" is a portable-only downgrade; v1/v2/switch 2 are different models when searching OLED
        "downgrades_Always": {"lite"},
        "exclusive_variants": {"oled", "lite"},
        "phrases": ["switch 2", "joy con avulso", "par de joy con", "dock avulsa"],
    },
    {
        "name": "playstation5",
        "triggers": {"ps5"},
        "anchors": ["ps5"],
        # Do NOT forbid "slim" or "digital" on a general PS5 search — those are the standard ~R$ 3.300-4.100 consoles!
        "downgrades_Always": {"ps4", "ps3"},
        "exclusive_variants": {"pro"},
        "phrases": [
            "controle dualsense", "dualsense wireless", "headset pulse", "camera hd",
            "tampa do console", "suporte vertical", "base de carregamento", "leitor de disco",
            "unidade de disco", "playstation portal",
        ],
    },
    {
        "name": "iphone",
        "triggers": {"iphone"},
        "anchors": ["iphone"],
        "downgrades_Always": set(),
        "exclusive_variants": {"pro", "max", "plus", "mini", "se", "air"},
        "phrases": [],
    },
    {
        "name": "galaxy",
        "triggers": {"galaxy"},
        "anchors": ["galaxy"],
        "downgrades_Always": set(),
        "exclusive_variants": {"ultra", "plus", "fe", "edge"},
        "phrases": [],
    },
    {
        "name": "xbox",
        "triggers": {"xbox"},
        "anchors": ["xbox"],
        "downgrades_Always": {"one", "360"},
        "exclusive_variants": set(),
        "phrases": ["controle sem fio xbox", "controle xbox"],
    },
    {
        "name": "macbook",
        "triggers": {"macbook"},
        "anchors": ["macbook"],
        "downgrades_Always": set(),
        "exclusive_variants": {"air", "pro"},
        "phrases": [],
    },
    {
        "name": "ipad",
        "triggers": {"ipad"},
        "anchors": ["ipad"],
        "downgrades_Always": set(),
        "exclusive_variants": {"air", "pro", "mini"},
        "phrases": [],
    },
    {
        "name": "airpods",
        "triggers": {"airpods"},
        "anchors": ["airpods"],
        "downgrades_Always": set(),
        "exclusive_variants": {"pro", "max"},
        "phrases": [],
    },
    {
        "name": "gpu",
        "triggers": {"rtx", "gtx", "rx"},
        "anchors": [],
        "downgrades_Always": set(),
        "exclusive_variants": {"ti", "super", "xt", "xtx"},
        "phrases": [],
    },
]

ALL_VARIANT_WORDS: set[str] = {"lite", "oled", "pro", "max", "plus", "ultra", "mini", "fe", "ti", "super", "slim"}

FAMILY_ANCHORS = {
    "iphone", "galaxy", "redmi", "poco", "moto", "playstation", "ps", "xbox", "switch", "pixel",
    "note", "edge", "rtx", "gtx", "rx", "ryzen", "macbook", "ipad", "airpods", "watch", "kindle",
    "echo", "tab", "mi", "monitor", "tv",
}

NOISE_TOKENS = {
    "hdr10", "hdr400", "hdr600", "hdr", "4k", "8k", "5g", "4g", "1080p", "1440p", "2160p", "720p",
    "fhd", "uhd", "qhd", "wqhd", "full", "hd", "fullhd", "usb", "hdmi", "dp", "displayport", "vga",
    "led", "wled", "ips", "va", "tn", "wifi", "nfc", "gsync", "freesync", "sync", "adaptive",
    "nvidia", "amd", "srgb", "vesa", "bivolt", "mprt", "gtg", "mbr", "nativo", "widescreen",
}

STOPWORDS = {
    "de", "da", "do", "das", "dos", "para", "com", "sem", "e", "o", "a", "os", "as", "em", "por",
    "novo", "nova", "original", "lacrado", "nacional", "garantia", "nota", "fiscal", "preto", "branco",
    "azul", "vermelho", "cinza", "verde", "rosa", "roxo", "dourado", "prata", "cor",
}

CATEGORY_WORDS = {
    "console", "video", "game", "videogame", "aparelho", "smartphone", "celular", "notebook",
    "monitor", "gamer", "tv", "smart", "tela", "polegadas", "pol",
}

# Protected tokens that LLM is NEVER allowed to mark as forbidden unless the user didn't ask for them
NEVER_FORBID_ON_CONSOLE = {
    "slim", "digital", "standard", "edicao", "edition", "bundle", "jogo", "jogos",
    "825gb", "1tb", "2tb", "branco", "preto", "sony", "nintendo", "microsoft", "bivolt",
    "com leitor", "sem leitor", "midia fisica", "returnal", "ratchet", "spider", "astro",
    "mario", "wonder", "kart", "zelda",
}

UNIT_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)?(?:hz|ms|pol|polegadas|w|v|mp|p|mah)$")
CAPACITY_RE = re.compile(r"^\d+(?:gb|tb|mb)$")

USED_SET = {norm(t) for t in USED_TOKENS}
ACCESSORY_SET = {norm(t) for t in ACCESSORY_TOKENS}


def _token_present(token: str, tokens: set[str], flat_title: str) -> bool:
    t = norm(token).strip()
    if not t:
        return True
    if t in CATEGORY_WORDS:
        return True
    if " " in t:
        return f" {' '.join(tokenize(t))} " in flat_title
    if t in tokens:
        return True
    # Handle screen sizes like "27" matching "27pol", "27''", "27.0", "23.8" for 24
    if t.isdigit() and len(t) == 2:
        for tok in tokens:
            if tok == t or tok.startswith(t) and tok[len(t):] in UNITS:
                return True
        if t == "24" and ("23.8" in tokens or "23.8pol" in tokens or "24.5" in tokens):
            return True
    # Handle refresh rate like "144hz" matching "144" or "165hz" / "180hz" on generic monitor searches
    if t.endswith("hz"):
        num_part = t[:-2]
        if num_part in tokens:
            return True
    if t.replace(".", "").isdigit():
        for tok in tokens:
            if tok.startswith(t) and tok[len(t):] in UNITS:
                return True
    compact_title = flat_title.replace(" ", "")
    if len(t) >= 4 and t in compact_title:
        return True
    if len(t) >= 3 and re.search(r"[a-z]", t) and re.search(r"\d", t) and t in compact_title:
        return True
    if t in ("agon", "ultragear", "odyssey", "nitro") and any(
        code in compact_title for code in ("g50", "27g", "24g", "g3", "g4", "g5", "vg2", "kg2")
    ):
        return True
    return False


def _forbidden_hit(token: str, tokens: set[str], flat_title: str) -> bool:
    t = norm(token).strip()
    if not t:
        return False
    if " " in t:
        return f" {' '.join(tokenize(t))} " in flat_title
    return t in tokens


def heuristic_contract(query: str, canonical_name: str | None = None) -> SpecContract:
    qt = tokenize(query)
    qset = set(qt)
    qflat = flat(query)
    mandatory: "OrderedDict[str, None]" = OrderedDict()

    # Check if query has a specific alphanumeric model code (e.g. 24g50f, s24, m3)
    has_specific_model_code = any(
        re.search(r"[a-z]", t) and re.search(r"\d", t) and len(t) >= 5 and t not in NOISE_TOKENS and not UNIT_TOKEN_RE.match(t) and not CAPACITY_RE.match(t)
        for t in qt
    )

    for i, t in enumerate(qt):
        if t in NOISE_TOKENS or t in STOPWORDS or t in CATEGORY_WORDS:
            continue
        if UNIT_TOKEN_RE.match(t):
            # Keep refresh rate (e.g. 144hz) ONLY when user is doing a generic spec search without model code
            if not has_specific_model_code and t.endswith("hz"):
                mandatory[t] = None
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
            nxt = qt[i + 1] if i + 1 < len(qt) else ""
            is_screen_size = (
                len(t) == 2
                and 18 <= int(t) <= 85
                and bool(qset & {"monitor", "tv", "tela", "polegadas", "pol", "agon", "ultragear", "odyssey"})
            )
            if is_screen_size or len(t) >= 3 or prev in FAMILY_ANCHORS or nxt in ("polegadas", "pol", "hz"):
                if is_screen_size or not has_specific_model_code:
                    mandatory[t] = None
        elif len(t) >= 3 and t not in CATEGORY_WORDS:
            # Brand / product line token (e.g. nintendo, switch, aoc, agon, iphone, samsung)
            mandatory[t] = None

    forbidden: "OrderedDict[str, None]" = OrderedDict()
    for w in USED_SET | ACCESSORY_SET:
        if " " in w:
            if w not in qflat:
                forbidden[w] = None
        elif w not in qset:
            forbidden[w] = None

    for fam in FAMILY_RULES:
        if fam["triggers"] & qset:
            for a in fam["anchors"]:
                mandatory[a] = None
            for d in sorted(fam["downgrades_Always"]):
                if d not in qset:
                    forbidden[d] = None
            # Only forbid other exclusive_variants if the user explicitly asked for a specific variant,
            # OR if a variant is a higher/different tier (like "pro" when searching base "ps5" or "iphone 15")
            for v in sorted(fam["exclusive_variants"]):
                if v not in qset:
                    forbidden[v] = None
            if fam["name"] == "switch" and "oled" in qset:
                forbidden["v1"] = None
                forbidden["v2"] = None
            for p in fam["phrases"]:
                if p not in qflat:
                    forbidden[p] = None
            break

    for tok in list(forbidden):
        if tok in qset or tok in mandatory or tok in NEVER_FORBID_ON_CONSOLE:
            forbidden.pop(tok, None)

    if not mandatory:
        meaningful = [
            t for t in qt
            if t not in STOPWORDS and t not in NOISE_TOKENS and len(t) >= 2
        ]
        for t in meaningful[:3]:
            mandatory[t] = None

    return SpecContract(
        canonical_name=(canonical_name or query).strip(),
        mandatory_tokens=list(mandatory.keys()),
        forbidden_tokens=list(forbidden.keys()),
        source="heurística",
    )


async def build_spec_contract(query: str, canonical_name: str | None = None) -> SpecContract:
    """Trava A. Gemini decomposes the query; result is safely merged with the heuristic contract."""
    base = heuristic_contract(query, canonical_name)
    if not settings.gemini_api_key:
        return base

    prompt = (
        "Você é um validador estrito de catálogo de e-commerce brasileiro.\n"
        f"Termo buscado pelo usuário: '{query}'.\n"
        "Decomponha em um contrato de especificação. Retorne EXCLUSIVAMENTE JSON:\n"
        '{"canonical_name": "nome exato e completo do produto",\n'
        ' "mandatory_tokens": ["palavras/códigos que OBRIGATORIAMENTE precisam estar no título do anúncio"],\n'
        ' "forbidden_tokens": ["variações inferiores (ex: lite quando pediu oled), usados e acessórios avulsos"]}\n'
        "REGRAS CRÍTICAS:\n"
        "1. NUNCA coloque 'slim', 'digital', 'standard', 'bundle', 'jogo' ou '1tb' em forbidden_tokens quando o usuário buscar 'PlayStation 5' ou 'PS5' (pois o PS5 Slim e PS5 Digital são o console padrão!).\n"
        "2. NUNCA coloque palavras genéricas como 'console', 'polegadas', 'aparelho' em mandatory_tokens.\n"
        "3. Use minúsculas."
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
        if not n or n in CATEGORY_WORDS or n in STOPWORDS or n in NOISE_TOKENS:
            continue
        if all(part in qset for part in tokenize(n)) and n not in mandatory:
            mandatory.append(n)

    forbidden = list(base.forbidden_tokens)
    for tok in parsed.get("forbidden_tokens") or []:
        n = norm(str(tok)).strip()
        if not n or n in mandatory or n in NEVER_FORBID_ON_CONSOLE:
            continue
        if any(part in qset or part in NEVER_FORBID_ON_CONSOLE for part in tokenize(n)):
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
    fresh = heuristic_contract(canonical_name)
    return SpecContract(
        canonical_name=canonical_name,
        mandatory_tokens=fresh.mandatory_tokens,
        forbidden_tokens=fresh.forbidden_tokens,
        source="memória",
    )


def classify_forbidden(token: str) -> tuple[str, str]:
    t = norm(token)
    if t in USED_SET:
        return "usado", f"Usado/recondicionado ('{t}')"
    if t in ACCESSORY_SET:
        return "acessório", f"Acessório/serviço ('{t}')"
    return "variante", f"Versão diferente ('{t}')"


def check_title(title: str, spec: SpecContract, query_has_para: bool = False) -> Optional[tuple[str, str]]:
    tokens = set(tokenize(title))
    ft = flat(title)
    norm_title = norm(title)

    for tok in spec.forbidden_tokens:
        if _forbidden_hit(tok, tokens, ft):
            return classify_forbidden(tok)

    # Reject standalone game discs ("Jogo PS5 ...", "Game PS5 ...") when searching for a console
    if "ps5" in spec.mandatory_tokens or "switch" in spec.mandatory_tokens or "xbox" in spec.mandatory_tokens:
        if re.match(r"^\s*(?:jogo|game|midia fisica|pelicula|capa|case|controle|suporte|base|cabo|headset| bolsa|estojo)\b", norm_title):
            return "acessório", "Jogo avulso ou acessório"

    if not query_has_para:
        anchor_tokens = [t for t in tokenize(spec.canonical_name) if len(t) >= 4 and t not in STOPWORDS and t not in CATEGORY_WORDS][:2]
        for a in anchor_tokens:
            if re.search(rf"\b(?:para|p/|compativel com)\s+(?:o\s+|a\s+)?(?:\w+\s+){{0,2}}{re.escape(a)}\b", norm_title):
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


def apply_price_floor(
    items: list[dict[str, Any]],
    reference_price: float | None = None,
) -> tuple[list[dict[str, Any]], list[BlockedGroup], float | None]:
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


async def semantic_double_check(
    spec: SpecContract, offers: list[StoreOffer], top_n: int = 10
) -> tuple[list[StoreOffer], list[BlockedGroup], str]:
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
        "Você é um auditor de catálogo. O usuário buscou:\n"
        f"'{spec.canonical_name}' (tokens obrigatórios: {spec.mandatory_tokens}).\n"
        "Abaixo estão os menores preços encontrados. Elimine APENAS itens que sejam claramente outro produto, "
        "versão inferior proibida (ex: Switch Lite quando pediu Switch OLED), acessório avulso, jogo avulso ou usado.\n"
        "IMPORTANTE: se o usuário buscou 'PlayStation 5' ou 'Console PlayStation 5', modelos 'PS5 Slim', 'PS5 Digital' e bundles com jogos SÃO VÁLIDOS e NÃO devem ser eliminados!\n"
        f"Itens: {json.dumps(listing, ensure_ascii=False)}\n"
        'Responda EXCLUSIVAMENTE JSON: {"rejected": [{"id": "...", "motivo": "..."}]} '
        "(lista vazia se todos forem válidos)."
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
        return offers, [], "Gemini confirmou: ofertas validadas"

    group = BlockedGroup(category="verificação IA", label="Eliminado na dupla checagem semântica (Gemini)")
    kept: list[StoreOffer] = []
    for o in offers:
        if o.id in rejected:
            group.count += 1
            if len(group.examples) < 3:
                group.examples.append(f"{o.product_title[:70]} — {rejected[o.id][:60]}")
        else:
            kept.append(o)
    return kept, [group], f"Gemini eliminou {group.count} item(ns) divergente(s)"
