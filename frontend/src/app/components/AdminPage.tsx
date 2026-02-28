import { useState, useEffect } from 'react';
import { useNavigate } from "react-router-dom";
import { getMe, getUsers, updateUserRole, logout } from '../utils/api';

interface User {
  username: string;
  role: string;
  tenant: string;
  scopes: string[];
}

interface Session {
  sub: string;
  role: string;
  tenant: string;
  scope: string;
  iss?: string;
  aud?: string;
  typ?: string;
  exp?: number;
}

const AVAILABLE_ROLES = ['admin', 'member_plus', 'member'];

export function AdminPage() {
  const navigate = useNavigate();
  const [session, setSession] = useState<Session | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editingUser, setEditingUser] = useState<string | null>(null);
  const [selectedRole, setSelectedRole] = useState('');

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    setError('');
    try {
      const [sessionData, usersData] = await Promise.all([
        getMe(),
        getUsers(),
      ]);
      setSession(sessionData as Session);
      setUsers(usersData);
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes('401')) {
          // 인증 실패 시 로그인 페이지로
          navigate('/login');
        } else if (err.message.includes('403')) {
          setError('관리자 권한이 필요합니다.');
        } else {
          setError('데이터를 불러오는 중 오류가 발생했습니다.');
        }
      }
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = async () => {
    try {
      await logout();
      navigate('/login');
    } catch (err) {
      console.error('Logout error:', err);
      // 로그아웃 실패해도 로그인 페이지로 이동
      navigate('/login');
    }
  };

  const handleEditRole = (username: string, currentRole: string) => {
    setEditingUser(username);
    setSelectedRole(currentRole);
  };

  const handleSaveRole = async (username: string) => {
    try {
      await updateUserRole(username, selectedRole);
      // 성공 시 사용자 목록 새로고침
      await loadData();
      setEditingUser(null);
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes('400')) {
          alert('권한 변경에 실패했습니다. 마지막 관리자의 권한은 변경할 수 없습니다.');
        } else if (err.message.includes('404')) {
          alert('사용자를 찾을 수 없습니다.');
        } else {
          alert('권한 변경 중 오류가 발생했습니다.');
        }
      }
    }
  };

  const handleCancelEdit = () => {
    setEditingUser(null);
    setSelectedRole('');
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-600">로딩 중...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* 헤더 */}
      <header className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">관리자 페이지</h1>
            {session && (
              <p className="text-sm text-gray-600 mt-1">
                사용자: {session.sub} | 역할: {session.role} | 테넌트: {session.tenant}
              </p>
            )}
          </div>
          <button
            onClick={handleLogout}
            className="px-4 py-2 bg-red-600 text-white rounded-md hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500"
          >
            로그아웃
          </button>
        </div>
      </header>

      {/* 메인 컨텐츠 */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {error && (
          <div className="mb-4 rounded-md bg-red-50 p-4">
            <p className="text-sm text-red-800">{error}</p>
          </div>
        )}

        {/* 사용자 목록 */}
        <div className="bg-white shadow rounded-lg overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-xl font-semibold text-gray-900">사용자 목록</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    사용자명
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    역할
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    테넌트
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    권한
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    액션
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {users.map((user) => (
                  <tr key={user.username}>
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                      {user.username}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                      {editingUser === user.username ? (
                        <select
                          value={selectedRole}
                          onChange={(e) => setSelectedRole(e.target.value)}
                          className="px-2 py-1 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                        >
                          {AVAILABLE_ROLES.map((role) => (
                            <option key={role} value={role}>
                              {role}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span className={`px-2 py-1 inline-flex text-xs leading-5 font-semibold rounded-full ${
                          user.role === 'admin' 
                            ? 'bg-purple-100 text-purple-800' 
                            : user.role === 'member_plus'
                            ? 'bg-blue-100 text-blue-800'
                            : 'bg-gray-100 text-gray-800'
                        }`}>
                          {user.role}
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {user.tenant}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      <div className="flex flex-wrap gap-1">
                        {user.scopes.slice(0, 3).map((scope) => (
                          <span
                            key={scope}
                            className="px-2 py-0.5 bg-gray-100 text-gray-700 rounded text-xs"
                          >
                            {scope}
                          </span>
                        ))}
                        {user.scopes.length > 3 && (
                          <span className="px-2 py-0.5 bg-gray-100 text-gray-700 rounded text-xs">
                            +{user.scopes.length - 3}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                      {editingUser === user.username ? (
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleSaveRole(user.username)}
                            className="text-green-600 hover:text-green-900"
                          >
                            저장
                          </button>
                          <button
                            onClick={handleCancelEdit}
                            className="text-gray-600 hover:text-gray-900"
                          >
                            취소
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => handleEditRole(user.username, user.role)}
                          className="text-blue-600 hover:text-blue-900"
                        >
                          권한 변경
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* 세션 정보 */}
        {session && (
          <div className="mt-8 bg-white shadow rounded-lg overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200">
              <h2 className="text-xl font-semibold text-gray-900">세션 정보</h2>
            </div>
            <div className="px-6 py-4">
              <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <dt className="text-sm font-medium text-gray-500">사용자 ID</dt>
                  <dd className="mt-1 text-sm text-gray-900">{session.sub}</dd>
                </div>
                <div>
                  <dt className="text-sm font-medium text-gray-500">역할</dt>
                  <dd className="mt-1 text-sm text-gray-900">{session.role}</dd>
                </div>
                <div>
                  <dt className="text-sm font-medium text-gray-500">테넌트</dt>
                  <dd className="mt-1 text-sm text-gray-900">{session.tenant}</dd>
                </div>
                <div>
                  <dt className="text-sm font-medium text-gray-500">토큰 타입</dt>
                  <dd className="mt-1 text-sm text-gray-900">{session.typ || 'N/A'}</dd>
                </div>
                {session.exp && (
                  <div>
                    <dt className="text-sm font-medium text-gray-500">만료 시간</dt>
                    <dd className="mt-1 text-sm text-gray-900">
                      {new Date(session.exp * 1000).toLocaleString('ko-KR')}
                    </dd>
                  </div>
                )}
                <div className="sm:col-span-2">
                  <dt className="text-sm font-medium text-gray-500">스코프</dt>
                  <dd className="mt-1 text-sm text-gray-900">{session.scope}</dd>
                </div>
              </dl>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
