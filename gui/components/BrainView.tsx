import React, { useEffect, useState } from 'react';
import { Card } from './ui/Card';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  Legend
} from 'recharts';

const API_BASE = window.location.hostname === 'localhost' ? 'http://localhost:8000/api' : '/api';

type Observability = {
  memory?: {
    memory_quality?: Record<string, unknown>;
    review_queue?: {
      status?: string;
      total_candidates?: number;
      counts?: Record<string, number>;
      items?: Array<{ kind: string; summary?: string; scenario_key?: string }>;
    };
  };
  regime?: {
    regime?: { regime_id?: string; confidence?: number; advisory_bias?: string };
    fusion?: { status?: string; score?: number };
    propagation?: { path_id?: string; bias?: string; score?: number };
  };
  causal_edges?: { edges?: Array<Record<string, unknown>> };
  macro_observations?: { count?: number; entities_seen?: string[]; items?: Array<Record<string, unknown>> };
  economic_dynamics?: {
    status?: string;
    central_bank_aliases?: string[];
    coverage?: { entity_count?: number; edge_count?: number };
  };
};

export const BrainView: React.FC = () => {
  const [metrics, setMetrics] = useState<any>({ learning_curve: [], q_table: [], token_costs: [] });
  const [obs, setObs] = useState<Observability>({});

  // Only a real recorded epsilon counts as a trace. null = unmeasured, which
  // must render as absent rather than as a zero-valued flat curve.
  const hasEpsilonTrace = (metrics.learning_curve || []).some(
    (pt: any) => pt?.epsilon !== null && pt?.epsilon !== undefined
  );

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const res = await fetch(`${API_BASE}/brain/metrics`);
        if (res.ok) setMetrics(await res.json());
      } catch (e) { console.error(e); }
    };
    const fetchObs = async () => {
      try {
        const res = await fetch(`${API_BASE}/brain/observability`);
        if (res.ok) setObs(await res.json());
      } catch (e) { console.error(e); }
    };
    const interval = setInterval(() => {
      fetchMetrics();
      fetchObs();
    }, 4000);
    fetchMetrics();
    fetchObs();
    return () => clearInterval(interval);
  }, []);

  const quality = obs.memory?.memory_quality || {};
  const queue = obs.memory?.review_queue || {};
  const regime = obs.regime?.regime || {};
  const fusion = obs.regime?.fusion || {};
  const prop = obs.regime?.propagation || {};
  const edges = obs.causal_edges?.edges || [];
  const macroObs = obs.macro_observations?.items || [];
  const econ = obs.economic_dynamics || {};

  return (
    <div className="p-6 h-full overflow-y-auto space-y-6">

      <div className="grid grid-cols-4 gap-4">
        <Card title="Memory Review Queue" className="col-span-1">
          <div className="space-y-2 text-sm font-mono">
            <div className="text-terminal-accent text-lg">{queue.status || '—'}</div>
            <div className="text-terminal-muted">Candidates: {queue.total_candidates ?? 0}</div>
            {queue.counts && Object.entries(queue.counts).map(([k, v]) => (
              <div key={k} className="flex justify-between"><span>{k}</span><span>{v}</span></div>
            ))}
          </div>
        </Card>
        <Card title="Intermarket Regime" className="col-span-1">
          <div className="space-y-2 text-sm font-mono">
            <div className="text-terminal-secondary text-lg">{regime.regime_id || 'UNKNOWN'}</div>
            <div>Confidence: {(regime.confidence ?? 0).toFixed?.(2) ?? regime.confidence}</div>
            <div>Bias: {regime.advisory_bias || '—'}</div>
            <div>Fusion: {fusion.status || '—'}</div>
            <div>Path: {prop.path_id ? `${prop.bias} (${prop.path_id})` : '—'}</div>
          </div>
        </Card>
        <Card title="Macro Observations (L1)" className="col-span-1">
          <div className="text-sm font-mono space-y-1 max-h-32 overflow-y-auto">
            <div className="text-terminal-muted mb-1">Count: {obs.macro_observations?.count ?? 0}</div>
            {macroObs.slice(0, 5).map((row: any, i: number) => (
              <div key={i} className="truncate">{row.entity}: {row.value ?? '—'} ({row.provider})</div>
            ))}
            {macroObs.length === 0 && <div className="text-terminal-muted">No observations yet</div>}
          </div>
        </Card>
        <Card title="Economic Dynamics SoT" className="col-span-1">
          <div className="text-sm font-mono space-y-1">
            <div>Status: <span className="text-terminal-success">{econ.status || '—'}</span></div>
            <div>Entities: {econ.coverage?.entity_count ?? '—'}</div>
            <div>Edges: {econ.coverage?.edge_count ?? '—'}</div>
            <div className="text-[10px] text-terminal-muted truncate" title={(econ.central_bank_aliases || []).join(', ')}>
              CBs: {(econ.central_bank_aliases || []).join(', ') || '—'}
            </div>
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <Card title="Approved Causal Edges (top)" className="col-span-1 h-64">
          <div className="overflow-y-auto h-full text-xs font-mono">
            <table className="w-full">
              <thead className="text-terminal-muted uppercase">
                <tr><th className="text-left py-1">Source</th><th className="text-left">Target</th><th>Sign</th><th>Status</th></tr>
              </thead>
              <tbody>
                {edges.length === 0 ? (
                  <tr><td colSpan={4} className="text-center py-4 text-terminal-muted">No approved edges</td></tr>
                ) : edges.map((e: any, i: number) => (
                  <tr key={i} className="border-t border-terminal-border/30">
                    <td className="py-1">{e.source_name}</td>
                    <td>{e.target_name}</td>
                    <td className="text-center">{e.sign}</td>
                    <td className="text-center text-terminal-accent">{e.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card title="Memory Quality (retrieval)" className="col-span-1 h-64">
          <div className="text-xs font-mono space-y-2 overflow-y-auto h-full">
            <div>Retrievals: {quality.retrieval_count ?? 0}</div>
            <div>Scenarios: {quality.scenario_documents ?? 0} (balanced: {quality.balanced_scenarios ?? 0})</div>
            <div>Stale facts: {quality.stale_active_atomic_facts ?? 0}</div>
            <div>Zero-result rate: {quality.zero_result_rate != null ? (Number(quality.zero_result_rate) * 100).toFixed(1) + '%' : '—'}</div>
            {queue.items && queue.items.slice(0, 4).map((item, i) => (
              <div key={i} className="truncate text-terminal-muted border-t border-terminal-border/20 pt-1">
                [{item.kind}] {item.summary || item.scenario_key || '—'}
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-2 gap-6 h-80">
        <Card title="Learning Curve (Cumulative Reward)" className="col-span-1">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={metrics.learning_curve}>
              <CartesianGrid strokeDasharray="3 3" stroke="#333" />
              <XAxis dataKey="episode" stroke="#737373" fontSize={12} />
              <YAxis stroke="#737373" fontSize={12} />
              <Tooltip
                contentStyle={{ backgroundColor: '#161616', borderColor: '#262626', color: '#e5e5e5' }}
                itemStyle={{ color: '#00e5ff' }}
              />
              <Line type="monotone" dataKey="reward" stroke="#00e5ff" strokeWidth={2} dot={false} activeDot={{ r: 6 }} />
            </LineChart>
          </ResponsiveContainer>
        </Card>

        <Card
          title="Exploration Rate (Epsilon Decay)"
          className="col-span-1"
          headerAction={
            metrics.epsilon_current != null ? (
              <span className="text-terminal-muted text-xs font-mono">
                &epsilon; now {Number(metrics.epsilon_current).toFixed(3)}
              </span>
            ) : null
          }
        >
          {hasEpsilonTrace ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={metrics.learning_curve}>
                <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                <XAxis dataKey="episode" stroke="#737373" fontSize={12} />
                <YAxis stroke="#737373" fontSize={12} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#161616', borderColor: '#262626', color: '#e5e5e5' }}
                  itemStyle={{ color: '#d946ef' }}
                />
                {/* connectNulls stays off: unrecorded episodes render as a gap,
                    never as an invented flat segment. */}
                <Line type="step" dataKey="epsilon" stroke="#d946ef" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-full flex items-center justify-center text-terminal-muted text-xs text-center px-4">
              No per-episode exploration trace recorded yet.
              <br />
              Nothing is plotted until the trainer measures one.
            </div>
          )}
        </Card>
      </div>

      <div className="grid grid-cols-3 gap-6 h-80">
        <Card title="Top Performing States (Q-Table)" className="col-span-2">
          <table className="w-full text-sm text-left">
            <thead className="text-terminal-muted text-xs uppercase bg-terminal-panel/50">
              <tr>
                <th className="px-4 py-2">State Description</th>
                <th className="px-4 py-2">Best Action</th>
                <th className="px-4 py-2 text-right">Q-Value (Exp. Reward)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-terminal-border">
              {metrics.q_table.length === 0 ? (
                <tr><td colSpan={3} className="text-center py-4 text-terminal-muted">No Q-Learning Data Yet</td></tr>
              ) : metrics.q_table.map((stat: any, i: number) => (
                <tr key={i} className="hover:bg-terminal-panel/30">
                  <td className="px-4 py-2 font-mono text-terminal-accent">{stat.state}</td>
                  <td className="px-4 py-2 font-bold">{stat.best_action}</td>
                  <td className="px-4 py-2 text-right text-terminal-success">{(stat.value).toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Token Usage & Costs" className="col-span-1">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={metrics.token_costs} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="#333" horizontal={false} />
              <XAxis type="number" stroke="#737373" fontSize={10} />
              <YAxis dataKey="provider" type="category" stroke="#e5e5e5" fontSize={10} width={100} />
              <Tooltip cursor={{ fill: 'transparent' }} contentStyle={{ backgroundColor: '#161616', borderColor: '#262626' }} />
              <Legend />
              <Bar dataKey="input" fill="#3b82f6" stackId="a" name="Input Tokens" barSize={20} />
              <Bar dataKey="output" fill="#05ffa1" stackId="a" name="Output Tokens" barSize={20} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>

    </div>
  );
};
