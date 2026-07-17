const BASE = '';

async function post<T>(path: string, body?: unknown, token?: string): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || `API ${path}: ${resp.status}`);
  }
  return resp.json();
}

async function get<T>(path: string, token?: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const resp = await fetch(`${BASE}${path}`, { headers });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || `API ${path}: ${resp.status}`);
  }
  return resp.json();
}

export interface UserInfo {
  id: number;
  username: string;
  role: string;
}

export interface TokenResponse {
  token: string;
  user: UserInfo;
}

export interface PlatformAccount {
  platform: string;
  account_name: string | null;
  is_valid: boolean;
  last_updated: string | null;
  cookie_count: number;
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

export interface Strategy {
  id: string;
  name: string;
}

export interface StockSelectionRecord {
  code: string;
  name: string;
  sector: string;
  selection_date: string;
  timestamp: string;
  source: string;
  sub_sources: string[];
  overall_score: number;
  sentiment_norm: number;
  tick_norm: number;
  flow_norm: number;
  tech_norm: number;
  kline_norm: number;
  price: number;
  pct_change: number;
}

export const authApi = {
  register: (username: string, password: string) =>
    post<TokenResponse>('/api/auth/register', { username, password }),
  login: (username: string, password: string) =>
    post<TokenResponse>('/api/auth/login', { username, password }),
  me: (token: string) =>
    get<UserInfo>('/api/auth/me', token),
};

export const platformApi = {
  getAccounts: (token: string) =>
    get<{ accounts: PlatformAccount[] }>('/api/platform/accounts', token),

  startLogin: (platform: string, token: string) =>
    post<LoginStartResponse>(`/api/platform/${platform}/login/start`, undefined, token),

  loginStatus: (platform: string, token: string) =>
    get<LoginStatusResponse>(`/api/platform/${platform}/login/status`, token),

  cancelLogin: (platform: string, token: string) =>
    post<{ status: string }>(`/api/platform/${platform}/login/cancel`, undefined, token),

  createPost: (platform: string, content: string, token: string, imageUrl?: string) =>
    post<{ success: boolean; message?: string; url?: string; error?: string }>(
      `/api/platform/${platform}/post`,
      { content, image_url: imageUrl },
      token,
    ),

  createComment: (platform: string, postId: number, content: string, token: string) =>
    post<{ success: boolean; message?: string; error?: string }>(
      `/api/platform/${platform}/comment`,
      { post_id: postId, content },
      token,
    ),
};

export const stockSelectionApi = {
  getStrategies: (token: string) =>
    get<{ strategies: Strategy[] }>('/api/stock-selection/strategies', token),

  getRecords: (source: string, token: string) =>
    get<{ source: string; records: StockSelectionRecord[]; count: number }>(
      `/api/stock-selection/records?source=${encodeURIComponent(source)}`,
      token,
    ),
};
