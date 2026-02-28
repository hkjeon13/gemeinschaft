const profileEl = document.getElementById("profile");
const resultEl = document.getElementById("result");
const usernameEl = document.getElementById("username");
const passwordEl = document.getElementById("password");
const conversationIdEl = document.getElementById("conversation-id");
const messageEl = document.getElementById("message");

function getToken() {
  return localStorage.getItem("access_token") || "";
}

function setToken(token) {
  if (!token) {
    localStorage.removeItem("access_token");
    return;
  }
  localStorage.setItem("access_token", token);
}

function pretty(data) {
  return JSON.stringify(data, null, 2);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(path, {
    ...options,
    headers,
  });

  const text = await res.text();
  let body = text;
  try {
    body = JSON.parse(text);
  } catch (_) {}

  if (!res.ok) {
    throw new Error(pretty({ status: res.status, body }));
  }

  return body;
}

async function login(evt) {
  evt.preventDefault();
  try {
    const body = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: usernameEl.value.trim(),
        password: passwordEl.value,
      }),
    });

    setToken(body.access_token);
    resultEl.textContent = pretty({ message: "login ok", access_expires_in: body.access_expires_in });
  } catch (error) {
    resultEl.textContent = String(error.message || error);
  }
}

async function loadProfile() {
  try {
    const body = await api("/api/auth/me");
    profileEl.textContent = pretty(body);
  } catch (error) {
    profileEl.textContent = String(error.message || error);
  }
}

async function listConversations() {
  try {
    const body = await api("/api/conversation/");
    resultEl.textContent = pretty(body);
  } catch (error) {
    resultEl.textContent = String(error.message || error);
  }
}

async function loadConversation() {
  const conversationId = conversationIdEl.value.trim();
  if (!conversationId) {
    resultEl.textContent = "conversation id is required";
    return;
  }

  try {
    const body = await api(`/api/conversation/${encodeURIComponent(conversationId)}`);
    resultEl.textContent = pretty(body);
  } catch (error) {
    resultEl.textContent = String(error.message || error);
  }
}

async function sendMessage() {
  const conversationId = conversationIdEl.value.trim();
  const message = messageEl.value.trim();
  if (!conversationId || !message) {
    resultEl.textContent = "conversation id and message are required";
    return;
  }

  try {
    const body = await api(`/api/conversation/${encodeURIComponent(conversationId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    resultEl.textContent = pretty(body);
  } catch (error) {
    resultEl.textContent = String(error.message || error);
  }
}

document.getElementById("login-form").addEventListener("submit", login);
document.getElementById("me-btn").addEventListener("click", loadProfile);
document.getElementById("list-btn").addEventListener("click", listConversations);
document.getElementById("load-conversation").addEventListener("click", loadConversation);
document.getElementById("send-message").addEventListener("click", sendMessage);
document.getElementById("logout-btn").addEventListener("click", () => {
  setToken("");
  profileEl.textContent = "Token cleared";
});
