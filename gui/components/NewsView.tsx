import React, { useEffect, useState } from 'react';
import { Card } from './ui/Card';
import { Globe, Clock } from 'lucide-react';
import { apiFetch } from '../api';


export const NewsView: React.FC = () => {
  const [events, setEvents] = useState<any[]>([]);

  useEffect(() => {
    const fetchNews = async () => {
      try {
        const res = await apiFetch(`/news`);
        if (res.ok) setEvents(await res.json());
      } catch (e) { console.error(e); }
    };
    const interval = setInterval(fetchNews, 5000);
    fetchNews();
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="p-6 h-full overflow-y-auto space-y-6">
      <div className="grid grid-cols-3 gap-6">
        <Card title="Macro Narrative Summary" className="col-span-2 h-48">
          <div className="flex gap-4">
            <div className="w-1 bg-terminal-accent h-full rounded-full"></div>
            <div>
              <h3 className="text-lg font-bold text-terminal-text mb-2">Market Narrative</h3>
              <p className="text-terminal-muted text-sm leading-relaxed">
                {/* This would be populated by the LLM's high level summary */}
                System is monitoring economic events. Active analysis is aggregating news...
              </p>
            </div>
          </div>
        </Card>

        <Card title="Central Bank Watch" className="col-span-1 h-48 flex items-center justify-center relative overflow-hidden">
          <div className="text-center z-10">
            <div className="text-terminal-muted text-sm mb-1">Next FOMC Meeting</div>
            <div className="text-3xl font-mono font-bold text-terminal-text">--d --h --m</div>
          </div>
          <div className="absolute -right-4 -bottom-4 opacity-5">
            <Globe size={150} />
          </div>
        </Card>
      </div>

      <Card title="Economic Calendar" className="flex-1">
        <table className="w-full text-sm text-left font-mono">
          <thead className="text-terminal-muted text-xs uppercase bg-terminal-panel/50 sticky top-0">
            <tr>
              <th className="px-4 py-3">Time</th>
              <th className="px-4 py-3">Impact</th>
              <th className="px-4 py-3">Currency</th>
              <th className="px-4 py-3">Event</th>
              <th className="px-4 py-3 text-right">Actual</th>
              <th className="px-4 py-3 text-right">Forecast</th>
              <th className="px-4 py-3 text-right">Deviation</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-terminal-border">
            {events.length === 0 ? (
              <tr><td colSpan={7} className="text-center py-8 text-terminal-muted">No Active Economic Events</td></tr>
            ) : events.map((event, i) => (
              <tr key={i} className="hover:bg-terminal-panel/30 transition-colors group">
                <td className="px-4 py-3 flex items-center gap-2 text-terminal-text">
                  <Clock size={14} className="text-terminal-muted" />
                  {event.time}
                </td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold border 
                    ${event.importance === 'HIGH' ? 'bg-terminal-danger/10 text-terminal-danger border-terminal-danger/20' :
                      event.importance === 'MEDIUM' ? 'bg-terminal-warning/10 text-terminal-warning border-terminal-warning/20' :
                        'bg-terminal-success/10 text-terminal-success border-terminal-success/20'}`}>
                    {event.importance || 'LOW'}
                  </span>
                </td>
                <td className="px-4 py-3 text-terminal-text font-bold">{event.currency || 'USD'}</td>
                <td className="px-4 py-3 text-terminal-text">{event.event}</td>
                <td className="px-4 py-3 text-right text-terminal-text font-bold">{event.actual || '-'}</td>
                <td className="px-4 py-3 text-right text-terminal-muted">{event.forecast || '-'}</td>
                <td className="px-4 py-3 text-right">{event.deviation || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
};