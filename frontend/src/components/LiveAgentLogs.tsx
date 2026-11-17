"use client";

import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { CheckCircle2, Loader2, AlertTriangle, Terminal } from "lucide-react";
import { SSEEvent } from "@/types/radar";

interface LiveAgentLogsProps {
  logs: SSEEvent[];
  isSearching: boolean;
}

export function LiveAgentLogs({ logs, isSearching }: LiveAgentLogsProps) {
  if (logs.length === 0) return null;

  const latestProgress = logs[logs.length - 1]?.progress_pct ?? 0;

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="mx-auto mb-8 w-full max-w-4xl rounded-2xl border border-gray-800 bg-radar-surface/90 p-4 shadow-xl"
    >
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Terminal className="h-4 w-4 text-emerald-400" />
          <span className="font-mono text-xs font-semibold uppercase tracking-wider text-gray-300">
            Live Agent Pipeline
          </span>
        </div>
        <span className="font-mono text-xs text-emerald-400">
          {latestProgress}%
        </span>
      </div>

      <div className="mb-4 h-1.5 w-full overflow-hidden rounded-full bg-gray-800">
        <motion.div
          className="h-full bg-gradient-to-r from-emerald-500 via-cyan-400 to-emerald-400"
          initial={{ width: 0 }}
          animate={{ width: `${latestProgress}%` }}
          transition={{ duration: 0.35 }}
        />
      </div>

      <div className="max-h-48 space-y-2 overflow-y-auto pr-1 font-mono text-xs">
        <AnimatePresence initial={false}>
          {logs.map((log, idx) => {
            const isLastRunning =
              isSearching && idx === logs.length - 1 && log.status === "running";
            return (
              <motion.div
                key={`${log.step}-${idx}`}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                className="flex items-start gap-2.5 rounded-lg bg-gray-900/60 px-3 py-2"
              >
                {isLastRunning ? (
                  <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-cyan-400" />
                ) : log.status === "warning" || log.status === "error" ? (
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
                ) : (
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
                )}
                <div className="flex-1 text-gray-300">
                  <span className="mr-2 text-gray-500">
                    [Agente {log.step || "!"}]
                  </span>
                  <span>{log.message}</span>
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </motion.section>
  );
}
