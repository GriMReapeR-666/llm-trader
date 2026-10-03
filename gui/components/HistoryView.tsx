import React, { useState, useMemo } from 'react';
import { Card } from './ui/Card';
import { MOCK_HISTORY_SESSIONS } from '../constants';
import { HistorySession, ReasoningLog, TradePosition } from '../types';
import { Calendar, Bot, TrendingUp, TrendingDown, MessageSquare, ArrowRight } from 'lucide-react';

export const HistoryView: React.FC = () => {
  const [selectedSessionId, setSelectedSessionId] = useState<string>(MOCK_HISTORY_SESSIONS[0].id);

  const selectedSession = useMemo(() => 
    MOCK_HISTORY_SESSIONS.find(s => s.id === selectedSessionId), 
  [selectedSessionId]);

  // Combine logs and trades into a single chronological timeline
  const timelineEvents = useMemo(() => {
    if (!selectedSession) return [];

    const logsWithTypes = selectedSession.logs.map(log => ({
      ...log,
      type: 'LOG' as const,
      sortTime: log.timestamp
    }));

    const tradesWithTypes = selectedSession.trades.map(trade => ({
      ...trade,
      type: 'TRADE' as const,
      sortTime: trade.openTime
    }));

    return [...logsWithTypes, ...tradesWithTypes].sort((a, b) => 
      a.sortTime.localeCompare(b.sortTime)
    );
  }, [selectedSession]);

  return (
    <div className="h-full flex gap-6 p-6 overflow-hidden">
      
      {/* Sidebar - Date Selector */}
      <div className="w-64 flex flex-col gap-4 shrink-0">
        <Card title="Past Sessions" className="h-full">
          <div className="space-y-2">
            {MOCK_HISTORY_SESSIONS.map(session => (
              <button
                key={session.id}
                onClick={() => setSelectedSessionId(session.id)}
                className={`w-full p-3 rounded text-left border transition-all flex flex-col gap-1
                  ${selectedSessionId === session.id 
                    ? 'bg-terminal-accent/10 border-terminal-accent text-terminal-text shadow-[0_0_10px_rgba(0,229,255,0.1)]' 
                    : 'bg-terminal-black/50 border-terminal-border text-terminal-muted hover:bg-terminal-panel hover:text-terminal-text hover:border-terminal-muted'}`}
              >
                <div className="flex justify-between items-center">
                   <span className="font-mono text-sm font-bold flex items-center gap-2">
                      <Calendar size={12} /> {session.date}
                   </span>
                </div>
                <div className="flex justify-between items-center text-xs mt-1">
                  <span className={`${session.totalPnL >= 0 ? 'text-terminal-success' : 'text-terminal-danger'}`}>
                    {session.totalPnL >= 0 ? '+' : ''}${session.totalPnL.toFixed(2)}
                  </span>
                  <span className="text-terminal-muted font-mono">{session.trades.length} Trades</span>
                </div>
              </button>
            ))}
          </div>
        </Card>
      </div>

      {/* Main Content - Details & Timeline */}
      {selectedSession && (
        <div className="flex-1 flex flex-col gap-6 overflow-hidden">
          
          {/* Daily Stats Header */}
          <div className="grid grid-cols-3 gap-6 shrink-0">
             <Card className="flex items-center gap-4 px-6 py-4">
                <div className="p-3 bg-terminal-panel rounded-full text-terminal-accent">
                   <Bot size={24} />
                </div>
                <div>
                   <div className="text-terminal-muted text-xs uppercase tracking-wider font-bold">Model Used</div>
                   <div className="text-terminal-text font-mono font-bold">{selectedSession.model}</div>
                </div>
             </Card>

             <Card className="flex items-center gap-4 px-6 py-4">
                <div className={`p-3 bg-terminal-panel rounded-full ${selectedSession.totalPnL >= 0 ? 'text-terminal-success' : 'text-terminal-danger'}`}>
                   {selectedSession.totalPnL >= 0 ? <TrendingUp size={24} /> : <TrendingDown size={24} />}
                </div>
                <div>
                   <div className="text-terminal-muted text-xs uppercase tracking-wider font-bold">Net PnL</div>
                   <div className={`text-xl font-mono font-bold ${selectedSession.totalPnL >= 0 ? 'text-terminal-success' : 'text-terminal-danger'}`}>
                     {selectedSession.totalPnL >= 0 ? '+' : ''}${selectedSession.totalPnL.toFixed(2)}
                   </div>
                </div>
             </Card>

             <Card className="flex items-center gap-4 px-6 py-4">
                 <div className="p-3 bg-terminal-panel rounded-full text-terminal-secondary">
                   <MessageSquare size={24} />
                </div>
                <div>
                   <div className="text-terminal-muted text-xs uppercase tracking-wider font-bold">Reasoning Steps</div>
                   <div className="text-terminal-text font-mono font-bold">{selectedSession.logs.length} Steps Logged</div>
                </div>
             </Card>
          </div>

          {/* Timeline */}
          <Card title={`Timeline of Thought & Action (${selectedSession.date})`} className="flex-1">
             <div className="relative pl-6 space-y-8 py-4">
                {/* Vertical Line */}
                <div className="absolute left-[39px] top-4 bottom-4 w-0.5 bg-terminal-border"></div>

                {timelineEvents.map((item, index) => (
                  <div key={index} className="relative flex gap-6 items-start group">
                    
                    {/* Time Stamp */}
                    <div className="w-16 pt-1 text-right shrink-0">
                      <span className="text-xs font-mono text-terminal-muted">{item.sortTime}</span>
                    </div>

                    {/* Timeline Node */}
                    <div className={`z-10 w-3 h-3 rounded-full mt-1.5 shrink-0 outline outline-4 outline-terminal-black
                       ${item.type === 'TRADE' 
                          ? 'bg-terminal-warning' 
                          : 'bg-terminal-border group-hover:bg-terminal-accent transition-colors'
                       }`}>
                    </div>

                    {/* Content */}
                    <div className="flex-1 pb-2">
                       {item.type === 'LOG' ? (
                         <div className="bg-terminal-panel/30 p-3 rounded border border-transparent hover:border-terminal-border transition-colors">
                           <div className="flex items-center gap-2 mb-1">
                             <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase
                                ${(item as ReasoningLog).level === 'THOUGHT' ? 'bg-terminal-secondary/20 text-terminal-secondary' : 
                                  (item as ReasoningLog).level === 'WARN' ? 'bg-terminal-warning/20 text-terminal-warning' : 
                                  (item as ReasoningLog).level === 'ERROR' ? 'bg-terminal-danger/20 text-terminal-danger' :
                                  'bg-terminal-accent/20 text-terminal-accent'}`}>
                               {(item as ReasoningLog).level}
                             </span>
                             {(item as ReasoningLog).confidence && (
                               <span className="text-[10px] text-terminal-muted font-mono">Conf: {((item as ReasoningLog).confidence! * 100).toFixed(0)}%</span>
                             )}
                           </div>
                           <p className="text-sm text-terminal-text leading-relaxed font-mono">
                             {(item as ReasoningLog).message}
                           </p>
                         </div>
                       ) : (
                         <div className={`p-4 rounded border-l-4 bg-terminal-panel shadow-lg
                            ${(item as TradePosition).netPnL >= 0 ? 'border-l-terminal-success' : 'border-l-terminal-danger'}`}>
                            <div className="flex justify-between items-start mb-2">
                               <div className="flex items-center gap-2">
                                  <span className="font-bold text-terminal-text font-mono text-lg">{(item as TradePosition).symbol}</span>
                                  <span className={`text-xs font-bold px-1.5 py-0.5 rounded 
                                    ${(item as TradePosition).side === 'BUY' ? 'bg-terminal-success/20 text-terminal-success' : 'bg-terminal-danger/20 text-terminal-danger'}`}>
                                    {(item as TradePosition).side}
                                  </span>
                                  <span className="text-terminal-muted text-xs">Vol: {(item as TradePosition).volume}</span>
                               </div>
                               <div className={`font-mono font-bold 
                                 ${(item as TradePosition).netPnL >= 0 ? 'text-terminal-success' : 'text-terminal-danger'}`}>
                                 {(item as TradePosition).netPnL >= 0 ? '+' : ''}${(item as TradePosition).netPnL.toFixed(2)}
                               </div>
                            </div>
                            <div className="text-xs text-terminal-muted flex items-center gap-4 font-mono">
                               <span>Entry: <span className="text-terminal-text">{(item as TradePosition).entryPrice}</span></span>
                               <ArrowRight size={12} />
                               <span>Exit: <span className="text-terminal-text">{(item as TradePosition).currentPrice}</span></span>
                            </div>
                         </div>
                       )}
                    </div>

                  </div>
                ))}
             </div>
          </Card>
        </div>
      )}
    </div>
  );
};