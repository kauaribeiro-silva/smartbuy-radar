import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE_NAME, verifySessionToken } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const secret = process.env.APP_ACCESS_PASSWORD || "";
  if (secret) {
    const token = req.cookies.get(SESSION_COOKIE_NAME)?.value;
    const valid = await verifySessionToken(token, secret);
    if (!valid) {
      return NextResponse.json({ detail: "Não autorizado." }, { status: 401 });
    }
  }

  const apiBase =
    process.env.NEXT_PUBLIC_API_URL ||
    process.env.BACKEND_API_URL ||
    "http://localhost:8000";

  const formData = await req.formData();

  const upstream = await fetch(`${apiBase}/api/radar/stream`, {
    method: "POST",
    headers: secret ? { "X-Radar-Key": secret } : undefined,
    body: formData,
  });

  if (!upstream.ok || !upstream.body) {
    const errText = await upstream.text();
    return new NextResponse(errText || "Erro no backend.", {
      status: upstream.status || 502,
    });
  }

  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
