"use client";

import React from "react";
import {
  TrendingDown,
  Clock,
  AlertTriangle,
  Sparkles,
  ExternalLink,
  Search,
  Layers,
  PackagePlus,
} from "lucide-react";
import {
  Recommendation,
  TimingVerdict,
  formatBRL,
} from "@/types/radar";

interface BuyingIntelligenceProps {
  timing?: TimingVerdict | null;
  recommendations?: Recommendation[];
  onTriggerSearch: (query: string) => void;
}

export function BuyingIntelligence({
  timing,
  recommendations = [],
  onTriggerSearch,
}: BuyingIntelligenceProps) {
  if (!timing && recommendations.length === 0) return null;

  const badgeStyle =
    timing?.level === "green"
      ? {
          bg: "border-emerald-500/50 bg-emerald-500/10",
          pill: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
          dot: "🟢",
          icon: <TrendingDown className="h-5 w-5 text-emerald-400" />,
        }
      : timing?.level === "red"
      ? {
          bg: "border-red-500/50 bg-red-500/10",
          pill: "bg-red-500/20 text-red-300 border-red-500/40",
          dot: "🔴",
          icon: <AlertTriangle className="h-5 w-5 text-red-400" />,
        }
      : {
          bg: "border-amber-500/50 bg-amber-500/10",
          pill: "bg-amber-500/20 text-amber-300 border-amber-500/40",
          dot: "🟡",
          icon: <Clock className="h-5 w-5 text-amber-400" />,
        };

  return (
    <section className="mx-auto mb-10 w-full max-w-6xl space-y-5">
      <div className="flex items-center gap-2">
        <Sparkles className="h-5 w-5 text-emerald-400" />
        <h2 className="text-lg font-bold text-white">
          Inteligência de Compra &amp; Recomendações
        </h2>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        {/* Timing Thermometer */}
        {timing && (
          <div
            className={`lg:col-span-5 flex flex-col justify-between rounded-2xl border ${badgeStyle.bg} p-5 shadow-xl`}
          >
            <div>
              <div className="mb-3 flex items-center justify-between">
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-bold ${badgeStyle.pill}`}
                >
                  <span>{badgeStyle.dot}</span>
                  <span>{timing.label}</span>
                </span>
                {badgeStyle.icon}
              </div>

              <h3 className="text-base font-bold text-white">
                {timing.headline}
              </h3>

              <ul className="mt-3 space-y-1.5 text-xs text-gray-300">
                {timing.reasons.map((r, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" />
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-2 border-t border-gray-800/80 pt-3 text-center text-xs">
              <div className="rounded-lg bg-gray-900/70 p-2">
                <span className="block text-[10px] uppercase text-gray-500">
                  Preço Atual
                </span>
                <span className="font-mono font-bold text-white">
                  {formatBRL(timing.current_price)}
                </span>
              </div>
              <div className="rounded-lg bg-gray-900/70 p-2">
                <span className="block text-[10px] uppercase text-gray-500">
                  Piso Registrado
                </span>
                <span className="font-mono font-bold text-emerald-400">
                  {formatBRL(timing.lowest_seen)}
                </span>
              </div>
              <div className="rounded-lg bg-gray-900/70 p-2">
                <span className="block text-[10px] uppercase text-gray-500">
                  Mediana Lojas
                </span>
                <span className="font-mono font-bold text-gray-300">
                  {formatBRL(timing.median_seen)}
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Cross-Sell & Alternative Recommendations */}
        {recommendations.length > 0 && (
          <div
            className={`${
              timing ? "lg:col-span-7" : "lg:col-span-12"
            } grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3`}
          >
            {recommendations.map((rec, idx) => (
              <div
                key={idx}
                className="flex flex-col justify-between rounded-2xl border border-gray-800 bg-radar-surface p-4 transition hover:border-gray-700"
              >
                <div>
                  <div className="mb-2 flex items-center justify-between">
                    <span
                      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[10px] font-bold uppercase ${
                        rec.kind === "alternative"
                          ? "bg-indigo-500/20 text-indigo-300 border border-indigo-500/30"
                          : "bg-cyan-500/20 text-cyan-300 border border-cyan-500/30"
                      }`}
                    >
                      {rec.kind === "alternative" ? (
                        <>
                          <Layers className="h-3 w-3" />
                          <span>Alternativa Direta</span>
                        </>
                      ) : (
                        <>
                          <PackagePlus className="h-3 w-3" />
                          <span>Ecossistema / Complemento</span>
                        </>
                      )}
                    </span>
                  </div>

                  <h4 className="text-xs font-bold text-white line-clamp-2">
                    {rec.title}
                  </h4>
                  <p className="mt-1 text-[11px] text-gray-400 line-clamp-2">
                    {rec.reason}
                  </p>
                </div>

                <div className="mt-3 border-t border-gray-800/80 pt-2.5">
                  <div className="flex items-baseline justify-between">
                    <span className="font-mono text-base font-extrabold text-emerald-400">
                      {formatBRL(rec.price_brl)}
                    </span>
                    <span className="text-[11px] text-gray-400 truncate max-w-[110px]">
                      {rec.store}
                    </span>
                  </div>
                  {rec.installments && (
                    <p className="text-[10px] text-gray-500">
                      {rec.installments}
                    </p>
                  )}

                  <div className="mt-2.5 flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => onTriggerSearch(rec.query)}
                      className="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1.5 text-[11px] font-semibold text-gray-200 hover:bg-gray-700"
                    >
                      <Search className="h-3 w-3 text-cyan-400" />
                      <span>Analisar no Radar</span>
                    </button>
                    {rec.url && (
                      <a
                        href={rec.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center justify-center rounded-lg bg-emerald-500/15 p-1.5 text-emerald-300 hover:bg-emerald-500/25"
                        title="Abrir oferta na loja"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
