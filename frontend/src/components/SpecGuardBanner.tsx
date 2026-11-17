"use client";

import React, { useState } from "react";
import {
  ShieldCheck,
  Filter,
  ChevronDown,
  ChevronUp,
  Radio,
} from "lucide-react";
import {
  BlockedGroup,
  ChannelStatus,
  SpecContract,
  formatBRL,
} from "@/types/radar";

interface SpecGuardBannerProps {
  spec?: SpecContract | null;
  blockedGroups?: BlockedGroup[];
  channels?: ChannelStatus[];
  priceFloorBrl?: number | null;
  semanticCheck?: string;
}

export function SpecGuardBanner({
  spec,
  blockedGroups = [],
  channels = [],
  priceFloorBrl,
  semanticCheck,
}: SpecGuardBannerProps) {
  const [expanded, setExpanded] = useState(false);

  if (!spec) return null;

  const totalBlocked = blockedGroups.reduce((acc, g) => acc + g.count, 0);

  return (
    <section className="mx-auto mb-6 w-full max-w-6xl rounded-2xl border border-gray-800 bg-radar-surface/90 p-4 shadow-lg">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 font-semibold text-emerald-300">
            <ShieldCheck className="h-3.5 w-3.5" />
            StrictSpecValidator Ativo
          </span>

          {spec.mandatory_tokens.length > 0 && (
            <span className="rounded-lg border border-gray-800 bg-gray-900 px-2.5 py-1 text-gray-300">
              Tokens Obrigatórios:{" "}
              <strong className="font-mono text-cyan-300">
                {spec.mandatory_tokens.join(", ")}
              </strong>
            </span>
          )}

          {priceFloorBrl && (
            <span className="rounded-lg border border-gray-800 bg-gray-900 px-2.5 py-1 text-gray-300">
              Piso Anti-Golpe (55%):{" "}
              <strong className="font-mono text-emerald-400">
                {formatBRL(priceFloorBrl)}
              </strong>
            </span>
          )}
        </div>

        {totalBlocked > 0 && (
          <button
            type="button"
            onClick={() => setExpanded((prev) => !prev)}
            className="inline-flex items-center gap-1.5 rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs font-semibold text-amber-300 transition hover:bg-amber-500/20"
          >
            <Filter className="h-3.5 w-3.5" />
            <span>
              Ignorados: {totalBlocked} anúncios (
              {blockedGroups
                .slice(0, 2)
                .map((g) => `${g.count} ${g.label.split("(")[0].trim()}`)
                .join(" / ")}
              )
            </span>
            {expanded ? (
              <ChevronUp className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
          </button>
        )}
      </div>

      {/* Multi-channel status pills */}
      {channels.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-gray-800/70 pt-2.5 text-[11px] text-gray-400">
          <span className="inline-flex items-center gap-1 text-gray-500">
            <Radio className="h-3 w-3 text-cyan-400" />
            Canais varridos:
          </span>
          {channels.map((ch) => (
            <span
              key={ch.name}
              className={`rounded-md border px-2 py-0.5 font-mono ${
                ch.count > 0
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                  : "border-gray-800 bg-gray-900 text-gray-500"
              }`}
            >
              {ch.name}: {ch.count}
            </span>
          ))}
          {semanticCheck && (
            <span className="ml-auto text-[11px] text-gray-500">
              Trava C: {semanticCheck}
            </span>
          )}
        </div>
      )}

      {expanded && blockedGroups.length > 0 && (
        <div className="mt-3 space-y-2 rounded-xl border border-amber-500/20 bg-gray-900/80 p-3 text-xs">
          <p className="font-semibold text-amber-300">
            Detalhamento de Variações Inferiores e Acessórios Bloqueados:
          </p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {blockedGroups.map((g, idx) => (
              <div
                key={idx}
                className="rounded-lg border border-gray-800 bg-gray-950/70 p-2.5"
              >
                <div className="flex items-center justify-between font-semibold text-gray-200">
                  <span>{g.label}</span>
                  <span className="rounded bg-amber-500/20 px-1.5 py-0.5 font-mono text-[11px] text-amber-300">
                    {g.count}x
                  </span>
                </div>
                {g.examples.length > 0 && (
                  <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] text-gray-400">
                    {g.examples.map((ex, i) => (
                      <li key={i} className="truncate">
                        {ex}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
