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
        <span className="absolute left-6 top-1/2 -translate-y-1/2 z-[9999] w-80 rounded-xl border border-gray-200 bg-white shadow-xl px-3.5 py-3 text-xs text-gray-700 leading-relaxed whitespace-pre-wrap pointer-events-none">
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
      <label className={`flex items-center text-sm font-medium mb-1.5 ${highlighted ? 'text-indigo-700' : 'text-gray-700'}`}>
        <span>{label}</span>
        {tooltip && <InfoTooltip text={tooltip} />}
        {highlighted && (
          <span className="ml-2 px-1.5 py-0.5 bg-indigo-100 text-indigo-700 text-xs rounded-md">활성</span>
        )}
      </label>
      <textarea
        value={value}
        onChange={(e) => handleChange(e.target.value)}
        className={`w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 font-mono text-sm transition-colors ${
          error
            ? 'border-red-400 focus:ring-red-400'
            : highlighted
            ? 'border-indigo-300 focus:ring-indigo-500 bg-indigo-50/50'
            : 'border-gray-200 focus:ring-indigo-500 bg-white'
        }`}
        rows={4}
        placeholder={placeholder || '{}'}
        spellCheck={false}
      />
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}

// ─── RoleBadge ────────────────────────────────────────────────────────────────
function RoleBadge({ role }: { role: string }) {
  const cls =
    role === 'admin' ? 'bg-purple-100 text-purple-700' :
    role === 'member_plus' ? 'bg-indigo-100 text-indigo-700' :
    'bg-gray-100 text-gray-600';
  return <span className={`px-2 py-0.5 text-xs rounded-full ${cls}`}>{role}</span>;
}

