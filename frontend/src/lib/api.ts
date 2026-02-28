import { exportJWK, generateKeyPair, importJWK, SignJWT } from "jose";
import type { JWK, KeyLike } from "jose";

export class ApiError extends Error {
  status: number;

  body: unknown;

  constructor(status: number, body: unknown) {
    super(`API ${status}`);
    this.status = status;
    this.body = body;
  }
}

export function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function clearClientSecurityContext(): void {
  sessionStorage.removeItem(DPOP_PRIVATE_JWK_STORAGE_KEY);
  sessionStorage.removeItem(DPOP_PUBLIC_JWK_STORAGE_KEY);
  dpopStatePromise = null;
}

let refreshInFlight: Promise<boolean> | null = null;
type DpopPublicJwk = Pick<JWK, "kty" | "crv" | "x" | "y">;
type DpopPrivateKey = KeyLike | Uint8Array;
type DpopState = { privateKey: DpopPrivateKey; publicJwk: DpopPublicJwk };
let dpopStatePromise: Promise<DpopState> | null = null;

const DPOP_PRIVATE_JWK_STORAGE_KEY = "dpop_private_jwk";
const DPOP_PUBLIC_JWK_STORAGE_KEY = "dpop_public_jwk";

function readCookie(name: string): string {
  const target = `${name}=`;
  for (const item of document.cookie.split(";")) {
    const trimmed = item.trim();
    if (trimmed.startsWith(target)) {
      return decodeURIComponent(trimmed.slice(target.length));
    }
  }
  return "";
}

function shouldSendCsrf(method: string): boolean {
  return !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isJwk(value: unknown): value is JWK {
  if (!isObject(value)) {
    return false;
  }
  return typeof value.kty === "string";
}

function isDpopPublicJwk(value: unknown): value is DpopPublicJwk {
  if (!isObject(value)) {
    return false;
  }
  return value.kty === "EC" && value.crv === "P-256" && typeof value.x === "string" && typeof value.y === "string";
}

async function getDpopState(): Promise<DpopState> {
  if (dpopStatePromise) {
    return dpopStatePromise;
  }

  dpopStatePromise = (async () => {
    const storedPrivate = sessionStorage.getItem(DPOP_PRIVATE_JWK_STORAGE_KEY);
    const storedPublic = sessionStorage.getItem(DPOP_PUBLIC_JWK_STORAGE_KEY);
    if (storedPrivate && storedPublic) {
      const privateJwk = JSON.parse(storedPrivate) as unknown;
      const publicJwk = JSON.parse(storedPublic) as unknown;
      if (isJwk(privateJwk) && isDpopPublicJwk(publicJwk)) {
        const privateKey = await importJWK(privateJwk, "ES256");
        return { privateKey, publicJwk };
      }
      clearClientSecurityContext();
    }

    const pair = await generateKeyPair("ES256", { extractable: true });
    const privateJwk = await exportJWK(pair.privateKey);
    const publicJwk = await exportJWK(pair.publicKey);
    if (!isJwk(privateJwk) || !isDpopPublicJwk(publicJwk)) {
      throw new Error("Failed to generate valid DPoP JWK.");
    }
    sessionStorage.setItem(DPOP_PRIVATE_JWK_STORAGE_KEY, JSON.stringify(privateJwk));
    sessionStorage.setItem(DPOP_PUBLIC_JWK_STORAGE_KEY, JSON.stringify(publicJwk));
    return { privateKey: pair.privateKey, publicJwk };
  })();

  const statePromise = dpopStatePromise;
  if (!statePromise) {
    throw new Error("Failed to initialize DPoP state.");
  }
  return statePromise;
}

async function createDpopProof(path: string, method: string): Promise<string> {
  const { privateKey, publicJwk } = await getDpopState();
  const htu = new URL(path, window.location.origin).toString();
  return new SignJWT({
    htu,
    htm: method.toUpperCase(),
    jti: crypto.randomUUID(),
    iat: Math.floor(Date.now() / 1000),
  })
    .setProtectedHeader({ typ: "dpop+jwt", alg: "ES256", jwk: publicJwk })
    .sign(privateKey);
}

async function tryRefreshSession(): Promise<boolean> {
  if (refreshInFlight) {
    return refreshInFlight;
  }

  refreshInFlight = (async () => {
    try {
      const dpop = await createDpopProof("/api/auth/refresh", "POST");
      const csrf = readCookie("csrf_token");
      const headers = new Headers({ DPoP: dpop });
      if (csrf) {
        headers.set("x-csrf-token", csrf);
      }
      const response = await fetch("/api/auth/refresh", {
        method: "POST",
        headers,
        credentials: "same-origin",
      });
      return response.ok;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

export async function api<T>(path: string, options: RequestInit = {}, canRetry = true): Promise<T> {
  const headers = new Headers(options.headers ?? {});
  const method = (options.method ?? "GET").toUpperCase();
  headers.set("DPoP", await createDpopProof(path, method));

  if (shouldSendCsrf(method)) {
    const csrf = readCookie("csrf_token");
    if (csrf) {
      headers.set("x-csrf-token", csrf);
    }
  }

  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
  });

  const raw = await response.text();
  let body: unknown = raw;
  try {
    body = JSON.parse(raw);
  } catch {
    // Keep raw text.
  }

  if (response.status === 401 && canRetry && !path.startsWith("/api/auth/")) {
    const refreshed = await tryRefreshSession();
    if (refreshed) {
      return api<T>(path, options, false);
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, body);
  }

  return body as T;
}
