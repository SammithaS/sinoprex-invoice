/**
 * Optional site-wide password gate (no user accounts yet). Off unless SITE_PASSWORD is set on
 * the server. The cookie holds a SHA-256 of the password with a fixed prefix, never the
 * password itself; changing SITE_PASSWORD logs everyone out.
 */
export const GATE_COOKIE = "iiq_gate";

export function gatePassword(): string {
  return process.env.SITE_PASSWORD ?? "";
}

export async function gateToken(password: string): Promise<string> {
  const data = new TextEncoder().encode(`invoiceiq-gate:${password}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Constant-time string comparison (same length → compares every character). */
export function sameText(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
