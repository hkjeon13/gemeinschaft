import { useState } from 'react';
import { login, register } from '../utils/api';

interface LoginModalProps {
  onSuccess: () => void;
  /** 닫기 버튼 표시 여부 (닫으면 onClose 호출) */
  onClose?: () => void;
}

type AuthMode = 'login' | 'register';

export function LoginModal({ onSuccess, onClose }: LoginModalProps) {
  const [mode, setMode] = useState<AuthMode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const [regName, setRegName] = useState('');
  const [regUsername, setRegUsername] = useState('');
  const [regEmail, setRegEmail] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [regPasswordConfirm, setRegPasswordConfirm] = useState('');

  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(false);

  const switchMode = (next: AuthMode) => {
    setMode(next);
    setError('');
    setNotice('');
    setLoading(false);
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setNotice('');
    setLoading(true);
    try {
      await login(username, password);
      onSuccess();
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes('401')) {
          setError('아이디 또는 비밀번호가 올바르지 않습니다.');
        } else if (err.message.includes('429')) {
          setError('로그인 시도 횟수가 초과되었습니다. 잠시 후 다시 시도해주세요.');
        } else if (err.message.includes('Email verification is required')) {
          setError('이메일 인증이 필요합니다. 메일함의 인증 링크를 먼저 클릭해주세요.');
        } else if (err.message.includes('403')) {
          setError('접근이 거부되었습니다.');
        } else {
          setError('로그인 중 오류가 발생했습니다. 다시 시도해주세요.');
        }
      }
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setNotice('');

    if (regPassword !== regPasswordConfirm) {
      setError('비밀번호 확인이 일치하지 않습니다.');
      return;
    }

    setLoading(true);
    try {
      const response = await register({
        name: regName,
        username: regUsername,
        password: regPassword,
        email: regEmail,
      });
      setUsername(regUsername);
      setPassword('');
      setRegPassword('');
      setRegPasswordConfirm('');
      setNotice(response.verification_required
        ? '회원가입이 완료되었습니다. 이메일 인증 링크를 클릭한 뒤 로그인해주세요.'
        : '회원가입이 완료되었습니다. 바로 로그인할 수 있습니다.');
      setMode('login');
      setError('');
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes('409')) {
          setError('이미 사용 중인 아이디 또는 이메일입니다.');
        } else if (err.message.includes('400')) {
          setError('입력값을 확인해주세요. (이메일 형식/비밀번호 길이 등)');
        } else {
          setError('회원가입 중 오류가 발생했습니다. 다시 시도해주세요.');
        }
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm px-4">
      <div
        className="w-full max-w-sm bg-white rounded-2xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 pt-6 pb-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-sm">
              <svg viewBox="0 0 20 20" fill="white" className="w-5 h-5">
                <path fillRule="evenodd" d="M10 1a4.5 4.5 0 0 0-4.5 4.5V9H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2h-.5V5.5A4.5 4.5 0 0 0 10 1Zm3 8V5.5a3 3 0 1 0-6 0V9h6Z" clipRule="evenodd" />
              </svg>
            </div>
            <div>
              <h2 className="text-sm font-semibold text-gray-900">{mode === 'login' ? '로그인' : '회원가입'}</h2>
              <p className="text-xs text-gray-400 mt-0.5">
                {mode === 'login' ? '계정에 로그인하세요' : '이메일 인증 기반 계정을 만드세요'}
              </p>
            </div>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              aria-label="닫기"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
              </svg>
            </button>
          )}
        </div>

        <div className="px-6 pb-2">
          <div className="grid grid-cols-2 bg-gray-100 rounded-lg p-1">
            <button
              type="button"
              onClick={() => switchMode('login')}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${mode === 'login' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500'}`}
            >
              로그인
            </button>
            <button
              type="button"
              onClick={() => switchMode('register')}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${mode === 'register' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500'}`}
            >
              회원가입
            </button>
          </div>
        </div>

        {mode === 'login' ? (
          <form onSubmit={handleLogin} className="px-6 pb-6 space-y-4">
            <div className="space-y-3">
              <div>
                <label htmlFor="modal-username" className="block text-xs font-medium text-gray-600 mb-1.5">
                  사용자명
                </label>
                <input
                  id="modal-username"
                  type="text"
                  required
                  autoFocus
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="사용자명 입력"
                  className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent transition-all"
                  disabled={loading}
                />
              </div>
              <div>
                <label htmlFor="modal-password" className="block text-xs font-medium text-gray-600 mb-1.5">
                  비밀번호
                </label>
                <input
                  id="modal-password"
                  type="password"
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="비밀번호 입력"
                  className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent transition-all"
                  disabled={loading}
                />
              </div>
            </div>

            {notice && (
              <div className="flex items-start gap-2 rounded-xl bg-emerald-50 border border-emerald-100 px-3 py-2.5">
                <p className="text-xs text-emerald-700">{notice}</p>
              </div>
            )}
            {error && (
              <div className="flex items-start gap-2 rounded-xl bg-red-50 border border-red-100 px-3 py-2.5">
                <p className="text-xs text-red-700">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 rounded-xl text-sm font-medium text-white transition-all disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              style={{ background: loading ? '#a5b4fc' : '#4f46e5' }}
            >
              {loading ? '로그인 중...' : '로그인'}
            </button>
          </form>
        ) : (
          <form onSubmit={handleRegister} className="px-6 pb-6 space-y-4">
            <div className="space-y-3">
              <div>
                <label htmlFor="modal-reg-name" className="block text-xs font-medium text-gray-600 mb-1.5">
                  이름
                </label>
                <input
                  id="modal-reg-name"
                  type="text"
                  required
                  autoFocus
                  value={regName}
                  onChange={(e) => setRegName(e.target.value)}
                  placeholder="홍길동"
                  className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent transition-all"
                  disabled={loading}
                />
              </div>
              <div>
                <label htmlFor="modal-reg-username" className="block text-xs font-medium text-gray-600 mb-1.5">
                  아이디
                </label>
                <input
                  id="modal-reg-username"
                  type="text"
                  required
                  autoComplete="username"
                  value={regUsername}
                  onChange={(e) => setRegUsername(e.target.value)}
                  placeholder="사용할 아이디"
                  className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent transition-all"
                  disabled={loading}
                />
              </div>
              <div>
                <label htmlFor="modal-reg-email" className="block text-xs font-medium text-gray-600 mb-1.5">
                  이메일
                </label>
                <input
                  id="modal-reg-email"
                  type="email"
                  required
                  autoComplete="email"
                  value={regEmail}
                  onChange={(e) => setRegEmail(e.target.value)}
                  placeholder="name@example.com"
                  className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent transition-all"
                  disabled={loading}
                />
              </div>
              <div>
                <label htmlFor="modal-reg-password" className="block text-xs font-medium text-gray-600 mb-1.5">
                  비밀번호
                </label>
                <input
                  id="modal-reg-password"
                  type="password"
                  required
                  autoComplete="new-password"
                  value={regPassword}
                  onChange={(e) => setRegPassword(e.target.value)}
                  placeholder="8자 이상"
                  className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent transition-all"
                  disabled={loading}
                />
              </div>
              <div>
                <label htmlFor="modal-reg-password-confirm" className="block text-xs font-medium text-gray-600 mb-1.5">
                  비밀번호 확인
                </label>
                <input
                  id="modal-reg-password-confirm"
                  type="password"
                  required
                  autoComplete="new-password"
                  value={regPasswordConfirm}
                  onChange={(e) => setRegPasswordConfirm(e.target.value)}
                  placeholder="비밀번호를 다시 입력"
                  className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent transition-all"
                  disabled={loading}
                />
              </div>
            </div>

            {error && (
              <div className="flex items-start gap-2 rounded-xl bg-red-50 border border-red-100 px-3 py-2.5">
                <p className="text-xs text-red-700">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 rounded-xl text-sm font-medium text-white transition-all disabled:opacity-60 disabled:cursor-not-allowed"
              style={{ background: loading ? '#a5b4fc' : '#4f46e5' }}
            >
              {loading ? '회원가입 중...' : '회원가입'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
