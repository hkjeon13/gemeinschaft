import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router';
import {
  getUsers,
  updateUserRole,
  logout,
  getModels,
  createModel,
  updateModel,
  deleteModel,
} from '../utils/api';
import { LoginModal } from './LoginModal';

interface User {
  username: string;
  role: string;
  tenant: string;
  scopes: string[];
}

interface Model {
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
  has_api_key: boolean;
  has_webhook_secret: boolean;
  is_active: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

interface ModelForm {
  model_id: string;
  provider: string;
  openai_api: 'chat.completions' | 'responses';
  model: string;
  display_name: string;
  description: string;
  parameters: string;
  client_options: string;
  chat_create_options: string;
  responses_create_options: string;
  api_key: string;
  api_keys: string[];
  use_multi_keys: boolean;
  append_api_keys: string[];
  webhook_secret: string;
  clear_api_key: boolean;
  clear_webhook_secret: boolean;
  is_active: boolean;
  is_default: boolean;
}

const AVAILABLE_ROLES = ['admin', 'member_plus', 'member'];
type Tab = 'users' | 'models';

const emptyModelForm: ModelForm = {
  model_id: '',
  provider: 'openai',
  openai_api: 'chat.completions',
  model: '',
  display_name: '',
  description: '',
  parameters: '{}',
  client_options: '{}',
  chat_create_options: '{}',
  responses_create_options: '{}',
  api_key: '',
  api_keys: [],
  use_multi_keys: false,
  append_api_keys: [],
  webhook_secret: '',
  clear_api_key: false,
  clear_webhook_secret: false,
  is_active: true,
  is_default: false,
};

// ─── Tooltip descriptions ─────────────────────────────────────────────────────
const FIELD_TOOLTIPS = {
  parameters:
    '모델의 기본 호출 파라미터입니다.\n대화 API 요청마다 기본값으로 적용되며, 요청 시 개별 오버라이드도 가능합니다.\n\n예: { "temperature": 0.7, "max_tokens": 1024 }',
  client_options:
    'OpenAI 클라이언트를 초기화할 때 사용하는 옵션입니다.\nAPI 호출 자체가 아닌 클라이언트 연결 설정에 영향을 줍니다.\n\n허용 키: organization, project, base_url, timeout, max_retries, default_headers, default_query, strict_response_validation\n\n예: { "organization": "org-xxx", "project": "proj_yyy", "timeout": 30 }',
  chat_create_options:
    'openai_api = "chat.completions" 일 때\nchat.completions.create() 호출에 전달되는 추가 옵션입니다.\n이 API가 선택된 경우에만 실행 시 사용됩니다.\n\n금지 키(예약): messages, model, stream\n\n예: { "reasoning_effort": "medium", "temperature": 0.5 }',
  responses_create_options:
    'openai_api = "responses" 일 때\nresponses.create() 호출에 전달되는 추가 옵션입니다.\n이 API가 선택된 경우에만 실행 시 사용됩니다.\n\n금지 키(예약): input, model, stream\n\n예: { "reasoning": { "effort": "medium" }, "max_output_tokens": 2048 }',
};

// ─── InfoTooltip ──────────────────────────────────────────────────────────────
function InfoTooltip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span
      className="relative inline-flex items-center ml-1.5 align-middle"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-4 h-4 rounded-full border border-gray-400 text-gray-400 hover:border-indigo-500 hover:text-indigo-500 flex items-center justify-center focus:outline-none transition-colors flex-shrink-0"
        aria-label="설명 보기"
      >
        <span className="text-[10px] font-bold leading-none select-none">i</span>
      </button>
      {open && (
        <span className="absolute left-6 top-1/2 -translate-y-1/2 z-[9999] w-72 rounded-xl border border-gray-200 bg-white shadow-xl px-3.5 py-3 text-xs text-gray-700 leading-relaxed whitespace-pre-wrap pointer-events-none">
          {text}
        </span>
      )}
    </span>
  );
}

// ─── JsonEditorField ──────────────────────────────────────────────────────────
function JsonEditorField({
  label, value, onChange, placeholder, highlighted, tooltip,
}: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; highlighted?: boolean; tooltip?: string;
}) {
  const [error, setError] = useState('');
  const handleChange = (v: string) => {
    onChange(v);
    try { JSON.parse(v); setError(''); } catch { setError('유효하지 않은 JSON입니다'); }
  };
  return (
    <div>
      <label className={`flex items-center text-xs font-medium mb-1.5 ${highlighted ? 'text-indigo-700' : 'text-gray-700'}`}>
        <span>{label}</span>
        {tooltip && <InfoTooltip text={tooltip} />}
        {highlighted && (
          <span className="ml-2 px-1.5 py-0.5 bg-indigo-100 text-indigo-700 text-[10px] rounded-md">활성</span>
        )}
      </label>
      <textarea
        value={value}
        onChange={(e) => handleChange(e.target.value)}
        className={`w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 font-mono text-xs transition-colors ${
          error
            ? 'border-red-400 focus:ring-red-400'
            : highlighted
            ? 'border-indigo-300 focus:ring-indigo-500 bg-indigo-50/50'
            : 'border-gray-200 focus:ring-indigo-500 bg-white'
        }`}
        rows={3}
        placeholder={placeholder || '{}'}
        spellCheck={false}
      />
      {error && <p className="mt-1 text-[11px] text-red-600">{error}</p>}
    </div>
  );
}

// ─── RoleBadge ────────────────────────────────────────────────────────────────
function RoleBadge({ role }: { role: string }) {
  const cls =
    role === 'admin' ? 'bg-purple-100 text-purple-700' :
    role === 'member_plus' ? 'bg-indigo-100 text-indigo-700' :
    'bg-gray-100 text-gray-600';
  return <span className={`px-1.5 py-0.5 text-[10px] rounded-full ${cls}`}>{role}</span>;
}

