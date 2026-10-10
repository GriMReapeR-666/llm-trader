import React, { useEffect, useState } from 'react';
import { Card } from './ui/Card';
import { XCircle, Edit, Ban, PauseCircle } from 'lucide-react';
import { TradePosition, HistorySession } from '../types';
import { apiFetch } from '../api';


export const TradingFloor: React.FC = () => {
  const [positions, setPositions] = useState<TradePosition[]>([]);
  const [history, setHistory] = useState<any[]>([]);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const posRes = await apiFetch(`/trading/positions`);
        if (posRes.ok) setPositions(await posRes.json());

        const histRes = await apiFetch(`/trading/history`);
        if (histRes.ok) setHistory(await histRes.json());
      } catch (e) {
        console.error(e);
      }
    };
    const interval = setInterval(fetchData, 1000);
    fetchData();
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="p-6 h-full overflow-y-auto space-y-6">

      {/* Manual Controls */}
      <div className="grid grid-cols-4 gap-4">
        <button className="flex items-center justify-center gap-2 py-4 bg-terminal-danger/20 border border-terminal-danger/50 text-terminal-danger rounded-lg hover:bg-terminal-danger hover:text-white transition-all font-bold uppercase tracking-widest">
          <Ban size={24} /> Emergency Stop
        </button>
        <button className="flex items-center justify-center gap-2 py-4 bg-terminal-warning/20 border border-terminal-warning/50 text-terminal-warning rounded-lg hover:bg-terminal-warning hover:text-black transition-all font-bold uppercase tracking-widest">
          <PauseCircle size={24} /> Pause Agent
        </button>
        <Card className="col-span-2 flex items-center px-4">
          <div className="flex w-full gap-4 items-center">
            <span className="text-terminal-muted uppercase text-xs font-bold">Manual Override:</span>
            <input type="text" placeholder="SYMBOL" className="bg-terminal-black border border-terminal-border px-3 py-1 rounded text-sm w-24 text-center font-mono" />
            <select className="bg-terminal-black border border-terminal-border px-3 py-1 rounded text-sm font-mono text-terminal-text">
              <option>BUY</option>
              <option>SELL</option>
            </select>
            <input type="number" placeholder="Lots" className="bg-terminal-black border border-terminal-border px-3 py-1 rounded text-sm w-20 text-center font-mono" />
            <button className="bg-terminal-accent text-black px-4 py-1 rounded font-bold text-sm hover:bg-white transition-colors ml-auto">EXECUTE</button>
          </div>
        </Card>
      </div>

      <Card title="Active Positions" className="min-h-[300px]">
        <table className="w-full text-sm text-left font-mono">
          <thead className="text-terminal-muted text-xs uppercase bg-terminal-panel/50 sticky top-0">
            <tr>
              <th className="px-4 py-3">ID</th>
              <th className="px-4 py-3">Symbol</th>
              <th className="px-4 py-3">Side</th>
              <th className="px-4 py-3 text-right">Volume</th>
              <th className="px-4 py-3 text-right">Entry</th>
              <th className="px-4 py-3 text-right">Current</th>
              <th className="px-4 py-3 text-right">SL</th>
              <th className="px-4 py-3 text-right">TP</th>
              <th className="px-4 py-3 text-right">Swap/Comm</th>
              <th className="px-4 py-3 text-right">Net PnL</th>
              <th className="px-4 py-3 text-center">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-terminal-border">
            {positions.length === 0 ? (
              <tr><td colSpan={11} className="text-center py-8 text-terminal-muted">No Open Positions</td></tr>
            ) : positions.map((pos) => (
              <tr key={pos.id} className="hover:bg-terminal-panel/30 transition-colors">
                <td className="px-4 py-3 text-terminal-muted">{pos.id}</td>
                <td className="px-4 py-3 font-bold text-terminal-text">{pos.symbol}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-xs font-bold 
                    ${pos.side === 'BUY' ? 'bg-terminal-success/20 text-terminal-success' : 'bg-terminal-danger/20 text-terminal-danger'}`}>
                    {pos.side}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">{pos.volume}</td>
                <td className="px-4 py-3 text-right text-terminal-muted">{pos.entryPrice.toFixed(5)}</td>
                <td className="px-4 py-3 text-right font-bold text-terminal-text">{pos.currentPrice.toFixed(5)}</td>
                <td className="px-4 py-3 text-right text-terminal-danger">{pos.sl.toFixed(5)}</td>
                <td className="px-4 py-3 text-right text-terminal-success">{pos.tp.toFixed(5)}</td>
                <td className="px-4 py-3 text-right text-terminal-muted">{(pos.swap + pos.commission).toFixed(2)}</td>
                <td className={`px-4 py-3 text-right font-bold text-lg 
                  ${pos.netPnL >= 0 ? 'text-terminal-success' : 'text-terminal-danger'}`}>
                  {pos.netPnL.toFixed(2)}
                </td>
                <td className="px-4 py-3 flex justify-center gap-2">
                  <button className="p-1 hover:text-terminal-accent text-terminal-muted transition-colors"><Edit size={16} /></button>
                  <button className="p-1 hover:text-terminal-danger text-terminal-danger transition-colors"><XCircle size={16} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Order History" className="opacity-70">
        <div className="max-h-60 overflow-y-auto">
          <table className="w-full text-sm text-left font-mono">
            <thead className="text-terminal-muted text-xs uppercase bg-terminal-panel/50 sticky top-0">
              <tr>
                <th className="px-4 py-2">Time</th>
                <th className="px-4 py-2">Symbol</th>
                <th className="px-4 py-2">Action</th>
                <th className="px-4 py-2">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-terminal-border">
              {history.length === 0 ? (
                <tr><td colSpan={4} className="text-center py-4 text-terminal-muted">No History Yet</td></tr>
              ) : history.slice(0, 20).map((trade, i) => (
                <tr key={i}>
                  <td className="px-4 py-2">{trade.time}</td>
                  <td className="px-4 py-2">{trade.symbol}</td>
                  <td className="px-4 py-2 text-terminal-text">{trade.action}</td>
                  <td className={`px-4 py-2 font-bold ${(trade.result.includes('WIN')) ? 'text-terminal-success' : 'text-terminal-danger'}`}>
                    {trade.result}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

    </div>
  );
};