import { MarketEvent, TradePosition, StrategyStat, ReasoningLog, HistorySession } from './types';
import { BadgeDollarSign, Activity, BrainCircuit, Newspaper, Settings } from 'lucide-react';
import React from 'react';

export const MOCK_POSITIONS: TradePosition[] = [
  {
    id: 'ORD-1024',
    symbol: 'EURUSD',
    side: 'SELL',
    volume: 1.5,
    entryPrice: 1.0850,
    currentPrice: 1.0825,
    sl: 1.0880,
    tp: 1.0750,
    swap: -2.50,
    commission: -10.50,
    netPnL: 345.00,
    openTime: '08:15:30',
  },
  {
    id: 'ORD-1025',
    symbol: 'XAUUSD',
    side: 'BUY',
    volume: 0.1,
    entryPrice: 2035.50,
    currentPrice: 2032.10,
    sl: 2025.00,
    tp: 2050.00,
    swap: -0.80,
    commission: -5.00,
    netPnL: -34.00,
    openTime: '09:30:12',
  }
];

export const MOCK_EVENTS: MarketEvent[] = [
  { id: '1', time: '13:30', country: 'USD', event: 'CPI m/m', impact: 'HIGH', actual: '0.4%', forecast: '0.3%', deviation: '+0.1%' },
  { id: '2', time: '13:30', country: 'USD', event: 'Core CPI m/m', impact: 'HIGH', actual: '0.3%', forecast: '0.3%', deviation: '0.0%' },
  { id: '3', time: '14:15', country: 'EUR', event: 'ECB Press Conference', impact: 'HIGH', actual: '', forecast: '', deviation: '' },
  { id: '4', time: '15:00', country: 'USD', event: 'Flash Services PMI', impact: 'MEDIUM', actual: '51.2', forecast: '52.0', deviation: '-0.8' },
  { id: '5', time: '19:00', country: 'USD', event: 'FOMC Member Speaks', impact: 'MEDIUM', actual: '', forecast: '', deviation: '' },
];

export const MOCK_STRATEGIES: StrategyStat[] = [
  { state: 'RSI < 30 + Uplift', action: 'BUY', winRate: 68.5, avgReward: 12.4 },
  { state: 'MACD Bearish Cross', action: 'SELL', winRate: 62.1, avgReward: 8.2 },
  { state: 'Bollinger Breakout', action: 'BUY', winRate: 45.0, avgReward: -2.1 },
  { state: 'Support Bounce H1', action: 'BUY', winRate: 55.4, avgReward: 5.5 },
];

export const INITIAL_LOGS: ReasoningLog[] = [
  { id: '1', timestamp: '10:00:01', level: 'INFO', message: 'System initialized. MT5 Connected.' },
  { id: '2', timestamp: '10:00:02', level: 'THOUGHT', message: 'Analyzing market structure on H1 timeframe.', confidence: 0.85 },
  { id: '3', timestamp: '10:00:05', level: 'THOUGHT', message: 'Detected bearish divergence on RSI (14). Awaiting confirmation.', confidence: 0.72 },
  { id: '4', timestamp: '10:05:00', level: 'WARN', message: 'High impact news event approaching in 30 mins (USD CPI).' },
];

export const MOCK_HISTORY_SESSIONS: HistorySession[] = [
  {
    id: 'SESS-001',
    date: '2023-10-25',
    model: 'Claude 3.5 Sonnet',
    totalPnL: 1245.50,
    tradeCount: 4,
    trades: [
      {
        id: 'HIST-1', symbol: 'GBPUSD', side: 'BUY', volume: 1.0, entryPrice: 1.2150, currentPrice: 1.2200, 
        sl: 1.2100, tp: 1.2250, swap: 0, commission: -7, netPnL: 500, openTime: '10:30:00'
      }
    ],
    logs: [
      { id: 'L1', timestamp: '10:15:00', level: 'THOUGHT', message: 'GBPUSD showing strong rejection at 1.2150 support level.', confidence: 0.9 },
      { id: 'L2', timestamp: '10:20:00', level: 'THOUGHT', message: 'News catalyst (UK GDP) positive. Aligning with technicals.', confidence: 0.95 },
      { id: 'L3', timestamp: '10:25:00', level: 'INFO', message: 'Placing BUY Limit order for GBPUSD.' },
      { id: 'L4', timestamp: '10:30:00', level: 'INFO', message: 'Order Filled. Monitoring trade.' },
      { id: 'L5', timestamp: '14:00:00', level: 'THOUGHT', message: 'Price approaching resistance. Tightening stops.', confidence: 0.8 }
    ]
  },
  {
    id: 'SESS-002',
    date: '2023-10-24',
    model: 'GPT-4o',
    totalPnL: -120.00,
    tradeCount: 6,
    trades: [
       {
        id: 'HIST-2', symbol: 'USDJPY', side: 'SELL', volume: 0.5, entryPrice: 149.80, currentPrice: 150.10, 
        sl: 150.00, tp: 148.50, swap: -5, commission: -3.5, netPnL: -120, openTime: '09:15:00'
      }
    ],
    logs: [
      { id: 'L1', timestamp: '09:00:00', level: 'THOUGHT', message: 'USDJPY overextended on H4. RSI Divergence detected.', confidence: 0.65 },
      { id: 'L2', timestamp: '09:10:00', level: 'WARN', message: 'Trend is still strongly bullish. Counter-trend trade involves high risk.', confidence: 0.5 },
      { id: 'L3', timestamp: '09:15:00', level: 'INFO', message: 'Executing short scalp.' },
      { id: 'L4', timestamp: '09:45:00', level: 'ERROR', message: 'Stop loss hit. Analyzing failure: momentum was too strong.' }
    ]
  },
  {
    id: 'SESS-003',
    date: '2023-10-23',
    model: 'Gemini 1.5 Pro',
    totalPnL: 450.20,
    tradeCount: 3,
    trades: [],
    logs: [
      { id: 'L1', timestamp: '08:00:00', level: 'INFO', message: 'Session started. No high impact news today.' },
      { id: 'L2', timestamp: '12:00:00', level: 'THOUGHT', message: 'Market ranging. Low volatility mode engaged.', confidence: 0.9 }
    ]
  }
];