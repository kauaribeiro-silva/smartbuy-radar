"use client";

import React, { useState } from "react";
import {
  ExternalLink,
  ShieldAlert,
  ShieldCheck,
  Star,
  Tag,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { StoreOffer, formatBRL } from "@/types/radar";

interface ComparisonTableProps {
  offers: StoreOffer[];
  rejectedCount: number;
  rejectedReasons: string[];
}

export function ComparisonTable({
  offers,
  rejectedCount,
  rejectedReasons,
}: ComparisonTableProps) {
  const [showRejected, setShowRejected] = useState(false);

  if (offers.length === 0 && rejectedCount === 0) return null;

  return (
    <section className="mx-auto mb-12 w-full max-w-6xl">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-bold text-white">
            Tabela Comparativa Completa ({offers.length} lojas confiáveis)
          </h2>
          <p className="text-xs text-gray-400">
            Ordenada pelo menor Custo Efetivo Real (preço com cupom descontando cashback/pontos).
          </p>
        </div>

        {rejectedCount > 0 && (
          <button
            type="button"
            onClick={() => setShowRejected((s) => !s)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-300 hover:bg-amber-500/20"
          >
            <ShieldAlert className="h-3.5 w-3.5" />
            <span>{rejectedCount} anúncios bloqueados pela Trava Anti-Golpe</span>
            {showRejected ? (
              <ChevronUp className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
          </button>
        )}
      </div>

      {showRejected && rejectedReasons.length > 0 && (
        <div className="mb-4 rounded-xl border border-amber-500/20 bg-gray-900/80 p-3 text-xs text-gray-300">
          <p className="mb-1.5 font-semibold text-amber-300">
            Anúncios descartados automaticamente:
          </p>
          <ul className="list-disc space-y-1 pl-4 text-gray-400">
            {rejectedReasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="overflow-x-auto rounded-2xl border border-gray-800 bg-radar-surface shadow-xl">
        <table className="w-full min-w-[820px] border-collapse text-left text-xs sm:text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900/80 text-[11px] uppercase tracking-wider text-gray-400">
              <th className="px-4 py-3.5">Loja &amp; Vendedor</th>
              <th className="px-4 py-3.5">Preço no Pix</th>
              <th className="px-4 py-3.5">Parcelado</th>
              <th className="px-4 py-3.5">Cupom Ativo</th>
              <th className="px-4 py-3.5">Melhor Cashback / Pontos</th>
              <th className="px-4 py-3.5">Custo Efetivo Real</th>
              <th className="px-4 py-3.5 text-right">Oferta</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/80">
            {offers.map((offer) => (
              <tr
                key={offer.id}
                className="transition hover:bg-gray-900/50"
              >
                <td className="px-4 py-3.5">
                  <div className="font-semibold text-white">
                    {offer.store_name}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-400">
                    <span className="inline-flex items-center gap-1 text-emerald-400">
                      <ShieldCheck className="h-3 w-3" />
                      {offer.trust_score}
                    </span>
                    <span>•</span>
                    <span className="truncate max-w-[160px]">
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

                <td className="px-4 py-3.5 font-mono font-semibold text-gray-100">
                  {formatBRL(offer.price_pix)}
                </td>

                <td className="px-4 py-3.5">
                  <div className="font-mono text-gray-200">
                    {formatBRL(offer.price_installment_total)}
                  </div>
                  <div className="text-[11px] text-gray-400">
                    {offer.installments_count}x de {formatBRL(offer.installment_value)}{" "}
                    {offer.interest_free ? "s/ juros" : "c/ juros"}
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
                    <span className="text-xs text-gray-500">Sem cupom ativo</span>
                  )}
                </td>

                <td className="px-4 py-3.5">
                  {offer.best_benefit ? (
                    <div>
                      <div className="font-medium text-cyan-300">
                        {offer.best_benefit.platform} ({offer.best_benefit.rate_label})
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
                  {formatBRL(offer.effective_net_price)}
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
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
