"use client";

import React, { useRef, useState } from "react";
import {
  Camera,
  Link2,
  Radar,
  Sparkles,
  Upload,
  X,
  AlertCircle,
  Zap,
  CreditCard,
  Layers,
} from "lucide-react";
import { IdentifiedProduct } from "@/types/radar";

export type PaymentFilterMode = "all" | "pix" | "installment";

interface SearchHeroProps {
  isSearching: boolean;
  mileValueBrl: number;
  onMileValueChange: (val: number) => void;
  paymentFilter: PaymentFilterMode;
  onPaymentFilterChange: (mode: PaymentFilterMode) => void;
  onSearch: (payload: {
    inputText: string;
    imageFile: File | null;
    variationOverride?: string;
    skipAmbiguityPrompt?: boolean;
  }) => void;
  ambiguityProduct: IdentifiedProduct | null;
  onDismissAmbiguity: () => void;
}

export function SearchHero({
  isSearching,
  mileValueBrl,
  onMileValueChange,
  paymentFilter,
  onPaymentFilterChange,
  onSearch,
  ambiguityProduct,
  onDismissAmbiguity,
}: SearchHeroProps) {
  const [inputText, setInputText] = useState("");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const attachImage = (file: File) => {
    if (!file.type.startsWith("image/")) return;
    setImageFile(file);
    const reader = new FileReader();
    reader.onload = () => setImagePreview(reader.result as string);
    reader.readAsDataURL(file);
  };

  const clearImage = () => {
    setImageFile(null);
    setImagePreview(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith("image/")) {
        const blob = items[i].getAsFile();
        if (blob) {
          e.preventDefault();
          attachImage(blob);
          return;
        }
      }
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) attachImage(file);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim() && !imageFile) return;
    onSearch({ inputText: inputText.trim(), imageFile });
  };

  return (
    <section className="mx-auto w-full max-w-4xl pt-8 pb-6">
      <div className="mb-6 text-center">
        <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-1 text-xs font-medium text-emerald-400">
          <Radar className="h-3.5 w-3.5 animate-pulse" />
          <span>Inteligência Multimodal • E-commerce Brasil</span>
        </div>
        <h1 className="bg-gradient-to-r from-white via-gray-100 to-gray-400 bg-clip-text text-3xl font-bold tracking-tight text-transparent sm:text-5xl">
          SmartBuy Radar
        </h1>
        <p className="mt-2.5 text-sm text-gray-400 sm:text-base">
          Cole o <span className="text-gray-200 font-medium">link do produto</span>, digite o{" "}
          <span className="text-gray-200 font-medium">modelo</span> ou{" "}
          <span className="text-gray-200 font-medium">cole/envie uma imagem (Ctrl+V)</span>.
          Nós encontramos o menor preço real com cupom ativo, pontos e cashback.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        onPaste={handlePaste}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={`relative rounded-2xl border bg-radar-surface/90 p-3 shadow-2xl transition-all ${
          isDragging
            ? "border-emerald-400 ring-2 ring-emerald-500/30"
            : "border-gray-800 hover:border-gray-700"
        }`}
      >
        {imagePreview && (
          <div className="mb-3 flex items-center gap-3 rounded-xl border border-gray-800 bg-gray-900/70 p-2.5">
            <img
              src={imagePreview}
              alt="Preview do produto"
              className="h-14 w-14 rounded-lg object-cover border border-gray-700"
            />
            <div className="flex-1 min-w-0">
              <p className="truncate text-xs font-medium text-emerald-400">
                Imagem pronta para análise Gemini Vision
              </p>
              <p className="truncate text-xs text-gray-400">
                {imageFile?.name || "Captura colada da área de transferência"}
              </p>
            </div>
            <button
              type="button"
              onClick={clearImage}
              className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-800 hover:text-white"
              title="Remover imagem"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="relative flex-1 flex items-center">
            <Link2 className="pointer-events-none absolute left-3.5 h-5 w-5 text-gray-500" />
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder={
                imageFile
                  ? "Opcional: adicione uma dica (ex: 256GB, 220V)..."
                  : "Cole a URL do produto, digite o modelo ou dê Ctrl+V na foto..."
              }
              disabled={isSearching}
              className="w-full rounded-xl bg-gray-900/80 py-3.5 pl-11 pr-4 text-sm text-white placeholder-gray-500 outline-none ring-1 ring-gray-800 focus:ring-emerald-500/60"
            />
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) attachImage(f);
            }}
          />

          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={isSearching}
              onClick={() => fileInputRef.current?.click()}
              className="inline-flex items-center gap-2 rounded-xl border border-gray-800 bg-gray-900 px-3.5 py-3.5 text-xs font-medium text-gray-300 transition hover:border-gray-700 hover:bg-gray-800 hover:text-white disabled:opacity-50"
              title="Enviar imagem ou print do produto"
            >
              <Camera className="h-4 w-4 text-cyan-400" />
              <span className="hidden md:inline">Imagem</span>
            </button>

            <button
              type="submit"
              disabled={isSearching || (!inputText.trim() && !imageFile)}
              className="inline-flex flex-1 sm:flex-none items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-emerald-500 to-cyan-500 px-6 py-3.5 text-sm font-semibold text-gray-950 shadow-lg shadow-emerald-500/20 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Sparkles className="h-4 w-4" />
              <span>{isSearching ? "Varrendo..." : "Otimizar Compra"}</span>
            </button>
          </div>
        </div>

        {/* Filter Bar: Payment Mode + Mile Value */}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-gray-800/70 pt-3 px-1 text-xs">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-gray-400 font-medium">Modalidade:</span>
            <button
              type="button"
              onClick={() => onPaymentFilterChange("all")}
              className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 font-medium transition ${
                paymentFilter === "all"
                  ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                  : "bg-gray-900 text-gray-400 border border-gray-800 hover:text-gray-200"
              }`}
            >
              <Layers className="h-3.5 w-3.5" />
              <span>Todas</span>
            </button>
            <button
              type="button"
              onClick={() => onPaymentFilterChange("pix")}
              className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 font-medium transition ${
                paymentFilter === "pix"
                  ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40"
                  : "bg-gray-900 text-gray-400 border border-gray-800 hover:text-gray-200"
              }`}
            >
              <Zap className="h-3.5 w-3.5" />
              <span>À Vista (Pix / 1x)</span>
            </button>
            <button
              type="button"
              onClick={() => onPaymentFilterChange("installment")}
              className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 font-medium transition ${
                paymentFilter === "installment"
                  ? "bg-indigo-500/20 text-indigo-300 border border-indigo-500/40"
                  : "bg-gray-900 text-gray-400 border border-gray-800 hover:text-gray-200"
              }`}
            >
              <CreditCard className="h-3.5 w-3.5" />
              <span>Parcelado (Cartão)</span>
            </button>
          </div>

          <div className="flex items-center gap-3">
            <span className="hidden lg:inline-flex items-center gap-1 text-gray-500">
              <Upload className="h-3.5 w-3.5" />
              Ctrl+V para colar print
            </span>
            <label
              className="flex items-center gap-2 text-gray-400"
              title="Valor estimado de cada 1.000 pontos Livelo/Esfera em R$ para calcular seu desconto real"
            >
              <span>Valor do Milheiro (1.000 pts):</span>
              <div className="flex items-center rounded-lg border border-gray-800 bg-gray-900 px-2 py-1">
                <span className="mr-1 text-gray-500">R$</span>
                <input
                  type="number"
                  min={10}
                  max={80}
                  step={1}
                  value={mileValueBrl}
                  onChange={(e) => onMileValueChange(Number(e.target.value) || 30)}
                  className="w-12 bg-transparent font-mono text-xs text-emerald-400 outline-none"
                />
              </div>
            </label>
          </div>
        </div>
      </form>

      {ambiguityProduct && (
        <div className="mt-4 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
            <div className="flex-1">
              <p className="text-sm font-semibold text-amber-300">
                Confirme a variação exata de {ambiguityProduct.exact_model}
              </p>
              <p className="mt-1 text-xs text-gray-300">
                Pela imagem não foi possível confirmar a capacidade/voltagem exata. Escolha uma opção abaixo:
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                {ambiguityProduct.ambiguity_options.map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    onClick={() => {
                      onDismissAmbiguity();
                      onSearch({
                        inputText: ambiguityProduct.exact_model,
                        imageFile: null,
                        variationOverride: opt,
                      });
                    }}
                    className="rounded-lg border border-amber-500/40 bg-gray-900 px-3 py-1.5 text-xs font-medium text-amber-200 transition hover:bg-amber-500/20"
                  >
                    {opt}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => {
                    onDismissAmbiguity();
                    onSearch({
                      inputText: ambiguityProduct.exact_model,
                      imageFile,
                      skipAmbiguityPrompt: true,
                    });
                  }}
                  className="rounded-lg border border-gray-700 bg-gray-900 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-800"
                >
                  Buscar versão padrão
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
