"use client";

import React, { useMemo, useState } from "react";
import {
  ExternalLink,
  ShieldAlert,
  ShieldCheck,
  Star,
  Tag,
  ChevronDown,
  ChevronUp,
  Zap,
  CreditCard,
  Layers,
} from "lucide-react";
import { StoreOffer, formatBRL } from "@/types/radar";
import { PaymentFilterMode } from "@/components/SearchHero";

interface ComparisonTableProps {
  offers: StoreOffer[];
  rejectedCount: number;
  rejectedReasons: string[];
  paymentFilter: PaymentFilterMode;
  onPaymentFilterChange: (mode: PaymentFilterMode) => void;
}

export function ComparisonTable({
  offers,
  rejectedCount,
  rejectedReasons,
  paymentFilter,
  onPaymentFilterChange,
}: ComparisonTableProps) {
  const [showRejected, setShowRejected] = useState(false);
  const [onlyInterestFree, setOnlyInterestFree] = useState(false);

  const displayedOffers = useMemo(() => {
    let list = [...offers];
    if (paymentFilter === "installment" && onlyInterestFree) {
      list = list.filter((o) => o.interest_free);
    }
    list.sort((a, b) => {
      if (paymentFilter === "pix") {
        const aPix = a.price_pix - a.coupon_discount_brl;
        const bPix = b.price_pix - b.coupon_discount_brl;
        return aPix - bPix;
      }
      if (paymentFilter === "installment") {
        const aInst = a.price_installment_total - a.coupon_discount_brl;
        const bInst = b.price_installment_total - b.coupon_discount_brl;
        if (aInst !== bInst) return aInst - bInst;
        return b.installments_count - a.installments_count;
      }
      return a.effective_net_price - b.effective_net_price;
    });
    return list;
  }, [offers, paymentFilter, onlyInterestFree]);

  if (offers.length === 0 && rejectedCount === 0) return null;

  return (
    <section className="mx-auto mb-12 w-full max-w-6xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-white">
            Tabela Comparativa Completa ({displayedOffers.length} lojas confiáveis)
          </h2>
          <p className="text-xs text-gray-400">
            {paymentFilter === "pix"
              ? "Ordenada pelo menor Preço À Vista (Pix / 1x)."
              : paymentFilter === "installment"
              ? "Ordenada pelo menor Total Parcelado no Cartão."
              : "Ordenada pelo menor Custo Efetivo Real (descontando cupom e cashback/pontos)."}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Interactive Payment Mode Filter */}
          <div className="inline-flex rounded-xl border border-gray-800 bg-gray-900/90 p-1 text-xs">
            <button
              type="button"
              onClick={() => onPaymentFilterChange("all")}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-medium transition ${
                paymentFilter === "all"
                  ? "bg-emerald-500 text-gray-950 font-semibold"
                  : "text-gray-400 hover:text-white"
              }`}
            >
              <Layers className="h-3.5 w-3.5" />
              <span>Todos</span>
            </button>
            <button
              type="button"
              onClick={() => onPaymentFilterChange("pix")}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-medium transition ${
                paymentFilter === "pix"
                  ? "bg-cyan-400 text-gray-950 font-semibold"
                  : "text-gray-400 hover:text-white"
              }`}
            >
              <Zap className="h-3.5 w-3.5" />
              <span>À Vista (Pix)</span>
            </button>
            <button
              type="button"
              onClick={() => onPaymentFilterChange("installment")}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-medium transition ${
                paymentFilter === "installment"
                  ? "bg-indigo-400 text-gray-950 font-semibold"
                  : "text-gray-400 hover:text-white"
              }`}
            >
              <CreditCard className="h-3.5 w-3.5" />
              <span>Parcelado</span>
            </button>
          </div>

          {paymentFilter === "installment" && (
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-xl border border-indigo-500/30 bg-indigo-500/10 px-3 py-2 text-xs text-indigo-200">
              <input
                type="checkbox"
                checked={onlyInterestFree}
                onChange={(e) => setOnlyInterestFree(e.target.checked)}
                className="rounded border-gray-700 bg-gray-900 text-indigo-500"
              />
              <span>Apenas sem juros</span>
            </label>
          )}

          {rejectedCount > 0 && (
            <button
              type="button"
              onClick={() => setShowRejected((s) => !s)}
              className="inline-flex items-center gap-1.5 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-300 hover:bg-amber-500/20"
            >
              <ShieldAlert className="h-3.5 w-3.5" />
              <span>{rejectedCount} bloqueados</span>
              {showRejected ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </button>
          )}
        </div>
      </div>

      {showRejected && rejectedReasons.length > 0 && (
        <div className="mb-4 rounded-xl border border-amber-500/20 bg-gray-900/80 p-3 text-xs text-gray-300">
          <p className="mb-1.5 font-semibold text-amber-300">
            Anúncios descartados pela Trava Anti-Golpe:
          </p>
          <ul className="list-disc space-y-1 pl-4 text-gray-400">
            {rejectedReasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="overflow-x-auto rounded-2xl border border-gray-800 bg-radar-surface shadow-xl">
        <table className="w-full min-w-[840px] border-collapse text-left text-xs sm:text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900/80 text-[11px] uppercase tracking-wider text-gray-400">
              <th className="px-4 py-3.5">Loja &amp; Vendedor</th>
              <th
                className={`px-4 py-3.5 ${
                  paymentFilter === "pix" ? "text-cyan-300 font-bold" : ""
                }`}
              >
                Preço À Vista (Pix)
              </th>
              <th
                className={`px-4 py-3.5 ${
                  paymentFilter === "installment"
                    ? "text-indigo-300 font-bold"
                    : ""
                }`}
              >
                Compra Parcelada
              </th>
              <th className="px-4 py-3.5">Cupom Ativo</th>
              <th className="px-4 py-3.5">Melhor Cashback / Pontos</th>
              <th className="px-4 py-3.5">
                {paymentFilter === "installment"
                  ? "Custo Efetivo Parcelado"
                  : "Custo Efetivo Real"}
              </th>
              <th className="px-4 py-3.5 text-right">Oferta</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/80">
            {displayedOffers.map((offer) => {
              const benefitReturn = offer.best_benefit?.estimated_return_brl || 0;
              const effectiveInstallment = Math.max(
                0,
                offer.price_installment_total -
                  offer.coupon_discount_brl -
                  benefitReturn
              );
              const effectiveDisplay =
                paymentFilter === "installment"
                  ? effectiveInstallment
                  : offer.effective_net_price;

              return (
                <tr key={offer.id} className="transition hover:bg-gray-900/50">
                  <td className="px-4 py-3.5">
                    <div className="font-semibold text-white">
                      {offer.store_name}
                    </div>
                    <div className="mt-0.5 text-[11px] text-gray-400 line-clamp-1 max-w-[230px]">
                      {offer.product_title}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-400">
                      <span className="inline-flex items-center gap-1 text-emerald-400">
                        <ShieldCheck className="h-3 w-3" />
                        Trust {offer.trust_score}
                      </span>
                      <span>•</span>
                      <span className="truncate max-w-[140px]">
                        {offer.seller_name}
                      </span>
                      {offer.seller_rating && (
                        <span className="inline-flex items-center gap-0.5 text-amber-400">
                          <Star className="h-3 w-3 fill-amber-400" />
                          {offer.seller_rating.toFixed(1)}
                        </span>
                      )}
                    </div>
                  </td>

                  <td
                    className={`px-4 py-3.5 font-mono ${
                      paymentFilter === "pix"
                        ? "bg-cyan-500/5 text-base font-bold text-cyan-300"
                        : "font-semibold text-gray-100"
                    }`}
                  >
                    {formatBRL(offer.price_pix)}
                  </td>

                  <td
                    className={`px-4 py-3.5 ${
                      paymentFilter === "installment" ? "bg-indigo-500/5" : ""
                    }`}
                  >
                    <div
                      className={`font-mono ${
                        paymentFilter === "installment"
                          ? "text-base font-bold text-indigo-300"
                          : "text-gray-200"
                      }`}
                    >
                      {offer.installments_count}x de{" "}
                      {formatBRL(offer.installment_value)}
                    </div>
                    <div className="text-[11px] text-gray-400">
                      Total: {formatBRL(offer.price_installment_total)} (
                      {offer.interest_free ? "sem juros" : "com juros"})
                    </div>
                  </td>

                  <td className="px-4 py-3.5">
                    {offer.applied_coupon ? (
                      <div>
                        <span
                          className={`inline-flex items-center gap-1 rounded px-2 py-0.5 font-mono text-[11px] font-semibold ${
                            offer.applied_coupon.status === "CONFIRMADO_HOJE"
                              ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                              : "bg-amber-500/15 text-amber-300 border border-amber-500/30"
                          }`}
                        >
                          <Tag className="h-3 w-3" />
                          {offer.applied_coupon.code}
                        </span>
                        <div className="mt-1 text-[11px] text-gray-400">
                          -{formatBRL(offer.coupon_discount_brl)} •{" "}
                          {offer.applied_coupon.status === "CONFIRMADO_HOJE"
                            ? "Confirmado hoje"
                            : "Testar no checkout"}
                        </div>
                      </div>
                    ) : (
                      <span className="text-xs text-gray-500">
                        Sem cupom extra
                      </span>
                    )}
                  </td>

                  <td className="px-4 py-3.5">
                    {offer.best_benefit ? (
                      <div>
                        <div className="font-medium text-cyan-300">
                          {offer.best_benefit.platform} (
                          {offer.best_benefit.rate_label})
                        </div>
                        <div className="text-[11px] text-emerald-400">
                          Retorno:{" "}
                          {offer.best_benefit.estimated_points > 0
                            ? `${offer.best_benefit.estimated_points.toLocaleString("pt-BR")} pts (~${formatBRL(offer.best_benefit.estimated_return_brl)})`
                            : formatBRL(offer.best_benefit.estimated_return_brl)}
                        </div>
                      </div>
                    ) : (
                      <span className="text-xs text-gray-500">—</span>
                    )}
                  </td>

                  <td className="px-4 py-3.5 font-mono text-base font-bold text-emerald-400">
                    {formatBRL(effectiveDisplay)}
                  </td>

                  <td className="px-4 py-3.5 text-right">
                    <a
                      href={offer.product_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-3 py-1.5 text-xs font-semibold text-white transition hover:border-emerald-500/50 hover:bg-gray-700"
                    >
                      <span>Comprar</span>
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
