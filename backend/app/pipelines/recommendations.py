"""Timing Thermometer (Buy Now vs. Wait) & Live Cross-Sell / Alternative Recommendations."""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from app.models import (
    IdentifiedProduct,
    Recommendation,
    SpecContract,
    StoreOffer,
    TimingVerdict,
)
from app.utils.browser import DEFAULT_HEADERS
from app.utils.parsers import parse_brl_price

logger = logging.getLogger(__name__)


def compute_timing_verdict(
    offers: list[StoreOffer],
    history_rows: list[dict[str, Any]],
    lowest_seen_db: float | None = None,
) -> TimingVerdict:
    """
    Compute the Timing Thermometer:
    - green: MOMENTO IDEAL DE COMPRA
    - yellow: PREÇO NORMAL DE MERCADO
    - red: PREÇO INFLADO — AGUARDE
    """
    if not offers:
        return TimingVerdict(
            level="yellow",
            label="SEM ESTOQUE CONFIÁVEL NO MOMENTO",
            headline="Ative o monitoramento para receber alerta assim que surgir oferta validada.",
            reasons=["Nenhuma oferta passou nas 3 travas anti-downgrade agora."],
            watch_suggested=True,
        )

    pix_prices = sorted(o.price_pix for o in offers if o.price_pix > 0)
    eff_prices = sorted(o.effective_net_price for o in offers if o.effective_net_price > 0)
    best_pix = pix_prices[0]
    best_eff = eff_prices[0] if eff_prices else best_pix
    market_median = round(statistics.median(pix_prices), 2)

    hist_prices = [
        float(r["price_pix"])
        for r in history_rows
        if r.get("price_pix") is not None and float(r["price_pix"]) > 0
    ]
    all_seen = hist_prices + [best_pix]
    lowest_seen = round(
        min([lowest_seen_db] + all_seen) if lowest_seen_db else min(all_seen), 2
    )
    median_seen = round(statistics.median(all_seen), 2)

    has_community_deal = any(o.source in ("Pelando", "Promobit") for o in offers[:3])
    best_offer = min(offers, key=lambda o: o.effective_net_price)
    hack_savings_pct = (
        round(((best_pix - best_eff) / best_pix) * 100.0, 1) if best_pix > 0 else 0.0
    )
    vs_market_pct = (
        round(((market_median - best_eff) / market_median) * 100.0, 1)
        if market_median > 0
        else 0.0
    )

    reasons: list[str] = []

    # RED: current price is > 8% above historical lowest seen in SQLite
    if lowest_seen and best_pix > lowest_seen * 1.08:
        diff = round(best_pix - lowest_seen, 2)
        reasons.append(
            f"O menor preço atual (R$ {best_pix:,.2f}) está R$ {diff:,.2f} acima do menor valor já registrado no seu radar (R$ {lowest_seen:,.2f})."
        )
        reasons.append(
            "Recomendamos definir um Preço Alvo no topo da página e aguardar nova queda ou cupom."
        )
        return TimingVerdict(
            level="red",
            label="PREÇO INFLADO — AGUARDE",
            headline=f"Este produto já esteve R$ {diff:,.2f} mais barato recentemente.",
            reasons=reasons,
            current_price=best_pix,
            lowest_seen=lowest_seen,
            median_seen=median_seen,
            history_points=len(hist_prices),
            watch_suggested=True,
        )

    # GREEN: at historical floor + strong community deal or >= 6% below market median / strong hack
    if (
        has_community_deal
        or vs_market_pct >= 6.0
        or hack_savings_pct >= 6.0
        or (len(hist_prices) >= 2 and best_pix <= lowest_seen * 1.01)
    ):
        if best_pix <= lowest_seen * 1.01:
            reasons.append(
                f"Preço à vista (R$ {best_pix:,.2f}) está no piso histórico monitorado pelo radar."
            )
        if vs_market_pct >= 3.0:
            reasons.append(
                f"Custo efetivo real (R$ {best_eff:,.2f}) está {vs_market_pct:.1f}% abaixo da mediana das lojas (R$ {market_median:,.2f})."
            )
        if has_community_deal:
            reasons.append(
                "Há promoção ativa validada nas comunidades (Pelando / Promobit) nas últimas horas."
            )
        if best_offer.applied_coupon:
            reasons.append(
                f"Cupom ativo identificado ({best_offer.applied_coupon.code}) somado a {best_offer.best_benefit.platform if best_offer.best_benefit else 'desconto no Pix'}."
            )
        return TimingVerdict(
            level="green",
            label="MOMENTO IDEAL DE COMPRA",
            headline=f"Piso de mercado atingido com Custo Efetivo Real de R$ {best_eff:,.2f}.",
            reasons=reasons or ["Oferta no piso das lojas confiáveis com retorno de pontos/cashback."],
            current_price=best_pix,
            lowest_seen=lowest_seen,
            median_seen=median_seen,
            history_points=len(hist_prices),
            watch_suggested=False,
        )

    # YELLOW: standard market price
    reasons.append(
        f"Preço atual (R$ {best_pix:,.2f}) está alinhado à mediana de mercado (R$ {market_median:,.2f})."
    )
    reasons.append(
        f"Usando o Smart Hack ({best_offer.store_name}), o custo efetivo cai para R$ {best_eff:,.2f}."
    )
    return TimingVerdict(
        level="yellow",
        label="PREÇO NORMAL DE MERCADO",
        headline="Vale a pena se tiver pressa e executar o combo de pontos/cashback.",
        reasons=reasons,
        current_price=best_pix,
        lowest_seen=lowest_seen,
        median_seen=median_seen,
        history_points=len(hist_prices),
        watch_suggested=True,
    )


