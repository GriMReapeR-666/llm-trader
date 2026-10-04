import React, { useEffect, useRef, useState } from 'react';
import { Card } from './ui/Card';
import { WorkflowState, ReasoningLog } from '../types';
import { Brain, Cpu, Target, TrendingUp, AlertTriangle } from 'lucide-react';
import { apiFetch } from '../api';

interface DashboardProps {
  workflowState: WorkflowState;
}


export const Dashboard: React.FC<DashboardProps> = ({ workflowState }) => {
  const [logs, setLogs] = useState<ReasoningLog[]>([]);
  const [dashboardData, setDashboardData] = useState<{ narrative: string, health: any } | null>(null);
  const [autoScroll, setAutoScroll] = useState(false); // Default off as requested
  const logEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll logs
  useEffect(() => {
    if (autoScroll) {
      logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, autoScroll]);

  // Poll Logs & Dashboard Data
  useEffect(() => {
    const fetchData = async () => {
      try {
        const [logsRes, dashRes] = await Promise.all([
          apiFetch(`/logs`),
          apiFetch(`/dashboard`)
        ]);

        if (logsRes.ok) {
          setLogs(await logsRes.json());
        }
        if (dashRes.ok) {
          setDashboardData(await dashRes.json());
        }
      } catch (e) {
        console.error(e);
      }
    };

    const interval = setInterval(fetchData, 1000);
    fetchData();
    return () => clearInterval(interval);
  }, []);

  const getConfidenceColor = (conf: number) => {
    if (conf >= 0.8) return 'text-terminal-success';
    if (conf >= 0.5) return 'text-terminal-warning';
    return 'text-terminal-error';
  }

  const health = dashboardData?.health || { api_latency: 0, memory_usage: 0, token_usage: 0 };

  return (
    <div className="h-full grid grid-cols-12 gap-4 p-6 overflow-y-auto">

      {/* Metrics Cards (Summary) */}
      <div className="col-span-12 grid grid-cols-4 gap-4">
        <Card title="Current Focus" icon={<Target size={18} />}>
          <div className="text-xl font-bold text-terminal-accent">{workflowState}</div>
        </Card>
        {/* Add more metric cards as placeholders if needed or remove if clutter */}
      </div>

      {/* Live Reasoning Feed */}
      <div className="col-span-8 h-96">
        <Card title="Live Reasoning Feed (Top-Level Thought Process)" className="h-full font-mono text-sm relative">
          <div className="absolute top-3 right-4 flex gap-4 items-center">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="accent-terminal-accent"
              />
              <span className="text-xs text-terminal-muted hover:text-terminal-text transition-colors">AUTO-SCROLL</span>
            </label>
            <div className="flex gap-2 items-center">
              <span className="w-2 h-2 rounded-full bg-terminal-accent animate-pulse"></span>
              <span className="text-xs text-terminal-accent tracking-widest uppercase">Live</span>
            </div>
          </div>
          <div className="space-y-3 pb-2 h-[300px] overflow-y-auto">
            {logs.map((log, i) => (
              <div key={i} className="flex gap-4 border-l-2 border-terminal-border pl-3 hover:bg-terminal-panel/50 transition-colors p-1 rounded-r">
                <span className="text-terminal-muted shrink-0 text-xs mt-1">{log.timestamp}</span>
                <div className="flex flex-col">
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-bold px-1.5 py-0.5 rounded 
                      ${log.level === 'THOUGHT' ? 'bg-terminal-secondary/20 text-terminal-secondary' :
                        log.level === 'WARN' ? 'bg-terminal-warning/20 text-terminal-warning' :
                          'bg-terminal-accent/20 text-terminal-accent'}`}>
                      {log.level}
                    </span>
                    {log.confidence && log.confidence > 0 && (
                      <span className={`text-xs font-bold ${getConfidenceColor(log.confidence)}`}>
                        [Conf: {(log.confidence * 100).toFixed(0)}%]
                      </span>
                    )}
                  </div>
                  <p className="text-terminal-text mt-1 leading-relaxed whitespace-pre-wrap">{log.message}</p>
                </div>
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        </Card>
      </div>

      {/* Bottom Spacer / Future Expansion */}
      <div className="col-span-4 h-96 space-y-4">
        {/* Placeholder for future widgets or specific signals */}
        <div className="h-full border border-terminal-border/20 rounded flex items-center justify-center text-terminal-muted/20 text-sm">
          [SIGNAL STRENGTH VISUALIZATION]
        </div>
      </div>

    </div>
  );
};