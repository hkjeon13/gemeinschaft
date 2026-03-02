// API 클라이언트 유틸리티

import { generateDpopProof } from './dpop';

const configuredBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').trim();
const BASE_URL = (configuredBaseUrl || 'https://dataset.fin-ally.net/api').replace(/\/+$/, '');
const CSRF_STORAGE_KEY = 'csrf_token';

// 디버그 모드
const DEBUG = true;

interface FetchOptions extends RequestInit {
  skipRefresh?: boolean;
}

function resolveRequestCredentials(): RequestCredentials {
  const configured = (import.meta.env.VITE_API_CREDENTIALS ?? '').trim().toLowerCase();
  if (configured === 'omit' || configured === 'same-origin' || configured === 'include') {
    return configured;
  }
  return 'include';
}

const REQUEST_CREDENTIALS = resolveRequestCredentials();

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function saveCsrfToken(token: string): void {
  if (!token) {
    return;
  }
  try {
    sessionStorage.setItem(CSRF_STORAGE_KEY, token);
  } catch {
    // Ignore storage failures in restricted environments.
  }
}

function getStoredCsrfToken(): string | null {
  try {
    return sessionStorage.getItem(CSRF_STORAGE_KEY);
  } catch {
    return null;
  }
}

// CSRF 토큰 가져오기 (쿠키에서)
function getCsrfToken(): string | null {
  const match = document.cookie.match(/csrf_token=([^;]+)/);
  if (match?.[1]) {
    return match[1];
  }
  return getStoredCsrfToken();
}

function captureCsrfToken(payload: unknown): void {
  if (!isRecord(payload)) {
    return;
  }
  const token = payload.csrf_token;
  if (typeof token === 'string' && token) {
    saveCsrfToken(token);
  }
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
        credentials: REQUEST_CREDENTIALS,
        headers: {
          'DPoP': dpopProof,
          'Content-Type': 'application/json',
        },
      });

      if (response.ok) {
        try {
          const payload = await response.json();
          captureCsrfToken(payload);
        } catch {
          // Ignore empty/non-JSON refresh response.
        }
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
      // 로그인 요청이 아닌 경우에만 경고 표시
      if (!endpoint.includes('/auth/login')) {
        console.warn('[API] CSRF token not found - will be obtained from response');
      }
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
      credentials: REQUEST_CREDENTIALS,
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
        // 토큰 갱신 실패 시 인증 필요 이벤트 발행
        console.error('[API] Token refresh failed, dispatching auth:required event');
        window.dispatchEvent(new CustomEvent('auth:required'));
        throw new Error('Authentication failed');
      }
    }

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`[API] Error response:`, errorText);
      throw new Error(`API Error ${response.status}: ${errorText}`);
    }

    // 204 No Content 응답 처리 (DELETE 등)
    if (response.status === 204) {
      return null as T;
    }

    const data = await response.json();
    captureCsrfToken(data);
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

// 회원가입
export async function register(data: {
  name: string;
  username: string;
  password: string;
  email: string;
}) {
  return apiRequest<{ message: string; verification_required: boolean }>('/auth/register', {
    method: 'POST',
    body: JSON.stringify(data),
    skipRefresh: true,
  });
}

// 내 세션 조회
export async function getMe() {
  return apiRequest<{
    sub: string;
    role?: string;
    tenant: string;
    scope: string;
    iss?: string;
    aud?: string;
    typ?: string;
    exp: number;
    name: string;
    email?: string | null;
    email_verified: boolean;
    profile_image_data_url?: string | null;
  }>('/auth/me');
}

