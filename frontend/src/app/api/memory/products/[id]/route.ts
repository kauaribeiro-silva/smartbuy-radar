import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE_NAME, verifySessionToken } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function PATCH(
  req: NextRequest,
  context: { params: Promise<{ id: string }> }
) {
  const secret = process.env.APP_ACCESS_PASSWORD || "";
  if (secret) {
    const token = req.cookies.get(SESSION_COOKIE_NAME)?.value;
    if (!(await verifySessionToken(token, secret))) {
      return NextResponse.json({ detail: "Não autorizado." }, { status: 401 });
    }
  }

  const { id } = await context.params;
  const body = await req.text();
  const apiBase =
    process.env.NEXT_PUBLIC_API_URL ||
    process.env.BACKEND_API_URL ||
    "http://localhost:8000";

  const upstream = await fetch(`${apiBase}/api/memory/products/${id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...(secret ? { "X-Radar-Key": secret } : {}),
    },
    body,
  });

  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, { status: upstream.status });
}

export async function DELETE(
  req: NextRequest,
  context: { params: Promise<{ id: string }> }
) {
  const secret = process.env.APP_ACCESS_PASSWORD || "";
  if (secret) {
    const token = req.cookies.get(SESSION_COOKIE_NAME)?.value;
    if (!(await verifySessionToken(token, secret))) {
      return NextResponse.json({ detail: "Não autorizado." }, { status: 401 });
    }
  }

  const { id } = await context.params;
  const apiBase =
    process.env.NEXT_PUBLIC_API_URL ||
    process.env.BACKEND_API_URL ||
    "http://localhost:8000";

  const upstream = await fetch(`${apiBase}/api/memory/products/${id}`, {
    method: "DELETE",
    headers: secret ? { "X-Radar-Key": secret } : undefined,
  });

  const data = await upstream.json().catch(() => ({ ok: true }));
  return NextResponse.json(data, { status: upstream.status });
}
