/**
 * Disha API client. The auth token is the PER-USER signed token issued by
 * POST /auth/login and held in localStorage — no shared secret ships in the
 * bundle anymore. 401 responses clear the token and bounce to /login.
 */

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const TOKEN_KEY = "disha_user_token";

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown, msg?: string) {
    super(msg ?? `API ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

export type FetchOpts = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  /** Multipart (FormData) body — skips JSON encoding & Content-Type. */
  formData?: FormData;
  /** Query string params. */
  params?: Record<string, string | number | boolean | undefined>;
  /** AbortSignal for cancellation. */
  signal?: AbortSignal;
};

function buildUrl(path: string, params?: FetchOpts["params"]): string {
  const url = new URL(
    path.startsWith("http") ? path : `${API_BASE}${path}`,
  );
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null) continue;
      url.searchParams.set(k, String(v));
    }
  }
  return url.toString();
}

export async function apiFetch<T = unknown>(
  path: string,
  opts: FetchOpts = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.formData) {
    body = opts.formData;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  const res = await fetch(buildUrl(path, opts.params), {
    method: opts.method ?? "GET",
    headers,
    body,
    signal: opts.signal,
    cache: "no-store",
  });

  // 204 No Content
  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }

  if (!res.ok) {
    // Expired/invalid session → clean up and send the user to login.
    // (Skip for the login call itself so its own 401 shows inline.)
    if (
      res.status === 401 &&
      typeof window !== "undefined" &&
      !path.includes("/auth/login") &&
      !window.location.pathname.startsWith("/login")
    ) {
      clearToken();
      window.location.href = "/login";
    }
    const detailMsg =
      (parsed as { detail?: string; title?: string } | undefined)?.detail ??
      (parsed as { title?: string } | undefined)?.title ??
      `HTTP ${res.status}`;
    throw new ApiError(res.status, parsed, String(detailMsg));
  }
  return parsed as T;
}

/** SWR fetcher that bakes in apiFetch. */
export const swrFetcher = <T = unknown>(path: string): Promise<T> =>
  apiFetch<T>(path);

export const API_BASE_URL = API_BASE;
