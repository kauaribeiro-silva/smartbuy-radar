"use client";

import React, { useState } from "react";
import {
  Bell,
  ExternalLink,
  Flame,
  History,
  RefreshCw,
  Target,
  X,
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
  const [editingTargetId, setEditingTargetId] = useState<number | null>(null);
  const [targetInput, setTargetInput] = useState<string>("");
  const [savingTarget, setSavingTarget] = useState(false);

  if (items.length === 0 && alerts.length === 0) return null;

  const handleRefresh = async (id: number) => {
    setRefreshingId(id);
    try {
      await onRefreshItem(id);
    } finally {
      setRefreshingId(null);
    }
  };

  const openTargetEditor = (item: TrackedProduct) => {
    if (editingTargetId === item.id) {
      setEditingTargetId(null);
      return;
    }
    setEditingTargetId(item.id);
    setTargetInput(item.target_price ? String(item.target_price) : "");
  };

  const handleSaveTarget = async (id: number) => {
    const num = Number(targetInput.replace(",", "."));
    setSavingTarget(true);
    try {
      await onUpdateTargetPrice(id, num > 0 ? num : null);
      setEditingTargetId(null);
    } finally {
      setSavingTarget(false);
    }
  };

  return (
    <section className="mx-auto mb-5 w-full max-w-4xl space-y-2">
      {/* Compact Price-Drop Alert Banner */}
      {alerts.map((al) => (
        <div
          key={`${al.product_id}-${al.kind}`}
          className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-3.5 py-2 text-xs"
        >
          <div className="flex items-center gap-2">
            <Flame className="h-4 w-4 shrink-0 text-emerald-400" />
            <span className="font-medium text-gray-100">{al.message}</span>
          </div>

          <div className="flex items-center gap-2">
            {al.best_url && (
              <a
                href={al.best_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded-lg bg-emerald-500 px-2.5 py-1 text-[11px] font-bold text-gray-950 hover:bg-emerald-400"
              >
                <span>Abrir {al.best_store || "Loja"}</span>
                <ExternalLink className="h-3 w-3" />
              </a>
            )}
            <button
              type="button"
              onClick={() => onDismissAlert(al.product_id)}
              className="text-gray-400 hover:text-white"
              title="Dispensar alerta"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      ))}

      {/* Discreet Single-Line Recent Searches Pill Bar */}
      {items.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-gray-800/80 bg-radar-surface/70 px-3 py-2 text-xs">
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-gray-400">
            <History className="h-3.5 w-3.5 text-cyan-400" />
            <span>Recentes:</span>
          </span>

          <div className="flex flex-1 flex-wrap items-center gap-1.5">
            {items.slice(0, 6).map((item) => {
              const isRefreshing = refreshingId === item.id;
              const isEditing = editingTargetId === item.id;

              return (
                <div
                  key={item.id}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-gray-800 bg-gray-900/90 px-2.5 py-1 text-[11px] transition hover:border-gray-700"
                >
                  <button
                    type="button"
                    onClick={() => onTriggerSearch(item.search_query)}
                    className="max-w-[160px] truncate font-medium text-gray-200 hover:text-emerald-400"
                    title={`Pesquisar ${item.canonical_name}`}
                  >
                    {item.canonical_name}
                  </button>

                  {item.last_checked_price && (
                    <span className="font-mono font-semibold text-emerald-400">
                      {formatBRL(item.last_checked_price)}
                    </span>
                  )}

                  {item.is_watching && (
                    <span
                      title={
                        item.target_price
                          ? `Alvo: ${formatBRL(item.target_price)}`
                          : "Monitorando"
                      }
                    >
                      <Bell className="h-3 w-3 text-cyan-400" />
                    </span>
                  )}

                  <button
                    type="button"
                    onClick={() => openTargetEditor(item)}
                    className="text-gray-500 hover:text-cyan-400"
                    title="Definir preço alvo"
                  >
                    <Target className="h-3 w-3" />
                  </button>

                  <button
                    type="button"
                    disabled={isRefreshing}
                    onClick={() => handleRefresh(item.id)}
                    className="text-gray-500 hover:text-emerald-400 disabled:opacity-50"
                    title="Revalidar preço agora"
                  >
                    <RefreshCw
                      className={`h-3 w-3 ${isRefreshing ? "animate-spin text-emerald-400" : ""}`}
                    />
                  </button>

                  {item.best_url && (
                    <a
                      href={item.best_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-gray-500 hover:text-white"
                      title={`Abrir oferta na ${item.best_store || "loja"}`}
                    >
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  )}

                  <button
                    type="button"
                    onClick={() => onDeleteItem(item.id)}
                    className="text-gray-600 hover:text-red-400"
                    title="Remover"
                  >
                    <X className="h-3 w-3" />
                  </button>

                  {isEditing && (
                    <div className="ml-1 flex items-center gap-1 border-l border-gray-800 pl-1.5">
                      <input
                        type="number"
                        placeholder="Alvo R$"
                        value={targetInput}
                        onChange={(e) => setTargetInput(e.target.value)}
                        className="w-16 rounded border border-gray-700 bg-gray-950 px-1.5 py-0.5 font-mono text-[10px] text-white outline-none"
                      />
                      <button
                        type="button"
                        disabled={savingTarget}
                        onClick={() => handleSaveTarget(item.id)}
                        className="rounded bg-cyan-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-cyan-300 hover:bg-cyan-500/30"
                      >
                        OK
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
