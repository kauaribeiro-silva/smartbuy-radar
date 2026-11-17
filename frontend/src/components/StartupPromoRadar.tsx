"use client";

import React, { useState } from "react";
import {
  Bell,
  ExternalLink,
  Flame,
  History,
  RefreshCw,
  Search,
  Target,
  Trash2,
  X,
  Tag,
} from "lucide-react";
import {
  RadarAlert,
  TrackedProduct,
  formatBRL,
} from "@/types/radar";

interface StartupPromoRadarProps {
  items: TrackedProduct[];
  alerts: RadarAlert[];
  onTriggerSearch: (query: string) => void;
  onRefreshItem: (id: number) => Promise<void>;
  onUpdateTargetPrice: (id: number, target: number | null) => Promise<void>;
  onDismissAlert: (id: number) => Promise<void>;
  onDeleteItem: (id: number) => Promise<void>;
}

export function StartupPromoRadar({
  items,
  alerts,
  onTriggerSearch,
  onRefreshItem,
  onUpdateTargetPrice,
  onDismissAlert,
  onDeleteItem,
}: StartupPromoRadarProps) {
  const [refreshingId, setRefreshingId] = useState<number | null>(null);
  const [targetInputs, setTargetInputs] = useState<Record<number, string>>({});
  const [savingTargetId, setSavingTargetId] = useState<number | null>(null);

  if (items.length === 0 && alerts.length === 0) return null;

  const handleRefresh = async (id: number) => {
    setRefreshingId(id);
    try {
      await onRefreshItem(id);
    } finally {
      setRefreshingId(null);
    }
  };

  const handleSaveTarget = async (item: TrackedProduct) => {
    const raw =
      targetInputs[item.id] ??
      (item.target_price ? String(item.target_price) : "");
    const num = Number(raw.replace(",", "."));
    setSavingTargetId(item.id);
    try {
      await onUpdateTargetPrice(item.id, num > 0 ? num : null);
    } finally {
      setSavingTargetId(null);
    }
  };

  return (
    <section className="mx-auto mb-6 w-full max-w-6xl space-y-4">
      {/* Price-Drop & Coupon Opportunity Alert Banners */}
      {alerts.map((al) => (
        <div
          key={`${al.product_id}-${al.kind}`}
          className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-emerald-500/50 bg-gradient-to-r from-emerald-500/20 via-cyan-500/10 to-radar-surface p-4 shadow-lg"
        >
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-500/20 text-emerald-400">
              <Flame className="h-5 w-5 animate-bounce" />
            </div>
            <div>
              <p className="text-xs font-bold uppercase tracking-wider text-emerald-400">
                Radar de Oportunidade Detectado
              </p>
              <p className="text-sm font-semibold text-white">{al.message}</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {al.best_url && (
              <a
                href={al.best_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-500 px-3.5 py-2 text-xs font-bold text-gray-950 transition hover:bg-emerald-400"
              >
                <span>Ver na {al.best_store || "Loja"}</span>
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
            <button
              type="button"
              onClick={() => onDismissAlert(al.product_id)}
              className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-800 hover:text-white"
              title="Dispensar alerta"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      ))}

      {/* Tracked Products Memory Grid */}
      {items.length > 0 && (
        <div className="rounded-2xl border border-gray-800 bg-radar-surface/85 p-4 shadow-xl">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <History className="h-4 w-4 text-cyan-400" />
              <h2 className="text-xs font-bold uppercase tracking-wider text-gray-200 sm:text-sm">
                Radar das Suas Últimas Buscas &amp; Monitoramento de Preço Alvo
              </h2>
            </div>
            <span className="text-[11px] text-gray-500">
              Memória persistente SQLite • Revalidação em 1 clique
            </span>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {items.slice(0, 6).map((item) => {
              const isRefreshing = refreshingId === item.id;
              const currentTargetVal =
                targetInputs[item.id] !== undefined
                  ? targetInputs[item.id]
                  : item.target_price
                  ? String(item.target_price)
                  : "";

              return (
                <div
                  key={item.id}
                  className="flex flex-col justify-between rounded-xl border border-gray-800/90 bg-gray-900/60 p-3.5 transition hover:border-gray-700"
                >
                  <div>
                    <div className="flex items-start justify-between gap-2">
                      <button
                        type="button"
                        onClick={() => onTriggerSearch(item.search_query)}
                        className="text-left text-xs font-bold text-white hover:text-emerald-400 line-clamp-1"
                        title={item.canonical_name}
                      >
                        {item.canonical_name}
                      </button>
                      <button
                        type="button"
                        onClick={() => onDeleteItem(item.id)}
                        className="text-gray-500 hover:text-red-400"
                        title="Remover do histórico"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>

                    <div className="mt-2 flex items-baseline justify-between">
                      <div>
                        <span className="text-[10px] uppercase text-gray-500">
                          Último Preço ({item.best_store || "Loja"})
                        </span>
                        <div className="font-mono text-base font-extrabold text-emerald-400">
                          {formatBRL(item.last_checked_price)}
                        </div>
                      </div>
                      <div className="text-right">
                        <span className="text-[10px] uppercase text-gray-500">
                          Menor Histórico
                        </span>
                        <div className="font-mono text-xs font-semibold text-gray-300">
                          {formatBRL(item.lowest_seen_price)}
                        </div>
                      </div>
                    </div>

                    {item.active_coupon && (
                      <div className="mt-1.5 inline-flex items-center gap-1 rounded bg-amber-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold text-amber-300">
                        <Tag className="h-3 w-3" />
                        Cupom: {item.active_coupon}
                      </div>
                    )}
                  </div>

                  {/* Target Price + Action Buttons */}
                  <div className="mt-3 space-y-2 border-t border-gray-800/80 pt-2.5">
                    <div className="flex items-center gap-1.5">
                      <Target className="h-3.5 w-3.5 shrink-0 text-cyan-400" />
                      <span className="text-[11px] text-gray-400">Alvo R$:</span>
                      <input
                        type="number"
                        placeholder="Ex: 1600"
                        value={currentTargetVal}
                        onChange={(e) =>
                          setTargetInputs((prev) => ({
                            ...prev,
                            [item.id]: e.target.value,
                          }))
                        }
                        className="w-20 rounded-lg border border-gray-800 bg-gray-950 px-2 py-1 font-mono text-xs text-white outline-none focus:border-cyan-500/60"
                      />
                      <button
                        type="button"
                        disabled={savingTargetId === item.id}
                        onClick={() => handleSaveTarget(item)}
                        className="rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-2 py-1 text-[11px] font-medium text-cyan-300 hover:bg-cyan-500/20"
                      >
                        {savingTargetId === item.id ? "..." : "Salvar"}
                      </button>
                      {item.is_watching && (
                        <span className="ml-auto" title="Monitoramento ativo">
                          <Bell className="h-3.5 w-3.5 text-emerald-400" />
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        disabled={isRefreshing}
                        onClick={() => handleRefresh(item.id)}
                        className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1.5 text-[11px] font-semibold text-gray-200 transition hover:bg-gray-700 disabled:opacity-50"
                      >
                        <RefreshCw
                          className={`h-3 w-3 text-emerald-400 ${
                            isRefreshing ? "animate-spin" : ""
                          }`}
                        />
                        <span>
                          {isRefreshing ? "Revalidando..." : "Revalidar Agora"}
                        </span>
                      </button>

                      <button
                        type="button"
                        onClick={() => onTriggerSearch(item.search_query)}
                        className="inline-flex items-center justify-center gap-1 rounded-lg bg-emerald-500/15 px-2.5 py-1.5 text-[11px] font-semibold text-emerald-300 transition hover:bg-emerald-500/25"
                        title="Abrir análise completa"
                      >
                        <Search className="h-3 w-3" />
                        <span>Varredura</span>
                      </button>

                      {item.best_url && (
                        <a
                          href={item.best_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center justify-center rounded-lg border border-gray-800 bg-gray-900 p-1.5 text-gray-300 hover:text-white"
                          title="Ir direto à melhor oferta salva"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
