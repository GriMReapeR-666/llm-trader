import React, { useState } from 'react';
import { Lock } from 'lucide-react';
import { login } from '../api';

// Shown whenever gui_api answers 401. The token is the install-time API token
// (~/.config/llm-trader/api.token, or %APPDATA%\llm-trader\api.token); it is
// sent once to /api/auth/login and then lives only in an HttpOnly cookie.
export function LoginScreen({ onSuccess }: { onSuccess: () => void }) {
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      if (await login(token.trim())) {
        setToken('');
        onSuccess();
      } else {
        setError('Token rejected.');
      }
    } catch {
      setError('Cannot reach the API server on port 8000.');
    }
    setBusy(false);
  };

  return (
    <div className="flex h-screen items-center justify-center bg-terminal-black text-terminal-text font-sans">
      <form onSubmit={submit} className="w-96 space-y-4 border border-terminal-border bg-terminal-panel p-6 rounded">
        <h1 className="flex items-center gap-2 font-mono text-lg text-terminal-accent">
          <Lock size={18} /> LLM Trader sign-in
        </h1>
        <p className="text-sm text-terminal-muted">
          Paste the API token from <code>~/.config/llm-trader/api.token</code>
          {' '}(Windows: <code>%APPDATA%\llm-trader\api.token</code>).
        </p>
        <input
          type="password"
          autoFocus
          autoComplete="off"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="w-full rounded border border-terminal-border bg-terminal-black px-3 py-2 font-mono"
          aria-label="API token"
        />
        {error && <p className="text-sm text-red-400">{error}</p>}
        <button
          type="submit"
          disabled={busy || !token.trim()}
          className="w-full rounded bg-terminal-accent px-3 py-2 font-mono text-terminal-black disabled:opacity-50"
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
