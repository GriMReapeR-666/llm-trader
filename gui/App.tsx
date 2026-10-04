import React, { useState, useEffect } from 'react';
import { Sidebar } from './components/Sidebar';
import { Dashboard } from './components/Dashboard';
import { NewsView } from './components/NewsView';
import { BrainView } from './components/BrainView';
import { TradingFloor } from './components/TradingFloor';
import { SettingsView } from './components/SettingsView';
import { HistoryView } from './components/HistoryView';
import { ViewState, WorkflowState } from './types';
import { Clock, Cpu } from 'lucide-react';
import { apiFetch, UNAUTHORIZED_EVENT } from './api';
import { LoginScreen } from './components/LoginScreen';


export default function App() {
  const [currentView, setCurrentView] = useState<ViewState>('SETTINGS'); // Default to SETTINGS
  const [isConnected, setIsConnected] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [workflowState, setWorkflowState] = useState<WorkflowState>(WorkflowState.IDLE);
  const [cycleCount, setCycleCount] = useState(0);
  const [uptime, setUptime] = useState<string>('00:00:00');
  const [account, setAccount] = useState({ balance: 0, equity: 0, pnl: 0 });
  const [needsLogin, setNeedsLogin] = useState(false);
  const [sessionKey, setSessionKey] = useState(0); // remounts views after sign-in

  // Any 401 from gui_api (no or expired session cookie) shows the sign-in screen.
  useEffect(() => {
    const onUnauthorized = () => setNeedsLogin(true);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  // Efficient consolidated polling with graceful offline resilience
  useEffect(() => {
    let active = true;

    const fetchSystemData = async () => {
      try {
        const [statusRes, accountRes] = await Promise.allSettled([
          apiFetch(`/status`),
          apiFetch(`/account`)
        ]);

        if (!active) return;

        if (statusRes.status === 'fulfilled' && statusRes.value.ok) {
          const data = await statusRes.value.json();
          setWorkflowState(data.workflowState as WorkflowState);
          setCycleCount(data.cycleCount);
          setUptime(data.uptime);
          setIsConnected(data.isConnected);
          setIsRunning(data.isRunning || false);
        } else {
          setIsConnected(false);
        }

        if (accountRes.status === 'fulfilled' && accountRes.value.ok) {
          const data = await accountRes.value.json();
          setAccount(data);
        }
      } catch {
        if (active) setIsConnected(false);
      }
    };

    if (needsLogin) return () => { active = false; };
    fetchSystemData();
    const interval = setInterval(fetchSystemData, 1500);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [needsLogin]);

  const getViewTitle = (view: ViewState) => {
    switch (view) {
      case 'DASHBOARD': return 'MISSION CONTROL';
      case 'NEWS': return 'NEWS & MACRO DRIVERS';
      case 'BRAIN': return 'BRAIN & MEMORY / ECONOMICS';
      case 'TRADING': return 'ACTIVE TRADING FLOOR';
      case 'HISTORY': return 'HISTORICAL ARCHIVES';
      case 'SETTINGS': return 'SYSTEM CONFIGURATION';
      default: return 'SYSTEM';
    }
  };

  const renderView = () => {
    switch (currentView) {
      case 'DASHBOARD': return <Dashboard workflowState={workflowState} />;
      case 'NEWS': return <NewsView />;
      case 'BRAIN': return <BrainView />;
      case 'TRADING': return <TradingFloor />;
      case 'HISTORY': return <HistoryView />;
      case 'SETTINGS': return <SettingsView onStart={() => setCurrentView('DASHBOARD')} />;
      default: return <Dashboard workflowState={workflowState} />;
    }
  };

  if (needsLogin) {
    return <LoginScreen onSuccess={() => { setNeedsLogin(false); setSessionKey((k) => k + 1); }} />;
  }

  return (
    <div className="flex h-screen bg-terminal-black text-terminal-text overflow-hidden font-sans">
      <Sidebar
        currentView={currentView}
        onViewChange={setCurrentView}
        isConnected={isConnected}
        balance={account.balance}
        equity={account.equity}
        pnl={account.pnl}
      />

      <main className="flex-1 ml-64 flex flex-col relative bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-terminal-panel via-terminal-black to-terminal-black">
        {/* Background Grid Effect */}
        <div className="absolute inset-0 bg-[linear-gradient(rgba(20,20,20,0.5)_1px,transparent_1px),linear-gradient(90deg,rgba(20,20,20,0.5)_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none -z-10"></div>

        {/* Top Title Bar */}
        <header className="h-16 border-b border-terminal-border bg-terminal-black/90 backdrop-blur-sm flex items-center justify-between px-6 z-10 shrink-0">
          <div className="flex items-center gap-4">
            <h2 className="text-lg font-mono font-bold text-terminal-text tracking-wider flex items-center gap-2">
              <span className="text-terminal-muted">SYS //</span>
              <span className="text-terminal-accent">{getViewTitle(currentView)}</span>
            </h2>
          </div>

          <div className="flex items-center gap-8">
            {/* Uptime / Clock */}
            <div className="flex items-center gap-3 text-right">
              <Clock size={16} className="text-terminal-muted" />
              <div className="flex flex-col leading-none">
                <span className="text-[10px] text-terminal-muted uppercase tracking-widest mb-0.5">System Time</span>
                <span className="font-mono text-terminal-text font-medium">{uptime}</span>
              </div>
            </div>

            {/* Cycle Count */}
            <div className="flex items-center gap-3 text-right pl-8 border-l border-terminal-border/50">
              <Cpu size={16} className="text-terminal-secondary" />
              <div className="flex flex-col leading-none">
                <span className="text-[10px] text-terminal-muted uppercase tracking-widest mb-0.5">Agent Cycle</span>
                <span className="font-mono text-terminal-secondary font-bold text-lg">#{cycleCount.toString().padStart(6, '0')}</span>
              </div>
            </div>
          </div>
        </header>

        {/* View Content Wrapper */}
        <div key={sessionKey} className="flex-1 overflow-hidden relative">
          {renderView()}
        </div>
      </main>
    </div>
  );
}