// 내 프로필 수정
export async function updateMe(data: {
  name?: string;
  profile_image_data_url?: string;
  clear_profile_image?: boolean;
}) {
  return apiRequest<{
    sub: string;
    role?: string;
    tenant: string;
    scope: string;
    iss?: string;
    aud?: string;
    typ?: string;
    exp: number;
    name: string;
    email?: string | null;
    email_verified: boolean;
    profile_image_data_url?: string | null;
  }>('/auth/me', {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
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

// 사용자 삭제
export async function deleteUser(username: string) {
  return apiRequest(`/admin/users/${username}`, {
    method: 'DELETE',
  });
}

// 로그아웃
export async function logout() {
  return apiRequest('/auth/logout', {
    method: 'POST',
  });
}

// 대화 목록 조회
export async function getConversationList() {
  return apiRequest<Array<{
    conversation_id: string;
    title?: string;
    message_count: number;
    updated_at: string;
    has_unread?: boolean;
  }>>('/conversation/list');
}

// 대화 제목 수정
export async function updateConversationTitle(conversationId: string, title: string) {
  return apiRequest<{ conversation_id: string; title: string }>(
    `/conversation/${conversationId}/title`,
    { method: 'PATCH', body: JSON.stringify({ title }) }
  );
}

// ─── 대화 모델 ────────────────────────────────────────────────────────────────

export interface ConversationModelOption {
  model_id: string;
  provider: string;
  openai_api: string;
  model: string;
  display_name: string;
  description?: string;
  is_global_default: boolean;
  is_user_default: boolean;
  image_data_url?: string;
}

export async function getConversationModelList() {
  return apiRequest<ConversationModelOption[]>('/conversation/model/list');
}

export async function getConversationDefaultModel() {
  return apiRequest<{ model_id: string; display_name: string; source: string }>('/conversation/model/default');
}

export async function setConversationDefaultModel(model_id: string) {
  return apiRequest<void>('/conversation/model/default', {
    method: 'PUT',
    body: JSON.stringify({ model_id }),
  });
}

export async function deleteConversationDefaultModel() {
  return apiRequest<{ model_id: string; display_name: string; source: string }>('/conversation/model/default', {
    method: 'DELETE',
  });
}

export async function setConversationModelImage(modelId: string, imageDataUrl: string) {
  return apiRequest<{ model_id: string; image_data_url: string }>(`/conversation/model/${modelId}/image`, {
    method: 'PUT',
    body: JSON.stringify({ image_data_url: imageDataUrl }),
  });
}

export async function deleteConversationModelImage(modelId: string) {
  return apiRequest<void>(`/conversation/model/${modelId}/image`, {
    method: 'DELETE',
  });
}

// 대화 상세 조회
export async function getConversation(conversationId: string) {
  return apiRequest<{
    conversation_id: string;
    tenant_id: string;
    user_id: string;
    messages: Array<{
      message_id: string;
      message: string;
      created_at: string;
    }>;
    updated_at: string;
  }>(`/conversation/${conversationId}`);
}

// 메시지 추가
export async function addMessage(conversationId: string, message: string, modelId?: string) {
  return apiRequest<{
    conversation_id: string;
    tenant_id: string;
    user_id: string;
    messages: Array<{
      message_id: string;
      message: string;
      created_at: string;
    }>;
    updated_at: string;
  }>(`/conversation/${conversationId}`, {
    method: 'POST',
    body: JSON.stringify({
      messages: [{ role: 'user', content: [{ type: 'text', text: message }] }],
      ...(modelId ? { model_id: modelId } : {}),
    }),
  });
}

// ─── 대화방 모델 리스트 ──────────────────────────────────────────────────────

export interface ConversationRoomModel {
  model_id: string;
  display_name: string;
  model: string;
  provider: string;
  openai_api?: string;
  image_data_url?: string;
}

/** 대화방에 설정된 모델 리스트 조회 (없으면 사용자 기본 모델 1개로 자동 초기화) */
export async function getConversationRoomModels(conversationId: string) {
  const res = await apiRequest<{ conversation_id: string; models: ConversationRoomModel[] }>(
    `/conversation/${conversationId}/models`
  );
  return res.models ?? [];
}

/** 대화방에 모델 추가 */
export async function addConversationRoomModel(conversationId: string, modelId: string) {
  const res = await apiRequest<{ conversation_id: string; models: ConversationRoomModel[] }>(
    `/conversation/${conversationId}/models`,
    { method: 'POST', body: JSON.stringify({ model_id: modelId }) }
  );
  return res.models ?? [];
}

/** 대화방에서 모델 제거 (마지막 1개는 400) */
export async function removeConversationRoomModel(conversationId: string, modelId: string) {
  const res = await apiRequest<{ conversation_id: string; models: ConversationRoomModel[] }>(
    `/conversation/${conversationId}/models/${modelId}`,
    { method: 'DELETE' }
  );
  return res.models ?? [];
}

// 모델 목록 조회
export async function getModels() {
  return apiRequest<Array<{
    model_id: string;
    provider: string;
    openai_api: 'chat.completions' | 'responses';
    model: string;
    display_name: string;
    description: string;
    parameters: Record<string, unknown>;
    client_options: Record<string, unknown>;
    chat_create_options: Record<string, unknown>;
    responses_create_options: Record<string, unknown>;
    api_key_refs: Array<{
      key_id: string;
      masked_key: string;
    }>;
    has_api_key: boolean;
    has_webhook_secret: boolean;
    is_active: boolean;
    is_default: boolean;
    created_at: string;
    updated_at: string;
  }>>('/admin/models');
}

// 모델 등록
export async function createModel(data: {
  model_id?: string;
  provider?: string;
  openai_api?: 'chat.completions' | 'responses';
  model: string;
  display_name?: string;
  description?: string;
  parameters?: Record<string, unknown>;
  client_options?: Record<string, unknown>;
  chat_create_options?: Record<string, unknown>;
  responses_create_options?: Record<string, unknown>;
  api_key?: string;
  api_keys?: string[];
  webhook_secret?: string;
  is_active?: boolean;
  is_default?: boolean;
}) {
  return apiRequest('/admin/models', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

// 모델 수정
export async function updateModel(
  modelId: string,
  data: {
    provider?: string;
    openai_api?: 'chat.completions' | 'responses';
    model?: string;
    display_name?: string;
    description?: string;
    parameters?: Record<string, unknown>;
    client_options?: Record<string, unknown>;
    chat_create_options?: Record<string, unknown>;
    responses_create_options?: Record<string, unknown>;
    api_key?: string;
    api_keys?: string[];
    append_api_keys?: string[];
    remove_api_key_ids?: string[];
    webhook_secret?: string;
    clear_api_key?: boolean;
    clear_webhook_secret?: boolean;
    is_active?: boolean;
    is_default?: boolean;
  }
) {
  return apiRequest(`/admin/models/${modelId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

// 모델 삭제
export async function deleteModel(modelId: string) {
  return apiRequest(`/admin/models/${modelId}`, {
    method: 'DELETE',
  });
}

// 대화 숨기기 (소프트 삭제)
export async function hideConversation(conversationId: string) {
  return apiRequest<{ conversation_id: string; visible: boolean }>(
    `/conversation/${conversationId}`,
    { method: 'DELETE' }
  );
}

// 연속 대화 (입력 없이 이어서 답변)
export async function continueConversation(
  conversationId: string,
  options: {
    model_id?: string;
    model_ids?: string[];
    min_interval_seconds?: number;
    max_interval_seconds?: number;
    max_turns?: number;
  } = {}
) {
  return apiRequest<{
    conversation_id: string;
    tenant_id: string;
    user_id: string;
    messages: Array<{
      message_id: string;
      message: string;
      role?: string;
      model_id?: string;
      model_name?: string;
      model_display_name?: string;
      provider?: string;
      created_at: string;
    }>;
    updated_at: string;
  }>(`/conversation/${conversationId}/continue`, {
    method: 'POST',
    body: JSON.stringify(options),
  });
}
