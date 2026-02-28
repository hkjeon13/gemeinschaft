// API 클라이언트 유틸리티

import { generateDpopProof } from './dpop';

const BASE_URL = 'https://dataset.fin-ally.net/api';

// 디버그 모드
const DEBUG = true;

interface FetchOptions extends RequestInit {
  skipRefresh?: boolean;
}

// CSRF 토큰 가져오기 (쿠키에서)
function getCsrfToken(): string | null {
  const match = document.cookie.match(/csrf_token=([^;]+)/);
  return match ? match[1] : null;
}

let isRefreshing = false;
let refreshPromise: Promise<boolean> | null = null;

// 토큰 갱신
async function refreshToken(): Promise<boolean> {
  if (isRefreshing && refreshPromise) {
    return refreshPromise;
  }

  isRefreshing = true;
  refreshPromise = (async () => {
    try {
      const url = `${BASE_URL}/auth/refresh`;
      const dpopProof = await generateDpopProof('POST', url);

      const response = await fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'DPoP': dpopProof,
          'Content-Type': 'application/json',
        },
      });

      if (response.ok) {
        return true;
      }
      return false;
    } catch (error) {
      console.error('Token refresh failed:', error);
      return false;
    } finally {
      isRefreshing = false;
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

// API 요청 함수
export async function apiRequest<T>(
  endpoint: string,
  options: FetchOptions = {}
): Promise<T> {
  const { skipRefresh, ...fetchOptions } = options;
  const url = `${BASE_URL}${endpoint}`;
  const method = fetchOptions.method || 'GET';

  if (DEBUG) {
    console.log(`[API] ${method} ${endpoint}`);
  }

  // DPoP proof 생성
  const dpopProof = await generateDpopProof(method, url);

  // 헤더 설정
  const headers: Record<string, string> = {
    'DPoP': dpopProof,
    'Content-Type': 'application/json',
    ...(fetchOptions.headers as Record<string, string>),
  };

  // 상태 변경 요청일 경우 CSRF 토큰 추가
  if (['POST', 'PATCH', 'PUT', 'DELETE'].includes(method)) {
    const csrfToken = getCsrfToken();
    if (csrfToken) {
      headers['x-csrf-token'] = csrfToken;
      if (DEBUG) {
        console.log('[API] Added CSRF token:', csrfToken.substring(0, 20) + '...');
      }
    } else {
      console.warn('[API] CSRF token not found in cookies');
    }
  }

  if (DEBUG) {
    console.log('[API] Request headers:', Object.keys(headers));
    console.log('[API] Full URL:', url);
  }

  // 요청 실행
  try {
    const response = await fetch(url, {
      ...fetchOptions,
      credentials: 'same-origin',
      headers,
    });

    if (DEBUG) {
      console.log(`[API] Response status: ${response.status} ${response.statusText}`);
    }

    // 401 에러일 경우 토큰 갱신 시도
    if (response.status === 401 && !skipRefresh) {
      console.log('[API] 401 received, attempting token refresh...');
      const refreshed = await refreshToken();
      if (refreshed) {
        console.log('[API] Token refreshed, retrying request...');
        // 토큰 갱신 성공 시 재요청
        return apiRequest<T>(endpoint, { ...options, skipRefresh: true });
      } else {
        // 토큰 갱신 실패 시 로그인 페이지로 이동
        console.error('[API] Token refresh failed, redirecting to login');
        window.location.href = '/login';
        throw new Error('Authentication failed');
      }
    }

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`[API] Error response:`, errorText);
      throw new Error(`API Error ${response.status}: ${errorText}`);
    }

    const data = await response.json();
    if (DEBUG) {
      console.log('[API] Response data:', data);
    }
    return data;
  } catch (error) {
    console.error('[API] Request failed:', error);
    throw error;
  }
}

// 로그인
export async function login(username: string, password: string) {
  return apiRequest('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
    skipRefresh: true,
  });
}

// 내 세션 조회
export async function getMe() {
  return apiRequest('/auth/me');
}

// 사용자 목록 조회
export async function getUsers() {
  return apiRequest<Array<{
    username: string;
    role: string;
    tenant: string;
    scopes: string[];
  }>>('/admin/users');
}

// 사용자 권한 수정
export async function updateUserRole(username: string, role: string) {
  return apiRequest(`/admin/users/${username}`, {
    method: 'PATCH',
    body: JSON.stringify({ role }),
  });
}

// 로그아웃
export async function logout() {
  return apiRequest('/auth/logout', {
    method: 'POST',
  });
}