def _candidate_specs_for_product(
    product: IdentifiedProduct, spec: SpecContract
) -> list[dict[str, str]]:
    """Select 1 direct alternative + 2 ecosystem complements tailored to the searched product."""
    low = f"{spec.canonical_name} {product.search_query} {product.category}".lower()

    if "switch" in low:
        return [
            {
                "kind": "alternative",
                "query": "Console PlayStation 5 Slim Digital 1TB",
                "reason": "Alternativa de nova geração para comparar custo-benefício na mesma faixa gamer.",
            },
            {
                "kind": "complement",
                "query": "Cartao MicroSD SanDisk 256GB Nintendo Switch",
                "reason": "Complemento essencial para expandir os 64GB internos do Switch OLED.",
            },
            {
                "kind": "complement",
                "query": "Controle Nintendo Switch Pro Controller Original",
                "reason": "Controle oficial ergonômico para jogar o Switch OLED na TV (Dock).",
            },
        ]

    if "monitor" in low:
        return [
            {
                "kind": "alternative",
                "query": "Monitor Gamer LG UltraGear 24 180Hz IPS 24GS60F",
                "reason": "Concorrente direto IPS Full HD com 180Hz na mesma faixa de preço.",
            },
            {
                "kind": "complement",
                "query": "Suporte Articulado Monitor Pistao a Gas ELG F80N",
                "reason": "Braço articulado mais vendido do Brasil para liberar espaço na mesa gamer.",
            },
            {
                "kind": "complement",
                "query": "Cabo DisplayPort 1.4 8K 144Hz 240Hz",
                "reason": "Garante 144Hz/180Hz + G-Sync/FreeSync com largura de banda máxima.",
            },
        ]

    if "iphone" in low:
        return [
            {
                "kind": "alternative",
                "query": "Smartphone Samsung Galaxy S24 256GB 5G",
                "reason": "Alternativa topo de linha com 256GB para comparar custo efetivo.",
            },
            {
                "kind": "complement",
                "query": "Carregador Apple USB-C 20W Original",
                "reason": "Fonte original de recarga rápida (não inclusa na caixa do iPhone).",
            },
            {
                "kind": "complement",
                "query": "Fone Apple AirPods Pro 2 USB-C",
                "reason": "Integração nativa com ecossistema iOS e cancelamento de ruído ativo.",
            },
        ]

    if "ps5" in low or "playstation" in low:
        return [
            {
                "kind": "alternative",
                "query": "Console Xbox Series X 1TB",
                "reason": "Concorrente direto de 4K/120Hz para comparar ofertas ativas.",
            },
            {
                "kind": "complement",
                "query": "Controle Sony DualSense PS5 Sem Fio",
                "reason": "Segundo controle oficial DualSense em promoção para multiplayer.",
            },
            {
                "kind": "complement",
                "query": "SSD NVMe M.2 1TB Kingston Fury Renegade PS5",
                "reason": "SSD com dissipador compatível com a expansão oficial do PS5.",
            },
        ]

    if "notebook" in low or "macbook" in low:
        return [
            {
                "kind": "alternative",
                "query": "Notebook Gamer Acer Nitro V15 RTX 3050",
                "reason": "Alternativa com GPU dedicada para comparar desempenho por real gasto.",
            },
            {
                "kind": "complement",
                "query": "Mouse Sem Fio Logitech MX Anywhere 3S",
                "reason": "Periférico de alta precisão para produtividade e transporte.",
            },
            {
                "kind": "complement",
                "query": "SSD Externo Portatil Kingston XS1000 1TB",
                "reason": "Backup rápido USB-C 1.050MB/s para expandir armazenamento.",
            },
        ]

    return [
        {
            "kind": "alternative",
            "query": f"{product.brand} {product.category} Pro".strip(),
            "reason": "Versão superior na mesma linha para comparar se a diferença de preço compensa.",
        },
        {
            "kind": "complement",
            "query": "Filtro de Linha Clamper Energia 5 Protetor Surto",
            "reason": "Proteção contra surtos elétricos recomendada para eletrônicos.",
        },
    ]


