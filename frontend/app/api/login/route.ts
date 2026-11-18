import { GATE_COOKIE, gatePassword, gateToken, sameText } from "@/lib/gate";

/** POST {password} → sets the gate cookie (30 days) when it matches SITE_PASSWORD. */
export async function POST(request: Request) {
  const expected = gatePassword();
  if (!expected) return Response.json({ ok: true });
  let password = "";
  try {
    const body = (await request.json()) as { password?: unknown };
    password = typeof body.password === "string" ? body.password : "";
  } catch {
    /* not JSON */
  }
  const [given, want] = await Promise.all([gateToken(password), gateToken(expected)]);
  if (!sameText(given, want)) {
    await new Promise((r) => setTimeout(r, 600)); // slow down guessing
    return Response.json({ detail: "Wrong password" }, { status: 401 });
  }
  const res = Response.json({ ok: true });
  const secure = new URL(request.url).protocol === "https:" ? "; Secure" : "";
  res.headers.append(
    "set-cookie",
    `${GATE_COOKIE}=${want}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${60 * 60 * 24 * 30}${secure}`,
  );
  return res;
}
