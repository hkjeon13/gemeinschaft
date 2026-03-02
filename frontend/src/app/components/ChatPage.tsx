import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router';
import { createPortal } from 'react-dom';
import { marked } from 'marked';
import '../../styles/markdown.css';
import { LoginModal } from './LoginModal';
import {
  getConversationList,
  getConversation,
  addMessage,
  logout,
  getMe,
  updateMe,
  hideConversation,
  updateConversationTitle,
  getConversationModelList,
  getConversationDefaultModel,
  setConversationDefaultModel,
  deleteConversationDefaultModel,
  setConversationModelImage,
  deleteConversationModelImage,
  getConversationRoomModels,
  addConversationRoomModel,
  removeConversationRoomModel,
  getContinueConversationStatus,
  startContinueConversation,
  stopContinueConversation,
  type ConversationModelOption,
  type ConversationRoomModel,
} from '../utils/api';

marked.setOptions({ gfm: true, breaks: true });

function renderMarkdown(content: string): string {
  return marked.parse(content, { async: false }) as string;
}

// ─── 인터페이스 ───────────────────────────────────────────────────────────────

interface ConversationListItem {
  conversation_id: string;
  title?: string;
  message_count: number;
  updated_at: string;
  has_unread?: boolean;
}

interface Message {
  message_id: string;
  message: string;
  role?: string;
  model_id?: string;
  model_name?: string;
  model_display_name?: string;
  provider?: string;
  created_at: string;
  _optimistic?: true;
}

interface Conversation {
  conversation_id: string;
  tenant_id: string;
  user_id: string;
  messages: Message[];
  updated_at: string;
}

interface ConversationModel {
  model_id: string;
  model_display_name: string;
  model_name: string;
  provider: string;
  image_data_url?: string;
}

interface CurrentUserProfile {
  sub: string;
  role?: string;
  name: string;
  email?: string | null;
  email_verified: boolean;
  profile_image_data_url?: string | null;
}

// ─── 헬퍼 ────────────────────────────────────────────────────────────────────

function isUserMessage(msg: Message, index: number): boolean {
  if (msg.role) return msg.role === 'user';
  return index % 2 === 0;
}

function extractModels(messages: Message[]): ConversationModel[] {
  const seen = new Set<string>();
  const result: ConversationModel[] = [];
  for (const msg of messages) {
    const key = msg.model_id ?? msg.model_name;
    if (msg.role === 'assistant' && key && !seen.has(key)) {
      seen.add(key);
      result.push({
        model_id: msg.model_id ?? key,
        model_display_name: msg.model_display_name ?? msg.model_name ?? msg.model_id ?? 'Assistant',
        model_name: msg.model_name ?? '',
        provider: msg.provider ?? '',
      });
    }
  }
  return result;
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
}

function formatDate(iso: string) {
  const d = new Date(iso);
  const now = new Date();
  const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
  if (diffDays === 0) return formatTime(iso);
  if (diffDays < 7) return d.toLocaleDateString('ko-KR', { weekday: 'short' });
  return d.toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' });
}

function readImageFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith('image/')) {
      reject(new Error('이미지 파일만 업로드할 수 있습니다.'));
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : '';
      if (!result) {
        reject(new Error('이미지 파일을 읽을 수 없습니다.'));
        return;
      }
      resolve(result);
    };
    reader.onerror = () => reject(new Error('이미지 파일을 읽는 중 오류가 발생했습니다.'));
    reader.readAsDataURL(file);
  });
}

// ─── 아바타 ───────────────────────────────────────────────────────────────────

const AVATAR_COLORS = [
  { bg: '#6366f1', text: '#fff' },
  { bg: '#0ea5e9', text: '#fff' },
  { bg: '#10b981', text: '#fff' },
  { bg: '#f59e0b', text: '#fff' },
  { bg: '#ec4899', text: '#fff' },
  { bg: '#8b5cf6', text: '#fff' },
];

function colorFor(id: string) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h += id.charCodeAt(i);
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