// ─── AdminPage ────────────────────────────────────────────────────────────────
export function AdminPage() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<Tab>('users');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [pendingDeleteModelId, setPendingDeleteModelId] = useState<string | null>(null);

  const [users, setUsers] = useState<User[]>([]);
  const [editingUser, setEditingUser] = useState<string | null>(null);
  const [selectedRole, setSelectedRole] = useState('');

  const [models, setModels] = useState<Model[]>([]);
  const [showModelForm, setShowModelForm] = useState(false);
  const [editingModel, setEditingModel] = useState<string | null>(null);
  const [editingModelData, setEditingModelData] = useState<Model | null>(null);
  const [modelForm, setModelForm] = useState<ModelForm>(emptyModelForm);

  useEffect(() => { loadData(); }, [activeTab]);

  const loadData = async () => {
    setLoading(true); setError('');
    try {
      if (activeTab === 'users') setUsers(await getUsers());
      else setModels(await getModels());
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes('401')) navigate('/login');
        else if (err.message.includes('403')) setError('관리자 권한이 필요합니다.');
        else setError('데이터를 불러오는 중 오류가 발생했습니다.');
      }
    } finally { setLoading(false); }
  };

  const handleLogout = async () => {
    try { await logout(); } catch { /* ignore */ }
    navigate('/login');
  };

  const handleEditRole = (username: string, currentRole: string) => {
    setEditingUser(username); setSelectedRole(currentRole);
  };

  const handleSaveRole = async (username: string) => {
    setActionMessage('');
    try {
      await updateUserRole(username, selectedRole);
      await loadData(); setEditingUser(null);
      setActionMessage('권한이 업데이트되었습니다.');
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes('400')) setActionMessage('권한 변경에 실패했습니다. 마지막 관리자의 권한은 변경할 수 없습니다.');
        else if (err.message.includes('404')) setActionMessage('사용자를 찾을 수 없습니다.');
        else setActionMessage('권한 변경 중 오류가 발생했습니다.');
      }
    }
  };

  const handleNewModel = () => {
    setEditingModel(null); setEditingModelData(null);
    setPendingDeleteModelId(null);
    setActionMessage('');
    setModelForm(emptyModelForm); setShowModelForm(true);
  };

  const handleEditModel = (model: Model) => {
    setEditingModel(model.model_id); setEditingModelData(model);
    setPendingDeleteModelId(null);
    setActionMessage('');
    setModelForm({
      model_id: model.model_id, provider: model.provider,
      openai_api: model.openai_api, model: model.model,
      display_name: model.display_name, description: model.description,
      parameters: JSON.stringify(model.parameters, null, 2),
      client_options: JSON.stringify(model.client_options, null, 2),
      chat_create_options: JSON.stringify(model.chat_create_options, null, 2),
      responses_create_options: JSON.stringify(model.responses_create_options, null, 2),
      api_key: '', webhook_secret: '',
      clear_api_key: false, clear_webhook_secret: false,
      is_active: model.is_active, is_default: model.is_default,
    });
    setShowModelForm(true);
  };

  const parseJsonField = (raw: string, label: string): Record<string, unknown> | null => {
    try { return JSON.parse(raw); }
    catch {
      setActionMessage(`${label}는 유효한 JSON이어야 합니다.`);
      return null;
    }
  };

  const handleSaveModel = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionMessage('');
    const parameters = parseJsonField(modelForm.parameters, 'Parameters'); if (!parameters) return;
    const clientOptions = parseJsonField(modelForm.client_options, 'Client Options'); if (!clientOptions) return;
    const chatCreateOptions = parseJsonField(modelForm.chat_create_options, 'Create Options (chat)'); if (!chatCreateOptions) return;
    const responsesCreateOptions = parseJsonField(modelForm.responses_create_options, 'Create Options (responses)'); if (!responsesCreateOptions) return;

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
        if (modelForm.api_key) data.api_key = modelForm.api_key;
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
        if (modelForm.api_key) data.api_key = modelForm.api_key;
        if (modelForm.webhook_secret) data.webhook_secret = modelForm.webhook_secret;
        await createModel(data);
      }
      setShowModelForm(false);
      await loadData();
      setActionMessage(editingModel ? '모델이 수정되었습니다.' : '모델이 등록되었습니다.');
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes('409')) setActionMessage('이미 존재하는 Model ID입니다.');
        else if (err.message.includes('400')) {
          const match = err.message.match(/API Error 400: (.+)/);
          try {
            const body = match ? JSON.parse(match[1]) : null;
            setActionMessage(`검증 오류: ${body?.detail ?? match?.[1] ?? '입력 데이터 검증에 실패했습니다.'}`);
          } catch {
            setActionMessage(`검증 오류: ${match?.[1] ?? '입력 데이터 검증에 실패했습니다.'}`);
          }
        } else if (err.message.includes('422')) setActionMessage('입력 데이터가 유효하지 않습니다. 필드 타입을 확인하세요.');
        else setActionMessage('모델 저장 중 오류가 발생했습니다.');
      }
    }
  };

  const handleDeleteModel = async (modelId: string) => {
    if (pendingDeleteModelId !== modelId) {
      setPendingDeleteModelId(modelId);
      setActionMessage(`모델 "${modelId}"를 삭제하려면 삭제를 한 번 더 누르세요.`);
      return;
    }
    setActionMessage('');
    try {
      await deleteModel(modelId); await loadData();
      setPendingDeleteModelId(null);
      setActionMessage(`모델 "${modelId}"가 삭제되었습니다.`);
    } catch (err) {
      if (err instanceof Error)
        setActionMessage(err.message.includes('400') ? '마지막 남은 모델은 삭제할 수 없습니다.' : '모델 삭제 중 오류가 발생했습니다.');
      setPendingDeleteModelId(null);
    }
  };

  const updateForm = (patch: Partial<ModelForm>) => setModelForm((f) => ({ ...f, ...patch }));

  // ─── Loading ───────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-gray-300 border-t-indigo-500 rounded-full animate-spin" />
          <span className="text-sm text-gray-400">불러오는 중...</span>
        </div>
      </div>
    );
  }

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

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ── 사이드바 ── */}
      <aside className="w-64 flex flex-col flex-shrink-0 bg-[#f3f4f6] border-r border-gray-200">
        {/* 브랜드 헤더 */}
        <div className="px-4 pt-5 pb-4">
          <div className="flex items-center gap-2 mb-6">
            
            <div>
              <span className="text-sm font-semibold text-gray-800">Gemeinschaft</span>
              <p className="text-[10px] text-gray-400 leading-none mt-0.5">Admin</p>
            </div>
          </div>

          {/* 네비게이션 */}
          <nav className="space-y-0.5">
            {navItems.map(({ id, label, icon }) => {
              const isActive = activeTab === id;
              return (
                <button
                  key={id}
                  onClick={() => setActiveTab(id)}
                  className={`w-full relative flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-all ${
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

        {/* 하단 사용자 영역 */}
        <div className="px-3 py-3 border-t border-gray-200">
          <div className="flex items-center gap-2.5">
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
      <main className="flex-1 flex flex-col min-w-0 bg-gray-50">
        {/* 상단 헤더 */}
        <header className="bg-white/80 backdrop-blur-md border-b border-gray-200/80 px-6 py-3.5 flex items-center justify-between gap-4 z-10 flex-shrink-0">
          <h1 className="text-sm font-semibold text-gray-800">
            {activeTab === 'users' ? '사용자 관리' : '모델 관리'}
          </h1>
          {activeTab === 'models' && (
            <button
              onClick={handleNewModel}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-indigo-500 text-white hover:bg-indigo-600 transition-colors shadow-sm"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                <path d="M8.75 3.75a.75.75 0 0 0-1.5 0v3.5h-3.5a.75.75 0 0 0 0 1.5h3.5v3.5a.75.75 0 0 0 1.5 0v-3.5h3.5a.75.75 0 0 0 0-1.5h-3.5v-3.5Z" />
              </svg>
              새 모델 등록
            </button>
          )}
        </header>

        <div className="flex-1 overflow-y-auto p-6">
          {error && (
            <div className="mb-4 rounded-xl bg-red-50 border border-red-100 px-4 py-3">
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}
          {actionMessage && (
            <div className="mb-4 rounded-xl border border-amber-100 bg-amber-50 px-4 py-3">
              <p className="text-sm text-amber-800">{actionMessage}</p>
            </div>
          )}

          {/* ── 사용자 관리 ── */}
          {activeTab === 'users' && (
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
              <div className="px-5 py-3.5 border-b border-gray-100 flex items-center justify-between">
                <span className="text-sm font-medium text-gray-700">전체 {users.length}명</span>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full">
                  <thead>
                    <tr className="bg-gray-50/80">
                      {['사용자명', '역할', '테넌트', '권한 범위', ''].map((h) => (
                        <th key={h} className="px-5 py-3 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {users.map((user) => (
                      <tr key={user.username} className="hover:bg-gray-50/50 transition-colors">
                        <td className="px-5 py-3.5 whitespace-nowrap">
                          <div className="flex items-center gap-2.5">
                            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center flex-shrink-0">
                              <span className="text-white text-[10px] font-semibold">{user.username[0]?.toUpperCase()}</span>
                            </div>
                            <span className="text-sm font-medium text-gray-800">{user.username}</span>
                          </div>
                        </td>
                        <td className="px-5 py-3.5 whitespace-nowrap">
                          {editingUser === user.username ? (
                            <select
                              value={selectedRole}
                              onChange={(e) => setSelectedRole(e.target.value)}
                              className="px-2 py-1 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                            >
                              {AVAILABLE_ROLES.map((role) => <option key={role} value={role}>{role}</option>)}
                            </select>
                          ) : (
                            <RoleBadge role={user.role} />
                          )}
                        </td>
                        <td className="px-5 py-3.5 whitespace-nowrap text-sm text-gray-500">{user.tenant}</td>
                        <td className="px-5 py-3.5 text-sm text-gray-500">
                          <div className="flex flex-wrap gap-1">
                            {user.scopes.slice(0, 3).map((scope) => (
                              <span key={scope} className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded-md text-xs">{scope}</span>
                            ))}
                            {user.scopes.length > 3 && (
                              <span className="px-2 py-0.5 bg-gray-100 text-gray-500 rounded-md text-xs">+{user.scopes.length - 3}</span>
                            )}
                          </div>
                        </td>
                        <td className="px-5 py-3.5 whitespace-nowrap text-sm text-right">
                          {editingUser === user.username ? (
                            <div className="flex gap-2 justify-end">
                              <button onClick={() => handleSaveRole(user.username)} className="px-2.5 py-1 rounded-lg bg-indigo-500 text-white text-xs hover:bg-indigo-600 transition-colors">저장</button>
                              <button onClick={() => { setEditingUser(null); setSelectedRole(''); }} className="px-2.5 py-1 rounded-lg bg-gray-100 text-gray-600 text-xs hover:bg-gray-200 transition-colors">취소</button>
                            </div>
                          ) : (
                            <button onClick={() => handleEditRole(user.username, user.role)} className="px-2.5 py-1 rounded-lg text-xs text-indigo-600 hover:bg-indigo-50 transition-colors">권한 변경</button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── 모델 관리 ── */}
          {activeTab === 'models' && (
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
              <div className="px-5 py-3.5 border-b border-gray-100">
                <span className="text-sm font-medium text-gray-700">전체 {models.length}개</span>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full">
                  <thead>
                    <tr className="bg-gray-50/80">
                      {['Model ID', '표시명', 'Provider', 'API', 'Model', '상태', '시크릿', ''].map((h) => (
                        <th key={h} className="px-4 py-3 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wider">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {models.map((model) => (
                      <tr key={model.model_id} className="hover:bg-gray-50/50 transition-colors">
                        <td className="px-4 py-3.5 whitespace-nowrap">
                          <div className="flex items-center gap-1.5">
                            <span className="text-sm font-medium text-gray-800">{model.model_id}</span>
                            {model.is_default && (
                              <span className="px-1.5 py-0.5 bg-green-100 text-green-700 text-[10px] rounded-md">기본</span>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3.5 whitespace-nowrap text-sm text-gray-700">{model.display_name || <span className="text-gray-300">—</span>}</td>
                        <td className="px-4 py-3.5 whitespace-nowrap text-sm text-gray-500">{model.provider}</td>
                        <td className="px-4 py-3.5 whitespace-nowrap">
                          <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${
                            model.openai_api === 'responses' ? 'bg-violet-100 text-violet-700' : 'bg-sky-100 text-sky-700'
                          }`}>{model.openai_api}</span>
                        </td>
                        <td className="px-4 py-3.5 whitespace-nowrap text-sm text-gray-500">{model.model}</td>
                        <td className="px-4 py-3.5 whitespace-nowrap">
                          <span className={`px-2 py-0.5 text-xs rounded-full font-medium ${
                            model.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                          }`}>{model.is_active ? '활성' : '비활성'}</span>
                        </td>
                        <td className="px-4 py-3.5 whitespace-nowrap">
                          <div className="flex flex-col gap-1">
                            <span className={`px-2 py-0.5 text-[10px] rounded-md ${model.has_api_key ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-400'}`}>
                              {model.has_api_key ? '🔑 API Key' : '— API Key'}
                            </span>
                            <span className={`px-2 py-0.5 text-[10px] rounded-md ${model.has_webhook_secret ? 'bg-orange-100 text-orange-700' : 'bg-gray-100 text-gray-400'}`}>
                              {model.has_webhook_secret ? '🔐 Webhook' : '— Webhook'}
                            </span>
                          </div>
                        </td>
                        <td className="px-4 py-3.5 whitespace-nowrap text-sm">
                          <div className="flex gap-2">
                            <button onClick={() => handleEditModel(model)} className="px-2.5 py-1 rounded-lg text-xs text-indigo-600 hover:bg-indigo-50 transition-colors">수정</button>
                            <button
                              onClick={() => handleDeleteModel(model.model_id)}
                              className={`px-2.5 py-1 rounded-lg text-xs transition-colors ${
                                pendingDeleteModelId === model.model_id
                                  ? 'bg-red-500 text-white hover:bg-red-600'
                                  : 'text-red-500 hover:bg-red-50'
                              }`}
                            >
                              {pendingDeleteModelId === model.model_id ? '삭제 확인' : '삭제'}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </main>

      {/* ── 모델 등록/수정 모달 ── */}
      {showModelForm && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-start justify-center overflow-y-auto z-50 p-6">
          <div className="w-full max-w-3xl bg-white rounded-2xl shadow-2xl border border-gray-100 my-6">
            {/* 모달 헤더 */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
              <h2 className="text-sm font-semibold text-gray-900">
                {editingModel ? (
                  <span className="flex items-center gap-2">
                    모델 수정
                    <span className="px-2 py-0.5 bg-indigo-100 text-indigo-700 text-xs rounded-md font-mono">{editingModel}</span>
                  </span>
                ) : '새 모델 등록'}
              </h2>
              <button
                onClick={() => setShowModelForm(false)}
                className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              >
                <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
                  <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
                </svg>
              </button>
            </div>

            <form onSubmit={handleSaveModel} className="px-6 py-5 space-y-6">
              {/* 기본 정보 */}
              <section>
                <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-3">기본 정보</p>

                {!editingModel && (
                  <div className="mb-3">
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">
                      Model ID
                      <span className="ml-1.5 text-gray-400 font-normal">(선택 — 비우면 자동 생성)</span>
                    </label>
                    <input
                      type="text"
                      value={modelForm.model_id}
                      onChange={(e) => updateForm({ model_id: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm"
                      placeholder="비우면 서버가 자동 생성"
                    />
                  </div>
                )}

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">Provider</label>
                    <input
                      type="text"
                      value={modelForm.provider}
                      onChange={(e) => updateForm({ provider: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm"
                      placeholder="openai"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">OpenAI API <span className="text-red-400">*</span></label>
                    <select
                      value={modelForm.openai_api}
                      onChange={(e) => updateForm({ openai_api: e.target.value as 'chat.completions' | 'responses' })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm bg-white"
                      required
                    >
                      <option value="chat.completions">chat.completions</option>
                      <option value="responses">responses</option>
                    </select>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3 mt-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">Model <span className="text-red-400">*</span></label>
                    <input
                      type="text"
                      value={modelForm.model}
                      onChange={(e) => updateForm({ model: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm"
                      required
                      placeholder="gpt-4o-mini"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">Display Name</label>
                    <input
                      type="text"
                      value={modelForm.display_name}
                      onChange={(e) => updateForm({ display_name: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm"
                      placeholder="GPT-4o Mini"
                    />
                  </div>
                </div>

                <div className="mt-3">
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">Description</label>
                  <textarea
                    value={modelForm.description}
                    onChange={(e) => updateForm({ description: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm resize-none"
                    rows={2}
                  />
                </div>

                <div className="flex gap-5 mt-3">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={modelForm.is_active}
                      onChange={(e) => updateForm({ is_active: e.target.checked })}
                      className="rounded accent-indigo-500"
                    />
                    <span className="text-sm text-gray-700">활성화</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={modelForm.is_default}
                      onChange={(e) => updateForm({ is_default: e.target.checked })}
                      className="rounded accent-indigo-500"
                    />
                    <span className="text-sm text-gray-700">기본 모델로 설정</span>
                  </label>
                </div>
              </section>

              {/* JSON 파라미터 */}
              <section>
                <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-3">파라미터 (JSON)</p>
                <div className="space-y-4">
                  <JsonEditorField
                    label="Parameters"
                    value={modelForm.parameters}
                    onChange={(v) => updateForm({ parameters: v })}
                    placeholder='{"temperature": 0.7, "max_tokens": 1024}'
                    tooltip={FIELD_TOOLTIPS.parameters}
                  />
                  <JsonEditorField
                    label="Client Options"
                    value={modelForm.client_options}
                    onChange={(v) => updateForm({ client_options: v })}
                    placeholder='{"organization": "org-...", "project": "proj_..."}'
                    tooltip={FIELD_TOOLTIPS.client_options}
                  />
                  {modelForm.openai_api === 'chat.completions' ? (
                    <JsonEditorField
                      label="Create Options"
                      value={modelForm.chat_create_options}
                      onChange={(v) => updateForm({ chat_create_options: v })}
                      placeholder='{"reasoning_effort": "medium"}'
                      highlighted
                      tooltip={FIELD_TOOLTIPS.chat_create_options}
                    />
                  ) : (
                    <JsonEditorField
                      label="Create Options"
                      value={modelForm.responses_create_options}
                      onChange={(v) => updateForm({ responses_create_options: v })}
                      placeholder='{"reasoning": {"effort": "medium"}}'
                      highlighted
                      tooltip={FIELD_TOOLTIPS.responses_create_options}
                    />
                  )}
                </div>
                <p className="mt-2 text-xs text-gray-400">
                  OpenAI API를 바꾸면 해당 Create Options로 전환됩니다. 실행 시에는 선택된{' '}
                  <span className="font-mono text-indigo-600">{modelForm.openai_api}</span> 옵션만 사용됩니다.
                </p>
              </section>

              {/* 시크릿 */}
              <section>
                <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-3">시크릿 (Write-only)</p>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">
                      API Key
                      {editingModelData && (
                        <span className={`ml-1.5 text-xs ${editingModelData.has_api_key ? 'text-blue-600' : 'text-gray-400'}`}>
                          {editingModelData.has_api_key ? '(설정됨)' : '(미설정)'}
                        </span>
                      )}
                    </label>
                    <input
                      type="password"
                      value={modelForm.api_key}
                      onChange={(e) => updateForm({ api_key: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm"
                      placeholder={editingModel ? '비워두면 기존 키 유지' : 'sk-...'}
                      autoComplete="new-password"
                    />
                    {editingModel && editingModelData?.has_api_key && (
                      <label className="flex items-center gap-2 mt-1.5 cursor-pointer">
                        <input type="checkbox" checked={modelForm.clear_api_key} onChange={(e) => updateForm({ clear_api_key: e.target.checked })} className="rounded accent-red-500" />
                        <span className="text-xs text-red-500">API Key 삭제</span>
                      </label>
                    )}
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">
                      Webhook Secret
                      {editingModelData && (
                        <span className={`ml-1.5 text-xs ${editingModelData.has_webhook_secret ? 'text-orange-600' : 'text-gray-400'}`}>
                          {editingModelData.has_webhook_secret ? '(설정됨)' : '(미설정)'}
                        </span>
                      )}
                    </label>
                    <input
                      type="password"
                      value={modelForm.webhook_secret}
                      onChange={(e) => updateForm({ webhook_secret: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm"
                      placeholder={editingModel ? '비워두면 기존 시크릿 유지' : 'whsec-...'}
                      autoComplete="new-password"
                    />
                    {editingModel && editingModelData?.has_webhook_secret && (
                      <label className="flex items-center gap-2 mt-1.5 cursor-pointer">
                        <input type="checkbox" checked={modelForm.clear_webhook_secret} onChange={(e) => updateForm({ clear_webhook_secret: e.target.checked })} className="rounded accent-red-500" />
                        <span className="text-xs text-red-500">Webhook Secret 삭제</span>
                      </label>
                    )}
                  </div>
                </div>
              </section>

              {/* 버튼 */}
              <div className="flex justify-end gap-2 pt-4 border-t border-gray-100">
                <button
                  type="button"
                  onClick={() => setShowModelForm(false)}
                  className="px-4 py-2 rounded-lg border border-gray-200 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
                >
                  취소
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 rounded-lg bg-indigo-500 text-white text-sm hover:bg-indigo-600 transition-colors shadow-sm"
                >
                  {editingModel ? '수정 저장' : '등록'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
