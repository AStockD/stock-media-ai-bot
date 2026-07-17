const BASE = '/api/platform';

async function post<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`API ${path}: ${resp.status} ${err}`);
  }
  return resp.json();
}

async function get<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`);
  if (!resp.ok) throw new Error(`API ${path}: ${resp.status}`);
  return resp.json();
}

export interface CookieStatus {
  valid: boolean;
  last_updated?: string;
  cookie_count: number;
  platform: string;
}

export interface LoginStartResponse {
  status: string;
  qr_image?: string;
  message?: string;
  error?: string;
}

export interface LoginStatusResponse {
  status: string;
  cookie_count?: number;
  message?: string;
  error?: string;
}

export const api = {
  cookieStatus: () => get<CookieStatus>('/cookie/status'),
  startLogin: () => post<LoginStartResponse>('/xueqiu/login/start'),
  loginStatus: () => get<LoginStatusResponse>('/xueqiu/login/status'),
  cancelLogin: () => post<{ status: string }>('/xueqiu/login/cancel'),
};
