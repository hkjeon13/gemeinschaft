import { FormEvent, useEffect, useMemo, useState } from "react";

import { api, ApiError, pretty, setToken } from "../lib/api";
import type { AdminUser, Profile, TokenPair } from "../types";

type AdminMode = "loading" | "login" | "denied" | "dashboard";

const DEFAULT_ROLE_OPTIONS = ["user", "member", "member_plus", "admin"] as const;

function formatError(error: unknown): string {
  if (error instanceof ApiError) {
    return pretty({ status: error.status, body: error.body });
  }
  return String(error);
}

export function AdminPage(): JSX.Element {
  const [mode, setMode] = useState<AdminMode>("loading");
  const [loginUsername, setLoginUsername] = useState("psyche");
  const [loginPassword, setLoginPassword] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roleDrafts, setRoleDrafts] = useState<Record<string, string>>({});
  const [resultText, setResultText] = useState("Ready");

  const deniedMessage = useMemo(() => {
    if (!profile) {
      return "Logged in user is not an admin.";
    }
    return `Logged in as '${profile.sub}' with role '${profile.role ?? "unknown"}'. Admin role is required.`;
  }, [profile]);

  const resetToLogin = () => {
    setToken("");
    setLoginPassword("");
    setProfile(null);
    setUsers([]);
    setRoleDrafts({});
    setMode("login");
  };

  const loadUsers = async () => {
    const loadedUsers = await api<AdminUser[]>("/api/admin/users");
    setUsers(loadedUsers);
    setRoleDrafts(
      loadedUsers.reduce<Record<string, string>>((acc, user) => {
        acc[user.username] = user.role;
        return acc;
      }, {}),
    );
  };

  const evaluateSession = async () => {
    const hasToken = Boolean(localStorage.getItem("access_token"));
    if (!hasToken) {
      setMode("login");
      return;
    }

    try {
      const me = await api<Profile>("/api/auth/me");
      setProfile(me);

      if (me.role !== "admin") {
        alert("관리자 권한이 없습니다. 관리자 계정으로 다시 로그인해 주세요.");
        setMode("denied");
        return;
      }

      await loadUsers();
      setMode("dashboard");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setToken("");
        setMode("login");
        return;
      }
      setResultText(formatError(error));
      setMode("login");
    }
  };

  useEffect(() => {
    evaluateSession();
  }, []);

  const onLogin = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const tokenPair = await api<TokenPair>("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: loginUsername.trim(), password: loginPassword }),
      });

      setToken(tokenPair.access_token);
      setResultText(pretty({ message: "login ok", access_expires_in: tokenPair.access_expires_in }));
      await evaluateSession();
    } catch (error) {
      setResultText(formatError(error));
    }
  };

  const onRefresh = async () => {
    setMode("loading");
    await evaluateSession();
  };

  const onUpdateRole = async (username: string) => {
    const targetRole = roleDrafts[username] ?? "user";
    try {
      const updated = await api<AdminUser>(`/api/admin/users/${encodeURIComponent(username)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: targetRole }),
      });

      setResultText(
        pretty({
          message: "role updated",
          username,
          after: updated.role,
        }),
      );
      await loadUsers();
    } catch (error) {
      setResultText(formatError(error));
    }
  };

  return (
    <main className="layout admin-layout">
      <section className="card hero admin-hero">
        <p className="eyebrow">dataset.fin-ally.net/admin</p>
        <h1>Admin Dashboard</h1>
        <p className="muted">Manage users and roles with admin JWT.</p>
        <p className="muted">
          <a href="/">Back to console</a>
        </p>
      </section>

      {mode === "loading" && (
        <section className="card">
          <h2>Checking session...</h2>
          <p className="muted">Verifying token and role.</p>
        </section>
      )}

      {mode === "login" && (
        <section className="card">
          <h2>Admin Login</h2>
          <p className="muted">Sign in with an admin account to open the dashboard.</p>
          <form className="stack admin-form" onSubmit={onLogin}>
            <label>
              Username
              <input value={loginUsername} onChange={(e) => setLoginUsername(e.target.value)} required />
            </label>
            <label>
              Password
              <input type="password" value={loginPassword} onChange={(e) => setLoginPassword(e.target.value)} required />
            </label>
            <button type="submit">Login</button>
          </form>
        </section>
      )}

      {mode === "denied" && (
        <section className="card">
          <h2>Access denied</h2>
          <p className="muted">{deniedMessage}</p>
          <div className="stack admin-actions-row">
            <button type="button" onClick={resetToLogin}>
              관리자로 로그인
            </button>
            <button type="button" className="ghost" onClick={resetToLogin}>
              로그아웃
            </button>
          </div>
        </section>
      )}

      {mode === "dashboard" && (
        <section className="stack">
          <section className="card grid admin-top-grid">
            <div>
              <h2>Current session</h2>
              <pre className="output">{pretty(profile)}</pre>
            </div>
            <div>
              <h2>Actions</h2>
              <div className="stack">
                <button type="button" onClick={onRefresh}>
                  Refresh session
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      await loadUsers();
                      setResultText(pretty({ message: "users reloaded" }));
                    } catch (error) {
                      setResultText(formatError(error));
                    }
                  }}
                >
                  Reload users
                </button>
                <button type="button" className="ghost" onClick={resetToLogin}>
                  Logout
                </button>
              </div>
            </div>
          </section>

          <section className="card">
            <div className="admin-table-header">
              <h2>Users</h2>
              <p className="muted">Role can be updated inline for each user.</p>
            </div>
            <div className="table-wrap">
              <table className="users-table">
                <thead>
                  <tr>
                    <th>Username</th>
                    <th>Tenant</th>
                    <th>Role</th>
                    <th>Scopes</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => {
                    const roleOptions = Array.from(new Set([user.role, ...DEFAULT_ROLE_OPTIONS]));
                    return (
                      <tr key={user.username}>
                        <td>{user.username}</td>
                        <td>{user.tenant}</td>
                        <td>
                          <select
                            value={roleDrafts[user.username] ?? user.role}
                            onChange={(e) =>
                              setRoleDrafts((prev) => ({
                                ...prev,
                                [user.username]: e.target.value,
                              }))
                            }
                          >
                            {roleOptions.map((role) => (
                              <option key={role} value={role}>
                                {role}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td>{(user.scopes ?? []).join(" ")}</td>
                        <td>
                          <button type="button" onClick={() => onUpdateRole(user.username)}>
                            권한 수정
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <h2>Result</h2>
            <pre className="output">{resultText}</pre>
          </section>
        </section>
      )}
    </main>
  );
}