async def _fetch_quick_live_offer(
    cand: dict[str, str], main_price: float | None
) -> Recommendation | None:
    """Fetch the best live offer on Zoom/Promobit for a recommendation candidate."""
    q = cand["query"]
    search_url = f"https://www.zoom.com.br/search?q={quote_plus(q)}"
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=8.0
        ) as client:
            resp = await client.get(search_url)
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.text, "lxml")
            nd = soup.find("script", id="__NEXT_DATA__")
            if not nd or not nd.string:
                return None
            hits = (
                json.loads(nd.string)
                .get("props", {})
                .get("initialReduxState", {})
                .get("hits", {})
                .get("hits", [])
            )
            if not isinstance(hits, list) or not hits:
                return None

            top = hits[0]
            title = str(top.get("name") or q).strip()
            price = parse_brl_price(top.get("price"))
            if not price:
                return None
            store = (
                top.get("merchantName")
                or (top.get("bestOffer") or {}).get("merchantName")
                or "Zoom"
            )
            raw_url = str(top.get("url") or "")
            url = (
                f"https://www.zoom.com.br{raw_url}"
                if raw_url.startswith("/")
                else (raw_url or search_url)
            )
            inst = top.get("installments") or {}
            inst_str = (
                f"{inst.get('amount_months')}x de R$ {float(inst.get('price', 0)):,.2f}"
                if inst.get("amount_months") and inst.get("price")
                else "À vista no Pix"
            )

            delta_brl = round(price - main_price, 2) if main_price else None
            delta_pct = (
                round(((price - main_price) / main_price) * 100.0, 1)
                if main_price and main_price > 0
                else None
            )
            near = bool(delta_pct is not None and abs(delta_pct) <= 20.0)

            return Recommendation(
                kind=cand["kind"],  # type: ignore[arg-type]
                title=title,
                query=q,
                reason=cand["reason"],
                store=str(store),
                price_brl=price,
                url=url,
                installments=inst_str,
                delta_vs_main_brl=delta_brl,
                delta_pct=delta_pct,
                near_price=near,
            )
    except Exception as exc:
        logger.debug("Quick recommendation lookup failed for %s: %s", q, exc)
        return None


async def build_recommendations(
    product: IdentifiedProduct,
    spec: SpecContract,
    current_best_price: float | None,
) -> list[Recommendation]:
    """Build live Cross-Sell & Alternative recommendations with real prices."""
    candidates = _candidate_specs_for_product(product, spec)
    results = await asyncio.gather(
        *[_fetch_quick_live_offer(c, current_best_price) for c in candidates],
        return_exceptions=True,
    )
    recs: list[Recommendation] = []
    for r in results:
        if isinstance(r, Recommendation):
            recs.append(r)
    return recs
