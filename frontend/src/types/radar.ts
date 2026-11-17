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
  source?: string;
  deal_age_hours?: number | null;
  deal_note?: string;
  embedded_coupon_code?: string;
  page_coupon?: CouponInfo | null;
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

export interface SpecContract {
  canonical_name: string;
  mandatory_tokens: string[];
  forbidden_tokens: string[];
  source: string;
}

export interface BlockedGroup {
  category: string;
  label: string;
  count: number;
  examples: string[];
}

export interface ChannelStatus {
  name: string;
  status: "ok" | "vazio" | "bloqueado" | "indisponivel";
  count: number;
  detail: string;
}

export interface TimingVerdict {
  level: "green" | "yellow" | "red";
  label: string;
  headline: string;
  reasons: string[];
  current_price: number | null;
  lowest_seen: number | null;
  median_seen: number | null;
  history_points: number;
  watch_suggested: boolean;
}

export interface Recommendation {
  kind: "alternative" | "complement";
  title: string;
  query: string;
  reason: string;
  store: string;
  price_brl: number | null;
  url: string;
  installments: string;
  delta_vs_main_brl: number | null;
  delta_pct: number | null;
  near_price: boolean;
}

export interface RadarResult {
  product: IdentifiedProduct;
  reference_price_brl: number | null;
  mile_value_brl: number;
  offers: StoreOffer[];
  verdicts: VerdictCard[];
  rejected_offers_count: number;
  rejected_reasons: string[];
  spec?: SpecContract | null;
  blocked_groups?: BlockedGroup[];
  channels?: ChannelStatus[];
  price_floor_brl?: number | null;
  semantic_check?: string;
  timing?: TimingVerdict | null;
  recommendations?: Recommendation[];
  tracked_product_id?: number | null;
  generated_at: string;
}

export interface PriceHistoryPoint {
  id: number;
  product_id: number;
  price_pix: number | null;
  price_installments: number | null;
  effective_price: number | null;
  store: string;
  recorded_at: string;
}

export interface TrackedProduct {
  id: number;
  product_key: string;
  canonical_name: string;
  search_query: string;
  mandatory_tokens: string[];
  forbidden_tokens: string[];
  first_seen_price: number | null;
  lowest_seen_price: number | null;
  last_checked_price: number | null;
  best_store: string;
  best_url: string;
  active_coupon: string;
  last_checked_at: string | null;
  is_watching: boolean;
  target_price: number | null;
  alert_kind: string;
  alert_message: string;
  alert_amount: number | null;
  alert_at: string | null;
  alert_dismissed: boolean;
  category: string;
  brand: string;
  image_url: string;
  history?: PriceHistoryPoint[];
}

export interface RadarAlert {
  product_id: number;
  kind: string;
  message: string;
  amount: number | null;
  at: string | null;
  canonical_name: string;
  best_url: string;
  best_store: string;
}

export interface StartupRadarData {
  items: TrackedProduct[];
  alerts: RadarAlert[];
  recommendations: Recommendation[];
}

export interface SSEEvent {
  step: number;
  stage: string;
  status: "running" | "done" | "warning" | "error" | "ambiguity" | "result";
  message: string;
  progress_pct: number;
  data?: {
    product?: IdentifiedProduct;
    spec?: SpecContract;
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
