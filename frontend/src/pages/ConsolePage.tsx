import { FormEvent, useState } from "react";

import { api, ApiError, pretty, setToken } from "../lib/api";
import type { ConversationDetail, ConversationSummary, Profile, TokenPair } from "../types";

function formatError(error: unknown): string {
  if (error instanceof ApiError) {
    return pretty({ status: error.status, body: error.body });
  }
  return String(error);
}

export function ConsolePage(): JSX.Element {
  const [username, setUsername] = useState("psyche");
  const [password, setPassword] = useState("");
  const [conversationId, setConversationId] = useState("demo-room");
  const [message, setMessage] = useState("");
  const [profileText, setProfileText] = useState("Not loaded");
  const [resultText, setResultText] = useState("Ready");

  const onLogin = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const tokenPair = await api<TokenPair>("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      setToken(tokenPair.access_token);
      setResultText(pretty({ message: "login ok", access_expires_in: tokenPair.access_expires_in }));
    } catch (error) {
      setResultText(formatError(error));
    }
  };

  const onLoadProfile = async () => {
    try {
      const profile = await api<Profile>("/api/auth/me");
      setProfileText(pretty(profile));
    } catch (error) {
      setProfileText(formatError(error));
    }
  };

  const onListConversations = async () => {
    try {
      const conversations = await api<ConversationSummary[]>("/api/conversation/");
      setResultText(pretty(conversations));
    } catch (error) {
      setResultText(formatError(error));
    }
  };

  const onLoadConversation = async () => {
    const id = conversationId.trim();
    if (!id) {
      setResultText("conversation id is required");
      return;
    }

    try {
      const detail = await api<ConversationDetail>(`/api/conversation/${encodeURIComponent(id)}`);
      setResultText(pretty(detail));
    } catch (error) {
      setResultText(formatError(error));
    }
  };

  const onSendMessage = async () => {
    const id = conversationId.trim();
    const text = message.trim();

    if (!id || !text) {
      setResultText("conversation id and message are required");
      return;
    }

    try {
      const detail = await api<ConversationDetail>(`/api/conversation/${encodeURIComponent(id)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      setResultText(pretty(detail));
    } catch (error) {
      setResultText(formatError(error));
    }
  };

  return (
    <main className="layout">
      <section className="card hero">
        <p className="eyebrow">dataset.fin-ally.net</p>
        <h1>Dataset Console</h1>
        <p className="muted">
          This page is served on port 10015. API calls are routed through <code>/api/*</code>.
        </p>
        <p className="muted">
          Admin page: <a href="/admin">/admin</a>
        </p>
      </section>

      <section className="card grid">
        <div>
          <h2>1) Login</h2>
          <form className="stack" onSubmit={onLogin}>
            <label>
              Username
              <input value={username} onChange={(e) => setUsername(e.target.value)} required />
            </label>
            <label>
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            <button type="submit">Sign in</button>
          </form>
        </div>

        <div>
          <h2>2) Token / Profile</h2>
          <div className="stack">
            <button type="button" onClick={onLoadProfile}>
              Load /auth/me
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setToken("");
                setProfileText("Token cleared");
              }}
            >
              Clear token
            </button>
          </div>
          <pre className="output">{profileText}</pre>
        </div>
      </section>

      <section className="card grid">
        <div>
          <h2>3) Conversations</h2>
          <div className="stack inline">
            <input value={conversationId} onChange={(e) => setConversationId(e.target.value)} />
            <button type="button" onClick={onLoadConversation}>
              Load
            </button>
          </div>
          <div className="stack">
            <textarea rows={4} value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Write a message" />
            <button type="button" onClick={onSendMessage}>
              Send message
            </button>
            <button type="button" className="ghost" onClick={onListConversations}>
              List conversations
            </button>
          </div>
        </div>

        <div>
          <h2>Response</h2>
          <pre className="output">{resultText}</pre>
        </div>
      </section>
    </main>
  );
}