// ─── CloseBtn ─────────────────────────────────────────────────────────────────
function CloseBtn({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors flex-shrink-0"
    >
      <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
        <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
      </svg>
    </button>
  );
}

// ─── UserDetailPopup ──────────────────────────────────────────────────────────
function UserDetailPopup({
  user,
  onClose,
  onRoleSaved,
}: {
  user: User;
  onClose: () => void;
  onRoleSaved: () => void;
}) {
  const [editingRole, setEditingRole] = useState(false);
  const [selectedRole, setSelectedRole] = useState(user.role);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateUserRole(user.username, selectedRole);
      onRoleSaved();
      onClose();
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes('400')) alert('마지막 관리자의 권한은 변경할 수 없습니다.');
        else alert('권한 변경 중 오류가 발생했습니다.');
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="w-full max-w-sm bg-white rounded-2xl shadow-2xl" onClick={(e) => e.stopPropagation()}>
        {/* 헤더 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-full bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center flex-shrink-0">
              <span className="text-white text-sm font-semibold">{user.username[0]?.toUpperCase()}</span>
            </div>
            <div>
              <p className="text-sm font-semibold text-gray-900">{user.username}</p>
              <p className="text-[11px] text-gray-400">{user.tenant}</p>
            </div>
          </div>
          <CloseBtn onClick={onClose} />
        </div>

        {/* 내용 */}
        <div className="px-5 py-4 space-y-4">
          {/* 역할 */}
          <div>
            <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2">역할</p>
            {editingRole ? (
              <div className="flex items-center gap-2">
                <select
                  value={selectedRole}
                  onChange={(e) => setSelectedRole(e.target.value)}
                  className="flex-1 px-2.5 py-1.5 border border-gray-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
                >
                  {AVAILABLE_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-2.5 py-1.5 rounded-lg bg-indigo-500 text-white text-xs hover:bg-indigo-600 transition-colors disabled:opacity-50"
                >
                  {saving ? '저장 중...' : '저장'}
                </button>
                <button
                  onClick={() => { setEditingRole(false); setSelectedRole(user.role); }}
                  className="px-2.5 py-1.5 rounded-lg bg-gray-100 text-gray-600 text-xs hover:bg-gray-200 transition-colors"
                >
                  취소
                </button>
              </div>
            ) : (
              <div className="flex items-center justify-between">
                <RoleBadge role={user.role} />
                <button
                  onClick={() => setEditingRole(true)}
                  className="text-[11px] text-indigo-600 hover:underline"
                >
                  권한 변경
                </button>
              </div>
            )}
          </div>

          {/* 테넌트 */}
          <div>
            <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">테넌트</p>
            <p className="text-xs text-gray-700 font-mono bg-gray-50 px-2.5 py-1.5 rounded-lg">{user.tenant || '—'}</p>
          </div>

          {/* 권한 범위 */}
          <div>
            <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2">권한 범위 ({user.scopes.length})</p>
            <div className="flex flex-wrap gap-1">
              {user.scopes.length === 0
                ? <span className="text-xs text-gray-400">없음</span>
                : user.scopes.map((s) => (
                    <span key={s} className="px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded text-[10px] font-mono">{s}</span>
                  ))
              }
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── ModelDetailPopup ─────────────────────────────────────────────────────────
function ModelDetailPopup({
  model,
  onClose,
  onEdit,
  onDelete,
}: {
  model: Model;
  onClose: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const fmtJson = (obj: Record<string, unknown>) => {
    try { return JSON.stringify(obj, null, 2); } catch { return '{}'; }
  };
  const isEmpty = (obj: Record<string, unknown>) => Object.keys(obj).length === 0;

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" onClick={onClose}>
      <div className="w-full max-w-lg bg-white rounded-2xl shadow-2xl my-4" onClick={(e) => e.stopPropagation()}>
        {/* 헤더 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-semibold text-gray-900 font-mono truncate">{model.model_id}</span>
            {model.is_default && <span className="px-1.5 py-0.5 bg-green-100 text-green-700 text-[10px] rounded-md flex-shrink-0">기본</span>}
            {!model.is_active && <span className="px-1.5 py-0.5 bg-gray-100 text-gray-500 text-[10px] rounded-md flex-shrink-0">비활성</span>}
          </div>
          <CloseBtn onClick={onClose} />
        </div>

        {/* 내용 */}
        <div className="px-5 py-4 space-y-4">
          {/* 기본 정보 */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            <InfoRow label="표시명" value={model.display_name || '—'} />
            <InfoRow label="Provider" value={model.provider} mono />
            <InfoRow label="Model" value={model.model} mono />
            <InfoRow
              label="OpenAI API"
              value={
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                  model.openai_api === 'responses' ? 'bg-violet-100 text-violet-700' : 'bg-sky-100 text-sky-700'
                }`}>{model.openai_api}</span>
              }
            />
          </div>

          {model.description && (
            <div>
              <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1">설명</p>
              <p className="text-xs text-gray-600">{model.description}</p>
            </div>
          )}

          {/* 시크릿 상태 */}
          <div className="flex gap-2">
            <span className={`px-2 py-1 text-[10px] rounded-lg ${model.has_api_key ? 'bg-blue-50 text-blue-700' : 'bg-gray-50 text-gray-400'}`}>
              🔑 API Key {model.has_api_key ? '설정됨' : '미설정'}
            </span>
            <span className={`px-2 py-1 text-[10px] rounded-lg ${model.has_webhook_secret ? 'bg-orange-50 text-orange-700' : 'bg-gray-50 text-gray-400'}`}>
              🔐 Webhook {model.has_webhook_secret ? '설정됨' : '미설정'}
            </span>
          </div>

          {/* JSON 파라미터 (비어있지 않은 것만) */}
          {!isEmpty(model.parameters) && (
            <JsonBlock label="Parameters" value={fmtJson(model.parameters)} />
          )}
          {!isEmpty(model.client_options) && (
            <JsonBlock label="Client Options" value={fmtJson(model.client_options)} />
          )}
          {model.openai_api === 'chat.completions' && !isEmpty(model.chat_create_options) && (
            <JsonBlock label="Create Options (chat)" value={fmtJson(model.chat_create_options)} highlighted />
          )}
          {model.openai_api === 'responses' && !isEmpty(model.responses_create_options) && (
            <JsonBlock label="Create Options (responses)" value={fmtJson(model.responses_create_options)} highlighted />
          )}

          {/* 날짜 */}
          <div className="flex gap-4 text-[10px] text-gray-400 pt-1 border-t border-gray-50">
            <span>생성: {new Date(model.created_at).toLocaleString('ko-KR')}</span>
            <span>수정: {new Date(model.updated_at).toLocaleString('ko-KR')}</span>
          </div>
        </div>

        {/* 액션 */}
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-gray-100 bg-gray-50/50 rounded-b-2xl">
          <button
            onClick={() => { onClose(); onDelete(); }}
            className="px-3 py-1.5 rounded-lg text-xs text-red-500 hover:bg-red-50 transition-colors"
          >
            삭제
          </button>
          <button
            onClick={() => { onClose(); onEdit(); }}
            className="px-3 py-1.5 rounded-lg bg-indigo-500 text-white text-xs hover:bg-indigo-600 transition-colors"
          >
            수정
          </button>
        </div>
      </div>
    </div>
  );
}

function InfoRow({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-0.5">{label}</p>
      {typeof value === 'string'
        ? <p className={`text-xs text-gray-700 truncate ${mono ? 'font-mono' : ''}`}>{value}</p>
        : value
      }
    </div>
  );
}

function JsonBlock({ label, value, highlighted }: { label: string; value: string; highlighted?: boolean }) {
  return (
    <div>
      <p className={`text-[10px] font-semibold uppercase tracking-wider mb-1 ${highlighted ? 'text-indigo-500' : 'text-gray-400'}`}>{label}</p>
      <pre className={`text-[10px] font-mono rounded-lg p-2.5 overflow-x-auto whitespace-pre-wrap break-all ${highlighted ? 'bg-indigo-50 text-indigo-800' : 'bg-gray-50 text-gray-700'}`}>
        {value}
      </pre>
    </div>
  );
}

// ─── AdminPage ────────────────────────────────────────────────────────────────
export function AdminPage() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<Tab>('users');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showLoginModal, setShowLoginModal] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const [users, setUsers] = useState<User[]>([]);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);

  const [models, setModels] = useState<Model[]>([]);
  const [viewingModel, setViewingModel] = useState<Model | null>(null);
  const [showModelForm, setShowModelForm] = useState(false);
  const [editingModel, setEditingModel] = useState<string | null>(null);
  const [editingModelData, setEditingModelData] = useState<Model | null>(null);
  const [modelForm, setModelForm] = useState<ModelForm>(emptyModelForm);

  useEffect(() => {
    const handler = () => setShowLoginModal(true);
    window.addEventListener('auth:required', handler);
    return () => window.removeEventListener('auth:required', handler);
  }, []);

  useEffect(() => { loadData(); }, [activeTab]);

  const loadData = async () => {
    setLoading(true); setError('');
    try {
      if (activeTab === 'users') setUsers(await getUsers());
      else setModels(await getModels());
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes('403')) setError('관리자 권한이 필요합니다.');
        else if (!err.message.includes('Authentication failed')) setError('데이터를 불러오는 중 오류가 발생했습니다.');
      }
    } finally { setLoading(false); }
  };

  const handleLoginSuccess = async () => {
    setShowLoginModal(false);
    await loadData();
  };

  const handleLogout = async () => {
    try { await logout(); } catch { /* ignore */ }
    navigate('/chat');
  };

  const handleNewModel = () => {
    setEditingModel(null); setEditingModelData(null);
    setModelForm(emptyModelForm); setShowModelForm(true);
  };

  const handleEditModel = (model: Model) => {
    setEditingModel(model.model_id); setEditingModelData(model);
    setModelForm({
      model_id: model.model_id, provider: model.provider,
      openai_api: model.openai_api, model: model.model,
      display_name: model.display_name, description: model.description,
      parameters: JSON.stringify(model.parameters, null, 2),
      client_options: JSON.stringify(model.client_options, null, 2),
      chat_create_options: JSON.stringify(model.chat_create_options, null, 2),
      responses_create_options: JSON.stringify(model.responses_create_options, null, 2),
      api_key: '', api_keys: [], use_multi_keys: false,
      append_api_keys: [],
      webhook_secret: '',
      clear_api_key: false, clear_webhook_secret: false,
      is_active: model.is_active, is_default: model.is_default,
    });
    setShowModelForm(true);
  };

  const parseJsonField = (raw: string, label: string): Record<string, unknown> | null => {
    try { return JSON.parse(raw); }
    catch { alert(`${label}는 유효한 JSON이어야 합니다.`); return null; }
  };

  const handleSaveModel = async (e: React.FormEvent) => {
    e.preventDefault();
    const parameters = parseJsonField(modelForm.parameters, 'Parameters'); if (!parameters) return;
    const clientOptions = parseJsonField(modelForm.client_options, 'Client Options'); if (!clientOptions) return;
    const chatCreateOptions = parseJsonField(modelForm.chat_create_options, 'Create Options (chat)'); if (!chatCreateOptions) return;
    const responsesCreateOptions = parseJsonField(modelForm.responses_create_options, 'Create Options (responses)'); if (!responsesCreateOptions) return;

    // api_key / api_keys 준비 (둘 다 보내면 400)
    const multiKeys = modelForm.api_keys.map((k) => k.trim()).filter(Boolean);
    const hasSingleKey = !modelForm.use_multi_keys && modelForm.api_key.trim();
    const hasMultiKeys = modelForm.use_multi_keys && multiKeys.length > 0;

    try {
      if (editingModel) {
        const data: Parameters<typeof updateModel>[1] = {
          provider: modelForm.provider, openai_api: modelForm.openai_api, model: modelForm.model,
          display_name: modelForm.display_name || undefined, description: modelForm.description || undefined,
          parameters, client_options: clientOptions,
          chat_create_options: chatCreateOptions, responses_create_options: responsesCreateOptions,
          is_active: modelForm.is_active, is_default: modelForm.is_default,
          clear_api_key: modelForm.clear_api_key, clear_webhook_secret: modelForm.clear_webhook_secret,
        };
        if (hasMultiKeys) data.api_keys = multiKeys;
        else if (hasSingleKey) data.api_key = modelForm.api_key.trim();
        const appendKeys = modelForm.append_api_keys.map((k) => k.trim()).filter(Boolean);
        if (appendKeys.length > 0) data.append_api_keys = appendKeys;
        if (modelForm.webhook_secret) data.webhook_secret = modelForm.webhook_secret;
        await updateModel(editingModel, data);
      } else {
        const data: Parameters<typeof createModel>[0] = {
          ...(modelForm.model_id.trim() ? { model_id: modelForm.model_id.trim() } : {}),
          provider: modelForm.provider, openai_api: modelForm.openai_api, model: modelForm.model,
          display_name: modelForm.display_name || undefined, description: modelForm.description || undefined,
          parameters, client_options: clientOptions,
          chat_create_options: chatCreateOptions, responses_create_options: responsesCreateOptions,
          is_active: modelForm.is_active, is_default: modelForm.is_default,
        };
        if (hasMultiKeys) data.api_keys = multiKeys;
        else if (hasSingleKey) data.api_key = modelForm.api_key.trim();
        if (modelForm.webhook_secret) data.webhook_secret = modelForm.webhook_secret;
        await createModel(data);
      }
      setShowModelForm(false);
      await loadData();
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes('409')) alert('이미 존재하는 Model ID입니다.');
        else if (err.message.includes('400')) {
          const match = err.message.match(/API Error 400: (.+)/);
          try {
            const body = match ? JSON.parse(match[1]) : null;
            alert(`검증 오류: ${body?.detail ?? match?.[1] ?? '입력 데이터 검증에 실패했습니다.'}`);
          } catch { alert(`검증 오류: ${match?.[1] ?? '입력 데이터 검증에 실패했습니다.'}`); }
        } else if (err.message.includes('422')) alert('입력 데이터가 유효하지 않습니다. 필드 타입을 확인하세요.');
        else alert('모델 저장 중 오류가 발생했습니다.');
      }
    }
  };

  const handleDeleteModel = async (modelId: string) => {
    if (!confirm(`모델 "${modelId}"를 삭제하시겠습니까?`)) return;
    try {
      await deleteModel(modelId); await loadData();
    } catch (err) {
      if (err instanceof Error)
        alert(err.message.includes('400') ? '마지막 남은 모델은 삭제할 수 없습니다.' : '모델 삭제 중 오류가 발생했습니다.');
    }
  };

  const updateForm = (patch: Partial<ModelForm>) => setModelForm((f) => ({ ...f, ...patch }));

  // ─── Nav items ─────────────────────────────────────────────────────────────
  const navItems: { id: Tab; label: string; icon: React.ReactNode }[] = [
    {
      id: 'users',
      label: '사용자 관리',
      icon: (
        <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
          <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM12.735 14c.618 0 1.093-.561.872-1.139a6.002 6.002 0 0 0-11.215 0c-.22.578.254 1.139.872 1.139h9.47Z" />
        </svg>
      ),
    },
    {
      id: 'models',
      label: '모델 관리',
      icon: (
        <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
          <path d="M7 2.75A.75.75 0 0 1 7.75 2h.5a.75.75 0 0 1 0 1.5h-.5A.75.75 0 0 1 7 2.75ZM7 12.25a.75.75 0 0 1 .75-.75h.5a.75.75 0 0 1 0 1.5h-.5a.75.75 0 0 1-.75-.75ZM2.75 7A.75.75 0 0 0 2 7.75v.5a.75.75 0 0 0 1.5 0v-.5A.75.75 0 0 0 2.75 7ZM12.25 7a.75.75 0 0 0-.75.75v.5a.75.75 0 0 0 1.5 0v-.5a.75.75 0 0 0-.75-.75ZM8 5.5A2.5 2.5 0 1 0 8 10.5 2.5 2.5 0 0 0 8 5.5Z" />
        </svg>
      ),
    },
  ];

  // ─── Loading ───────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-gray-300 border-t-indigo-500 rounded-full animate-spin" />
          <span className="text-xs text-gray-400">불러오는 중...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ── 모바일 오버레이 ── */}
      {sidebarOpen && (
        <div className="fixed inset-0 bg-black/40 z-30 md:hidden" onClick={() => setSidebarOpen(false)} />
      )}

      {/* ── 사이드바 ── */}
      <aside className={`
        fixed inset-y-0 left-0 z-40 w-56 flex flex-col flex-shrink-0 bg-[#f3f4f6] border-r border-gray-200
        transition-transform duration-200 ease-in-out
        md:relative md:translate-x-0 md:z-auto
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
      `}>
        <div className="px-4 pt-5 pb-4">
          <div className="flex items-center justify-between gap-2 mb-5">
            <div>
              <span className="text-sm font-semibold text-gray-800">Gemeinschaft</span>
              <p className="text-[10px] text-gray-400 leading-none mt-0.5">Admin</p>
            </div>
            <button
              onClick={() => setSidebarOpen(false)}
              className="md:hidden w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-colors"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
              </svg>
            </button>
          </div>
          <nav className="space-y-0.5">
            {navItems.map(({ id, label, icon }) => {
              const isActive = activeTab === id;
              return (
                <button
                  key={id}
                  onClick={() => { setActiveTab(id); setSidebarOpen(false); }}
                  className={`w-full relative flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs transition-all ${
                    isActive
                      ? 'bg-white shadow-sm border border-gray-200/80 text-gray-900'
                      : 'text-gray-600 hover:bg-white/70 hover:text-gray-800'
                  }`}
                >
                  {isActive && (
                    <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 rounded-full bg-indigo-500" />
                  )}
                  <span className={isActive ? 'text-indigo-500' : 'text-gray-400'}>{icon}</span>
                  {label}
                </button>
              );
            })}
          </nav>
        </div>
        <div className="flex-1" />
        <div className="px-3 py-3 border-t border-gray-200">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center flex-shrink-0">
              <svg viewBox="0 0 16 16" fill="white" className="w-3.5 h-3.5">
                <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM12.735 14c.618 0 1.093-.561.872-1.139a6.002 6.002 0 0 0-11.215 0c-.22.578.254 1.139.872 1.139h9.47Z" />
              </svg>
            </div>
            <span className="text-xs truncate flex-1 font-medium text-gray-500">관리자</span>
            <button
              onClick={handleLogout}
              title="로그아웃"
              className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                <path fillRule="evenodd" d="M2 2.75A.75.75 0 0 1 2.75 2h6a.75.75 0 0 1 0 1.5h-6v9h6a.75.75 0 0 1 0 1.5h-6A.75.75 0 0 1 2 13.25V2.75Zm10.28 3.47a.75.75 0 0 1 0 1.06l-1.5 1.5a.75.75 0 0 1-1.06-1.06l.22-.22H6.75a.75.75 0 0 1 0-1.5h3.19l-.22-.22a.75.75 0 1 1 1.06-1.06l1.5 1.5Z" clipRule="evenodd" />
              </svg>
            </button>
          </div>
        </div>
      </aside>

      {/* ── 메인 영역 ── */}
      <main className="flex-1 flex flex-col min-w-0 bg-gray-50 w-full">
        {/* 상단 헤더 */}
        <header className="bg-white/80 backdrop-blur-md border-b border-gray-200/80 px-4 md:px-5 py-3 flex items-center justify-between gap-3 z-10 flex-shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <button
              onClick={() => setSidebarOpen(true)}
              className="md:hidden w-8 h-8 flex items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 transition-colors flex-shrink-0"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
                <path fillRule="evenodd" d="M1.5 3.25a.75.75 0 0 1 .75-.75h11.5a.75.75 0 0 1 0 1.5H2.25a.75.75 0 0 1-.75-.75Zm0 4a.75.75 0 0 1 .75-.75h11.5a.75.75 0 0 1 0 1.5H2.25a.75.75 0 0 1-.75-.75Zm0 4a.75.75 0 0 1 .75-.75h11.5a.75.75 0 0 1 0 1.5H2.25a.75.75 0 0 1-.75-.75Z" clipRule="evenodd" />
              </svg>
            </button>
            <h1 className="text-sm font-semibold text-gray-800">
              {activeTab === 'users' ? '사용자 관리' : '모델 관리'}
            </h1>
          </div>
          <div className="flex items-center gap-2">
            {activeTab === 'models' && (
              <button
                onClick={handleNewModel}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-indigo-500 text-white hover:bg-indigo-600 transition-colors shadow-sm"
              >
                <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3">
                  <path d="M8.75 3.75a.75.75 0 0 0-1.5 0v3.5h-3.5a.75.75 0 0 0 0 1.5h3.5v3.5a.75.75 0 0 0 1.5 0v-3.5h3.5a.75.75 0 0 0 0-1.5h-3.5v-3.5Z" />
                </svg>
                새 모델
              </button>
            )}
            <button
              onClick={() => navigate('/chat')}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-gray-600 hover:bg-gray-100 transition-colors"
              title="채팅으로 이동"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3">
                <path d="M14.78 3.284A2.25 2.25 0 0 0 12.75 2h-9.5A2.25 2.25 0 0 0 1 4.25v7.5A2.25 2.25 0 0 0 3.25 14H5.5a.75.75 0 0 0 .75-.75v-3.19l-.72.72a.75.75 0 1 1-1.06-1.06l2-2a.75.75 0 0 1 1.06 0l2 2a.75.75 0 1 1-1.06 1.06l-.72-.72V13.25a.75.75 0 0 0 .75.75h4.75A2.25 2.25 0 0 0 15 11.75v-7.5c0-.17-.02-.337-.056-.5Z" />
              </svg>
              채팅
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-3 md:p-5">
          {error && (
            <div className="mb-3 rounded-xl bg-red-50 border border-red-100 px-4 py-3">
              <p className="text-xs text-red-700">{error}</p>
            </div>
          )}

          {/* ── 사용자 관리 ── */}
          {activeTab === 'users' && (
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-100">
                <span className="text-xs font-medium text-gray-500">전체 {users.length}명 · 클릭하면 상세 정보</span>
              </div>
              <div className="divide-y divide-gray-50">
                {users.map((user) => (
                  <div
                    key={user.username}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 cursor-pointer transition-colors"
                    onClick={() => setSelectedUser(user)}
                  >
                    <div className="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center flex-shrink-0">
                      <span className="text-white text-[10px] font-semibold">{user.username[0]?.toUpperCase()}</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium text-gray-800 truncate">{user.username}</p>
                      <p className="text-[10px] text-gray-400 truncate">{user.tenant}</p>
                    </div>
                    <RoleBadge role={user.role} />
                    <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3 text-gray-300 flex-shrink-0">
                      <path fillRule="evenodd" d="M6.22 4.22a.75.75 0 0 1 1.06 0l3.25 3.25a.75.75 0 0 1 0 1.06l-3.25 3.25a.75.75 0 0 1-1.06-1.06L9.19 8 6.22 5.03a.75.75 0 0 1 0-1.06Z" clipRule="evenodd" />
                    </svg>
                  </div>
                ))}
                {users.length === 0 && (
                  <div className="py-10 text-center text-xs text-gray-400">사용자가 없습니다</div>
                )}
              </div>
            </div>
          )}

          {/* ── 모델 관리 ── */}
          {activeTab === 'models' && (
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-100">
                <span className="text-xs font-medium text-gray-500">전체 {models.length}개 · 클릭하면 상세 정보</span>
              </div>
              <div className="divide-y divide-gray-50">
                {models.map((model) => (
                  <div
                    key={model.model_id}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 cursor-pointer transition-colors"
                    onClick={() => setViewingModel(model)}
                  >
                    {/* 상태 dot */}
                    <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${model.is_active ? 'bg-green-400' : 'bg-gray-300'}`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <p className="text-xs font-medium text-gray-800 truncate font-mono">{model.model_id}</p>
                        {model.is_default && <span className="px-1 py-0.5 bg-green-100 text-green-700 text-[9px] rounded flex-shrink-0">기본</span>}
                      </div>
                      <p className="text-[10px] text-gray-400 truncate">
                        {model.display_name || model.model} · <span className="font-mono">{model.provider}</span>
                      </p>
                    </div>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium flex-shrink-0 ${
                      model.openai_api === 'responses' ? 'bg-violet-100 text-violet-600' : 'bg-sky-100 text-sky-600'
                    }`}>
                      {model.openai_api === 'responses' ? 'resp' : 'chat'}
                    </span>
                    <div className="flex gap-1 flex-shrink-0">
                      {model.has_api_key && <span className="text-[10px]">🔑</span>}
                      {model.has_webhook_secret && <span className="text-[10px]">🔐</span>}
                    </div>
                    <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3 text-gray-300 flex-shrink-0">
                      <path fillRule="evenodd" d="M6.22 4.22a.75.75 0 0 1 1.06 0l3.25 3.25a.75.75 0 0 1 0 1.06l-3.25 3.25a.75.75 0 0 1-1.06-1.06L9.19 8 6.22 5.03a.75.75 0 0 1 0-1.06Z" clipRule="evenodd" />
                    </svg>
                  </div>
                ))}
                {models.length === 0 && (
                  <div className="py-10 text-center text-xs text-gray-400">등록된 모델이 없습니다</div>
                )}
              </div>
            </div>
          )}
        </div>
      </main>

      {/* ── 사용자 상세 팝업 ── */}
      {selectedUser && (
        <UserDetailPopup
          user={selectedUser}
          onClose={() => setSelectedUser(null)}
          onRoleSaved={loadData}
        />
      )}

      {/* ── 모델 상세 팝업 ── */}
      {viewingModel && (
        <ModelDetailPopup
          model={viewingModel}
          onClose={() => setViewingModel(null)}
          onEdit={() => handleEditModel(viewingModel)}
          onDelete={() => handleDeleteModel(viewingModel.model_id)}
        />
      )}

      {/* ── 모델 등록/수정 폼 모달 ── */}
      {showModelForm && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-start justify-center overflow-y-auto z-50 p-3 md:p-6">
          <div className="w-full max-w-2xl bg-white rounded-2xl shadow-2xl border border-gray-100 my-3 md:my-6">
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-gray-100">
              <h2 className="text-sm font-semibold text-gray-900">
                {editingModel ? (
                  <span className="flex items-center gap-2">
                    모델 수정
                    <span className="px-2 py-0.5 bg-indigo-100 text-indigo-700 text-xs rounded-md font-mono">{editingModel}</span>
                  </span>
                ) : '새 모델 등록'}
              </h2>
              <CloseBtn onClick={() => setShowModelForm(false)} />
            </div>

            <form onSubmit={handleSaveModel} className="px-5 py-4 space-y-5">
              {/* 기본 정보 */}
              <section>
                <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-3">기본 정보</p>

                {!editingModel && (
                  <div className="mb-3">
                    <label className="block text-xs font-medium text-gray-700 mb-1.5">
                      Model ID <span className="text-gray-400 font-normal">(선택 — 비우면 자동 생성)</span>
                    </label>
                    <input
                      type="text"
                      value={modelForm.model_id}
                      onChange={(e) => updateForm({ model_id: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-xs"
                      placeholder="비우면 서버가 자동 생성"
                    />
                  </div>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1.5">Provider</label>
                    <input
                      type="text"
                      value={modelForm.provider}
                      onChange={(e) => updateForm({ provider: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-xs"
                      placeholder="openai"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1.5">OpenAI API <span className="text-red-400">*</span></label>
                    <select
                      value={modelForm.openai_api}
                      onChange={(e) => updateForm({ openai_api: e.target.value as 'chat.completions' | 'responses' })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-xs bg-white"
                      required
                    >
                      <option value="chat.completions">chat.completions</option>
                      <option value="responses">responses</option>
                    </select>
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1.5">Model <span className="text-red-400">*</span></label>
                    <input
                      type="text"
                      value={modelForm.model}
                      onChange={(e) => updateForm({ model: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-xs"
                      required
                      placeholder="gpt-4o-mini"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1.5">Display Name</label>
                    <input
                      type="text"
                      value={modelForm.display_name}
                      onChange={(e) => updateForm({ display_name: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-xs"
                      placeholder="GPT-4o Mini"
                    />
                  </div>
                </div>

                <div className="mt-3">
                  <label className="block text-xs font-medium text-gray-700 mb-1.5">Description</label>
                  <textarea
                    value={modelForm.description}
                    onChange={(e) => updateForm({ description: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-xs resize-none"
                    rows={2}
                  />
                </div>

                <div className="flex gap-5 mt-3">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked={modelForm.is_active} onChange={(e) => updateForm({ is_active: e.target.checked })} className="rounded accent-indigo-500" />
                    <span className="text-xs text-gray-700">활성화</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked={modelForm.is_default} onChange={(e) => updateForm({ is_default: e.target.checked })} className="rounded accent-indigo-500" />
                    <span className="text-xs text-gray-700">기본 모델로 설정</span>
                  </label>
                </div>
              </section>

              {/* JSON 파라미터 */}
              <section>
                <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-3">파라미터 (JSON)</p>
                <div className="space-y-3">
                  <JsonEditorField label="Parameters" value={modelForm.parameters} onChange={(v) => updateForm({ parameters: v })} placeholder='{"temperature": 0.7}' tooltip={FIELD_TOOLTIPS.parameters} />
                  <JsonEditorField label="Client Options" value={modelForm.client_options} onChange={(v) => updateForm({ client_options: v })} placeholder='{"organization": "org-..."}' tooltip={FIELD_TOOLTIPS.client_options} />
                  {modelForm.openai_api === 'chat.completions' ? (
                    <JsonEditorField label="Create Options (chat)" value={modelForm.chat_create_options} onChange={(v) => updateForm({ chat_create_options: v })} placeholder='{"reasoning_effort": "medium"}' highlighted tooltip={FIELD_TOOLTIPS.chat_create_options} />
                  ) : (
                    <JsonEditorField label="Create Options (responses)" value={modelForm.responses_create_options} onChange={(v) => updateForm({ responses_create_options: v })} placeholder='{"reasoning": {"effort": "medium"}}' highlighted tooltip={FIELD_TOOLTIPS.responses_create_options} />
                  )}
                </div>
                <p className="mt-2 text-[10px] text-gray-400">
                  OpenAI API를 바꾸면 해당 Create Options로 전환됩니다. 실행 시에는 선택된{' '}
                  <span className="font-mono text-indigo-600">{modelForm.openai_api}</span> 옵션만 사용됩니다.
                </p>
              </section>

              {/* 시크릿 */}
              <section>
                <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-3">시크릿 (Write-only)</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {/* API Key (단일 / 복수 토글) */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-xs font-medium text-gray-700 flex items-center gap-1.5">
                        API Key
                        {editingModelData && (
                          <span className={`text-[10px] ${editingModelData.has_api_key ? 'text-blue-600' : 'text-gray-400'}`}>
                            {editingModelData.has_api_key ? '(설정됨)' : '(미설정)'}
                          </span>
                        )}
                      </label>
                      <div className="flex items-center gap-0.5 p-0.5 rounded-lg bg-gray-100">
                        <button
                          type="button"
                          onClick={() => updateForm({ use_multi_keys: false, api_keys: [] })}
                          className={`px-2 py-0.5 rounded-md text-[10px] transition-colors ${!modelForm.use_multi_keys ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                        >
                          단일
                        </button>
                        <button
                          type="button"
                          onClick={() => updateForm({ use_multi_keys: true, api_key: '', api_keys: modelForm.api_keys.length > 0 ? modelForm.api_keys : [''] })}
                          className={`px-2 py-0.5 rounded-md text-[10px] transition-colors ${modelForm.use_multi_keys ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                        >
                          복수
                        </button>
                      </div>
                    </div>

                    {!modelForm.use_multi_keys ? (
                      <input
                        type="password"
                        value={modelForm.api_key}
                        onChange={(e) => updateForm({ api_key: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-xs"
                        placeholder={editingModel ? '비워두면 기존 키 유지' : 'sk-...'}
                        autoComplete="new-password"
                      />
                    ) : (
                      <div className="space-y-1.5">
                        {modelForm.api_keys.map((key, i) => (
                          <div key={i} className="flex items-center gap-1.5">
                            <input
                              type="password"
                              value={key}
                              onChange={(e) => {
                                const next = [...modelForm.api_keys];
                                next[i] = e.target.value;
                                updateForm({ api_keys: next });
                              }}
                              className="flex-1 px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-xs min-w-0"
                              placeholder={`sk-... (키 ${i + 1})`}
                              autoComplete="new-password"
                            />
                            <button
                              type="button"
                              onClick={() => updateForm({ api_keys: modelForm.api_keys.filter((_, j) => j !== i) })}
                              disabled={modelForm.api_keys.length === 1}
                              className="w-6 h-6 flex items-center justify-center rounded-md text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors disabled:opacity-30 disabled:cursor-not-allowed flex-shrink-0"
                              title="키 제거"
                            >
                              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3">
                                <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
                              </svg>
                            </button>
                          </div>
                        ))}
                        <button
                          type="button"
                          onClick={() => updateForm({ api_keys: [...modelForm.api_keys, ''] })}
                          className="w-full flex items-center justify-center gap-1 py-1.5 rounded-lg border border-dashed border-gray-300 text-[10px] text-gray-500 hover:border-indigo-400 hover:text-indigo-600 transition-colors"
                        >
                          <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3">
                            <path d="M8.75 3.75a.75.75 0 0 0-1.5 0v3.5h-3.5a.75.75 0 0 0 0 1.5h3.5v3.5a.75.75 0 0 0 1.5 0v-3.5h3.5a.75.75 0 0 0 0-1.5h-3.5v-3.5Z" />
                          </svg>
                          키 추가 ({modelForm.api_keys.filter(k => k.trim()).length}개 입력됨)
                        </button>
                        <p className="text-[10px] text-gray-400 leading-relaxed">복수 키는 저장 시 기존 키셋 전체를 교체합니다. 중복은 자동 제거됩니다.</p>
                      </div>
                    )}

                    {editingModel && editingModelData?.has_api_key && (
                      <label className="flex items-center gap-2 mt-1.5 cursor-pointer">
                        <input type="checkbox" checked={modelForm.clear_api_key} onChange={(e) => updateForm({ clear_api_key: e.target.checked })} className="rounded accent-red-500" />
                        <span className="text-[10px] text-red-500">API Key 전체 삭제</span>
                      </label>
                    )}

                    {/* 기존 키에 추가 (append) — 수정 모드 전용 */}
                    {editingModel && (
                      <div className="mt-3 pt-2.5 border-t border-gray-100">
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-[10px] font-semibold text-emerald-700 uppercase tracking-wider">기존에 키 추가</span>
                          {modelForm.append_api_keys.length === 0 && (
                            <button
                              type="button"
                              onClick={() => updateForm({ append_api_keys: [''] })}
                              className="text-[10px] text-emerald-600 hover:text-emerald-700 hover:underline"
                            >
                              + 입력란 열기
                            </button>
                          )}
                        </div>
                        {modelForm.append_api_keys.length > 0 && (
                          <div className="space-y-1.5">
                            {modelForm.append_api_keys.map((key, i) => (
                              <div key={i} className="flex items-center gap-1.5">
                                <input
                                  type="password"
                                  value={key}
                                  onChange={(e) => {
                                    const next = [...modelForm.append_api_keys];
                                    next[i] = e.target.value;
                                    updateForm({ append_api_keys: next });
                                  }}
                                  className="flex-1 px-3 py-2 border border-emerald-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-400 text-xs min-w-0 bg-emerald-50/40"
                                  placeholder={`sk-... (추가 키 ${i + 1})`}
                                  autoComplete="new-password"
                                />
                                <button
                                  type="button"
                                  onClick={() => updateForm({ append_api_keys: modelForm.append_api_keys.filter((_, j) => j !== i) })}
                                  className="w-6 h-6 flex items-center justify-center rounded-md text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors flex-shrink-0"
                                  title="제거"
                                >
                                  <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3">
                                    <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
                                  </svg>
                                </button>
                              </div>
                            ))}
                            <button
                              type="button"
                              onClick={() => updateForm({ append_api_keys: [...modelForm.append_api_keys, ''] })}
                              className="w-full flex items-center justify-center gap-1 py-1.5 rounded-lg border border-dashed border-emerald-300 text-[10px] text-emerald-600 hover:border-emerald-400 hover:text-emerald-700 transition-colors"
                            >
                              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3">
                                <path d="M8.75 3.75a.75.75 0 0 0-1.5 0v3.5h-3.5a.75.75 0 0 0 0 1.5h3.5v3.5a.75.75 0 0 0 1.5 0v-3.5h3.5a.75.75 0 0 0 0-1.5h-3.5v-3.5Z" />
                              </svg>
                              키 더 추가 ({modelForm.append_api_keys.filter(k => k.trim()).length}개 입력됨)
                            </button>
                            <p className="text-[10px] text-gray-400 leading-relaxed">기존 키셋을 유지한 채 뒤에 추가합니다. 중복은 자동 제거됩니다.</p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Webhook Secret */}
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1.5">
                      Webhook Secret
                      {editingModelData && (
                        <span className={`ml-1.5 text-[10px] ${editingModelData.has_webhook_secret ? 'text-orange-600' : 'text-gray-400'}`}>
                          {editingModelData.has_webhook_secret ? '(설정됨)' : '(미설정)'}
                        </span>
                      )}
                    </label>
                    <input
                      type="password"
                      value={modelForm.webhook_secret}
                      onChange={(e) => updateForm({ webhook_secret: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-xs"
                      placeholder={editingModel ? '비워두면 기존 시크릿 유지' : 'whsec-...'}
                      autoComplete="new-password"
                    />
                    {editingModel && editingModelData?.has_webhook_secret && (
                      <label className="flex items-center gap-2 mt-1.5 cursor-pointer">
                        <input type="checkbox" checked={modelForm.clear_webhook_secret} onChange={(e) => updateForm({ clear_webhook_secret: e.target.checked })} className="rounded accent-red-500" />
                        <span className="text-[10px] text-red-500">Webhook Secret 삭제</span>
                      </label>
                    )}
                  </div>
                </div>
              </section>

              <div className="flex justify-end gap-2 pt-3 border-t border-gray-100">
                <button type="button" onClick={() => setShowModelForm(false)} className="px-4 py-2 rounded-lg border border-gray-200 text-xs text-gray-700 hover:bg-gray-50 transition-colors">
                  취소
                </button>
                <button type="submit" className="px-4 py-2 rounded-lg bg-indigo-500 text-white text-xs hover:bg-indigo-600 transition-colors shadow-sm">
                  {editingModel ? '수정 저장' : '등록'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── 로그인 모달 ── */}
      {showLoginModal && (
        <LoginModal
          onSuccess={handleLoginSuccess}
          onClose={() => navigate('/chat')}
        />
      )}
    </div>
  );
}