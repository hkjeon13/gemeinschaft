export class ApiError extends Error {
  status: number;

  body: unknown;

  constructor(status: number, body: unknown) {
    super(`API ${status}`);
    this.status = status;
    this.body = body;
  }
}

export function getToken(): string {
  return localStorage.getItem("access_token") ?? "";
}

export function setToken(token: string): void {
  if (!token) {
    localStorage.removeItem("access_token");
    return;
  }
  localStorage.setItem("access_token", token);
}

export function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers ?? {});
  const token = getToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(path, {
    ...options,
    headers,
  });

  const raw = await response.text();
  let body: unknown = raw;
  try {
    body = JSON.parse(raw);
  } catch {
    // Keep raw text.
  }

  if (!response.ok) {
    throw new ApiError(response.status, body);
  }

  return body as T;
}
