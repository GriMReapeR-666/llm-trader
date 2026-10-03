import React from 'react';
import { 
  LayoutDashboard, 
  Newspaper, 
  BrainCircuit, 
  CandlestickChart, 
  Settings,
  Activity,
  Wifi,
  WifiOff,
  History
} from 'lucide-react';
import { ViewState } from '../types';

interface SidebarProps {
  currentView: ViewState;
  onViewChange: (view: ViewState) => void;
  isConnected: boolean;
  balance: number;
  equity: number;
  pnl: number;
}

export const Sidebar: React.FC<SidebarProps> = ({ 
  currentView, 
  onViewChange, 
  isConnected,
  balance,
  equity,
  pnl
}) => {
  const menuItems = [
    { id: 'DASHBOARD', label: 'Mission Control', icon: <LayoutDashboard size={20} /> },
    { id: 'NEWS', label: 'News & Macro', icon: <Newspaper size={20} /> },
    { id: 'BRAIN', label: 'Brain & RL', icon: <BrainCircuit size={20} /> },
    { id: 'TRADING', label: 'Trading Floor', icon: <CandlestickChart size={20} /> },
    { id: 'HISTORY', label: 'Session History', icon: <History size={20} /> },
    { id: 'SETTINGS', label: 'System & Config', icon: <Settings size={20} /> },
  ];

  return (
    <aside className="w-64 bg-terminal-dark border-r border-terminal-border flex flex-col h-screen fixed left-0 top-0 z-50">
      {/* Header */}
      <div className="p-6 border-b border-terminal-border">
        <h1 className="text-xl font-bold text-terminal-accent tracking-tighter flex items-center gap-2">
          <Activity className="text-terminal-accent" />
          LLM TRADER PRO
        </h1>
        <div className={`mt-2 flex items-center gap-2 text-xs font-mono px-2 py-1 rounded ${isConnected ? 'bg-terminal-success/10 text-terminal-success' : 'bg-terminal-danger/10 text-terminal-danger'}`}>
          {isConnected ? <Wifi size={12} /> : <WifiOff size={12} />}
          <span>MT5: {isConnected ? 'ONLINE' : 'OFFLINE'}</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-6 px-3 space-y-1">
        {menuItems.map((item) => (
          <button
            key={item.id}
            onClick={() => onViewChange(item.id as ViewState)}
            className={`w-full flex items-center gap-3 px-3 py-3 rounded-md text-sm font-medium transition-all duration-200 
              ${currentView === item.id 
                ? 'bg-terminal-accent/10 text-terminal-accent border border-terminal-accent/20' 
                : 'text-terminal-muted hover:bg-terminal-panel hover:text-terminal-text'}`}
          >
            {item.icon}
            {item.label}
          </button>
        ))}
      </nav>

      {/* Quick Ticker */}
      <div className="p-4 border-t border-terminal-border bg-terminal-panel/30">
        <div className="space-y-3 font-mono text-sm">
          <div className="flex justify-between items-center">
            <span className="text-terminal-muted">Balance</span>
            <span className="text-terminal-text">${balance.toFixed(2)}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-terminal-muted">Equity</span>
            <span className="text-terminal-text">${equity.toFixed(2)}</span>
          </div>
          <div className="flex justify-between items-center pt-2 border-t border-terminal-border/50">
            <span className="text-terminal-muted">Daily PnL</span>
            <span className={`${pnl >= 0 ? 'text-terminal-success' : 'text-terminal-danger'}`}>
              {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
            </span>
          </div>
        </div>
        <div className="mt-4 text-xs text-terminal-muted text-center opacity-50">
          v2.4.0-stable
        </div>
      </div>
    </aside>
  );
};