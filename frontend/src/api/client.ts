// API 客户端：access token 仅存内存；refresh 走 HttpOnly cookie＋CSRF（ADR-0012）。
// 写请求携带 Idempotency-Key；结果未知时抛出 UnknownOutcome，调用方用同一 key 查询或重试。
import { currentLang, tr } from "../lib/i18n";
import type { components } from "./schema";

export type Schemas = components["schemas"];
export type Entry = Schemas["Entry"];
export type Dashboard = Schemas["Dashboard"];
export type Me = Schemas["Me"];
export type CategoryTree = Schemas["CategoryTree"];
export type Category = Schemas["Category"];
export type Currency = Schemas["Currency"];
export type Member = Schemas["Member"];
export type FxPreview = Schemas["FxPreview"];

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: { fields?: { field: string; code: string }[]; current?: unknown },
  ) {
    super(message);
  }
}

/** 写请求已发出但结果未知（网络中断／503 upstream_unknown）。不得换新 key 重试。 */
export class UnknownOutcome extends Error {
  constructor(public key: string, public path: string) {
    super(tr("提交结果未知：请先查询结果，再决定是否重试", "The result is unknown: check whether it was saved before trying again"));
  }
}

let accessToken: string | null = null;
let onUnauthenticated: () => void = () => {};

export function setAccessToken(token: string | null) {
  accessToken = token;
}
export function setUnauthenticatedHandler(fn: () => void) {
  onUnauthenticated = fn;
}

export function newKey(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

function csrfToken(): string {
  return document.cookie.split("; ").find((c) => c.startsWith("ledger_csrf="))?.split("=")[1] ?? "";
}

async function parse(res: Response): Promise<unknown> {
  if (res.status === 204) return null;
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

function toError(status: number, body: unknown): ApiError {
  const err = (body as { error?: { code: string; message: string; details?: ApiError["details"] } })?.error;
  return new ApiError(status, err?.code ?? "internal", err?.message ?? tr(`请求失败（${status}）`, `Request failed (${status})`), err?.details);
}

export async function refreshSession(): Promise<boolean> {
  const res = await fetch("/v1/auth/refresh", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrfToken() },
  });
  if (!res.ok) return false;
  const body = (await parse(res)) as { access_token: string };
  setAccessToken(body.access_token);
  return true;
}

type Options = { query?: Record<string, string | number | boolean | undefined>; key?: string; auth?: boolean };

export async function request<T>(method: string, path: string, body?: unknown, opts: Options = {}): Promise<T> {
  const write = method !== "GET";
  const key = write ? opts.key ?? newKey() : undefined;
  const qs = opts.query
    ? "?" + new URLSearchParams(Object.entries(opts.query).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)])).toString()
    : "";
  const send = () =>
    fetch(`/v1${path}${qs}`, {
      method,
      credentials: "same-origin",
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(accessToken && opts.auth !== false ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...(key ? { "Idempotency-Key": key } : {}),
        "X-Ledger-Lang": currentLang(), // 服务端据此返回英文错误信息
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  let res: Response;
  try {
    res = await send();
  } catch {
    if (write) throw new UnknownOutcome(key!, path);
    throw new ApiError(0, "network", tr("网络连接失败，请稍后重试", "Network error. Please try again."));
  }
  if (res.status === 401 && opts.auth !== false && accessToken) {
    // 同一 key 重试是安全的：服务端按回执去重
    if (await refreshSession()) {
      try {
        res = await send();
      } catch {
        if (write) throw new UnknownOutcome(key!, path);
        throw new ApiError(0, "network", tr("网络连接失败，请稍后重试", "Network error. Please try again."));
      }
    }
  }
  const data = await parse(res);
  if (res.ok) return data as T;
  const err = toError(res.status, data);
  if (err.code === "upstream_unknown" && write) throw new UnknownOutcome(key!, path);
  if (res.status === 401) onUnauthenticated();
  throw err;
}

export const api = {
  get: <T>(path: string, query?: Options["query"]) => request<T>("GET", path, undefined, { query }),
  post: <T>(path: string, body?: unknown, opts?: Options) => request<T>("POST", path, body, opts),
  patch: <T>(path: string, body?: unknown, opts?: Options) => request<T>("PATCH", path, body, opts),
  del: <T>(path: string, opts?: Options) => request<T>("DELETE", path, undefined, opts),
};
