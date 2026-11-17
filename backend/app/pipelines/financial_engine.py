from __future__ import annotations

from app.models import (
    BenefitOption,
    CouponInfo,
    IdentifiedProduct,
    RadarResult,
    StoreOffer,
    VerdictCard,
)
from app.pipelines.benefits_intel import calculate_benefit_value_brl
from app.pipelines.coupon_hunter import compute_coupon_discount


def _build_how_to_buy_steps(offer: StoreOffer, mode: str) -> list[str]:
    """Generate actionable step-by-step execution checklist so the user preserves tracking."""
    steps: list[str] = []

    if mode == "smart_hack" and offer.best_benefit:
        b = offer.best_benefit
        portal_hint = f" ({b.portal_url})" if b.portal_url else ""
        steps.append(
            f"1. Limpe o carrinho da {offer.store_name} antes de começar e acesse pelo portal {b.platform}{portal_hint} para ativar o rastreamento ({b.rate_label})."
        )
        steps.append(
            "2. Evite usar extensões concorrentes de cupom/cashback no navegador após o clique no portal para não sobrescrever o cookie de rastreamento."
        )
    else:
        steps.append(
            f"1. Acesse a oferta oficial na {offer.store_name} (vendedor verificado: {offer.seller_name or offer.store_name})."
        )

    step_num = len(steps) + 1
    if offer.applied_coupon:
        c = offer.applied_coupon
        steps.append(
            f"{step_num}. Adicione o produto ao carrinho e aplique o cupom `{c.code}` no checkout (desconto estimado de R$ {offer.coupon_discount_brl:,.2f} — {c.status.replace('_', ' ')})."
        )
        step_num += 1

    if mode == "installment":
        juros_txt = "sem juros" if offer.interest_free else "no cartão"
        steps.append(
            f"{step_num}. No pagamento, selecione Cartão de Crédito em {offer.installments_count}x de R$ {offer.installment_value:,.2f} {juros_txt}."
        )
    else:
        steps.append(
            f"{step_num}. Finalize o pagamento via Pix (ou 1x) para garantir o menor desembolso imediato."
        )

    if mode == "smart_hack" and offer.best_benefit:
        b = offer.best_benefit
        ret_txt = (
            f"{b.estimated_points:,} pontos (~R$ {b.estimated_return_brl:,.2f})"
            if b.estimated_points > 0
            else f"R$ {b.estimated_return_brl:,.2f} de cashback"
        )
        steps.append(
            f"{step_num + 1}. Tire print da tela de confirmação do pedido para garantir o crédito de {ret_txt} via {b.platform}."
        )

    return steps


