export interface IdentifiedProduct {
  brand: string;
  line: string;
  exact_model: string;
  color: string;
  capacity_or_voltage: string;
  ean_gtin: string;
  sku_part_number: string;
  category: string;
  search_query: string;
  reference_price_brl: number | null;
  reference_url: string | null;
  image_url: string | null;
  is_ambiguous: boolean;
  ambiguity_options: string[];
  confidence: number;
}

export interface CouponInfo {
  code: string;
  store_name: string;
  discount_type: "percent" | "fixed";
  discount_value: number;
  max_discount_brl: number | null;
  min_spend_brl: number | null;
  status: "CONFIRMADO_HOJE" | "TESTAR_NO_CHECKOUT";
  rule_description: string;
  source: string;
  verified_at: string;
}

export interface BenefitOption {
  platform: string;
  store_name: string;
  benefit_type: "points" | "cashback" | "native_perk";
  rate_label: string;
  points_per_brl: number;
  cashback_percent: number;
  estimated_points: number;
  estimated_return_brl: number;
  rule_summary: string;
  portal_url: string;
}

export interface StoreOffer {
  id: string;
  store_name: string;
  store_domain: string;
  seller_name: string;
  seller_rating: number | null;
  is_official_store: boolean;
  trust_score: number;
  trust_reason: string;
  product_title: string;
  product_url: string;
  in_stock: boolean;
  price_pix: number;
  price_installment_total: number;
  installments_count: number;
  installment_value: number;
  interest_free: boolean;
  applied_coupon: CouponInfo | null;
  coupon_discount_brl: number;
  best_benefit: BenefitOption | null;
  effective_net_price: number;
  all_coupons: CouponInfo[];
  all_benefits: BenefitOption[];
}

export interface VerdictCard {
  verdict_type: "best_pix" | "best_installment" | "best_smart_hack";
  title: string;
  subtitle: string;
  offer: StoreOffer;
  highlight_price_brl: number;
  net_price_brl: number;
  savings_vs_reference_brl: number | null;
  how_to_buy_steps: string[];
}

export interface RadarResult {
  product: IdentifiedProduct;
  reference_price_brl: number | null;
  mile_value_brl: number;
  offers: StoreOffer[];
  verdicts: VerdictCard[];
  rejected_offers_count: number;
  rejected_reasons: string[];
  generated_at: string;
}

export interface SSEEvent {
  step: number;
  stage: string;
  status: "running" | "done" | "warning" | "error" | "ambiguity" | "result";
  message: string;
  progress_pct: number;
  data?: {
    product?: IdentifiedProduct;
    result?: RadarResult;
  } | null;
}

export function formatBRL(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  }).format(value);
}
