import React from 'react';

export enum WorkflowState {
  IDLE = 'IDLE',
  DATA_INGESTION = 'DATA',
  REASONING = 'REASONING',
  ORDER_EXECUTION = 'ORDER',
  VERIFICATION = 'VERIFY'
}

export interface TradePosition {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  volume: number;
  entryPrice: number;
  currentPrice: number;
  sl: number;
  tp: number;
  swap: number;
  commission: number;
  netPnL: number;
  openTime: string;
}

export interface MarketEvent {
  id: string;
  time: string;
  country: string;
  event: string;
  impact: 'HIGH' | 'MEDIUM' | 'LOW';
  actual?: string;
  forecast?: string;
  deviation?: string;
}

export interface ReasoningLog {
  id: string;
  timestamp: string;
  level: 'INFO' | 'WARN' | 'ERROR' | 'THOUGHT';
  message: string;
  confidence?: number;
}

export interface StrategyStat {
  state: string;
  action: string;
  winRate: number;
  avgReward: number;
}

export interface HistorySession {
  id: string;
  date: string;
  model: string;
  totalPnL: number;
  tradeCount: number;
  logs: ReasoningLog[];
  trades: TradePosition[];
}

export interface NavItem {
  id: string;
  label: string;
  icon: React.ReactNode;
}

export type ViewState = 'DASHBOARD' | 'NEWS' | 'BRAIN' | 'TRADING' | 'SETTINGS' | 'HISTORY';