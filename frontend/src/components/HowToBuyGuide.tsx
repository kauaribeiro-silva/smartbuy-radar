"use client";

import React, { useState } from "react";
import { CheckSquare, Square, ListChecks, ExternalLink } from "lucide-react";
import { VerdictCard } from "@/types/radar";

interface HowToBuyGuideProps {
  verdict: VerdictCard | null;
}

export function HowToBuyGuide({ verdict }: HowToBuyGuideProps) {
  const [checkedSteps, setCheckedSteps] = useState<Record<number, boolean>>({});

  if (!verdict || verdict.how_to_buy_steps.length === 0) return null;

  const toggle = (idx: number) => {
    setCheckedSteps((prev) => ({ ...prev, [idx]: !prev[idx] }));
  };

  return (
    <section className="mx-auto mb-8 w-full max-w-6xl rounded-2xl border border-emerald-500/30 bg-radar-surface p-5 shadow-xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-400">
            <ListChecks className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-white sm:text-base">
              Guia Passo a Passo: Como Executar ({verdict.title})
            </h2>
            <p className="text-xs text-gray-400">
              Siga na ordem exata para garantir o desconto e não perder o rastreamento de pontos/cashback.
            </p>
          </div>
        </div>

        <a
          href={verdict.offer.product_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-xl bg-emerald-500 px-4 py-2 text-xs font-bold text-gray-950 transition hover:bg-emerald-400"
        >
          <span>Abrir {verdict.offer.store_name}</span>
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>

      <div className="space-y-2">
        {verdict.how_to_buy_steps.map((step, idx) => {
          const done = !!checkedSteps[idx];
          return (
            <button
              key={idx}
              type="button"
              onClick={() => toggle(idx)}
              className={`flex w-full items-start gap-3 rounded-xl border p-3 text-left text-xs transition sm:text-sm ${
                done
                  ? "border-emerald-500/30 bg-emerald-500/5 text-gray-400 line-through"
                  : "border-gray-800 bg-gray-900/60 text-gray-200 hover:border-gray-700"
              }`}
            >
              {done ? (
                <CheckSquare className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
              ) : (
                <Square className="mt-0.5 h-4 w-4 shrink-0 text-gray-500" />
              )}
              <span>{step}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
