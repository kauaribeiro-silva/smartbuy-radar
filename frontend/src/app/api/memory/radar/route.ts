import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE_NAME, verifySessionToken } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const secret = process.env.APP_ACCESS_PASSWORD || "";
  if (secret) {
    const token = req.cookies.get(SESSION_COOKIE_NAME)?.value;
    if (!(await verifySessionToken(token, secret))) {
      return NextResponse.json({ detail: "Não autorizado." }, { status: 401 });
    }
  }

  const apiBase =
    process.env.NEXT_PUBLIC_API_URL ||
    process.env.BACKEND_API_URL ||
    "http://localhost:8000";

  const upstream = await fetch(`${apiBase}/api/memory/radar`, {
    headers: secret ? { "X-Radar-Key": secret } : undefined,
    cache: "no-store",
  });

  const data = await upstream.json().catch(() => ({
    items: [],
    alerts: [],
    recommendations: [],
  }));
  return NextResponse.json(data, { status: upstream.status });
}
