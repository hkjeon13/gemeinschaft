import { useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router';

export function EmailVerificationPage() {
  const navigate = useNavigate();

  const { result, message } = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    const rawResult = params.get('result');
    const rawMessage = params.get('message');
    return {
      result: rawResult === 'success' ? 'success' : 'error',
      message: rawMessage || (rawResult === 'success' ? '이메일 인증이 완료되었습니다.' : '이메일 인증에 실패했습니다.'),
    };
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      navigate('/chat', { replace: true });
    }, 5000);
    return () => window.clearTimeout(timer);
  }, [navigate]);

  const success = result === 'success';

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
      <div className="w-full max-w-md rounded-2xl bg-white border border-gray-100 shadow-lg p-6">
        <div className="flex items-start gap-3">
          <div
            className={`w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 ${
              success ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'
            }`}
          >
            {success ? (
              <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                <path fillRule="evenodd" d="M16.704 5.29a1 1 0 0 1 .006 1.414l-7.25 7.313a1 1 0 0 1-1.42 0L3.29 9.267a1 1 0 0 1 1.414-1.414l4.046 4.046 6.543-6.603a1 1 0 0 1 1.41-.006Z" clipRule="evenodd" />
              </svg>
            ) : (
              <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                <path fillRule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm3.53-10.53a.75.75 0 0 0-1.06-1.06L10 8.94 7.53 6.47a.75.75 0 0 0-1.06 1.06L8.94 10l-2.47 2.47a.75.75 0 1 0 1.06 1.06L10 11.06l2.47 2.47a.75.75 0 0 0 1.06-1.06L11.06 10l2.47-2.47Z" clipRule="evenodd" />
              </svg>
            )}
          </div>
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-gray-900">
              {success ? '이메일 인증 완료' : '이메일 인증 실패'}
            </h1>
            <p className="mt-1 text-sm text-gray-600 break-words">{message}</p>
            <p className="mt-2 text-xs text-gray-400">5초 후 채팅 화면으로 이동합니다.</p>
          </div>
        </div>

        <div className="mt-5">
          <button
            onClick={() => navigate('/chat', { replace: true })}
            className="w-full py-2.5 rounded-xl text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
          >
            채팅으로 이동
          </button>
        </div>
      </div>
    </div>
  );
}
