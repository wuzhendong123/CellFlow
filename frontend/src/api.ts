import { getOperator } from "./operator";

export class ApiError extends Error {
  code: string;
  status: number;
  data: any;
  constructor(code: string, message: string, status: number, data: any) {
    super(message);
    this.code = code;
    this.status = status;
    this.data = data;
  }
}

export interface OpRequest {
  title: string;
  summary: string;
  reasonRequired?: boolean;
  danger?: string;
}

export interface OpAnswer {
  token: string;
  operator: string;
  reason: string;
}

type OpAsker = (req: OpRequest) => Promise<OpAnswer | null>;
let asker: OpAsker | null = null;
export function registerOpAsker(fn: OpAsker) {
  asker = fn;
}

function headers(extra?: Record<string, string>): Record<string, string> {
  return { "X-CF-Operator": encodeURIComponent(getOperator() || ""), ...(extra || {}) };
}

async function handle(resp: Response) {
  const text = await resp.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError("BAD_RESPONSE", text.slice(0, 200), resp.status, null);
  }
  if (!resp.ok || (body && body.code && body.code !== "OK")) {
    throw new ApiError(body?.code || "HTTP_" + resp.status, body?.message || resp.statusText, resp.status, body?.data);
  }
  return body?.data;
}

export async function api<T = any>(method: string, path: string, body?: any, extraHeaders?: Record<string, string>): Promise<T> {
  const init: RequestInit = { method, headers: headers(extraHeaders) };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  return handle(await fetch(path, init));
}

export const get = <T = any>(p: string) => api<T>("GET", p);
export const post = <T = any>(p: string, b?: any) => api<T>("POST", p, b ?? {});
export const put = <T = any>(p: string, b?: any) => api<T>("PUT", p, b ?? {});
export const patch = <T = any>(p: string, b?: any) => api<T>("PATCH", p, b ?? {});

/** 高危操作：弹出口令对话框（F16），带操作口令与操作人调用；用户取消返回 null。 */
export async function opCall<T = any>(req: OpRequest, method: string, path: string, body?: (a: OpAnswer) => any): Promise<T | null> {
  if (!asker) throw new Error("口令对话框未注册");
  for (;;) {
    const answer = await asker(req);
    if (!answer) return null;
    try {
      return await api<T>(method, path, body ? body(answer) : { reason: answer.reason }, {
        "X-CF-Op-Token": answer.token,
        "X-CF-Operator": encodeURIComponent(answer.operator),
      });
    } catch (e) {
      if (e instanceof ApiError && e.code === "OP_TOKEN_INVALID") {
        req = { ...req, danger: `口令错误${e.data?.remaining != null ? `，还可尝试 ${e.data.remaining} 次` : ""}` };
        continue;
      }
      throw e;
    }
  }
}

export function upload(file: File): Promise<any> {
  const fd = new FormData();
  fd.append("file", file);
  return api("POST", "/api/files", fd);
}
