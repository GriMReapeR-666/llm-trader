// One door for every web-GUI call to gui_api (Phase 2 review D6).
// Auth is the HttpOnly SameSite=Strict `gui_session` cookie that
// POST /api/auth/login sets; this module never sees or stores the token.

// Served by gui_api (any port) -> same origin. Vite dev server (3000/5173)
// -> the API on the same host, port 8000 (same site, so the cookie is sent).
const VITE_DEV_PORTS = ['3000', '5173'];
export const API_BASE = VITE_DEV_PORTS.includes(window.location.port)
  ? `${window.location.protocol}//${window.location.hostname}:8000/api`
  : '/api';

export const UNAUTHORIZED_EVENT = 'llmtrader:unauthorized';

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${API_BASE}${path}`, { ...init, credentials: 'include' });
  if (res.status === 401) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  return res;
}

export async function login(token: string): Promise<boolean> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  return res.ok;
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'include' });
}
