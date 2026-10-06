// Typed fetch client for the Studio API (docs/ui/API.md).
//
//   import { api, ApiError } from "@/lib/api";
//   const meta = await api.get<Meta>("/api/meta");
//   await api.post<JobStarted>("/api/jobs", body);
//   await api.put<Lab>(`/api/labs/${id}`, { yaml }, { ifMatch: etag });
//   const es = openEventSource(`/api/jobs/${id}/events`, { lastEventId });
//
// Auth: in production the server sets an HttpOnly cookie on first load (the browser sends it; JS
// never sees it). In `npm run dev` the page is served by Vite, so a `?token=` in the address bar is
// captured once, kept in sessionStorage for this tab, stripped from the URL, and sent as the
// X-Decider-Lab-Token header (and as ?token= on EventSource URLs, which cannot set headers).
// Writes always carry X-Studio: 1 (the server's CSRF guard).

import type { ApiErrorBody, ErrorCode } from "@/api/types";

const TOKEN_KEY = "dl-dev-token";
let token: string | null = null;

/** Read `?token=` once at startup, remember it for this tab and remove it from the address bar. */
export function captureTokenFromUrl(): void {
  try {
    const url = new URL(window.location.href);
    const t = url.searchParams.get("token");
    if (t) {
      token = t;
      sessionStorage.setItem(TOKEN_KEY, t);
      url.searchParams.delete("token");
      window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    } else {
      token = sessionStorage.getItem(TOKEN_KEY);
    }
  } catch {
    /* storage blocked: the cookie still works */
  }
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: ErrorCode;
  readonly hint?: string;
  readonly detail?: Record<string, unknown>;
  readonly requestId: string;

  constructor(status: number, body: ApiErrorBody["error"]) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.hint = body.hint;
    this.detail = body.detail;
    this.requestId = body.request_id;
  }

  static is(e: unknown, code?: ErrorCode): e is ApiError {
    return e instanceof ApiError && (code === undefined || e.code === code);
  }
}

export type Query = Record<string, string | number | boolean | null | undefined | string[]>;

export type RequestOptions = {
  query?: Query;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Sends If-Match (labs PUT/fix-secret). */
  ifMatch?: string;
  /** Return the raw Response (downloads, ETag headers). */
  raw?: boolean;
};

export function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v === undefined || v === null || v === "") continue;
    params.set(k, Array.isArray(v) ? v.join(",") : String(v));
  }
  const qs = params.toString();
  return qs ? `${path}${path.includes("?") ? "&" : "?"}${qs}` : path;
}

/** Percent-encode one path segment (suite names like `synthetic+cal`, refs like `file:…`). */
export const seg = (s: string) => encodeURIComponent(s);

const listeners = new Set<(e: ApiError) => void>();
/** Called for every 401 so the shell can show "Session expired". */
export function onUnauthorized(fn: (e: ApiError) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function request<T>(method: string, path: string, body?: unknown, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
  if (method !== "GET" && method !== "HEAD") headers["X-Studio"] = "1";
  if (token) headers["X-Decider-Lab-Token"] = token;
  if (opts.ifMatch) headers["If-Match"] = opts.ifMatch;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method,
      headers,
      body: payload,
      credentials: "same-origin",
      signal: opts.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, {
      code: "network",
      message: "Could not reach the Studio server.",
      hint: "Check that `decider-lab ui` is still running.",
      request_id: "",
    });
  }
  if (!res.ok) {
    let err: ApiErrorBody["error"];
    try {
      err = ((await res.json()) as ApiErrorBody).error;
    } catch {
      err = { code: res.status >= 500 ? "internal" : "bad_request", message: `Request failed (${res.status}).`, request_id: res.headers.get("X-Request-Id") ?? "" };
    }
    const apiErr = new ApiError(res.status, err);
    if (res.status === 401) listeners.forEach((fn) => fn(apiErr));
    throw apiErr;
  }
  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("Content-Type") ?? "";
  return (type.includes("json") ? await res.json() : await res.text()) as T;
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => request<T>("GET", path, undefined, opts),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>("POST", path, body ?? {}, opts),
  put: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>("PUT", path, body ?? {}, opts),
  patch: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>("PATCH", path, body ?? {}, opts),
  del: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>("DELETE", path, body, opts),
};

/** URL for a download link or <a href> (adds the dev token when there is one). */
export function fileUrl(path: string, query?: Query): string {
  return buildUrl(path, token ? { ...query, token } : query);
}

/** EventSource for an SSE endpoint. Pass `lastEventId` to resume (sent as a query param, since
 *  EventSource only sets Last-Event-ID itself on automatic reconnects). */
export function openEventSource(path: string, query?: Query): EventSource {
  return new EventSource(fileUrl(path, query), { withCredentials: true });
}

/** Parse an SSE MessageEvent's JSON data safely. */
export function eventData<T>(e: MessageEvent): T | null {
  try {
    return JSON.parse(e.data) as T;
  } catch {
    return null;
  }
}
