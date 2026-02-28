const profileEl = document.getElementById("profile");
const usersEl = document.getElementById("users");
const resultEl = document.getElementById("result");

function getToken() {
  return localStorage.getItem("access_token") || "";
}

function clearToken() {
  localStorage.removeItem("access_token");
}

function pretty(data) {
  return JSON.stringify(data, null, 2);
}

function scopesFromInput(value) {
  const trimmed = value.trim();
  if (!trimmed) {
    return [];
  }
  return [...new Set(trimmed.split(/\s+/).filter(Boolean))];
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(path, { ...options, headers });
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

async function loadProfile() {
  try {
    const body = await api("/api/auth/me");
    profileEl.textContent = pretty(body);
  } catch (error) {
    profileEl.textContent = String(error.message || error);
  }
}

async function login(evt) {
  evt.preventDefault();
  try {
    const body = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.getElementById("login-username").value.trim(),
        password: document.getElementById("login-password").value,
      }),
    });
    localStorage.setItem("access_token", body.access_token);
    resultEl.textContent = pretty({ message: "login ok", access_expires_in: body.access_expires_in });
    await loadProfile();
    await loadUsers();
  } catch (error) {
    resultEl.textContent = String(error.message || error);
  }
}

async function loadUsers() {
  try {
    const body = await api("/api/admin/users");
    usersEl.textContent = pretty(body);
  } catch (error) {
    usersEl.textContent = String(error.message || error);
  }
}

async function createUser(evt) {
  evt.preventDefault();
  try {
    const body = await api("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.getElementById("create-username").value.trim(),
        password: document.getElementById("create-password").value,
        role: document.getElementById("create-role").value.trim(),
        tenant: document.getElementById("create-tenant").value.trim(),
        scopes: scopesFromInput(document.getElementById("create-scopes").value),
      }),
    });
    resultEl.textContent = pretty(body);
    await loadUsers();
  } catch (error) {
    resultEl.textContent = String(error.message || error);
  }
}

async function updateUser(evt) {
  evt.preventDefault();
  const username = document.getElementById("update-username").value.trim();
  const password = document.getElementById("update-password").value;
  const role = document.getElementById("update-role").value.trim();
  const tenant = document.getElementById("update-tenant").value.trim();
  const scopesRaw = document.getElementById("update-scopes").value;

  const payload = {};
  if (password) payload.password = password;
  if (role) payload.role = role;
  if (tenant) payload.tenant = tenant;
  if (scopesRaw.trim()) payload.scopes = scopesFromInput(scopesRaw);

  if (Object.keys(payload).length === 0) {
    resultEl.textContent = "No update fields provided";
    return;
  }

  try {
    const body = await api(`/api/admin/users/${encodeURIComponent(username)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    resultEl.textContent = pretty(body);
    await loadUsers();
  } catch (error) {
    resultEl.textContent = String(error.message || error);
  }
}

async function deleteUser(evt) {
  evt.preventDefault();
  const username = document.getElementById("delete-username").value.trim();
  try {
    await api(`/api/admin/users/${encodeURIComponent(username)}`, {
      method: "DELETE",
    });
    resultEl.textContent = `Deleted: ${username}`;
    await loadUsers();
  } catch (error) {
    resultEl.textContent = String(error.message || error);
  }
}

document.getElementById("check-me").addEventListener("click", loadProfile);
document.getElementById("reload-users").addEventListener("click", loadUsers);
document.getElementById("login-form").addEventListener("submit", login);
document.getElementById("create-form").addEventListener("submit", createUser);
document.getElementById("update-form").addEventListener("submit", updateUser);
document.getElementById("delete-form").addEventListener("submit", deleteUser);
document.getElementById("clear-token").addEventListener("click", () => {
  clearToken();
  profileEl.textContent = "Token cleared";
});
