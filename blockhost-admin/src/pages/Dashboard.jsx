import React, { useEffect, useState, useCallback } from 'react';
import { getStats } from '../api';
import {
  DollarSign, Users, Server, Wifi, AlertTriangle, CheckCircle, Info, Zap,
  RefreshCw, TrendingUp,
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';

const fmt = (mb) => mb >= 1024 ? `${(mb / 1024).toFixed(1)}GB` : `${Math.round(mb)}MB`;
const fmtMoney = (v) => `$${Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function KpiCard({ icon: Icon, label, value, sub, color = 'var(--accent)' }) {
  return (
    <div className="kpi-card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</span>
        <div style={{ width: 30, height: 30, borderRadius: 8, background: `${color}22`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon size={15} color={color} />
        </div>
      </div>
      <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{sub}</div>}
    </div>
  );
}

function RamBar({ totalRam, activeRam, suspendedRam, freeRam }) {
  const total = Math.max(1, totalRam);
  const activePct = (activeRam / total) * 100;
  const suspPct = (suspendedRam / total) * 100;
  const freePct = (freeRam / total) * 100;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>Global RAM Usage</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
          {fmt(activeRam)} / {fmt(total)}
        </span>
      </div>
      <div className="ram-bar" style={{ height: 10, borderRadius: 5 }}>
        <div className="ram-bar-segment" style={{ width: `${activePct}%`, background: 'var(--green)' }} />
        <div className="ram-bar-segment" style={{ width: `${suspPct}%`, background: 'var(--amber)' }} />
        <div className="ram-bar-segment" style={{ width: `${freePct}%`, background: 'var(--bg-hover)' }} />
      </div>
      <div style={{ display: 'flex', gap: 16, marginTop: 8 }}>
        {[
          { color: 'var(--green)', label: 'Active', val: fmt(activeRam) },
          { color: 'var(--amber)', label: 'Suspended', val: fmt(suspendedRam) },
          { color: 'var(--text-muted)', label: 'Free', val: fmt(freeRam) },
        ].map(({ color, label, val }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11 }}>
            <div style={{ width: 8, height: 8, borderRadius: 2, background: color }} />
            <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
            <span style={{ color: 'var(--text-primary)', fontFamily: 'var(--mono)' }}>{val}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const ALERT_ICON = {
  error: <AlertTriangle size={14} color="var(--red)" />,
  warning: <Zap size={14} color="var(--amber)" />,
  info: <Info size={14} color="var(--blue)" />,
};

function AlertsFeed({ alerts }) {
  if (!alerts?.length) {
    return (
      <div style={{ padding: '20px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: 13, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
        <CheckCircle size={22} color="var(--green)" />
        All systems operational
      </div>
    );
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {alerts.map((a, i) => (
        <div key={i} className={`alert-item ${a.severity}`}>
          {ALERT_ICON[a.severity] || ALERT_ICON.info}
          <div>
            <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>{a.message}</div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
              {new Date(a.created_at).toLocaleString()}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px', fontSize: 12 }}>
      <div style={{ color: 'var(--text-secondary)', marginBottom: 4 }}>{label}</div>
      <div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{fmtMoney(payload[0].value)}</div>
    </div>
  );
};

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true); else setRefreshing(true);
    try {
      const data = await getStats();
      setStats(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(() => load(true), 5000);
    return () => clearInterval(id);
  }, [load]);

  if (loading) return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        {[...Array(4)].map((_, i) => <div key={i} className="skeleton" style={{ height: 90 }} />)}
      </div>
      <div className="skeleton" style={{ height: 200 }} />
    </div>
  );

  if (!stats) return <div style={{ color: 'var(--text-muted)' }}>Failed to load statistics.</div>;

  // Generate mock sparkline data from MRR (in reality you'd get daily data from backend)
  const sparkData = Array.from({ length: 12 }, (_, i) => ({
    month: ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][i],
    revenue: Math.random() * stats.mrr * 0.3 + stats.mrr * 0.7,
  }));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h2 style={{ fontSize: 18, marginBottom: 2 }}>Platform Overview</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: 12 }}>Live infrastructure metrics — auto-refreshes every 5s</p>
        </div>
        <button className="btn btn-outline btn-sm" onClick={() => load(true)} disabled={refreshing}>
          <RefreshCw size={12} style={{ animation: refreshing ? 'spin 1s linear infinite' : 'none' }} />
          Refresh
        </button>
      </div>

      {/* KPI Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12 }}>
        <KpiCard icon={DollarSign} label="Monthly Revenue" value={fmtMoney(stats.mrr)} sub={`${fmtMoney(stats.total_revenue)} all time`} color="var(--green)" />
        <KpiCard icon={Users} label="Active Users" value={stats.total_users.toLocaleString()} sub={`${stats.suspended_users} suspended`} color="var(--accent)" />
        <KpiCard icon={Server} label="Running Servers" value={stats.running_servers} sub={`${stats.total_servers} total`} color="var(--green)" />
        <KpiCard icon={Wifi} label="Offline Nodes" value={stats.offline_nodes} sub={`${stats.total_nodes} total nodes`} color={stats.offline_nodes > 0 ? 'var(--red)' : 'var(--text-muted)'} />
      </div>

      {/* RAM + Chart */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* RAM Visualizer */}
        <div className="kpi-card">
          <RamBar
            totalRam={stats.total_ram_mb}
            activeRam={stats.active_ram_mb}
            suspendedRam={stats.suspended_ram_mb}
            freeRam={stats.free_ram_mb}
          />
        </div>

        {/* MRR Chart */}
        <div className="kpi-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
            <TrendingUp size={14} color="var(--green)" />
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>Revenue Trend (12 months)</span>
          </div>
          <ResponsiveContainer width="100%" height={100}>
            <BarChart data={sparkData} barSize={12}>
              <XAxis dataKey="month" tick={{ fontSize: 9, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} />
              <YAxis hide />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="revenue" radius={[2, 2, 0, 0]}>
                {sparkData.map((_, i) => (
                  <Cell key={i} fill={i === sparkData.length - 1 ? 'var(--green)' : 'var(--accent)'} opacity={i === sparkData.length - 1 ? 1 : 0.5} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Alerts */}
      <div className="kpi-card" style={{ flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
          <AlertTriangle size={14} color={stats.alerts?.length ? 'var(--amber)' : 'var(--green)'} />
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
            System Alerts ({stats.alerts?.length || 0})
          </span>
        </div>
        <AlertsFeed alerts={stats.alerts} />
      </div>
    </div>
  );
}