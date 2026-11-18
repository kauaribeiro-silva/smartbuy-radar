"use client";

import React from "react";
import { motion } from "framer-motion";
import {
  CreditCard,
  ExternalLink,
  ShieldCheck,
  Sparkles,
  Tag,
  Zap,
} from "lucide-react";
import { VerdictCard, formatBRL } from "@/types/radar";

interface VerdictCardsProps {
  verdicts: VerdictCard[];
  selectedVerdictType: string;
  onSelectVerdict: (verdict: VerdictCard) => void;
  simulateCoupons?: boolean;
}

export function VerdictCards({
  verdicts,
  selectedVerdictType,
  onSelectVerdict,
  simulateCoupons = false,
}: VerdictCardsProps) {
  if (!verdicts || verdicts.length === 0) return null;

  const styleMap: Record<
    string,
    {
      icon: React.ReactNode;
      badge: string;
      border: string;
      glow: string;
      accentText: string;
    }
  > = {
    best_pix: {
      icon: <Zap className="h-5 w-5 text-cyan-400" />,
      badge: "MENOR DESEMBOLSO",
      border: "border-cyan-500/40",
      glow: "from-cyan-500/10 to-transparent",
      accentText: "text-cyan-400",
    },
    best_installment: {
      icon: <CreditCard className="h-5 w-5 text-indigo-400" />,
      badge: "MELHOR PARCELAMENTO",
      border: "border-indigo-500/40",
      glow: "from-indigo-500/10 to-transparent",
      accentText: "text-indigo-400",
    },
    best_smart_hack: {
      icon: <Sparkles className="h-5 w-5 text-emerald-400" />,
      badge: "MENOR CUSTO EFETIVO",
      border: "border-emerald-500/60",
      glow: "from-emerald-500/15 to-transparent",
      accentText: "text-emerald-400",
    },
  };

  return (
    <section className="mx-auto mb-8 w-full max-w-6xl">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {verdicts.map((v, idx) => {
          const st = styleMap[v.verdict_type] || styleMap.best_pix;
          const isSelected = selectedVerdictType === v.verdict_type;

          const hasSimCoupon =
            v.highlight_price_with_coupon_brl &&
            v.highlight_price_with_coupon_brl < v.highlight_price_brl;

          const displayPrice =
            simulateCoupons && v.highlight_price_with_coupon_brl
              ? v.highlight_price_with_coupon_brl
              : v.highlight_price_brl;

          return (
            <motion.div
              key={v.verdict_type}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: idx * 0.08 }}
              onClick={() => onSelectVerdict(v)}
              className={`cursor-pointer rounded-2xl border bg-gradient-to-b ${st.glow} bg-radar-surface p-5 transition-all ${
                isSelected
                  ? `${st.border} ring-2 ring-emerald-500/25 shadow-2xl`
                  : "border-gray-800 hover:border-gray-700"
              }`}
            >
              <div className="mb-3 flex items-center justify-between">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-900 px-2.5 py-1 font-mono text-[10px] font-semibold tracking-wider text-gray-300 border border-gray-800">
                  {st.icon}
                  {st.badge}
                </span>
                <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-400">
                  <ShieldCheck className="h-3.5 w-3.5" />
                  Trust {v.offer.trust_score}
                </span>
              </div>

              <h3 className="text-base font-bold text-white">{v.title}</h3>
              <p className="mt-0.5 text-xs text-gray-400 line-clamp-2">
                {v.subtitle}
              </p>

              <div className="mt-4 flex items-baseline gap-2">
                <span className={`font-mono text-3xl font-extrabold ${st.accentText}`}>
                  {formatBRL(displayPrice)}
                </span>
                {simulateCoupons && hasSimCoupon ? (
                  <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
                    c/ cupom
                  </span>
                ) : v.verdict_type === "best_smart_hack" ? (
                  <span className="text-[11px] text-gray-400">custo real</span>
                ) : null}
              </div>

              {!simulateCoupons && hasSimCoupon && v.offer.applied_coupon && (
                <p className="mt-1 text-[11px] font-medium text-amber-300">
                  Se cupom <span className="font-mono">{v.offer.applied_coupon.code}</span> aplicar:{" "}
                  <strong className="font-mono">
                    {formatBRL(v.highlight_price_with_coupon_brl)}
                  </strong>
                </p>
              )}

              {v.savings_vs_reference_brl && v.savings_vs_reference_brl > 0 && (
                <p className="mt-1 text-xs font-medium text-emerald-400">
                  Economia de {formatBRL(v.savings_vs_reference_brl)} vs. referência
                </p>
              )}

              <div className="mt-4 space-y-1.5 border-t border-gray-800/80 pt-3 text-xs text-gray-300">
                <div className="flex justify-between">
                  <span className="text-gray-500">Loja / Canal</span>
                  <span className="font-medium text-gray-200 truncate max-w-[180px]">
                    {v.offer.store_name}
                    {v.offer.source ? ` • ${v.offer.source}` : ""}
                  </span>
                </div>

                {v.offer.applied_coupon && (
                  <div className="flex items-center justify-between">
                    <span className="text-gray-500">Cupom Ativo</span>
                    <span className="inline-flex items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-amber-300">
                      <Tag className="h-3 w-3" />
                      {v.offer.applied_coupon.code}
                      {v.offer.coupon_discount_brl > 0
                        ? ` (-${formatBRL(v.offer.coupon_discount_brl)})`
                        : ""}
                    </span>
                  </div>
                )}

                {v.offer.best_benefit && (
                  <div className="flex items-center justify-between">
                    <span className="text-gray-500">Retorno</span>
                    <span className="font-medium text-emerald-400">
                      {v.offer.best_benefit.platform}:{" "}
                      {v.offer.best_benefit.estimated_points > 0
                        ? `${v.offer.best_benefit.estimated_points.toLocaleString("pt-BR")} pts (~${formatBRL(v.offer.best_benefit.estimated_return_brl)})`
                        : formatBRL(v.offer.best_benefit.estimated_return_brl)}
                    </span>
                  </div>
                )}
              </div>

              <div className="mt-4 flex items-center justify-between gap-2">
                <span className="text-[11px] text-gray-500">
                  {isSelected ? "Guia exibido abaixo" : "Clique p/ ver passo a passo"}
                </span>
                <a
                  href={v.offer.product_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-gray-700"
                >
                  <span>Ir à Loja</span>
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
              </div>
            </motion.div>
          );
        })}
      </div>
    </section>
  );
}
