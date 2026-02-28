// DPoP (Demonstration of Proof-of-Possession) 구현

let dpopKeyPair: CryptoKeyPair | null = null;
const DPOP_PRIVATE_JWK_STORAGE_KEY = 'dpop_private_jwk';
const DPOP_PUBLIC_JWK_STORAGE_KEY = 'dpop_public_jwk';

// 디버그 모드 (개발 시 true로 설정)
const DEBUG = true;

function clearStoredDpopKeys(): void {
  try {
    sessionStorage.removeItem(DPOP_PRIVATE_JWK_STORAGE_KEY);
    sessionStorage.removeItem(DPOP_PUBLIC_JWK_STORAGE_KEY);
  } catch {
    // Ignore storage failures in restricted environments.
  }
}

async function restoreDpopKeyPair(): Promise<CryptoKeyPair | null> {
  try {
    const storedPrivate = sessionStorage.getItem(DPOP_PRIVATE_JWK_STORAGE_KEY);
    const storedPublic = sessionStorage.getItem(DPOP_PUBLIC_JWK_STORAGE_KEY);
    if (!storedPrivate || !storedPublic) {
      return null;
    }

    const privateJwk = JSON.parse(storedPrivate) as JsonWebKey;
    const publicJwk = JSON.parse(storedPublic) as JsonWebKey;

    const privateKey = await crypto.subtle.importKey(
      'jwk',
      privateJwk,
      { name: 'ECDSA', namedCurve: 'P-256' },
      true,
      ['sign']
    );
    const publicKey = await crypto.subtle.importKey(
      'jwk',
      publicJwk,
      { name: 'ECDSA', namedCurve: 'P-256' },
      true,
      ['verify']
    );

    return { privateKey, publicKey };
  } catch {
    clearStoredDpopKeys();
    return null;
  }
}

async function persistDpopKeyPair(keyPair: CryptoKeyPair): Promise<void> {
  try {
    const privateJwk = await crypto.subtle.exportKey('jwk', keyPair.privateKey);
    const publicJwk = await crypto.subtle.exportKey('jwk', keyPair.publicKey);
    sessionStorage.setItem(DPOP_PRIVATE_JWK_STORAGE_KEY, JSON.stringify(privateJwk));
    sessionStorage.setItem(DPOP_PUBLIC_JWK_STORAGE_KEY, JSON.stringify(publicJwk));
  } catch {
    // Ignore storage failures in restricted environments.
  }
}

// DPoP 키 쌍 생성 또는 가져오기
async function getDpopKeyPair(): Promise<CryptoKeyPair> {
  if (dpopKeyPair) {
    return dpopKeyPair;
  }

  const restored = await restoreDpopKeyPair();
  if (restored) {
    dpopKeyPair = restored;
    return dpopKeyPair;
  }

  // ECDSA P-256 키 쌍 생성
  dpopKeyPair = await crypto.subtle.generateKey(
    {
      name: 'ECDSA',
      namedCurve: 'P-256',
    },
    true,
    ['sign', 'verify']
  );
  await persistDpopKeyPair(dpopKeyPair);

  return dpopKeyPair;
}

// 공개 키를 JWK 형식으로 내보내기
async function exportPublicKeyAsJWK(publicKey: CryptoKey): Promise<JsonWebKey> {
  return await crypto.subtle.exportKey('jwk', publicKey);
}

// Base64URL 인코딩
function base64UrlEncode(data: Uint8Array | string): string {
  const base64 = typeof data === 'string' 
    ? btoa(data) 
    : btoa(String.fromCharCode(...new Uint8Array(data)));
  return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

// DPoP proof JWT 생성
export async function generateDpopProof(
  method: string,
  url: string
): Promise<string> {
  const keyPair = await getDpopKeyPair();
  const jwk = await exportPublicKeyAsJWK(keyPair.publicKey);

  // URL 정규화: query string과 fragment 제거
  const normalizedUrl = new URL(url);
  const htu = `${normalizedUrl.origin}${normalizedUrl.pathname}`;

  // JWT 헤더
  const header = {
    alg: 'ES256',
    typ: 'dpop+jwt',
    jwk: {
      kty: jwk.kty,
      crv: jwk.crv,
      x: jwk.x,
      y: jwk.y,
    },
  };

  // JWT 페이로드
  const jti = crypto.randomUUID();
  const iat = Math.floor(Date.now() / 1000);
  
  const payload = {
    jti,
    htm: method.toUpperCase(),
    htu,
    iat,
  };

  if (DEBUG) {
    console.log('[DPoP] Generating proof:', {
      method: payload.htm,
      url: htu,
      originalUrl: url,
      jti,
      iat,
      iatDate: new Date(iat * 1000).toISOString(),
    });
  }

  const headerEncoded = base64UrlEncode(JSON.stringify(header));
  const payloadEncoded = base64UrlEncode(JSON.stringify(payload));
  const message = `${headerEncoded}.${payloadEncoded}`;

  // 서명 생성
  const encoder = new TextEncoder();
  const signature = await crypto.subtle.sign(
    {
      name: 'ECDSA',
      hash: 'SHA-256',
    },
    keyPair.privateKey,
    encoder.encode(message)
  );

  const signatureEncoded = base64UrlEncode(new Uint8Array(signature));
  const dpopProof = `${message}.${signatureEncoded}`;
  
  if (DEBUG) {
    console.log('[DPoP] Generated proof (first 50 chars):', dpopProof.substring(0, 50) + '...');
  }
  
  return dpopProof;
}
