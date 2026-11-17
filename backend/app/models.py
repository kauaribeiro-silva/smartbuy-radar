from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field


class IdentifiedProduct(BaseModel):
    brand: str = ""
    line: str = ""
    exact_model: str
    color: str = ""
    capacity_or_voltage: str = ""
    ean_gtin: str = ""
    sku_part_number: str = ""
    category: str = "Eletrônicos"
    search_query: str
    reference_price_brl: float | None = None
    reference_url: str | None = None
    image_url: str | None = None
    is_ambiguous: bool = False
    ambiguity_options: list[str] = Field(default_factory=list)
    confidence: float = 0.9


class CouponInfo(BaseModel):
    code: str
    store_name: str
    discount_type: Literal["percent", "fixed"] = "fixed"
    discount_value: float = 0.0
    max_discount_brl: float | None = None
    min_spend_brl: float | None = None
    status: Literal["CONFIRMADO_HOJE", "TESTAR_NO_CHECKOUT"] = "TESTAR_NO_CHECKOUT"
    rule_description: str = ""
    source: str = ""
    verified_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class BenefitOption(BaseModel):
    platform: str
    store_name: str
    benefit_type: Literal["points", "cashback", "native_perk"] = "cashback"
    rate_label: str
    points_per_brl: float = 0.0
    cashback_percent: float = 0.0
    estimated_points: int = 0
    estimated_return_brl: float = 0.0
    rule_summary: str = ""
    portal_url: str = ""


class StoreOffer(BaseModel):
    id: str
    store_name: str
    store_domain: str
    seller_name: str = ""
    seller_rating: float | None = None
    is_official_store: bool = False
    trust_score: int = 0
    trust_reason: str = ""
    product_title: str
    product_url: str
    in_stock: bool = True
    price_pix: float
    price_installment_total: float
    installments_count: int = 10
    installment_value: float = 0.0
    interest_free: bool = True
    applied_coupon: CouponInfo | None = None
    coupon_discount_brl: float = 0.0
    best_benefit: BenefitOption | None = None
    effective_net_price: float = 0.0
    all_coupons: list[CouponInfo] = Field(default_factory=list)
    all_benefits: list[BenefitOption] = Field(default_factory=list)
    # v2.0 — provenance & community signals
    source: str = ""
    deal_age_hours: float | None = None
    deal_note: str = ""
    embedded_coupon_code: str = ""
    page_coupon: CouponInfo | None = None


class SpecContract(BaseModel):
    """Trava A — contrato de especificação: o que um anúncio PRECISA ter / NÃO pode ter."""

    canonical_name: str
    mandatory_tokens: list[str] = Field(default_factory=list)
    forbidden_tokens: list[str] = Field(default_factory=list)
    source: str = "heuristic"


class BlockedGroup(BaseModel):
    category: str
    label: str
    count: int = 0
    examples: list[str] = Field(default_factory=list)


class ChannelStatus(BaseModel):
    name: str
    status: Literal["ok", "vazio", "bloqueado", "indisponivel"] = "ok"
    count: int = 0
    detail: str = ""


class TimingVerdict(BaseModel):
    level: Literal["green", "yellow", "red"]
    label: str
    headline: str
    reasons: list[str] = Field(default_factory=list)
    current_price: float | None = None
    lowest_seen: float | None = None
    median_seen: float | None = None
    history_points: int = 0
    watch_suggested: bool = False


class Recommendation(BaseModel):
    kind: Literal["alternative", "complement"]
    title: str
    query: str
    reason: str
    store: str = ""
    price_brl: float | None = None
    url: str = ""
    installments: str = ""
    delta_vs_main_brl: float | None = None
    delta_pct: float | None = None
    near_price: bool = False
    community_deal: dict[str, Any] | None = None


class VerdictCard(BaseModel):
    verdict_type: Literal["best_pix", "best_installment", "best_smart_hack"]
    title: str
    subtitle: str
    offer: StoreOffer
    highlight_price_brl: float
    net_price_brl: float
    savings_vs_reference_brl: float | None = None
    how_to_buy_steps: list[str] = Field(default_factory=list)


class RadarResult(BaseModel):
    product: IdentifiedProduct
    reference_price_brl: float | None = None
    mile_value_brl: float = 30.0
    offers: list[StoreOffer] = Field(default_factory=list)
    verdicts: list[VerdictCard] = Field(default_factory=list)
    rejected_offers_count: int = 0
    rejected_reasons: list[str] = Field(default_factory=list)
    # v2.0
    spec: SpecContract | None = None
    blocked_groups: list[BlockedGroup] = Field(default_factory=list)
    channels: list[ChannelStatus] = Field(default_factory=list)
    price_floor_brl: float | None = None
    semantic_check: str = ""
    timing: TimingVerdict | None = None
    recommendations: list[Recommendation] = Field(default_factory=list)
    tracked_product_id: int | None = None
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class SSEEvent(BaseModel):
    step: int
    stage: str
    status: Literal["running", "done", "warning", "error", "ambiguity", "result"]
    message: str
    progress_pct: int = 0
    data: dict[str, Any] | None = None
