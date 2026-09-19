import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { getAuditLogs } from '../api';
import { Search, RefreshCw, CreditCard, Server, User, Shield } from 'lucide-react';

const ACTION_ICONS = {
  server_start: { icon: '▶', color: 'var(--green)' },
  server_stop: { icon: '■', color: 'var(--amber)' },
  subscription_created: { icon: '✦', color: 'var(--blue)' },
  subscription_renewed: { icon: '↺', color: 'var(--blue)' },
  subscription_cancelled: { icon: '✕', color: 'var(--red)' },
  subscription_suspended: { icon: '⚠', color: 'var(--amber)' },
  payment_success: { icon: '✓', color: 'var(--green)' },
  payment_failed: { icon: '✕', color: 'var(--red)' },
  refund: { icon: '↩', color: 'var(--amber)' },
};

function LogEntry({ log }) {
  const cfg = ACTION_ICONS[log.action] || { icon: '•', color: 'var(--text-muted)' };
  const isAdmin = log.details?.admin_action;

  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 12, padding: '10px 0',
      borderBottom: '1px solid var(--border)',
    }}>
      {/* Icon */}
      <div style={{
        width: 28, height: 28, borderRadius: '50%', flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: `${cfg.color}22`, fontSize: 12, color: cfg.color,
        marginTop: 2,
      }}>
        {cfg.icon}
      </div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-primary)' }}>
            {log.action.replace(/_/g, ' ')}
          </span>
          {isAdmin && (
            <span style={{ fontSize: 10, background: 'var(--blue-bg)', color: 'var(--blue)', padding: '1px 6px', borderRadius: 4 }}>
              ADMIN
            </span>
          )}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 3 }}>
          <span style={{ color: 'var(--accent)' }}>{log.user_email}</span>
          {log.details && Object.keys(log.details).length > 0 && (
            <span style={{ color: 'var(--text-muted)', marginLeft: 8, fontFamily: 'var(--mono)', fontSize: 10 }}>
              {JSON.stringify(log.details).slice(0, 80)}
              {JSON.stringify(log.details).length > 80 ? '…' : ''}
            </span>
          )}
        </div>
      </div>

      {/* Time */}
      <div style={{ fontSize: 10, color: 'var(--text-muted)', flexShrink: 0, textAlign: 'right' }}>
        <div>{new Date(log.created_at).toLocaleDateString()}</div>
        <div>{new Date(log.created_at).toLocaleTimeString()}</div>
      </div>
    </div>
  );
}

export default function AuditLogs() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [actionFilter, setActionFilter] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try { setLogs(await getAuditLogs({ limit: 200 })); } catch { }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    return logs.filter(l => {
      if (actionFilter && l.action !== actionFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        return l.user_email?.toLowerCase().includes(q) ||
               l.action?.toLowerCase().includes(q);
      }
      return true;
    });
  }, [logs, search, actionFilter]);

  const uniqueActions = useMemo(() => [...new Set(logs.map(l => l.action))].sort(), [logs]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h2 style={{ fontSize: 18, marginBottom: 2 }}>Audit Logs</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{filtered.length} of {logs.length} events</p>
        </div>
        <button className="btn btn-outline btn-sm" onClick={load}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 200, maxWidth: 360 }}>
          <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input className="search-input" placeholder="Search by email or action…" value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <select className="select-input" value={actionFilter} onChange={e => setActionFilter(e.target.value)}>
          <option value="">All Actions</option>
          {uniqueActions.map(a => <option key={a} value={a}>{a.replace(/_/g, ' ')}</option>)}
        </select>
      </div>

      {/* Log feed */}
      <div className="kpi-card" style={{ padding: '4px 16px' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading audit logs…</div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>No audit logs found.</div>
        ) : (
          filtered.map(log => <LogEntry key={log.id} log={log} />)
        )}
      </div>
    </div>
  );
}
