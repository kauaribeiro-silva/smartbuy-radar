from app.models import BenefitOption, CouponInfo, IdentifiedProduct, StoreOffer
from app.pipelines.coupon_hunter import compute_coupon_discount
from app.pipelines.financial_engine import compute_financial_verdicts
from app.utils.parsers import (
    evaluate_offer_trust,
    extract_product_metadata,
    parse_brl_price,
)


def test_parse_brl_price():
    assert parse_brl_price("R$ 4.299,90") == 4299.90
    assert parse_brl_price("4299.50") == 4299.50
    assert parse_brl_price("R$ 899,00") == 899.00
    assert parse_brl_price("Preço indisponível") is None


def test_extract_product_metadata_json_ld():
    html = """
    <html>
      <head>
        <meta property="og:title" content="Notebook Exemplo" />
        <script type="application/ld+json">
        {
          "@context": "https://schema.org/",
          "@type": "Product",
          "name": "Apple iPhone 15 128GB Preto",
          "sku": "IPH15-128-BLK",
          "gtin13": "195949036040",
          "brand": {"@type": "Brand", "name": "Apple"},
          "offers": {
            "@type": "Offer",
            "price": "4599.00",
            "availability": "https://schema.org/InStock",
            "seller": {"@type": "Organization", "name": "Amazon.com.br"}
          }
        }
        </script>
      </head>
      <body></body>
    </html>
    """
    meta = extract_product_metadata(html, "https://www.amazon.com.br/dp/B0CHX")
    assert meta["title"] == "Apple iPhone 15 128GB Preto"
    assert meta["brand"] == "Apple"
    assert meta["gtin"] == "195949036040"
    assert meta["sku"] == "IPH15-128-BLK"
    assert meta["price_brl"] == 4599.00
    assert meta["seller_name"] == "Amazon.com.br"
    assert meta["in_stock"] is True


def test_evaluate_offer_trust_filters():
    # Official store passes
    ok, score, _ = evaluate_offer_trust(
        product_title="Apple iPhone 15 128GB Preto",
        target_query="iPhone 15 128GB",
        store_name="Amazon Brasil",
        seller_name="Amazon.com.br",
        seller_rating=4.9,
        is_official_store=True,
        price_pix=4399.0,
        reference_price=4599.0,
    )
    assert ok is True
    assert score >= 95

    # Refurbished rejected
    ok_refurb, _, reason_refurb = evaluate_offer_trust(
        product_title="Apple iPhone 15 128GB Recondicionado Vitrine",
        target_query="iPhone 15 128GB",
        store_name="Mercado Livre",
        seller_name="Loja X",
        seller_rating=4.8,
        is_official_store=False,
        price_pix=3500.0,
        reference_price=4599.0,
    )
    assert ok_refurb is False
    assert "recondicionado" in reason_refurb.lower()

    # Accessory bait rejected
    ok_acc, _, _ = evaluate_offer_trust(
        product_title="Capinha Silicone MagSafe para iPhone 15 128GB",
        target_query="iPhone 15 128GB",
        store_name="Shopee Oficial",
        seller_name="Loja Oficial",
        seller_rating=4.9,
        is_official_store=True,
        price_pix=79.0,
        reference_price=4599.0,
    )
    assert ok_acc is False

    # Low rating 3P rejected
    ok_low, _, _ = evaluate_offer_trust(
        product_title="Apple iPhone 15 128GB",
        target_query="iPhone 15 128GB",
        store_name="Mercado Livre",
        seller_name="Vendedor Qualquer",
        seller_rating=4.2,
        is_official_store=False,
        price_pix=4300.0,
        reference_price=4599.0,
    )
    assert ok_low is False


def test_compute_coupon_discount():
    c_pct = CouponInfo(
        code="MELI10",
        store_name="Mercado Livre",
        discount_type="percent",
        discount_value=10.0,
        max_discount_brl=200.0,
        min_spend_brl=1000.0,
        status="CONFIRMADO_HOJE",
    )
    assert compute_coupon_discount(c_pct, 4000.0) == 200.0  # capped
    assert compute_coupon_discount(c_pct, 1500.0) == 150.0  # 10%
    assert compute_coupon_discount(c_pct, 800.0) == 0.0     # below min_spend


def test_compute_financial_verdicts():
    product = IdentifiedProduct(
        brand="Apple",
        exact_model="iPhone 15 128GB",
        search_query="iPhone 15 128GB",
        reference_price_brl=4600.0,
    )
    offers = [
        StoreOffer(
            id="1",
            store_name="KaBuM!",
            store_domain="kabum.com.br",
            seller_name="KaBuM!",
            is_official_store=True,
            trust_score=96,
            product_title="iPhone 15 128GB",
            product_url="https://www.kabum.com.br/produto/1",
            price_pix=4100.0,
            price_installment_total=4500.0,
            installments_count=10,
            installment_value=450.0,
            interest_free=True,
        ),
        StoreOffer(
            id="2",
            store_name="Amazon Brasil",
            store_domain="amazon.com.br",
            seller_name="Amazon.com.br",
            is_official_store=True,
            trust_score=99,
            product_title="iPhone 15 128GB",
            product_url="https://www.amazon.com.br/dp/1",
            price_pix=4300.0,
            price_installment_total=4300.0,
            installments_count=12,
            installment_value=358.33,
            interest_free=True,
        ),
    ]
    coupons = {
        "Amazon Brasil": [
            CouponInfo(
                code="AMZ100",
                store_name="Amazon Brasil",
                discount_type="fixed",
                discount_value=100.0,
                status="CONFIRMADO_HOJE",
            )
        ]
    }
    benefits = {
        "Amazon Brasil": [
            BenefitOption(
                platform="Livelo",
                store_name="Amazon Brasil",
                benefit_type="points",
                rate_label="5 pts/R$",
                points_per_brl=5.0,
            )
        ]
    }

    result = compute_financial_verdicts(
        product=product,
        offers=offers,
        coupons_by_store=coupons,
        benefits_by_store=benefits,
        mile_value_brl=30.0,
    )
    verdict_map = {v.verdict_type: v for v in result.verdicts}

    # Best Pix: KaBuM! at 4100 vs Amazon 4200
    assert verdict_map["best_pix"].offer.store_name == "KaBuM!"
    assert verdict_map["best_pix"].highlight_price_brl == 4100.0

    # Best Installment: Amazon at 4200 (12x) vs KaBuM! 4500 (10x)
    assert verdict_map["best_installment"].offer.store_name == "Amazon Brasil"
    assert verdict_map["best_installment"].highlight_price_brl == 4200.0

    # Best Smart Hack: Amazon 4200 - (4200 * 5 / 1000 * 30 = 630) = 3570
    assert verdict_map["best_smart_hack"].offer.store_name == "Amazon Brasil"
    assert verdict_map["best_smart_hack"].highlight_price_brl == 3570.0
    assert len(verdict_map["best_smart_hack"].how_to_buy_steps) >= 3
