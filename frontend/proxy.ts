import { NextResponse, type NextRequest } from "next/server";
import { GATE_COOKIE, gatePassword, gateToken, sameText } from "@/lib/gate";

/** Password gate in front of every page and the backend proxy, when SITE_PASSWORD is set. */
export async function proxy(request: NextRequest) {
  const password = gatePassword();
  if (!password) return NextResponse.next();
  const cookie = request.cookies.get(GATE_COOKIE)?.value ?? "";
  if (cookie && sameText(cookie, await gateToken(password))) return NextResponse.next();

  const { pathname, search } = request.nextUrl;
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ detail: "Sign in first" }, { status: 401 });
  }
  const login = new URL("/login", request.url);
  login.searchParams.set("next", pathname + search);
  return NextResponse.redirect(login);
}

export const config = {
  // Everything except the login page/route and static assets.
  matcher: ["/((?!login|api/login|_next/static|_next/image|favicon.ico).*)"],
};