def compute_financial_verdicts(
    product: IdentifiedProduct,
    offers: list[StoreOffer],
    coupons_by_store: dict[str, list[CouponInfo]],
    benefits_by_store: dict[str, list[BenefitOption]],
    mile_value_brl: float = 30.0,
    rejected_count: int = 0,
    rejected_reasons: list[str] | None = None,
) -> RadarResult:
    """
    Pipeline 5: Calculate best coupon, best points/cashback portal, Effective Net Price,
    and build the 3 Verdict Cards (Best Pix, Best Installment, Best Smart Hack).
    """
    enriched_offers: list[StoreOffer] = []

    for offer in offers:
        store_coupons = coupons_by_store.get(offer.store_name, [])
        store_benefits = benefits_by_store.get(offer.store_name, [])

        # Pick best coupon for Pix price
        best_coupon: CouponInfo | None = None
        best_discount = 0.0
        for c in store_coupons:
            disc = compute_coupon_discount(c, offer.price_pix)
            if disc > best_discount:
                best_discount = disc
                best_coupon = c

        price_after_coupon_pix = round(max(0.0, offer.price_pix - best_discount), 2)

        # Evaluate each benefit against price_after_coupon_pix
        evaluated_benefits: list[BenefitOption] = []
        best_benefit: BenefitOption | None = None
        for b in store_benefits:
            pts, ret_brl = calculate_benefit_value_brl(
                net_price_brl=price_after_coupon_pix,
                points_per_brl=b.points_per_brl,
                cashback_percent=b.cashback_percent,
                mile_value_brl=mile_value_brl,
            )
            b_copy = b.model_copy(
                update={
                    "estimated_points": pts,
                    "estimated_return_brl": ret_brl,
                }
            )
            evaluated_benefits.append(b_copy)
            if best_benefit is None or b_copy.estimated_return_brl > best_benefit.estimated_return_brl:
                best_benefit = b_copy

        benefit_return = best_benefit.estimated_return_brl if best_benefit else 0.0
        effective_net = round(max(0.0, price_after_coupon_pix - benefit_return), 2)

        # Recompute installment value after coupon if applicable
        inst_discount = compute_coupon_discount(best_coupon, offer.price_installment_total) if best_coupon else 0.0
        inst_total_after = round(max(0.0, offer.price_installment_total - inst_discount), 2)
        inst_val_after = round(inst_total_after / max(1, offer.installments_count), 2)

        enriched_offers.append(
            offer.model_copy(
                update={
                    "applied_coupon": best_coupon,
                    "coupon_discount_brl": best_discount,
                    "best_benefit": best_benefit,
                    "effective_net_price": effective_net,
                    "installment_value": inst_val_after,
                    "all_coupons": store_coupons,
                    "all_benefits": evaluated_benefits,
                }
            )
        )

    verdicts: list[VerdictCard] = []
    ref_price = product.reference_price_brl

    if enriched_offers:
        # 1. Campeão À Vista (Pix / 1x)
        best_pix_offer = min(
            enriched_offers,
            key=lambda o: round(o.price_pix - o.coupon_discount_brl, 2),
        )
        pix_final = round(best_pix_offer.price_pix - best_pix_offer.coupon_discount_brl, 2)
        verdicts.append(
            VerdictCard(
                verdict_type="best_pix",
                title="Campeão À Vista (Pix)",
                subtitle=f"Menor desembolso imediato na {best_pix_offer.store_name}",
                offer=best_pix_offer,
                highlight_price_brl=pix_final,
                net_price_brl=best_pix_offer.effective_net_price,
                savings_vs_reference_brl=(
                    round(ref_price - pix_final, 2) if ref_price and ref_price > pix_final else None
                ),
                how_to_buy_steps=_build_how_to_buy_steps(best_pix_offer, "pix"),
            )
        )

        # 2. Campeão Parcelado (Sem Juros)
        interest_free_offers = [o for o in enriched_offers if o.interest_free] or enriched_offers
        best_inst_offer = min(
            interest_free_offers,
            key=lambda o: (
                round(o.price_installment_total - (compute_coupon_discount(o.applied_coupon, o.price_installment_total) if o.applied_coupon else 0.0), 2),
                -o.installments_count,
            ),
        )
        inst_coupon_disc = (
            compute_coupon_discount(best_inst_offer.applied_coupon, best_inst_offer.price_installment_total)
            if best_inst_offer.applied_coupon
            else 0.0
        )
        inst_final_total = round(max(0.0, best_inst_offer.price_installment_total - inst_coupon_disc), 2)
        verdicts.append(
            VerdictCard(
                verdict_type="best_installment",
                title="Campeão Parcelado",
                subtitle=(
                    f"{best_inst_offer.installments_count}x de R$ {best_inst_offer.installment_value:,.2f} "
                    f"{'sem juros' if best_inst_offer.interest_free else ''} na {best_inst_offer.store_name}"
                ),
                offer=best_inst_offer,
                highlight_price_brl=inst_final_total,
                net_price_brl=round(
                    max(0.0, inst_final_total - (best_inst_offer.best_benefit.estimated_return_brl if best_inst_offer.best_benefit else 0.0)),
                    2,
                ),
                savings_vs_reference_brl=(
                    round(ref_price - inst_final_total, 2) if ref_price and ref_price > inst_final_total else None
                ),
                how_to_buy_steps=_build_how_to_buy_steps(best_inst_offer, "installment"),
            )
        )

        # 3. Campeão Estratégico (Smart Hack)
        best_hack_offer = min(enriched_offers, key=lambda o: o.effective_net_price)
        hack_benefit_label = (
            f" + {best_hack_offer.best_benefit.platform} ({best_hack_offer.best_benefit.rate_label})"
            if best_hack_offer.best_benefit
            else ""
        )
        verdicts.append(
            VerdictCard(
                verdict_type="best_smart_hack",
                title="Campeão Estratégico (Smart Hack)",
                subtitle=f"Menor Custo Efetivo Real: {best_hack_offer.store_name}{hack_benefit_label}",
                offer=best_hack_offer,
                highlight_price_brl=best_hack_offer.effective_net_price,
                net_price_brl=best_hack_offer.effective_net_price,
                savings_vs_reference_brl=(
                    round(ref_price - best_hack_offer.effective_net_price, 2)
                    if ref_price and ref_price > best_hack_offer.effective_net_price
                    else None
                ),
                how_to_buy_steps=_build_how_to_buy_steps(best_hack_offer, "smart_hack"),
            )
        )

    # Sort table by effective_net_price ascending
    enriched_offers.sort(key=lambda o: o.effective_net_price)

    return RadarResult(
        product=product,
        reference_price_brl=ref_price,
        mile_value_brl=mile_value_brl,
        offers=enriched_offers,
        verdicts=verdicts,
        rejected_offers_count=rejected_count,
        rejected_reasons=rejected_reasons or [],
    )