function initialsOf(name: string) {
  const parts = name.trim().split(/[\s\-_]+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

function ModelAvatar({ model, size = 'md' }: { model: ConversationModel; size?: 'sm' | 'md' | 'lg' }) {
  const col = colorFor(model.model_id);
  const cls = size === 'sm' ? 'w-6 h-6 text-[9px]' : size === 'lg' ? 'w-10 h-10 text-sm' : 'w-8 h-8 text-xs';
  const [imageFailed, setImageFailed] = useState(false);

  useEffect(() => {
    setImageFailed(false);
  }, [model.image_data_url]);

  if (model.image_data_url && !imageFailed) {
    return (
      <img
        src={model.image_data_url}
        alt={model.model_display_name}
        onError={() => setImageFailed(true)}
        className={`${cls} rounded-full object-cover flex-shrink-0 ring-2 ring-white/10 bg-white`}
      />
    );
  }

  return (
    <div className={`${cls} rounded-full flex items-center justify-center flex-shrink-0 font-semibold select-none ring-2 ring-white/10`} style={{ background: col.bg, color: col.text }}>
      {initialsOf(model.model_display_name)}
    </div>
  );
}

function BotAvatar({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  const cls = size === 'sm' ? 'w-6 h-6' : size === 'lg' ? 'w-10 h-10' : 'w-8 h-8';
  return (
    <div className={`${cls} rounded-full bg-gradient-to-br from-slate-500 to-slate-700 flex items-center justify-center flex-shrink-0 ring-2 ring-white/10`}>
      <svg viewBox="0 0 20 20" fill="white" className="w-[45%] h-[45%]">
        <path fillRule="evenodd" d="M10 2a1 1 0 0 1 1 1v.5h3.5A1.5 1.5 0 0 1 16 5v9a1.5 1.5 0 0 1-1.5 1.5H10v.5a1 1 0 1 1-2 0V15H5.5A1.5 1.5 0 0 1 4 13.5V5A1.5 1.5 0 0 1 5.5 3.5H9V3a1 1 0 0 1 1-1Zm-1 4a1 1 0 1 0 0 2 1 1 0 0 0 0-2Zm2 0a1 1 0 1 0 0 2 1 1 0 0 0 0-2ZM7.5 11a.5.5 0 0 0 0 1h5a.5.5 0 0 0 0-1h-5Z" clipRule="evenodd" />
      </svg>
    </div>
  );
}

// ─── ContinueSettingsModal ────────────────────────────────────────────────────

interface ContinueSettings {
  minInterval: number;
  maxInterval: number;
  maxTurns: number;
}

function ContinueSettingsModal({
  onConfirm,
  onClose,
}: {
  onConfirm: (settings: ContinueSettings) => void;
  onClose: () => void;
}) {
  const [minInterval, setMinInterval] = useState(1);
  const [maxInterval, setMaxInterval] = useState(10);
  const [maxTurns, setMaxTurns] = useState(20);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (minInterval > maxInterval) {
      alert('최소 간격은 최대 간격보다 클 수 없습니다.');
      return;
    }
    onConfirm({ minInterval, maxInterval, maxTurns });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-sm mx-4 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-full bg-green-100 flex items-center justify-center">
              {/* play icon */}
              <svg viewBox="0 0 16 16" fill="none" className="w-3.5 h-3.5">
                <circle cx="8" cy="8" r="7" stroke="#22c55e" strokeWidth="1.5" />
                <path d="M6.5 5.5l4 2.5-4 2.5V5.5Z" fill="#22c55e" />
              </svg>
            </div>
            <span className="text-sm font-semibold text-gray-900">연속 대화 설정</span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
          >
            <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
              <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
            </svg>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-5 py-4 space-y-4">
          <p className="text-xs text-gray-400 leading-relaxed">
            현재 대화의 마지막 맥락으로 답변을 자동으로 이어서 생성���니다.<br />
            최대 턴 도달 시 자동으로 중단됩니다.
          </p>

          {/* 응답 간격 */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">응답 간격 (초)</label>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">최소</label>
                <input
                  type="number"
                  min={0}
                  max={300}
                  value={minInterval}
                  onChange={(e) => setMinInterval(Number(e.target.value))}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-green-400 text-center"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">최대</label>
                <input
                  type="number"
                  min={0}
                  max={300}
                  value={maxInterval}
                  onChange={(e) => setMaxInterval(Number(e.target.value))}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-green-400 text-center"
                />
              </div>
            </div>
            <p className="mt-1 text-[11px] text-gray-400">각 답변 생성 전 랜덤 지연이 적용됩니다</p>
          </div>

          {/* 최대 턴 */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">최대 턴 수</label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={1}
                max={200}
                value={maxTurns}
                onChange={(e) => setMaxTurns(Number(e.target.value))}
                className="w-24 px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-green-400 text-center"
              />
              <p className="text-xs text-gray-400">assistant 응답이 이 수에 도달하면 자동 중단</p>
            </div>
          </div>

          {/* 버튼 */}
          <div className="flex gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2 rounded-xl border border-gray-200 text-sm text-gray-600 hover:bg-gray-50 transition-colors"
            >
              취소
            </button>
            <button
              type="submit"
              className="flex-1 px-4 py-2 rounded-xl bg-green-500 text-white text-sm hover:bg-green-600 transition-colors shadow-sm flex items-center justify-center gap-1.5"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                <path d="M3 3.732a1.5 1.5 0 0 1 2.305-1.265l6.706 4.267a1.5 1.5 0 0 1 0 2.531l-6.706 4.268A1.5 1.5 0 0 1 3 12.267V3.732Z" />
              </svg>
              시작
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── ManageModelsModal ────────────────────────────────────────────────────────

function ManageModelsModal({
  conversationId,
  roomModels,
  isNew,
  onModelsChange,
  onClose,
}: {
  conversationId: string;
  roomModels: ConversationRoomModel[];
  isNew: boolean;
  onModelsChange: (models: ConversationRoomModel[]) => void;
  onClose: () => void;
}) {
  const [availableModels, setAvailableModels] = useState<Array<{
    model_id: string; provider: string; model: string;
    display_name: string; is_active: boolean; image_data_url?: string;
  }>>([]);
  const [loadingAvail, setLoadingAvail] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [adding, setAdding] = useState<string | null>(null);

  useEffect(() => {
    setLoadingAvail(true);
    getConversationModelList()
      .then((list) =>
        setAvailableModels(
          list.map((m) => ({
            model_id: m.model_id,
            provider: m.provider,
            model: m.model,
            display_name: m.display_name,
            // /conversation/model/list 는 이미 활성 모델만 반환
            is_active: true,
            image_data_url: m.image_data_url,
          }))
        )
      )
      .catch(console.error)
      .finally(() => setLoadingAvail(false));
  }, [conversationId]);

  const handleRemove = async (modelId: string) => {
    if (safeModels.length <= 1) return;
    if (isNew) {
      onModelsChange(safeModels.filter((m) => m.model_id !== modelId));
      return;
    }
    setRemoving(modelId);
    try {
      const updated = await removeConversationRoomModel(conversationId, modelId);
      onModelsChange(updated);
    } catch (e) {
      console.error(e);
      alert('모델 제거에 실패했습니다.');
    } finally {
      setRemoving(null);
    }
  };

  const handleAdd = async (modelId: string) => {
    setAdding(modelId);
    try {
      const updated = await addConversationRoomModel(conversationId, modelId);
      onModelsChange(updated);
    } catch (e) {
      console.error(e);
      alert('모델 추가에 실패했습니다.');
    } finally {
      setAdding(null);
    }
  };

  const safeModels = Array.isArray(roomModels) ? roomModels : [];
  const addedIds = new Set(safeModels.map((m) => m.model_id));
  const addableModels = availableModels.filter((m) => !addedIds.has(m.model_id));
  const displayModels = safeModels;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm mx-4 overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <h3 className="font-semibold text-gray-900">
              참여 모델
              <span className="text-gray-400 font-normal ml-1">({displayModels.length})</span>
            </h3>
          </div>
          <button onClick={onClose} className="w-7 h-7 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors">
            <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" /></svg>
          </button>
        </div>

        <ul className="max-h-60 overflow-y-auto divide-y divide-gray-50">
          {displayModels.length === 0 ? (
            <li className="px-5 py-8 text-center text-sm text-gray-400">참여한 모델이 없습니다</li>
          ) : displayModels.map((m) => {
            const avatarModel: ConversationModel = {
              model_id: m.model_id,
              model_display_name: m.display_name,
              model_name: m.model,
              provider: m.provider,
              image_data_url: m.image_data_url,
            };
            const isRemoving = removing === m.model_id;
            return (
              <li key={m.model_id} className="flex items-center gap-3 px-5 py-3">
                <ModelAvatar model={avatarModel} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-900 truncate">{m.display_name}</p>
                  <p className="text-xs text-gray-400 truncate mt-0.5">
                    {m.model || '—'}{m.provider ? ` · ${m.provider}` : ''}
                  </p>
                </div>
                <button
                  onClick={() => handleRemove(m.model_id)}
                  disabled={isRemoving || safeModels.length <= 1}
                  className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-full text-gray-300 hover:text-red-500 hover:bg-red-50 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                  title={safeModels.length <= 1 ? '마지막 모델은 제거할 수 없습니다' : '제거'}
                >
                  {isRemoving
                    ? <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" /></svg>
                    : <svg viewBox="0 0 20 20" fill="currentColor" className="w-3.5 h-3.5"><path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" /></svg>
                  }
                </button>
              </li>
            );
          })}
        </ul>

        <div className="border-t border-gray-100">
          {!showAdd ? (
            <button
              onClick={() => setShowAdd(true)}
              className="w-full flex items-center justify-center gap-2 px-5 py-3 text-sm text-indigo-600 hover:bg-indigo-50 transition-colors"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                <path d="M8.75 3.75a.75.75 0 0 0-1.5 0v3.5h-3.5a.75.75 0 0 0 0 1.5h3.5v3.5a.75.75 0 0 0 1.5 0v-3.5h3.5a.75.75 0 0 0 0-1.5h-3.5v-3.5Z" />
              </svg>
              모델 추가
            </button>
          ) : (
              <div>
                <div className="flex items-center justify-between px-4 pt-3 pb-1.5">
                  <span className="text-xs font-medium text-gray-500">추가할 모델 선택</span>
                  <button onClick={() => setShowAdd(false)} className="text-xs text-gray-400 hover:text-gray-600">닫기</button>
                </div>
                {loadingAvail ? (
                  <div className="flex items-center justify-center py-6">
                    <div className="w-4 h-4 border-2 border-gray-200 border-t-indigo-500 rounded-full animate-spin" />
                  </div>
                ) : addableModels.length === 0 ? (
                  <p className="text-center text-xs text-gray-400 px-4 pb-4">추가할 수 있는 모델이 없습니다</p>
                ) : (
                  <ul className="max-h-48 overflow-y-auto divide-y divide-gray-50 pb-2">
                    {addableModels.map((m) => {
                      const isAdding = adding === m.model_id;
                      const avatarModel: ConversationModel = {
                        model_id: m.model_id,
                        model_display_name: m.display_name,
                        model_name: m.model,
                        provider: m.provider,
                        image_data_url: m.image_data_url,
                      };
                      return (
                        <li key={m.model_id} className="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 transition-colors">
                          <ModelAvatar model={avatarModel} size="sm" />
                          <div className="min-w-0 flex-1">
                            <p className="text-sm text-gray-800 truncate">{m.display_name}</p>
                            <p className="text-[11px] text-gray-400 truncate">{m.provider}</p>
                          </div>
                          <button
                            onClick={() => handleAdd(m.model_id)}
                            disabled={isAdding}
                            className="flex-shrink-0 px-2.5 py-1 rounded-lg text-xs font-medium text-white disabled:opacity-50 transition-all"
                            style={{ background: '#4f46e5' }}
                          >
                            {isAdding ? '추가 중...' : '추가'}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            )}
          </div>
      </div>
    </div>
  );
}

// ─── DefaultModelModal ────────────────────────────────────────────────────────

function DefaultModelModal({ onClose }: { onClose: () => void }) {
  const [models, setModels] = useState<ConversationModelOption[]>([]);
  const [currentDefault, setCurrentDefault] = useState<{ model_id: string; display_name: string; source: string } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploadTargetModelId, setUploadTargetModelId] = useState<string | null>(null);
  const [imageBusyModelId, setImageBusyModelId] = useState<string | null>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    (async () => {
      try {
        const [list, def] = await Promise.all([getConversationModelList(), getConversationDefaultModel()]);
        setModels(list);
        setCurrentDefault(def);
        setSelected(def.source === 'user' ? def.model_id : null);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleSave = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await setConversationDefaultModel(selected);
      onClose();
    } catch (e) {
      console.error(e);
      alert('기본 모델 설정에 실패했습니다.');
    } finally {
      setSaving(false);
    }
  };

  const handleClear = async () => {
    setSaving(true);
    try {
      await deleteConversationDefaultModel();
      onClose();
    } catch (e) {
      console.error(e);
      alert('기본 모델 해제에 실패했습니다.');
    } finally {
      setSaving(false);
    }
  };

  const triggerUpload = (modelId: string) => {
    setUploadTargetModelId(modelId);
    imageInputRef.current?.click();
  };

  const handleImageFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const targetModelId = uploadTargetModelId;
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!targetModelId || !file) {
      setUploadTargetModelId(null);
      return;
    }

    setImageBusyModelId(targetModelId);
    try {
      const imageDataUrl = await readImageFileAsDataUrl(file);
      const result = await setConversationModelImage(targetModelId, imageDataUrl);
      setModels((prev) =>
        prev.map((item) =>
          item.model_id === targetModelId
            ? { ...item, image_data_url: result.image_data_url }
            : item
        )
      );
    } catch (e) {
      console.error(e);
      const msg = e instanceof Error ? e.message : '모델 이미지 업로드에 실패했습니다.';
      alert(msg);
    } finally {
      setImageBusyModelId(null);
      setUploadTargetModelId(null);
    }
  };

  const handleDeleteImage = async (modelId: string) => {
    setImageBusyModelId(modelId);
    try {
      await deleteConversationModelImage(modelId);
      setModels((prev) =>
        prev.map((item) =>
          item.model_id === modelId
            ? { ...item, image_data_url: undefined }
            : item
        )
      );
    } catch (e) {
      console.error(e);
      alert('모델 이미지 삭제에 실패했습니다.');
    } finally {
      setImageBusyModelId(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <input
          ref={imageInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={handleImageFileChange}
        />
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <h3 className="font-semibold text-gray-900">기본 모델 설정</h3>
            {currentDefault && (
              <p className="text-xs text-gray-400 mt-0.5">
                현재 적용: <span className="text-indigo-600">{currentDefault.display_name}</span>
                {currentDefault.source === 'global' && <span className="ml-1 text-gray-400">(글로벌 기본)</span>}
              </p>
            )}
          </div>
          <button onClick={onClose} className="w-7 h-7 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors">
            <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" /></svg>
          </button>
        </div>

        <div className="max-h-80 overflow-y-auto p-3 space-y-1.5">
          {loading ? (
            <div className="flex items-center justify-center py-10">
              <div className="w-5 h-5 border-2 border-gray-200 border-t-indigo-500 rounded-full animate-spin" />
            </div>
          ) : models.length === 0 ? (
            <p className="text-center text-sm text-gray-400 py-8">사용 가능한 모델이 없습니다</p>
          ) : models.map((m) => {
            const isSelected = selected === m.model_id;
            const avatarModel: ConversationModel = {
              model_id: m.model_id,
              model_display_name: m.display_name,
              model_name: m.model,
              provider: m.provider,
              image_data_url: m.image_data_url,
            };
            const imageBusy = imageBusyModelId === m.model_id;
            return (
              <div
                key={m.model_id}
                role="button"
                tabIndex={0}
                onClick={() => setSelected(isSelected ? null : m.model_id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setSelected(isSelected ? null : m.model_id);
                  }
                }}
                className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left transition-all border cursor-pointer ${
                  isSelected ? 'bg-indigo-50 border-indigo-200' : 'bg-gray-50/60 border-transparent hover:bg-gray-100'
                }`}
              >
                <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-all ${
                  isSelected ? 'border-indigo-500 bg-indigo-500' : 'border-gray-300'
                }`}>
                  {isSelected && <div className="w-1.5 h-1.5 bg-white rounded-full" />}
                </div>
                <ModelAvatar model={avatarModel} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-gray-900">{m.display_name}</span>
                    {m.is_global_default && (
                      <span className="text-[10px] px-1.5 py-0.5 bg-amber-100 text-amber-600 rounded-md">글로벌 기본</span>
                    )}
                  </div>
                  <p className="text-xs text-gray-400 mt-0.5 truncate">{m.model} · {m.provider}</p>
                  {m.description && <p className="text-xs text-gray-400 truncate mt-0.5">{m.description}</p>}
                </div>
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      triggerUpload(m.model_id);
                    }}
                    disabled={imageBusy}
                    className="px-2 py-1 rounded-md text-[11px] text-indigo-600 bg-indigo-50 hover:bg-indigo-100 disabled:opacity-40 disabled:cursor-not-allowed"
                    title="이미지 업로드"
                  >
                    {imageBusy ? '처리 중...' : m.image_data_url ? '변경' : '이미지'}
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDeleteImage(m.model_id);
                    }}
                    disabled={imageBusy || !m.image_data_url}
                    className="px-2 py-1 rounded-md text-[11px] text-gray-500 bg-gray-100 hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
                    title="이미지 삭제"
                  >
                    삭제
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        <div className="px-5 py-4 border-t border-gray-100 flex items-center justify-between gap-3">
          <button
            onClick={handleClear}
            disabled={saving || !currentDefault || currentDefault.source !== 'user'}
            className="text-sm text-gray-500 hover:text-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            기본 해제 (글로벌 복귀)
          </button>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 rounded-lg hover:bg-gray-100 transition-colors">취소</button>
            <button
              onClick={handleSave}
              disabled={saving || !selected}
              className="px-4 py-2 text-sm font-medium text-white rounded-lg transition-all disabled:opacity-40 disabled:cursor-not-allowed"
              style={{ background: '#4f46e5' }}
            >
              {saving ? '저장 중...' : '저장'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── ProfileModal ─────────────────────────────────────────────────────────────

function ProfileModal({
  username,
  onClose,
  onProfileUpdated,
}: {
  username: string;
  onClose: () => void;
  onProfileUpdated: (profile: CurrentUserProfile) => void;
}) {
  const [name, setName] = useState(username);
  const [initialName, setInitialName] = useState(username);
  const [email, setEmail] = useState<string | null>(null);
  const [emailVerified, setEmailVerified] = useState(false);
  const [profileImageDataUrl, setProfileImageDataUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const syncFromMe = (me: CurrentUserProfile) => {
    setName(me.name || me.sub);
    setInitialName(me.name || me.sub);
    setEmail(me.email ?? null);
    setEmailVerified(!!me.email_verified);
    setProfileImageDataUrl(me.profile_image_data_url ?? null);
    onProfileUpdated(me);
  };

  useEffect(() => {
    (async () => {
      try {
        const me = await getMe();
        syncFromMe(me);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleSaveName = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      alert('이름을 입력해주세요.');
      return;
    }
    if (trimmed === initialName.trim()) {
      onClose();
      return;
    }
    setSaving(true);
    try {
      const me = await updateMe({ name: trimmed });
      syncFromMe(me);
      onClose();
    } catch (e) {
      console.error(e);
      alert('프로필 저장에 실패했습니다.');
    } finally {
      setSaving(false);
    }
  };

  const handleUploadImage = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setSaving(true);
    try {
      const imageDataUrl = await readImageFileAsDataUrl(file);
      const me = await updateMe({ profile_image_data_url: imageDataUrl });
      syncFromMe(me);
    } catch (e) {
      console.error(e);
      const msg = e instanceof Error ? e.message : '프로필 이미지 업로드에 실패했습니다.';
      alert(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleRemoveImage = async () => {
    setSaving(true);
    try {
      const me = await updateMe({ clear_profile_image: true });
      syncFromMe(me);
    } catch (e) {
      console.error(e);
      alert('프로필 이미지 삭제에 실패했습니다.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm mx-4 overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={handleUploadImage}
        />
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h3 className="font-semibold text-gray-900">프로필 설정</h3>
          <button onClick={onClose} className="w-7 h-7 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors">
            <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" /></svg>
          </button>
        </div>
        {loading ? (
          <div className="py-10 flex items-center justify-center">
            <div className="w-5 h-5 border-2 border-gray-200 border-t-indigo-500 rounded-full animate-spin" />
          </div>
        ) : (
          <>
            <div className="px-5 py-5 flex flex-col items-center gap-4">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={saving}
                className="group relative rounded-full focus:outline-none focus:ring-2 focus:ring-indigo-300 disabled:opacity-60"
                title="이미지를 클릭해 업로드"
              >
                {profileImageDataUrl ? (
                  <img src={profileImageDataUrl} alt={name} className="w-16 h-16 rounded-full object-cover border border-gray-200" />
                ) : (
                  <div className="w-16 h-16 rounded-full bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center">
                    <svg viewBox="0 0 24 24" fill="white" className="w-8 h-8">
                      <path fillRule="evenodd" d="M7.5 6a4.5 4.5 0 1 1 9 0 4.5 4.5 0 0 1-9 0ZM3.751 20.105a8.25 8.25 0 0 1 16.498 0 .75.75 0 0 1-.437.695A18.683 18.683 0 0 1 12 22.5c-2.786 0-5.433-.608-7.812-1.7a.75.75 0 0 1-.437-.695Z" clipRule="evenodd" />
                    </svg>
                  </div>
                )}
                <span className="absolute inset-0 rounded-full bg-black/0 group-hover:bg-black/25 transition-colors flex items-center justify-center">
                  <svg viewBox="0 0 20 20" fill="white" className="w-5 h-5 opacity-0 group-hover:opacity-100 transition-opacity">
                    <path d="M4.25 4A2.25 2.25 0 0 0 2 6.25v7.5A2.25 2.25 0 0 0 4.25 16h11.5A2.25 2.25 0 0 0 18 13.75v-7.5A2.25 2.25 0 0 0 15.75 4h-2.09l-.76-1.52A1.5 1.5 0 0 0 11.56 1.5H8.44a1.5 1.5 0 0 0-1.34.98L6.34 4H4.25ZM10 13.5a3.25 3.25 0 1 0 0-6.5 3.25 3.25 0 0 0 0 6.5Z" />
                  </svg>
                </span>
              </button>
              <p className="text-[11px] text-gray-400 -mt-2">이미지를 클릭해 업로드</p>

              <div className="w-full space-y-3">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">이름</label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    maxLength={100}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-200"
                  />
                </div>
                <div className="text-xs text-gray-400">
                  <p>계정: {username}</p>
                  {email && <p className="mt-1">이메일: {email} {emailVerified ? '(인증됨)' : '(미인증)'}</p>}
                </div>
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={handleRemoveImage}
                    disabled={saving || !profileImageDataUrl}
                    className="px-3 py-2 text-xs rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    이미지 삭제
                  </button>
                </div>
              </div>
            </div>
            <div className="px-5 pb-5 flex gap-2">
              <button
                onClick={onClose}
                className="flex-1 py-2.5 text-sm font-medium text-gray-600 rounded-xl border border-gray-200 hover:bg-gray-50"
              >
                취소
              </button>
              <button
                onClick={handleSaveName}
                disabled={saving}
                className="flex-1 py-2.5 text-sm font-medium text-white rounded-xl disabled:opacity-40 disabled:cursor-not-allowed"
                style={{ background: '#4f46e5' }}
              >
                {saving ? '저장 중...' : '저장'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ─── UserMenuButton ───────────────────────────────────────────────────────────

function UserMenuButton({
  username,
  onLogout,
  onProfileUpdated,
}: {
  username: string;
  onLogout: () => void;
  onProfileUpdated: (profile: CurrentUserProfile) => void;
}) {
  const [open, setOpen] = useState(false);
  const [showDefaultModel, setShowDefaultModel] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const canPortal = typeof document !== 'undefined' && !!document.body;

  useEffect(() => {
    function outside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', outside);
    return () => document.removeEventListener('mousedown', outside);
  }, []);

  return (
    <>
      <div className="relative" ref={ref}>
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-all"
          title="더보기"
        >
          <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
            <path d="M3 9.5a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3ZM8 9.5a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3ZM13 9.5a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Z" />
          </svg>
        </button>

        {open && (
          <div className="absolute bottom-full left-0 mb-2 w-52 bg-white rounded-xl shadow-xl border border-gray-100 py-1.5 z-50 overflow-hidden">
            <div className="px-3.5 py-2.5 border-b border-gray-100 mb-1">
              <p className="text-xs font-medium text-gray-800 truncate">{username}</p>
              <p className="text-[11px] text-gray-400 mt-0.5">로그인된 계정</p>
            </div>

            <button
              className="w-full text-left px-3.5 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2.5 transition-colors"
              onClick={() => { setOpen(false); setShowProfile(true); }}
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 text-gray-400 flex-shrink-0">
                <path fillRule="evenodd" d="M15 8A7 7 0 1 1 1 8a7 7 0 0 1 14 0Zm-5-2a2 2 0 1 1-4 0 2 2 0 0 1 4 0ZM8 9.5c-2.29 0-3.516.85-3.99 1.516A5.5 5.5 0 0 0 13.49 11c-.474-.665-1.7-1.5-3.99-1.5Z" clipRule="evenodd" />
              </svg>
              프로필 설정
            </button>

            <button
              className="w-full text-left px-3.5 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2.5 transition-colors"
              onClick={() => { setOpen(false); setShowDefaultModel(true); }}
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 text-gray-400 flex-shrink-0">
                <path fillRule="evenodd" d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM0 8a8 8 0 1 1 16 0A8 8 0 0 1 0 8Zm9 0a1 1 0 1 1-2 0 1 1 0 0 1 2 0Zm-.25-4.75a.75.75 0 0 0-1.5 0v2.5a.75.75 0 0 0 1.5 0v-2.5Zm0 7a.75.75 0 0 0-1.5 0v2.5a.75.75 0 0 0 1.5 0v-2.5Z" clipRule="evenodd" />
              </svg>
              기본 모델 설정
            </button>

            <div className="mx-3 my-1 border-t border-gray-100" />

            <button
              className="w-full text-left px-3.5 py-2 text-sm text-red-500 hover:bg-red-50 flex items-center gap-2.5 transition-colors"
              onClick={() => { setOpen(false); onLogout(); }}
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 flex-shrink-0">
                <path fillRule="evenodd" d="M2 4.75A2.75 2.75 0 0 1 4.75 2h3a2.75 2.75 0 0 1 2.75 2.75v.5a.75.75 0 0 1-1.5 0v-.5c0-.69-.56-1.25-1.25-1.25h-3c-.69 0-1.25.56-1.25 1.25v6.5c0 .69.56 1.25 1.25 1.25h3c.69 0 1.25-.56 1.25-1.25v-.5a.75.75 0 0 1 1.5 0v.5A2.75 2.75 0 0 1 7.75 14h-3A2.75 2.75 0 0 1 2 11.25v-6.5Zm9.47.47a.75.75 0 0 1 1.06 0l2.25 2.25a.75.75 0 0 1 0 1.06l-2.25 2.25a.75.75 0 1 1-1.06-1.06l.97-.97H6a.75.75 0 0 1 0-1.5h6.44l-.97-.97a.75.75 0 0 1 0-1.06Z" clipRule="evenodd" />
              </svg>
              로그아웃
            </button>
          </div>
        )}
      </div>

      {showDefaultModel && canPortal && createPortal(
        <DefaultModelModal onClose={() => setShowDefaultModel(false)} />,
        document.body
      )}
      {showProfile && canPortal && createPortal(
        <ProfileModal
          username={username}
          onClose={() => setShowProfile(false)}
          onProfileUpdated={onProfileUpdated}
        />,
        document.body
      )}
    </>
  );
}

// ─── ConversationHeader ───────────────────────────────────────────────────────

function ConversationHeader({
  title,
  roomModels,
  conversationId,
  isNewConversation,
  onRoomModelsChange,
  onDelete,
  isContinuing,
  onOpenContinueSettings,
  onStopContinue,
  onMenuToggle,
}: {
  title: string;
  roomModels: ConversationRoomModel[];
  conversationId: string;
  isNewConversation: boolean;
  onRoomModelsChange: (models: ConversationRoomModel[]) => void;
  onDelete?: () => void;
  isContinuing?: boolean;
  onOpenContinueSettings?: () => void;
  onStopContinue?: () => void | Promise<void>;
  onMenuToggle?: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [showManageModels, setShowManageModels] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function outside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    document.addEventListener('mousedown', outside);
    return () => document.removeEventListener('mousedown', outside);
  }, []);

  const VISIBLE = 3;
  const safeRoomModels = Array.isArray(roomModels) ? roomModels : [];
  const visible = safeRoomModels.slice(0, VISIBLE);
  const overflow = safeRoomModels.length - VISIBLE;
  const toConvModel = (m: ConversationRoomModel): ConversationModel => ({
    model_id: m.model_id,
    model_display_name: m.display_name,
    model_name: m.model,
    provider: m.provider,
    image_data_url: m.image_data_url,
  });

  return (
    <>
      <header className="bg-white/80 backdrop-blur-md border-b border-gray-200/80 px-3 md:px-5 py-3 flex items-center justify-between gap-2 md:gap-4 z-10 flex-shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          {/* 모바일 햄버거 버튼 */}
          {onMenuToggle && (
            <button
              onClick={onMenuToggle}
              className="md:hidden flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 transition-colors"
              aria-label="메뉴 열기"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
                <path fillRule="evenodd" d="M1.5 3.25a.75.75 0 0 1 .75-.75h11.5a.75.75 0 0 1 0 1.5H2.25a.75.75 0 0 1-.75-.75Zm0 4a.75.75 0 0 1 .75-.75h11.5a.75.75 0 0 1 0 1.5H2.25a.75.75 0 0 1-.75-.75Zm0 4a.75.75 0 0 1 .75-.75h11.5a.75.75 0 0 1 0 1.5H2.25a.75.75 0 0 1-.75-.75Z" clipRule="evenodd" />
              </svg>
            </button>
          )}
          <h2 className="text-xs md:text-sm font-semibold text-gray-800 truncate">{title}</h2>
          {/* 연속 대화 중: 중지 버튼 (원 안에 네모) */}
          {isContinuing && (
            <button
              onClick={onStopContinue}
              title="연속 대화 중지"
              className="flex-shrink-0 w-6 h-6 rounded-full border-2 border-green-500 flex items-center justify-center hover:bg-green-50 transition-colors group animate-pulse"
            >
              <div className="w-2.5 h-2.5 rounded-sm bg-green-500 group-hover:bg-green-600 transition-colors" />
            </button>
          )}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {/* 참여 모델 아바타 스택 */}
          <button
            onClick={() => setShowManageModels(true)}
            className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-gray-50 hover:bg-gray-100 transition-colors"
            title="참여 모델 관리"
          >
            {safeRoomModels.length > 0 ? (
              <>
                <div className="flex items-center">
                  {visible.map((m, i) => (
                    <div
                      key={m.model_id}
                      title={m.display_name}
                      style={{ marginLeft: i === 0 ? 0 : '-7px', zIndex: visible.length - i }}
                      className="relative ring-2 ring-white rounded-full"
                    >
                      <ModelAvatar model={toConvModel(m)} size="sm" />
                    </div>
                  ))}
                  {overflow > 0 && (
                    <div
                      title={`외 ${overflow}개 모델`}
                      className="relative w-6 h-6 rounded-full bg-gray-300 text-gray-600 flex items-center justify-center text-[9px] font-semibold ring-2 ring-white"
                      style={{ marginLeft: '-7px', zIndex: 0 }}
                    >
                      +{overflow}
                    </div>
                  )}
                </div>
                <span className="text-[10px] md:text-xs text-gray-500">{safeRoomModels.length}개 모델</span>
              </>
            ) : (
              <>
                <div className="w-6 h-6 rounded-full bg-gray-200 flex items-center justify-center">
                  <svg viewBox="0 0 16 16" fill="#9ca3af" className="w-3.5 h-3.5">
                    <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM12.735 14c.618 0 1.093-.561.872-1.139a6.002 6.002 0 0 0-11.215 0c-.22.578.254 1.139.872 1.139h9.47Z" />
                  </svg>
                </div>
                <span className="text-[10px] md:text-xs text-gray-500">모델 설정</span>
              </>
            )}
          </button>

          <div className="relative" ref={menuRef}>
            <button
              onClick={() => setMenuOpen((v) => !v)}
              className="w-8 h-8 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
            >
              <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
                <path d="M10 3a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3ZM10 8.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3ZM11.5 15.5a1.5 1.5 0 1 0-3 0 1.5 1.5 0 0 0 3 0Z" />
              </svg>
            </button>

            {menuOpen && (
              <div className="absolute right-0 top-10 w-48 bg-white rounded-xl shadow-xl border border-gray-100 py-1.5 z-40 overflow-hidden">
                <button
                  className="w-full text-left px-3.5 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2.5 transition-colors"
                  onClick={() => { setMenuOpen(false); setShowManageModels(true); }}
                >
                  <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 text-gray-400">
                    <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM12.735 14c.618 0 1.093-.561.872-1.139a6.002 6.002 0 0 0-11.215 0c-.22.578.254 1.139.872 1.139h9.47Z" />
                  </svg>
                  참여 인원
                </button>

                {/* 연속 대화 */}
                {onOpenContinueSettings && (
                  <>
                    <div className="mx-3 my-1 border-t border-gray-100" />
                    {isContinuing ? (
                      <button
                        className="w-full text-left px-3.5 py-2 text-sm text-green-600 hover:bg-green-50 flex items-center gap-2.5 transition-colors"
                        onClick={() => { setMenuOpen(false); onStopContinue?.(); }}
                      >
                        {/* stop icon */}
                        <span className="w-3.5 h-3.5 flex-shrink-0 rounded-full border-2 border-green-500 flex items-center justify-center">
                          <span className="w-1.5 h-1.5 rounded-sm bg-green-500" />
                        </span>
                        연속 대화 중지
                      </button>
                    ) : (
                      <button
                        disabled={isNewConversation || safeRoomModels.length < 2}
                        className={`w-full text-left px-3.5 py-2 text-sm flex items-center gap-2.5 transition-colors ${
                          isNewConversation || safeRoomModels.length < 2
                            ? 'text-gray-300 cursor-not-allowed'
                            : 'text-gray-700 hover:bg-gray-50 cursor-pointer'
                        }`}
                        onClick={() => { if (!isNewConversation && safeRoomModels.length >= 2) { setMenuOpen(false); onOpenContinueSettings(); } }}
                        title={
                          isNewConversation
                            ? '메시지를 보낸 후 사용할 수 있습니다'
                            : safeRoomModels.length < 2
                              ? '모델을 2개 이상 추가해야 연속 대화를 시작할 수 있습니다'
                              : undefined
                        }
                      >
                        <svg viewBox="0 0 16 16" fill="none" className="w-3.5 h-3.5 flex-shrink-0">
                          <circle cx="8" cy="8" r="6.5" stroke={isNewConversation || safeRoomModels.length < 2 ? '#d1d5db' : '#9ca3af'} strokeWidth="1.5" />
                          <path d="M6.5 5.5l4 2.5-4 2.5V5.5Z" fill={isNewConversation || safeRoomModels.length < 2 ? '#d1d5db' : '#9ca3af'} />
                        </svg>
                        연속 대화
                      </button>
                    )}
                  </>
                )}

                {onDelete && (
                  <>
                    <div className="mx-3 my-1 border-t border-gray-100" />
                    <button
                      className="w-full text-left px-3.5 py-2 text-sm text-red-500 hover:bg-red-50 flex items-center gap-2.5 transition-colors"
                      onClick={() => { setMenuOpen(false); onDelete(); }}
                    >
                      <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                        <path fillRule="evenodd" d="M5 3.25V4H2.75a.75.75 0 0 0 0 1.5h.3l.815 8.15A1.5 1.5 0 0 0 5.357 15h5.285a1.5 1.5 0 0 0 1.493-1.35l.815-8.15h.3a.75.75 0 0 0 0-1.5H11v-.75A2.25 2.25 0 0 0 8.75 1h-1.5A2.25 2.25 0 0 0 5 3.25Zm2.25-.75a.75.75 0 0 0-.75.75V4h3v-.75a.75.75 0 0 0-.75-.75h-1.5ZM6.05 6a.75.75 0 0 1 .787.713l.275 5.5a.75.75 0 0 1-1.498.075l-.275-5.5A.75.75 0 0 1 6.05 6Zm3.9 0a.75.75 0 0 1 .712.787l-.275 5.5a.75.75 0 0 1-1.498-.075l.275-5.5a.75.75 0 0 1 .786-.712Z" clipRule="evenodd" />
                      </svg>
                      대화 삭제
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </header>

      {showManageModels && (
        <ManageModelsModal
          conversationId={conversationId}
          roomModels={roomModels}
          isNew={isNewConversation}
          onModelsChange={onRoomModelsChange}
          onClose={() => setShowManageModels(false)}
        />
      )}
    </>
  );
}

// ─── useIsMobile ─────────────────────────────────────────────────────────────

function useIsMobile(breakpoint = 768) {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth < breakpoint : false
  );
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < breakpoint);
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, [breakpoint]);
  return isMobile;
}

// ─── ChatPage ─────────────────────────────────────────────────────────────────

export function ChatPage() {
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const [currentUser, setCurrentUser] = useState<string>('');
  const [currentUserName, setCurrentUserName] = useState<string>('');
  const [currentUserProfileImage, setCurrentUserProfileImage] = useState<string | null>(null);
  const [currentUserRole, setCurrentUserRole] = useState<string>('');
  const [conversations, setConversations] = useState<ConversationListItem[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [currentConversation, setCurrentConversation] = useState<Conversation | null>(null);
  const [roomModels, setRoomModels] = useState<ConversationRoomModel[]>([]);
  const [pendingModel, setPendingModel] = useState<ConversationModel | null>(null);
  const [inputMessage, setInputMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [editingTitleId, setEditingTitleId] = useState<string | null>(null);
  const [editingTitleValue, setEditingTitleValue] = useState('');
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [continuingConversationId, setContinuingConversationId] = useState<string | null>(null);
  const [showContinueModal, setShowContinueModal] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showLoginModal, setShowLoginModal] = useState(false);
  const currentUserRef = useRef('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = () => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });

  useEffect(() => { scrollToBottom(); }, [currentConversation?.messages, sending]);
  useEffect(() => { loadInitialData(); }, []);
  useEffect(() => { currentUserRef.current = currentUser; }, [currentUser]);
  useEffect(() => {
    const handler = () => {
      // 비로그인 초기 진입에서는 자동으로 모달을 띄우지 않는다.
      if (currentUserRef.current) setShowLoginModal(true);
    };
    window.addEventListener('auth:required', handler);
    return () => window.removeEventListener('auth:required', handler);
  }, []);
  useEffect(() => {
    if (!currentUser || !selectedConversationId) return;
    let cancelled = false;

    const refreshStatus = async () => {
      try {
        const status = await getContinueConversationStatus(selectedConversationId);
        if (cancelled) return;
        if (status.running) {
          setContinuingConversationId(selectedConversationId);
          return;
        }
        if (status.active_conversation_id) {
          setContinuingConversationId(status.active_conversation_id);
          return;
        }
        setContinuingConversationId(null);
      } catch {
        if (!cancelled) {
          setContinuingConversationId(null);
        }
      }
    };

    refreshStatus();
    const timer = window.setInterval(refreshStatus, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [currentUser, selectedConversationId]);
  useEffect(() => {
    if (!currentUser || !selectedConversationId || continuingConversationId !== selectedConversationId) return;
    let cancelled = false;
    let inFlight = false;

    const refreshConversation = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const [conv, list] = await Promise.all([
          getConversation(selectedConversationId),
          getConversationList(),
        ]);
        if (cancelled) return;
        setCurrentConversation(conv);
        setConversations(list);
      } catch (err) {
        if (!cancelled) {
          console.error('Failed to refresh continuing conversation:', err);
        }
      } finally {
        inFlight = false;
      }
    };

    refreshConversation();
    const timer = window.setInterval(refreshConversation, 2200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [currentUser, selectedConversationId, continuingConversationId]);

  const loadInitialData = async () => {
    setLoading(true);
    setContinuingConversationId(null);
    try {
      const meData = await getMe();
      setCurrentUser(meData.sub);
      setCurrentUserName(meData.name || meData.sub);
      setCurrentUserProfileImage(meData.profile_image_data_url ?? null);
      setCurrentUserRole(meData.role ?? '');
      setConversations(await getConversationList());
    } catch {
      setCurrentUser('');
      setCurrentUserName('');
      setCurrentUserProfileImage(null);
      setCurrentUserRole('');
      setConversations([]);
      setCurrentConversation(null);
      setSelectedConversationId(null);
      setContinuingConversationId(null);
    } finally {
      setLoading(false);
    }
  };

  const handleLoginSuccess = async () => {
    setShowLoginModal(false);
    await loadInitialData();
  };

  const handleSelectConversation = async (id: string) => {
    setSidebarOpen(false); // 모바일: 대화 선택 시 사이드바 닫기
    setSelectedConversationId(id);
    setCurrentConversation(null); // 이전 conv-... ID 잔류로 isNewConversation 오작동 방지
    setPendingModel(null);
    // 선택 즉시 낙관적 읽음 처리 (GET /conversation/{id}가 mark_read=True로 호출됨)
    setConversations((prev) =>
      prev.map((c) => c.conversation_id === id ? { ...c, has_unread: false } : c)
    );
    try {
      const [conv, models] = await Promise.all([
        getConversation(id),
        getConversationRoomModels(id),
      ]);
      setCurrentConversation(conv);
      setRoomModels(Array.isArray(models) ? models : []);
    } catch (err) {
      console.error('Failed to load conversation:', err);
    }
  };

  const handleNewConversation = async () => {
    if (!currentUser) {
      setShowLoginModal(true);
      return;
    }
    setSidebarOpen(false); // 모바일: 새 대화 시 사이드바 닫기
    const id = `conv-${Date.now()}`;
    setSelectedConversationId(id);
    setCurrentConversation({ conversation_id: id, tenant_id: '', user_id: currentUser, messages: [], updated_at: new Date().toISOString() });
    setPendingModel(null);
    setRoomModels([]);
    // GET /conversation/{id}/models 는 없으면 사용자 기본 모델로 자동 초기화해서 반환
    try {
      const models = await getConversationRoomModels(id);
      setRoomModels(Array.isArray(models) ? models : []);
    } catch { /* 실패 시 빈 리스트 유지 */ }
    setTimeout(() => textareaRef.current?.focus(), 50);
  };

  const handleHideConversation = async (e: React.MouseEvent | null, convId: string) => {
    e?.stopPropagation();
    if (deletingId) return;
    setDeletingId(convId);
    try {
      if (continuingConversationId === convId) {
        try {
          await stopContinueConversation(convId);
        } catch (err) {
          console.error('Failed to stop continue runtime before delete:', err);
        }
        setContinuingConversationId(null);
      }
      await hideConversation(convId);
      setConversations((prev) => prev.filter((c) => c.conversation_id !== convId));
      if (selectedConversationId === convId) {
        setSelectedConversationId(null);
        setCurrentConversation(null);
      }
    } catch (err) {
      console.error('Failed to hide conversation:', err);
      alert('대화 삭제에 실패했습니다.');
    } finally {
      setDeletingId(null);
    }
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentUser) {
      setShowLoginModal(true);
      return;
    }
    if (!inputMessage.trim() || !selectedConversationId || sending) return;

    const text = inputMessage.trim();

    // 대화방 모델 리스트에서 랜덤으로 1개 선택
    let chosenModelId: string | undefined;
    let chosenModel: ConversationModel | null = null;
    const safeRoomModelsForSend = Array.isArray(roomModels) ? roomModels : [];
    if (safeRoomModelsForSend.length > 0) {
      const picked = safeRoomModelsForSend[Math.floor(Math.random() * safeRoomModelsForSend.length)];
      chosenModelId = picked.model_id;
      chosenModel = {
        model_id: picked.model_id,
        model_display_name: picked.display_name,
        model_name: picked.model,
        provider: picked.provider,
        image_data_url: picked.image_data_url,
      };
    }

    const optimisticMsg: Message = {
      message_id: `optimistic-${Date.now()}`,
      message: text,
      role: 'user',
      created_at: new Date().toISOString(),
      _optimistic: true,
    };
    setCurrentConversation((prev) => prev ? { ...prev, messages: [...prev.messages, optimisticMsg] } : prev);
    setInputMessage('');
    // 타이핑 인디케이터에 선택된 모델을 즉각 반영
    setPendingModel(chosenModel);
    setSending(true);

    const isNewConv = selectedConversationId.startsWith('conv-');

    try {
      const updated = await addMessage(selectedConversationId, text, chosenModelId);
      setCurrentConversation(updated);

      // 새 대화 → 실제 ID를 getConversationList await 전에 즉시 교체
      // (이 순서를 지켜야 conv-... ID가 잔류하는 창이 없어짐)
      if (isNewConv) {
        const realConvId = updated.conversation_id;
        if (realConvId !== selectedConversationId) {
          setSelectedConversationId(realConvId);
        }
        try {
          const serverModels = await getConversationRoomModels(realConvId);
          setRoomModels(Array.isArray(serverModels) ? serverModels : []);
        } catch { /* ignore */ }
      }

      setConversations(await getConversationList());
    } catch (err) {
      console.error('Failed to send message:', err);
      setCurrentConversation((prev) => prev ? { ...prev, messages: prev.messages.filter((m) => !m._optimistic) } : prev);
      setInputMessage(text);
      alert('메시지 전송에 실패했습니다.');
    } finally {
      setSending(false);
      setPendingModel(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage(e as unknown as React.FormEvent);
    }
  };

  const handleSaveTitle = async (convId: string) => {
    const newTitle = editingTitleValue.trim();
    setEditingTitleId(null);
    if (!newTitle) return;
    setConversations((prev) => prev.map((c) => c.conversation_id === convId ? { ...c, title: newTitle } : c));
    try {
      await updateConversationTitle(convId, newTitle);
    } catch (err) {
      console.error('Failed to update title:', err);
      alert('제목 수정에 실패했습니다.');
      setConversations(await getConversationList());
    }
  };

  const handleLogout = async () => {
    try { await logout(); } catch { /* ignore */ }
    setContinuingConversationId(null);
    // 로그아웃 후에는 채팅 화면만 유지하고, 채팅 액션 시 로그인 유도
    setCurrentUser('');
    setCurrentUserName('');
    setCurrentUserProfileImage(null);
    setCurrentUserRole('');
    setConversations([]);
    setCurrentConversation(null);
    setSelectedConversationId(null);
    setShowLoginModal(false);
  };

  const handleProfileUpdated = (profile: CurrentUserProfile) => {
    setCurrentUser(profile.sub);
    setCurrentUserName(profile.name || profile.sub);
    setCurrentUserProfileImage(profile.profile_image_data_url ?? null);
    setCurrentUserRole(profile.role ?? '');
  };

  const handleStartContinue = async (settings: ContinueSettings) => {
    if (!selectedConversationId) return;
    const convId = selectedConversationId;
    try {
      const runtime = await startContinueConversation(convId, {
        min_interval_seconds: settings.minInterval,
        max_interval_seconds: settings.maxInterval,
        max_turns: settings.maxTurns,
      });
      setShowContinueModal(false);
      if (runtime.running) {
        setContinuingConversationId(convId);
      } else if (runtime.active_conversation_id) {
        setContinuingConversationId(runtime.active_conversation_id);
      } else {
        setContinuingConversationId(null);
      }
    } catch (err) {
      console.error('Failed to start continue runtime:', err);
      const msg = err instanceof Error ? err.message : String(err);
      alert(`연속 대화 시작 실패: ${msg}`);
    }
  };

  const handleStopContinue = async () => {
    if (!selectedConversationId) return;
    try {
      const runtime = await stopContinueConversation(selectedConversationId);
      if (runtime.running) {
        setContinuingConversationId(selectedConversationId);
      } else if (runtime.active_conversation_id) {
        setContinuingConversationId(runtime.active_conversation_id);
      } else {
        setContinuingConversationId(null);
      }
    } catch (err) {
      console.error('Failed to stop continue runtime:', err);
      const msg = err instanceof Error ? err.message : String(err);
      alert(`연속 대화 중지 실패: ${msg}`);
    }
  };

  const roomModelImageMap = new Map(
    (Array.isArray(roomModels) ? roomModels : []).map((model) => [model.model_id, model.image_data_url])
  );
  const conversationModels = currentConversation
    ? extractModels(currentConversation.messages).map((model) => ({
      ...model,
      image_data_url: roomModelImageMap.get(model.model_id),
    }))
    : [];
  // 타이핑 인디케이터: pendingModel(랜덤 선택된 모델) 우선, 없으면 마지막 대화 모델
  const typingModel = pendingModel ?? (conversationModels.length > 0 ? conversationModels[conversationModels.length - 1] : null);
  const selectedConv = conversations.find((c) => c.conversation_id === selectedConversationId);
  const isSelectedConversationContinuing =
    !!selectedConversationId && continuingConversationId === selectedConversationId;

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

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ── 모바일 오버레이 배경 ── */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-30 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ── 좌측 사이드바 ── */}
      <aside className={`
        fixed inset-y-0 left-0 z-40 w-72 flex flex-col flex-shrink-0 bg-[#f3f4f6] border-r border-gray-200
        transition-transform duration-200 ease-in-out
        md:relative md:translate-x-0 md:z-auto
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
      `}>
        {/* 헤더 */}
        <div className="px-4 pt-5 pb-3">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-gray-800">Gemeinschaft</span>
            </div>
            {/* 모바일 닫기 버튼 */}
            <button
              onClick={() => setSidebarOpen(false)}
              className="md:hidden w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-colors"
              aria-label="사이드바 닫기"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
              </svg>
            </button>
          </div>

          <button
            onClick={handleNewConversation}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs md:text-sm font-medium bg-white border border-gray-200 text-gray-600 hover:bg-gray-50 hover:text-gray-800 hover:border-gray-300 transition-all shadow-sm"
          >
            <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 opacity-60">
              <path d="M8.75 3.75a.75.75 0 0 0-1.5 0v3.5h-3.5a.75.75 0 0 0 0 1.5h3.5v3.5a.75.75 0 0 0 1.5 0v-3.5h3.5a.75.75 0 0 0 0-1.5h-3.5v-3.5Z" />
            </svg>
            새 대화
          </button>
        </div>

        {/* 대화 목록 */}
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {conversations.length === 0 ? (
            <div className="px-4 py-10 text-center">
              <p className="text-xs text-gray-400">대화 기록이 없습니다</p>
            </div>
          ) : (
            <div className="space-y-0.5">
              {conversations.map((conv) => {
                const isSelected = selectedConversationId === conv.conversation_id;
                const hasUnread = !isSelected && !!conv.has_unread;
                return (
                  <div
                    key={conv.conversation_id}
                    className={`group relative flex items-center rounded-lg cursor-pointer transition-all ${
                      isSelected ? 'bg-white shadow-sm border border-gray-200/80' : 'hover:bg-white/70'
                    }`}
                    onClick={() => handleSelectConversation(conv.conversation_id)}
                  >
                    {isSelected && <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 rounded-full bg-indigo-500" />}

                    <div className="flex-1 min-w-0 px-3 py-2.5">
                      {editingTitleId === conv.conversation_id ? (
                        <input
                          className="w-full text-xs md:text-sm rounded px-1.5 py-0.5 outline-none bg-white border border-indigo-300 text-gray-800 focus:ring-2 focus:ring-indigo-100"
                          value={editingTitleValue}
                          onChange={(e) => setEditingTitleValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleSaveTitle(conv.conversation_id);
                            if (e.key === 'Escape') setEditingTitleId(null);
                          }}
                          onBlur={() => handleSaveTitle(conv.conversation_id)}
                          onClick={(e) => e.stopPropagation()}
                          autoFocus
                          maxLength={100}
                        />
                      ) : (
                        <div className="flex items-center gap-1 min-w-0">
                          <p className={`text-xs md:text-sm truncate flex-1 ${
                            isSelected ? 'text-gray-900' : hasUnread ? 'text-gray-900' : 'text-gray-600'
                          }`}>
                            {conv.title ?? conv.conversation_id}
                          </p>
                          {/* 미확인 파란 점 — 메뉴 열림 시 숨김 */}
                          {hasUnread && openMenuId !== conv.conversation_id && (
                            <span className="flex-shrink-0 w-1.5 h-1.5 rounded-full bg-blue-500 group-hover:opacity-0 transition-opacity" />
                          )}
                        </div>
                      )}
                      <div className="flex items-center gap-1.5 mt-0.5">
                        <span className={`text-[11px] ${hasUnread ? 'text-blue-400' : 'text-gray-400'}`}>{formatDate(conv.updated_at)}</span>
                        <span className="text-[10px] text-gray-300">·</span>
                        <span className="text-[11px] text-gray-400">{conv.message_count}개</span>
                      </div>
                    </div>

                    {/* ··· 메뉴 버튼 */}
                    <div className="relative flex-shrink-0 mr-1.5" onClick={(e) => e.stopPropagation()}>
                      {deletingId === conv.conversation_id ? (
                        <div className="p-1.5">
                          <svg className="w-3.5 h-3.5 text-gray-400 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
                          </svg>
                        </div>
                      ) : (
                        <>
                          <button
                            className={`p-1.5 rounded-md transition-all ${
                              openMenuId === conv.conversation_id
                                ? 'opacity-100 text-gray-600 bg-gray-200'
                                : 'opacity-0 group-hover:opacity-100 text-gray-400 hover:text-gray-600 hover:bg-gray-200'
                            }`}
                            title="더보기"
                            onClick={(e) => {
                              e.stopPropagation();
                              setOpenMenuId(openMenuId === conv.conversation_id ? null : conv.conversation_id);
                            }}
                          >
                            <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                              <path d="M2 8a1.5 1.5 0 1 1 3 0 1.5 1.5 0 0 1-3 0ZM6.5 8a1.5 1.5 0 1 1 3 0 1.5 1.5 0 0 1-3 0ZM11 8a1.5 1.5 0 1 1 3 0 1.5 1.5 0 0 1-3 0Z" />
                            </svg>
                          </button>

                          {openMenuId === conv.conversation_id && (
                            <>
                              <div
                                className="fixed inset-0 z-30"
                                onClick={(e) => { e.stopPropagation(); setOpenMenuId(null); }}
                              />
                              <div className="absolute right-0 top-full mt-1 w-36 bg-white rounded-xl shadow-lg border border-gray-100 py-1 z-40 overflow-hidden">
                                <button
                                  className="w-full text-left px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2 transition-colors"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setOpenMenuId(null);
                                    setEditingTitleId(conv.conversation_id);
                                    setEditingTitleValue(conv.title ?? conv.conversation_id);
                                  }}
                                >
                                  <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 text-gray-400 flex-shrink-0">
                                    <path d="M11.013 1.427a1.75 1.75 0 0 1 2.474 0l1.086 1.086a1.75 1.75 0 0 1 0 2.474l-8.61 8.61c-.21.21-.47.364-.756.445l-3.251.93a.75.75 0 0 1-.927-.928l.929-3.25c.081-.286.235-.547.445-.758l8.61-8.61Zm.176 4.823L9.75 4.81l-6.286 6.287a.253.253 0 0 0-.064.108l-.558 1.953 1.953-.558a.253.253 0 0 0 .108-.064Zm1.238-3.763a.25.25 0 0 0-.354 0L10.811 3.75l1.439 1.44 1.263-1.263a.25.25 0 0 0 0-.354Z" />
                                  </svg>
                                  제목 수정
                                </button>
                                <div className="mx-2 my-0.5 border-t border-gray-100" />
                                <button
                                  className="w-full text-left px-3 py-2 text-sm text-red-500 hover:bg-red-50 flex items-center gap-2 transition-colors"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setOpenMenuId(null);
                                    handleHideConversation(null, conv.conversation_id);
                                  }}
                                >
                                  <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 flex-shrink-0">
                                    <path fillRule="evenodd" d="M5 3.25V4H2.75a.75.75 0 0 0 0 1.5h.3l.815 8.15A1.5 1.5 0 0 0 5.357 15h5.285a1.5 1.5 0 0 0 1.493-1.35l.815-8.15h.3a.75.75 0 0 0 0-1.5H11v-.75A2.25 2.25 0 0 0 8.75 1h-1.5A2.25 2.25 0 0 0 5 3.25Zm2.25-.75a.75.75 0 0 0-.75.75V4h3v-.75a.75.75 0 0 0-.75-.75h-1.5ZM6.05 6a.75.75 0 0 1 .787.713l.275 5.5a.75.75 0 0 1-1.498.075l-.275-5.5A.75.75 0 0 1 6.05 6Zm3.9 0a.75.75 0 0 1 .712.787l-.275 5.5a.75.75 0 0 1-1.498-.075l.275-5.5a.75.75 0 0 1 .786-.712Z" clipRule="evenodd" />
                                  </svg>
                                  대화 삭제
                                </button>
                              </div>
                            </>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* 하단 유저 정보 */}
        <div className="px-3 py-3 border-t border-gray-200 space-y-2">
          {/* Admin 버튼 */}
          {currentUser && currentUserRole === 'admin' && (
            <button
              onClick={() => { setSidebarOpen(false); navigate('/admin'); }}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium text-indigo-600 hover:bg-indigo-50 transition-colors"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 flex-shrink-0 opacity-70">
                <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm0 2a5 5 0 1 1 0 10A5 5 0 0 1 8 3Zm0 1.5a.5.5 0 0 0-.5.5v2.793L5.854 9.44a.5.5 0 1 0 .707.707L8 8.707V5a.5.5 0 0 0-.5-.5Z" />
              </svg>
              <span className="truncate">관리자 페이지</span>
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3 flex-shrink-0 ml-auto opacity-40">
                <path fillRule="evenodd" d="M6.22 4.22a.75.75 0 0 1 1.06 0l3.25 3.25a.75.75 0 0 1 0 1.06l-3.25 3.25a.75.75 0 0 1-1.06-1.06L9.19 8 6.22 5.03a.75.75 0 0 1 0-1.06Z" clipRule="evenodd" />
              </svg>
            </button>
          )}
          {currentUser ? (
            <div className="flex items-center gap-2.5">
              {currentUserProfileImage ? (
                <img
                  src={currentUserProfileImage}
                  alt={currentUserName || currentUser}
                  className="w-7 h-7 rounded-full object-cover border border-gray-200 flex-shrink-0"
                />
              ) : (
                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center flex-shrink-0">
                  <svg viewBox="0 0 16 16" fill="white" className="w-3.5 h-3.5">
                    <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM12.735 14c.618 0 1.093-.561.872-1.139a6.002 6.002 0 0 0-11.215 0c-.22.578.254 1.139.872 1.139h9.47Z" />
                  </svg>
                </div>
              )}
              <span className="text-xs truncate flex-1 font-medium text-gray-500">
                {currentUserName || currentUser}
              </span>
              <UserMenuButton
                username={currentUser}
                onLogout={handleLogout}
                onProfileUpdated={handleProfileUpdated}
              />
            </div>
          ) : (
            <button
              onClick={() => setShowLoginModal(true)}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium text-indigo-600 hover:bg-indigo-50 transition-colors"
            >
              로그인
            </button>
          )}
        </div>
      </aside>

      {/* ── 우측 메인 채팅 영역 ── */}
      <main className="flex-1 flex flex-col min-w-0 bg-gray-50 w-full">
        {selectedConversationId && currentConversation ? (
          <>
            <ConversationHeader
              title={
                selectedConv?.title
                  ?? (currentConversation.conversation_id.startsWith('conv-') ? '새로운 대화' : currentConversation.conversation_id)
              }
              roomModels={roomModels}
              conversationId={currentConversation.conversation_id}
              isNewConversation={currentConversation.messages.length === 0}
              onRoomModelsChange={setRoomModels}
              onDelete={() => handleHideConversation(null, currentConversation.conversation_id)}
              isContinuing={isSelectedConversationContinuing}
              onOpenContinueSettings={() => setShowContinueModal(true)}
              onStopContinue={handleStopContinue}
              onMenuToggle={() => setSidebarOpen(true)}
            />

            <div className="flex-1 overflow-y-auto py-4 md:py-6 space-y-1">
              {currentConversation.messages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-8">
                  <div className="w-12 h-12 rounded-2xl bg-white shadow-sm border border-gray-100 flex items-center justify-center">
                    <svg viewBox="0 0 24 24" fill="none" stroke="#a5b4fc" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-6 h-6">
                      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                    </svg>
                  </div>
                  <div>
                    <p className="text-xs md:text-sm font-medium text-gray-700">새 대화를 시작하세요</p>
                    <p className="text-[11px] md:text-xs text-gray-400 mt-1">메시지를 입력하거나 Enter를 누르세요</p>
                  </div>
                </div>
              ) : (
                <div className="max-w-3xl lg:max-w-4xl xl:max-w-5xl 2xl:max-w-6xl mx-auto w-full px-3 md:px-4 space-y-4 md:space-y-6">
                  {currentConversation.messages.map((msg, index) => {
                    const isUser = isUserMessage(msg, index);

                    if (isUser) {
                      return (
                        <div key={msg.message_id} className="flex justify-end">
                          <div className="max-w-[88%] md:max-w-[75%]">
                            <div
                              className={`px-4 py-2.5 rounded-2xl rounded-br-sm shadow-sm transition-opacity ${msg._optimistic ? 'opacity-60' : ''}`}
                              style={{ background: '#4f46e5', color: 'white' }}
                            >
                              <p className="text-xs md:text-sm whitespace-pre-wrap break-words leading-relaxed">{msg.message}</p>
                            </div>
                            <p className="text-[11px] text-gray-400 mt-1.5 text-right pr-1">{formatTime(msg.created_at)}</p>
                          </div>
                        </div>
                      );
                    }

                    const msgModel: ConversationModel | undefined = msg.model_id || msg.model_name
                      ? {
                          model_id: msg.model_id ?? msg.model_name ?? 'assistant',
                          model_display_name: msg.model_display_name ?? msg.model_name ?? msg.model_id ?? 'Assistant',
                          model_name: msg.model_name ?? '',
                          provider: msg.provider ?? '',
                          image_data_url: msg.model_id ? roomModelImageMap.get(msg.model_id) : undefined,
                        }
                      : undefined;

                    return (
                      <div key={msg.message_id} className="flex items-start gap-3">
                        <div className="flex-shrink-0 mt-0.5">
                          {msgModel ? <ModelAvatar model={msgModel} /> : <BotAvatar />}
                        </div>
                        <div className="flex-1 min-w-0 max-w-[75%]">
                          <div className="flex items-center gap-2 mb-1.5">
                            <span className="text-xs font-medium text-gray-500">{msgModel?.model_display_name ?? 'Assistant'}</span>
                            {msgModel?.provider && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-gray-100 text-gray-400">{msgModel.provider}</span>
                            )}
                          </div>
                          <div className="bg-white rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm border border-gray-100/80">
                            <div className="md-body break-words" dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.message) }} />
                          </div>
                          <p className="text-[11px] text-gray-400 mt-1.5 pl-1">{formatTime(msg.created_at)}</p>
                        </div>
                      </div>
                    );
                  })}

                  {sending && (
                    <div className="flex items-start gap-3">
                      <div className="flex-shrink-0 mt-0.5">
                        {typingModel ? <ModelAvatar model={typingModel} /> : <BotAvatar />}
                      </div>
                      <div className="flex-1 min-w-0 max-w-[75%]">
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className="text-xs font-medium text-gray-500">{typingModel?.model_display_name ?? 'Assistant'}</span>
                          {typingModel?.provider && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-gray-100 text-gray-400">{typingModel.provider}</span>
                          )}
                        </div>
                        <div className="bg-white rounded-2xl rounded-tl-sm px-4 py-3.5 shadow-sm border border-gray-100/80">
                          <div className="flex items-center gap-1.5">
                            {[0, 150, 300].map((delay) => (
                              <span key={delay} className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: `${delay}ms`, animationDuration: '1s' }} />
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                  <div ref={messagesEndRef} />
                </div>
              )}
            </div>

            <div className="px-3 md:px-4 pb-4 md:pb-5 pt-2 md:pt-3">
              <form onSubmit={handleSendMessage} className="max-w-3xl lg:max-w-4xl xl:max-w-5xl 2xl:max-w-6xl mx-auto w-full">
                <div className="relative flex items-end bg-white rounded-2xl shadow-sm border border-gray-200 focus-within:border-indigo-300 focus-within:ring-2 focus-within:ring-indigo-100 transition-all">
                  <textarea
                    ref={textareaRef}
                    value={inputMessage}
                    onChange={(e) => {
                      setInputMessage(e.target.value);
                      e.target.style.height = 'auto';
                      e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px';
                    }}
                    onKeyDown={handleKeyDown}
                    placeholder={isMobile ? "메시지 입력... (Enter 전송)" : "메시지를 입력하세요... (Enter 전송, Shift+Enter 줄바꿈)"}
                    className="flex-1 resize-none px-3 md:px-4 py-3 md:py-3.5 bg-transparent text-xs md:text-sm text-gray-800 placeholder-gray-400 outline-none max-h-40 min-h-[46px] md:min-h-[52px]"
                    rows={1}
                    maxLength={4000}
                    disabled={sending}
                  />
                  <div className="pr-2 pb-2 flex-shrink-0">
                    <button
                      type="submit"
                      disabled={!inputMessage.trim() || sending}
                      className="w-9 h-9 flex items-center justify-center rounded-xl transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                      style={{ background: inputMessage.trim() && !sending ? '#4f46e5' : '#e5e7eb' }}
                    >
                      <svg viewBox="0 0 16 16" fill="white" className="w-4 h-4 translate-x-px">
                        <path d="M2.87 2.298a.75.75 0 0 0-.812 1.021L3.39 6.624a1 1 0 0 0 .928.626H8.25a.75.75 0 0 1 0 1.5H4.318a1 1 0 0 0-.927.626l-1.333 3.305a.75.75 0 0 0 .812 1.021 24.194 24.194 0 0 0 11.787-5.28.75.75 0 0 0 0-1.145A24.192 24.192 0 0 0 2.869 2.298Z" />
                      </svg>
                    </button>
                  </div>
                </div>
              </form>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col min-h-0">
            {/* 모바일 전용 상단 바 */}
            <div className="md:hidden flex-shrink-0 flex items-center gap-3 px-4 py-3 bg-white border-b border-gray-200">
              <button
                onClick={() => setSidebarOpen(true)}
                className="w-8 h-8 flex items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 transition-colors"
                aria-label="메뉴 열기"
              >
                <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
                  <path fillRule="evenodd" d="M1.5 3.25a.75.75 0 0 1 .75-.75h11.5a.75.75 0 0 1 0 1.5H2.25a.75.75 0 0 1-.75-.75Zm0 4a.75.75 0 0 1 .75-.75h11.5a.75.75 0 0 1 0 1.5H2.25a.75.75 0 0 1-.75-.75Zm0 4a.75.75 0 0 1 .75-.75h11.5a.75.75 0 0 1 0 1.5H2.25a.75.75 0 0 1-.75-.75Z" clipRule="evenodd" />
                </svg>
              </button>
              <span className="text-sm font-semibold text-gray-800">Gemeinschaft</span>
            </div>
          <div className="flex-1 flex flex-col items-center justify-center gap-4">
            <div className="w-16 h-16 rounded-2xl bg-white shadow-sm border border-gray-100 flex items-center justify-center">
              <svg viewBox="0 0 24 24" fill="none" stroke="#a5b4fc" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-8 h-8">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
            </div>
            <div className="text-center">
              <p className="text-xs md:text-sm font-medium text-gray-600">대화를 선택하거나 시작하세요</p>
              <p className="text-[11px] md:text-xs text-gray-400 mt-1">왼쪽 목록에서 대화를 선택하거나 새 대화를 만드세요</p>
            </div>
            <button
              onClick={handleNewConversation}
              className="mt-1 px-4 py-2 rounded-xl text-xs md:text-sm font-medium text-white transition-colors"
              style={{ background: '#4f46e5' }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#4338ca')}
              onMouseLeave={(e) => (e.currentTarget.style.background = '#4f46e5')}
            >
              + 새 대화 시작
            </button>
          </div>
          </div>
        )}
      </main>

      {/* 연속 대화 설정 모달 */}
      {showContinueModal && (
        <ContinueSettingsModal
          onConfirm={handleStartContinue}
          onClose={() => setShowContinueModal(false)}
        />
      )}
      {showLoginModal && (
        <LoginModal
          onSuccess={handleLoginSuccess}
          onClose={currentUser ? () => setShowLoginModal(false) : undefined}
        />
      )}
    </div>
  );
}
