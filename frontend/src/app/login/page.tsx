"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Lock, Radar, ArrowRight, AlertCircle } from "lucide-react";

export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password.trim()) return;
    setLoading(true);
    setError(null);

    try {
      const res = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.error || "Senha incorreta.");
        return;
      }

      router.push("/");
      router.refresh();
    } catch {
      setError("Erro ao validar acesso.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-radar-grid px-4">
      <div className="w-full max-w-md rounded-2xl border border-gray-800 bg-radar-surface/95 p-6 shadow-2xl">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
            <Lock className="h-6 w-6" />
          </div>
          <div className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-400">
            <Radar className="h-3.5 w-3.5" />
            <span>SmartBuy Radar • Uso Privado</span>
          </div>
          <h1 className="mt-1 text-2xl font-bold text-white">Acesso Restrito</h1>
          <p className="mt-1 text-xs text-gray-400">
            Digite sua senha pessoal para liberar o radar de compras.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Sua senha de acesso..."
              autoFocus
              className="w-full rounded-xl border border-gray-800 bg-gray-900/90 px-4 py-3 text-sm text-white placeholder-gray-500 outline-none focus:border-emerald-500/60"
            />
          </div>

          {error && (
            <div className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
              <AlertCircle className="h-4 w-4 shrink-0 text-red-400" />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !password.trim()}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-emerald-500 to-cyan-500 px-4 py-3 text-sm font-semibold text-gray-950 transition hover:brightness-110 disabled:opacity-50"
          >
            <span>{loading ? "Verificando..." : "Entrar"}</span>
            <ArrowRight className="h-4 w-4" />
          </button>
        </form>
      </div>
    </main>
  );
}
