// ApiClient: fetch wrapper with the bearer header, one refresh on 401 followed by a logout,
// and the error envelope mapped to ApiError. download() fetches a CSV and saves it.
import type { ApiErrorBody } from './types';

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '/api/v1';
export const C2_BASE: string = import.meta.env.VITE_C2_BASE ?? '/c2/api/v1';
export const C3_BASE: string = import.meta.env.VITE_C3_BASE ?? '/c3/api/v1';

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details?: Record<string, unknown>) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details ?? {};
  }

  /** details.field of a 400/422 answer, when the API names one. */
  get field(): string | undefined {
    const field = this.details.field;
    return typeof field === 'string' ? field : undefined;
  }

  get rule(): string | undefined {
    const rule = this.details.rule;
    return typeof rule === 'string' ? rule : undefined;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function errorMessage(error: unknown, fallback = 'Something went wrong'): string {
  if (isApiError(error)) return error.message || fallback;
  if (error instanceof Error) return error.message || fallback;
  return fallback;
}

type TokenSource = {
  getToken: () => string | null;
  refresh: () => Promise<string | null>;
  onAuthLost: () => void;
};

let tokenSource: TokenSource = {
  getToken: () => null,
  refresh: async () => null,
  onAuthLost: () => undefined,
};

export function bindTokenSource(source: TokenSource): void {
  tokenSource = source;
}

export function getAccessToken(): string | null {
  return tokenSource.getToken();
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}${path.includes('?') ? '&' : '?'}${qs}` : path;
}

async function parseError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody | null = null;
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    body = null;
  }
  const err = body?.error;
  return new ApiError(
    response.status,
    err?.code ?? `HTTP_${response.status}`,
    err?.message ?? `${response.status} ${response.statusText}`,
    err?.details,
  );
}

interface RequestOptions {
  base?: string;
  query?: Query;
  auth?: boolean;
  retry?: boolean;
  headers?: Record<string, string>;
}

async function request<T>(method: string, path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
  const base = options.base ?? API_BASE;
  const url = withQuery(`${base}${path}`, options.query);
  const headers: Record<string, string> = { Accept: 'application/json', ...(options.headers ?? {}) };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const token = tokenSource.getToken();
  if (options.auth !== false && token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'include',
  });
  if (response.status === 401 && options.auth !== false && options.retry !== false && base === API_BASE) {
    const fresh = await tokenSource.refresh();
    if (fresh) return request<T>(method, path, body, { ...options, retry: false });
    tokenSource.onAuthLost();
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const ApiClient = {
  get<T>(path: string, query?: Query, options?: RequestOptions): Promise<T> {
    return request<T>('GET', path, undefined, { ...options, query });
  },
  post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return request<T>('POST', path, body ?? {}, options);
  },
  put<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return request<T>('PUT', path, body ?? {}, options);
  },
  patch<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return request<T>('PATCH', path, body ?? {}, options);
  },
  /** Fetches a file with the bearer header and triggers a browser download. Returns the file name. */
  async download(path: string, query?: Query, fallbackName = 'export.csv'): Promise<string> {
    const token = tokenSource.getToken();
    const response = await fetch(withQuery(`${API_BASE}${path}`, query), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      credentials: 'include',
    });
    if (!response.ok) throw await parseError(response);
    const disposition = response.headers.get('Content-Disposition') ?? '';
    const match = /filename="?([^";]+)"?/.exec(disposition);
    const name = match?.[1] ?? fallbackName;
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(href), 2000);
    return name;
  },
};

/** C2 and C3 calls go through the same wrapper with another base path. */
export const c2 = {
  get<T>(path: string, query?: Query): Promise<T> {
    return request<T>('GET', path, undefined, { base: C2_BASE, query, retry: false });
  },
};

export const c3 = {
  get<T>(path: string, query?: Query): Promise<T> {
    return request<T>('GET', path, undefined, { base: C3_BASE, query, retry: false });
  },
  post<T>(path: string, body?: unknown, query?: Query): Promise<T> {
    return request<T>('POST', path, body ?? {}, { base: C3_BASE, query, retry: false });
  },
};
