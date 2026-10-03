import React, { useEffect, useState } from 'react';
import { Card } from './ui/Card';
import { Play, RefreshCw, AlertTriangle } from 'lucide-react';

interface Model {
  id: string;
  name: string;
  provider: string;
}

interface SettingsViewProps {
  onStart: () => void;
}

export const SettingsView: React.FC<SettingsViewProps> = ({ onStart }) => {
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeAccount, setActiveAccount] = useState("N/A");

  // Config State
  const [config, setConfig] = useState({
    mode: 'paper',
    symbol: 'EURUSD',
    timeframe: 'AUTO',
    model: 'mistralai/mistral-7b-instruct-v0.2',
    risk: 1.0,
    loops: 1,
    continuous: false,
    mt5_login: '',
    mt5_password: '',
    mt5_server: ''
  });

  const [savedAccounts, setSavedAccounts] = useState<any[]>([]);

  useEffect(() => {
    fetchModels();
    fetchStatus();
    fetchAccounts();
  }, []);

  const fetchAccounts = async () => {
    try {
      const res = await fetch('http://localhost:8000/api/config/accounts');
      const data = await res.json();
      setSavedAccounts(data.accounts || []);
    } catch (e) {
      console.error("Failed to fetch accounts", e);
    }
  };

  const fetchStatus = async () => {
    try {
      const res = await fetch('http://localhost:8000/api/status');
      const data = await res.json();
      if (data.activeAccountId) {
        setActiveAccount(data.activeAccountId);
      }
    } catch (e) {
      console.error("Failed to fetch status", e);
    }
  };

  const fetchModels = async () => {
    try {
      const res = await fetch('http://localhost:8000/api/config/models');
      const data = await res.json();
      setModels(data);
    } catch (e) {
      console.error("Failed to fetch models", e);
    }
  };

  const handleStart = async () => {
    setLoading(true);
    try {
      const res = await fetch('http://localhost:8000/api/control/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
      });
      const result = await res.json();
      if (result.status === 'success') {
        onStart();
      } else {
        alert("Failed to launch: " + result.message);
      }
    } catch (e) {
      alert("Error contacting server");
    }
    setLoading(false);
  };

  return (
    <div className="p-6 h-full overflow-y-auto space-y-6 flex flex-col items-center justify-center">

      <div className="w-full max-w-4xl space-y-6">
        <div className="text-center space-y-2">
          <h1 className="text-3xl font-bold text-terminal-accent tracking-wider">SYSTEM CONFIGURATION</h1>
          <p className="text-terminal-muted">Configure your trading session parameters before initialization.</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Card title="Trading Parameters">
            <div className="space-y-4 p-2">
              <div className="grid grid-cols-3 gap-4 items-center">
                <label className="text-sm text-terminal-muted">Mode</label>
                <select
                  className="col-span-2 bg-terminal-black border border-terminal-border rounded px-3 py-2 text-terminal-text outline-none focus:border-terminal-accent"
                  value={config.mode}
                  onChange={(e) => setConfig({ ...config, mode: e.target.value })}
                >
                  <option value="paper">📄 Paper (Simulation)</option>
                  <option value="demo">🟢 Demo (Real Execution)</option>
                  <option value="live">🔴 Live (Real Money)</option>
                </select>
              </div>

              <div className="grid grid-cols-3 gap-4 items-center">
                <label className="text-sm text-terminal-muted">Symbol</label>
                <input
                  type="text"
                  className="col-span-2 bg-terminal-black border border-terminal-border rounded px-3 py-2 text-terminal-text outline-none focus:border-terminal-accent uppercase"
                  value={config.symbol}
                  onChange={(e) => setConfig({ ...config, symbol: e.target.value.toUpperCase() })}
                />
              </div>

              <div className="grid grid-cols-3 gap-4 items-center">
                <label className="text-sm text-terminal-muted">Timeframe</label>
                <select
                  className="col-span-2 bg-terminal-black border border-terminal-border rounded px-3 py-2 text-terminal-text outline-none focus:border-terminal-accent"
                  value={config.timeframe}
                  onChange={(e) => setConfig({ ...config, timeframe: e.target.value })}
                >
                  <option value="AUTO">🤖 AUTO (Multi-TF Analysis)</option>
                  <option value="M1">M1</option>
                  <option value="M5">M5</option>
                  <option value="M15">M15</option>
                  <option value="H1">H1</option>
                  <option value="H4">H4</option>
                  <option value="D1">D1</option>
                </select>
              </div>

              <div className="grid grid-cols-3 gap-4 items-center">
                <label className="text-sm text-terminal-muted">Risk (%)</label>
                <input
                  type="number"
                  step="0.1"
                  className="col-span-2 bg-terminal-black border border-terminal-border rounded px-3 py-2 text-terminal-text outline-none focus:border-terminal-accent"
                  value={config.risk}
                  onChange={(e) => setConfig({ ...config, risk: parseFloat(e.target.value) })}
                />
              </div>
            </div>
          </Card>

          <Card title="Intelligence Core">
            <div className="space-y-4 p-2">
              <div className="grid grid-cols-3 gap-4 items-center">
                <label className="text-sm text-terminal-muted">Model</label>
                <select
                  className="col-span-2 bg-terminal-black border border-terminal-border rounded px-3 py-2 text-terminal-text outline-none focus:border-terminal-accent"
                  value={config.model}
                  onChange={(e) => setConfig({ ...config, model: e.target.value })}
                >
                  {models.map(m => (
                    <option key={m.id} value={m.id}>{m.name}</option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-3 gap-4 items-center">
                <label className="text-sm text-terminal-muted">Cycle Mode</label>
                <select
                  className="col-span-2 bg-terminal-black border border-terminal-border rounded px-3 py-2 text-terminal-text outline-none focus:border-terminal-accent"
                  value={config.continuous ? 'continuous' : 'single'}
                  onChange={(e) => setConfig({ ...config, continuous: e.target.value === 'continuous' })}
                >
                  <option value="single">Single / Fixed Loops</option>
                  <option value="continuous">🔄 Continuous (24/7)</option>
                </select>
              </div>

              {!config.continuous && (
                <div className="grid grid-cols-3 gap-4 items-center">
                  <label className="text-sm text-terminal-muted">Loop Count</label>
                  <input
                    type="number"
                    className="col-span-2 bg-terminal-black border border-terminal-border rounded px-3 py-2 text-terminal-text outline-none focus:border-terminal-accent"
                    value={config.loops}
                    onChange={(e) => setConfig({ ...config, loops: parseInt(e.target.value) })}
                  />
                </div>
              )}
            </div>
          </Card>
        </div>

        <Card title="Broker Connection (MetaTrader 5)">
          <div className="p-2 space-y-4">
            {/* Account Selector */}
            <div className="flex gap-2">
              <select
                className="flex-1 bg-terminal-black border border-terminal-border rounded px-3 py-2 text-terminal-text outline-none focus:border-terminal-accent"
                onChange={(e) => {
                  const acc = savedAccounts.find(a => a.name === e.target.value);
                  if (acc) {
                    // Auto-fill logic would need password which we don't send back for security
                    // Instead we just set the name and let user know it's selected
                    setConfig({ ...config, mt5_login: acc.login, mt5_server: acc.server, mt5_password: "" });
                    // Note: Ideally we'd have an endpoint to 'use' an account by name, but for now we rely on user re-entering password 
                    // OR we update the API to return encrypted/masked password if deemed safe locally?
                    // Actually, for local desktop app, we can just load the file content in backend if we pass the 'account_name'
                  }
                }}
              >
                <option value="">-- Select Saved Account --</option>
                {savedAccounts.map(acc => (
                  <option key={acc.name} value={acc.name}>
                    {acc.name} ({acc.login} - {acc.server})
                  </option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="space-y-1">
                <label className="text-xs text-terminal-muted uppercase">Login ID</label>
                <input
                  type="number"
                  placeholder="e.g. 12345678"
                  className="w-full bg-terminal-black border border-terminal-border rounded px-3 py-2 text-terminal-text outline-none focus:border-terminal-accent"
                  value={config.mt5_login}
                  onChange={(e) => setConfig({ ...config, mt5_login: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-terminal-muted uppercase">Password</label>
                <input
                  type="password"
                  placeholder="••••••••"
                  className="w-full bg-terminal-black border border-terminal-border rounded px-3 py-2 text-terminal-text outline-none focus:border-terminal-accent"
                  value={config.mt5_password}
                  onChange={(e) => setConfig({ ...config, mt5_password: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-terminal-muted uppercase">Server Name</label>
                <input
                  type="text"
                  placeholder="e.g. MetaQuotes-Demo"
                  className="w-full bg-terminal-black border border-terminal-border rounded px-3 py-2 text-terminal-text outline-none focus:border-terminal-accent"
                  value={config.mt5_server}
                  onChange={(e) => setConfig({ ...config, mt5_server: e.target.value })}
                />
              </div>
            </div>

            <div className="px-2 text-xs text-terminal-muted italic">
              * Select an account above OR enter credentials manually.
              (Note: For security, saved passwords must be re-entered or managed via config file currently).
            </div>
          </div>
          {/* Display Currently Connected Account */}
          <div className="border-t border-terminal-border mt-2 pt-2 px-2">
            <div className="text-xs text-terminal-accent flex justify-between">
              <span>ACTIVE CONNECTION:</span>
              <span className="font-mono font-bold text-white">{activeAccount !== "N/A" ? activeAccount : "Not Connected"}</span>
            </div>
          </div>
        </Card>

        {config.mode === 'live' && (
          <div className="bg-red-900/20 border border-red-500/50 p-4 rounded flex items-center gap-3 text-red-400">
            <AlertTriangle />
            <div className="text-sm">
              <strong>WARNING:</strong> Live trading mode selected. Real capital is at risk.
              Ensure you understand the risks before proceeding.
            </div>
          </div>
        )}

        <button
          onClick={handleStart}
          disabled={loading}
          className="w-full bg-terminal-accent text-black font-bold py-4 rounded text-lg hover:bg-terminal-text transition-all flex items-center justify-center gap-3 disabled:opacity-50"
        >
          {loading ? <RefreshCw className="animate-spin" /> : <Play />}
          {loading ? "INITIALIZING SYSTEMS..." : "LAUNCH TRADING SYSTEM"}
        </button>

      </div>
    </div>
  );
};