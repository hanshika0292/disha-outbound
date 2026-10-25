import { cookies } from "next/headers";
import { NextResponse } from "next/server";

/**
 * Marks the browser session for the route-guard middleware AFTER the client
 * has authenticated against the backend (POST {API}/auth/login). The real
 * security boundary is the per-user API token; this cookie only gates page
 * navigation so unauthenticated visitors land on /login.
 */
export async function POST() {
  const cookieStore = await cookies();
  cookieStore.set("disha_session", "1", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 30,
    path: "/",
  });
  return NextResponse.json({ ok: true });
}
