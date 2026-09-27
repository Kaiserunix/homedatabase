/**
 * HTTP 客户端：所有 API 请求的统一出口（resources.ts 的 http 分支经此访问）。
 *
 * 会话设计：后端以 HttpOnly Cookie 维持登录会话，浏览器每次请求自动携带，
 * 前端不保存、不读取任何令牌（无 Bearer、无 localStorage），因此每个请求都带
 * credentials: "include"。登录/登出只调用 /auth/login、/auth/logout，
 * Cookie 的设置与清除完全由后端完成；401 一律视为会话过期，交由路由层跳登录页。
 */

export class ApiError extends Error {
  /** HTTP 状态码；网络异常/超时为 0 */
  readonly status: number;
  readonly code?: string;
  /** 服务端返回的原始错误负载（如 FastAPI 422 的校验明细） */
  readonly details?: unknown;

  constructor(message: string, status: number, opts?: { code?: string; details?: unknown }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = opts?.code;
    this.details = opts?.details;
  }
}

/**
 * 会话过期广播：任一请求收到 401（页面打开后会话失效也算）时触发，
 * AuthProvider 据此把全局会话转为 anon，由路由守卫跳登录页并保留返回路径。
 */
const sessionExpiredListeners = new Set<() => void>();

export function onSessionExpired(listener: () => void): () => void {
  sessionExpiredListeners.add(listener);
  return () => {
    sessionExpiredListeners.delete(listener);
  };
}

function emitSessionExpired(): void {
  for (const listener of sessionExpiredListeners) listener();
}

export interface ApiFetchOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** JSON 对象或 FormData（FormData 原样透传，浏览器自设 multipart 边界） */
  body?: unknown;
  /** query 参数；undefined/null/空串自动跳过 */
  params?: Record<string, string | number | boolean | null | undefined>;
  /** 超时毫秒数，默认 15000 */
  timeout?: number;
}

function baseUrl(): string {
  // 缺省 /api：同源反代到后端；跨域部署时由 VITE_API_BASE 指定完整地址
  return (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";
}

function buildUrl(path: string, params?: ApiFetchOptions["params"]): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    qs.set(key, String(value));
  }
  const query = qs.toString();
  return `${baseUrl()}${path}${query ? `?${query}` : ""}`;
}

/** 非 2xx 归一化：尽量解析 FastAPI 的 detail 字段（字符串或 422 校验数组） */
async function toApiError(res: Response): Promise<ApiError> {
  let message = `请求失败（HTTP ${res.status}）`;
  let details: unknown;
  try {
    const data: unknown = await res.json();
    const detail = (data as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") {
      message = detail;
    } else if (Array.isArray(detail)) {
      // FastAPI 422: [{ loc: [...], msg: "...", type: "..." }]
      message = detail
        .map((d: { msg?: unknown }) => (typeof d?.msg === "string" ? d.msg : JSON.stringify(d)))
        .join("；");
      details = detail;
    } else if (detail !== undefined) {
      details = detail;
    }
  } catch {
    // 响应体非 JSON，保留默认文案
  }
  return new ApiError(message, res.status, { details });
}

async function rawFetch<T>(path: string, opts: ApiFetchOptions & { method: string }): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeout ?? 15000);
  try {
    const isFormData = typeof FormData !== "undefined" && opts.body instanceof FormData;
    const res = await fetch(buildUrl(path, opts.params), {
      method: opts.method,
      signal: controller.signal,
      // HttpOnly 会话 Cookie 随请求自动携带；前端不持有任何令牌
      credentials: "include",
      headers: {
        // FormData 由浏览器设置 multipart 边界，这里绝不手动设 Content-Type
        ...(opts.body !== undefined && !isFormData ? { "Content-Type": "application/json" } : {}),
      },
      body:
        opts.body === undefined
          ? undefined
          : isFormData
            ? (opts.body as FormData)
            : JSON.stringify(opts.body),
    });
    if (res.status === 401) {
      // 会话 Cookie 缺失或过期：广播会话过期（AuthProvider 转 anon 并跳登录页），不做前端刷新
      emitSessionExpired();
      throw new ApiError("登录已过期，请重新登录", 401, { code: "UNAUTHORIZED" });
    }
    if (!res.ok) throw await toApiError(res);
    if (res.status === 204) return undefined as T;
    const contentType = res.headers.get("content-type") ?? "";
    if (contentType.includes("application/json")) return (await res.json()) as T;
    // 非 JSON 响应（如报表 XLSX、备份下载的文件流）按 Blob 返回
    return (await res.blob()) as unknown as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("请求超时，请稍后重试", 0, { code: "TIMEOUT" });
    }
    throw new ApiError("网络异常，请检查网络连接", 0, { code: "NETWORK" });
  } finally {
    clearTimeout(timer);
  }
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * GET 幂等：网络/超时/5xx 失败自动重试 1 次，指数退避 300ms 起；
 * 4xx 属于请求本身的问题，不重试。
 */
export async function apiFetch<T>(path: string, opts: ApiFetchOptions = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const attempts = method === "GET" ? 2 : 1;
  let lastErr: unknown;
  for (let attempt = 0; attempt < attempts; attempt++) {
    if (attempt > 0) await sleep(300 * 2 ** (attempt - 1));
    try {
      return await rawFetch<T>(path, { ...opts, method });
    } catch (err) {
      lastErr = err;
      const retryable = err instanceof ApiError && (err.status === 0 || err.status >= 500);
      if (!retryable) throw err;
    }
  }
  throw lastErr;
}
