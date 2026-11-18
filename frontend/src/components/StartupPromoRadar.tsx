"use client";

import React, { useEffect, useState } from "react";
import {
  Bell,
  ExternalLink,
  Flame,
  History,
  RefreshCw,
  Send,
  Target,
  X,
  CheckCircle2,
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

  // Telegram 24/7 Config Popover
  const [showTelegram, setShowTelegram] = useState(false);
  const [tgConfigured, setTgConfigured] = useState(false);
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [savingTg, setSavingTg] = useState(false);
  const [tgStatusMsg, setTgStatusMsg] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/memory/telegram")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d) {
          setTgConfigured(!!d.configured);
          if (d.chat_id) setChatId(d.chat_id);
        }
      })
      .catch(() => {});
  }, []);

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

  const handleSaveTelegram = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingTg(true);
    setTgStatusMsg(null);
    try {
      const res = await fetch("/api/memory/telegram", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bot_token: botToken,
          chat_id: chatId,
          send_test: true,
        }),
      });
      const data = await res.json();
      setTgConfigured(!!data.configured);
      setTgStatusMsg(
        data.test_sent
          ? "Conectado! Mensagem de teste enviada ao seu Telegram."
          : "Salvo no banco persistente (/data)."
      );
      setBotToken("");
    } catch {
      setTgStatusMsg("Erro ao salvar configuração do Telegram.");
    } finally {
      setSavingTg(false);
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
        <div className="rounded-xl border border-gray-800/80 bg-radar-surface/70 px-3 py-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
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
                        className={`h-3 w-3 ${
                          isRefreshing ? "animate-spin text-emerald-400" : ""
                        }`}
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

            <button
              type="button"
              onClick={() => setShowTelegram((s) => !s)}
              className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 text-[11px] font-medium transition ${
                tgConfigured
                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                  : "border-gray-800 bg-gray-900 text-gray-400 hover:text-white"
              }`}
              title="Configurar alertas automáticos 24/7 no Telegram"
            >
              <Send className="h-3 w-3" />
              <span>{tgConfigured ? "Telegram Ativo" : "Alerta Telegram"}</span>
            </button>
          </div>

          {showTelegram && (
            <form
              onSubmit={handleSaveTelegram}
              className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-gray-800/80 pt-2.5 text-[11px]"
            >
              <span className="text-gray-400">
                Bot Monitor 24/7 (a cada 30 min):
              </span>
              <input
                type="password"
                placeholder={
                  tgConfigured
                    ? "Bot Token já salvo (opcional trocar)"
                    : "Telegram Bot Token (ex: 123456:ABC...)"
                }
                value={botToken}
                onChange={(e) => setBotToken(e.target.value)}
                className="flex-1 min-w-[180px] rounded-lg border border-gray-800 bg-gray-950 px-2.5 py-1 font-mono text-white outline-none focus:border-cyan-500/50"
              />
              <input
                type="text"
                placeholder="Chat ID (ex: 987654321)"
                value={chatId}
                onChange={(e) => setChatId(e.target.value)}
                className="w-36 rounded-lg border border-gray-800 bg-gray-950 px-2.5 py-1 font-mono text-white outline-none focus:border-cyan-500/50"
              />
              <button
                type="submit"
                disabled={savingTg}
                className="rounded-lg bg-cyan-500 px-3 py-1 font-semibold text-gray-950 hover:bg-cyan-400 disabled:opacity-50"
              >
                {savingTg ? "Testando..." : "Salvar & Testar"}
              </button>
              {tgStatusMsg && (
                <span className="inline-flex items-center gap-1 text-emerald-400">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  {tgStatusMsg}
                </span>
              )}
            </form>
          )}
        </div>
      )}
    </section>
  );
}
