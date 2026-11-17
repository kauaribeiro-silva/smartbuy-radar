"use client";

import React, { useState } from "react";
import { Barcode, PackageCheck, AlertOctagon } from "lucide-react";
import { SearchHero, PaymentFilterMode } from "@/components/SearchHero";
import { LiveAgentLogs } from "@/components/LiveAgentLogs";
import { VerdictCards } from "@/components/VerdictCards";
import { HowToBuyGuide } from "@/components/HowToBuyGuide";
import { ComparisonTable } from "@/components/ComparisonTable";
import {
  IdentifiedProduct,
  RadarResult,
  SSEEvent,
  VerdictCard,
  formatBRL,
} from "@/types/radar";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function HomePage() {
  const [isSearching, setIsSearching] = useState(false);
  const [mileValueBrl, setMileValueBrl] = useState(30);
  const [paymentFilter, setPaymentFilter] = useState<PaymentFilterMode>("all");
  const [logs, setLogs] = useState<SSEEvent[]>([]);
  const [result, setResult] = useState<RadarResult | null>(null);
  const [ambiguityProduct, setAmbiguityProduct] =
    useState<IdentifiedProduct | null>(null);
  const [selectedVerdict, setSelectedVerdict] = useState<VerdictCard | null>(
    null
  );
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handlePaymentFilterChange = (mode: PaymentFilterMode) => {
    setPaymentFilter(mode);
    if (!result) return;
    if (mode === "pix") {
      const v = result.verdicts.find((c) => c.verdict_type === "best_pix");
      if (v) setSelectedVerdict(v);
    } else if (mode === "installment") {
      const v = result.verdicts.find(
        (c) => c.verdict_type === "best_installment"
      );
      if (v) setSelectedVerdict(v);
    } else {
      const v =
        result.verdicts.find((c) => c.verdict_type === "best_smart_hack") ||
        result.verdicts[0];
      if (v) setSelectedVerdict(v);
    }
  };

  const handleSearch = async ({
    inputText,
    imageFile,
    variationOverride,
    skipAmbiguityPrompt,
  }: {
    inputText: string;
    imageFile: File | null;
    variationOverride?: string;
    skipAmbiguityPrompt?: boolean;
  }) => {
    setIsSearching(true);
    setLogs([]);
    setResult(null);
    setErrorMsg(null);
    setAmbiguityProduct(null);
    setSelectedVerdict(null);

    const formData = new FormData();
    const isUrl =
      inputText.startsWith("http://") || inputText.startsWith("https://");

    if (isUrl && !imageFile) {
      formData.append("url", inputText);
    } else if (inputText) {
      formData.append("query", inputText);
    }

    if (imageFile) {
      formData.append("image", imageFile);
    }
    if (variationOverride) {
      formData.append("variation_override", variationOverride);
    }
    if (skipAmbiguityPrompt) {
      formData.append("skip_ambiguity_prompt", "true");
    }
    formData.append("mile_value_brl", String(mileValueBrl));

    try {
      const response = await fetch(`${API_BASE}/api/radar/stream`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok || !response.body) {
        const errText = await response.text();
        throw new Error(errText || `Erro HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (value) {
          buffer += decoder
            .decode(value, { stream: !done })
            .replace(/\r\n/g, "\n");
        }
        const rawEvents = buffer.split("\n\n");
        buffer = done ? "" : rawEvents.pop() || "";

        for (const chunk of rawEvents) {
          if (!chunk.trim()) continue;
          const dataLines = chunk
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trim());

          if (dataLines.length === 0) continue;

          try {
            const evt: SSEEvent = JSON.parse(dataLines.join("\n"));
            setLogs((prev) => [...prev, evt]);

            if (evt.status === "ambiguity" && evt.data?.product) {
              setAmbiguityProduct(evt.data.product);
            } else if (evt.status === "result" && evt.data?.result) {
              const res = evt.data.result;
              setResult(res);
              const preferredType =
                paymentFilter === "pix"
                  ? "best_pix"
                  : paymentFilter === "installment"
                  ? "best_installment"
                  : "best_smart_hack";
              const chosen =
                res.verdicts.find((v) => v.verdict_type === preferredType) ||
                res.verdicts[0] ||
                null;
              setSelectedVerdict(chosen);
            } else if (evt.status === "error") {
              setErrorMsg(evt.message);
            }
          } catch {
            // Ignore malformed keepalive chunks
          }
        }

        if (done) break;
      }
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : "Falha ao conectar ao backend FastAPI.";
      setErrorMsg(message);
    } finally {
      setIsSearching(false);
    }
  };

  return (
    <main className="min-h-screen bg-radar-grid px-4 pb-16 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-6xl">
        <SearchHero
          isSearching={isSearching}
          mileValueBrl={mileValueBrl}
          onMileValueChange={setMileValueBrl}
          paymentFilter={paymentFilter}
          onPaymentFilterChange={handlePaymentFilterChange}
          onSearch={handleSearch}
          ambiguityProduct={ambiguityProduct}
          onDismissAmbiguity={() => setAmbiguityProduct(null)}
        />

        {errorMsg && (
          <div className="mx-auto mb-6 flex max-w-4xl items-center gap-3 rounded-xl border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-300">
            <AlertOctagon className="h-5 w-5 shrink-0 text-red-400" />
            <span>{errorMsg}</span>
          </div>
        )}

        <LiveAgentLogs logs={logs} isSearching={isSearching} />

        {result && (
          <>
            <section className="mx-auto mb-6 flex max-w-6xl flex-wrap items-center justify-between gap-4 rounded-2xl border border-gray-800 bg-radar-surface/90 p-4">
              <div className="flex items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-cyan-500/15 text-cyan-400">
                  <PackageCheck className="h-6 w-6" />
                </div>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-base font-bold text-white sm:text-lg">
                      {result.product.exact_model}
                    </h2>
                    {result.product.brand && (
                      <span className="rounded-md bg-gray-800 px-2 py-0.5 text-[11px] font-medium text-gray-300">
                        {result.product.brand}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-gray-400">
                    {result.product.ean_gtin && (
                      <span className="inline-flex items-center gap-1 font-mono">
                        <Barcode className="h-3.5 w-3.5 text-gray-500" />
                        EAN: {result.product.ean_gtin}
                      </span>
                    )}
                    {result.product.sku_part_number && (
                      <span className="font-mono">
                        SKU: {result.product.sku_part_number}
                      </span>
                    )}
                    {result.product.category && (
                      <span>Categoria: {result.product.category}</span>
                    )}
                  </div>
                </div>
              </div>

              {result.reference_price_brl && (
                <div className="text-right">
                  <p className="text-[11px] uppercase tracking-wider text-gray-500">
                    Preço de Referência
                  </p>
                  <p className="font-mono text-lg font-bold text-gray-300 line-through decoration-red-500/60">
                    {formatBRL(result.reference_price_brl)}
                  </p>
                </div>
              )}
            </section>

            <VerdictCards
              verdicts={result.verdicts}
              selectedVerdictType={selectedVerdict?.verdict_type || ""}
              onSelectVerdict={setSelectedVerdict}
            />

            <HowToBuyGuide verdict={selectedVerdict} />

            <ComparisonTable
              offers={result.offers}
              rejectedCount={result.rejected_offers_count}
              rejectedReasons={result.rejected_reasons}
              paymentFilter={paymentFilter}
              onPaymentFilterChange={handlePaymentFilterChange}
            />
          </>
        )}
      </div>
    </main>
  );
}
