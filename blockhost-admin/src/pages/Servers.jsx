import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { getServers, forceStopServer, forceStartServer } from '../api';
import StatusBadge from '../components/StatusBadge';
import { Modal } from '../components/Modal';
import { Search, RefreshCw, StopCircle, PlayCircle } from 'lucide-react';

const FLAVORS = {
  bedrock: '⬟ Bedrock',
  java_vanilla: '☕ Vanilla',
  paper: '📄 Paper',
  purpur: '💜 Purpur',
  fabric: '🧵 Fabric',
  forge: '🔨 Forge',
};

function timeAgo(iso) {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const d = Math.floor(diff / 86400000);
  if (d > 0) return `${d}d ago`;
  const h = Math.floor(diff / 3600000);
  if (h > 0) return `${h}h ago`;
  const m = Math.floor(diff / 60000);
  if (m > 0) return `${m}m ago`;
  return 'just now';
}

export default function Servers() {
  const [servers, setServers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [stateFilter, setStateFilter] = useState('');
  const [modal, setModal] = useState(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);

  const showToast = (msg, ok = true) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 3000);
  };

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try { setServers(await getServers()); } catch { }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(() => load(true), 5000);
    return () => clearInterval(id);
  }, [load]);

  const filtered = useMemo(() => {
    return servers.filter(s => {
      if (stateFilter && s.state !== stateFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        return s.world_name?.toLowerCase().includes(q) ||
          s.owner_email?.toLowerCase().includes(q) ||
          s.node_name?.toLowerCase().includes(q);
      }
      return true;
    });
  }, [servers, search, stateFilter]);

  const handleAction = async () => {
    setBusy(true);
    try {
      const { action, server } = modal;
      if (action === 'stop') await forceStopServer(server.id);
      else await forceStartServer(server.id);
      showToast(`Server "${server.world_name}" ${action === 'stop' ? 'stopped' : 'started'}`);
      load(true);
    } catch (err) {
      showToast(err.response?.data?.detail || 'Action failed', false);
    } finally { setBusy(false); setModal(null); }
  };

  const runningCount = servers.filter(s => s.state === 'running').length;
  const suspendedCount = servers.filter(s => s.state === 'suspended').length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {toast && (
        <div style={{
          position: 'fixed', top: 16, right: 16, zIndex: 999,
          background: toast.ok ? 'var(--green-bg)' : 'var(--red-bg)',
          border: `1px solid ${toast.ok ? 'var(--green)' : 'var(--red)'}`,
          color: toast.ok ? 'var(--green)' : 'var(--red)',
          padding: '10px 16px', borderRadius: 8, fontSize: 13, fontWeight: 500,
        }}>{toast.msg}</div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h2 style={{ fontSize: 18, marginBottom: 2 }}>Server Monitor</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
            {runningCount} running · {suspendedCount} suspended · {servers.length} total
          </p>
        </div>
        <button className="btn btn-outline btn-sm" onClick={() => load()}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 200, maxWidth: 400 }}>
          <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input
            className="search-input"
            placeholder="Search by name, owner, or node…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <select className="select-input" value={stateFilter} onChange={e => setStateFilter(e.target.value)}>
          <option value="">All States</option>
          <option value="running">Running</option>
          <option value="suspended">Suspended</option>
          <option value="provisioning">Provisioning</option>
          <option value="created">Created</option>
        </select>
      </div>

      {/* Table */}
      <div className="kpi-card" style={{ padding: 0, overflow: 'auto' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading servers…</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>World Name</th>
                <th>Owner</th>
                <th>Node</th>
                <th>Port</th>
                <th>Flavor</th>
                <th>Players</th>
                <th>State</th>
                <th>Last Active</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(s => (
                <tr key={s.id} style={{
                  background: s.state === 'running' ? 'rgba(63,185,80,0.02)' : undefined,
                }}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{s.world_name}</div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>{s.id.slice(0, 8)}…</div>
                  </td>
                  <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{s.owner_email}</td>
                  <td className="cell-mono" style={{ fontSize: 12 }}>{s.node_name || <span style={{ color: 'var(--text-muted)' }}>—</span>}</td>
                  <td className="cell-mono" style={{ fontSize: 12 }}>{s.port || '—'}</td>
                  <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{FLAVORS[s.flavor] || s.flavor}</td>
                  <td style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
                    {s.state === 'running' ? (
                      <span style={{ color: 'var(--green)' }}>●</span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)' }}>○</span>
                    )} {s.players_online}
                  </td>
                  <td><StatusBadge status={s.state} /></td>
                  <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>{timeAgo(s.last_activity)}</td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'flex', gap: 5, justifyContent: 'flex-end' }}>
                      {s.state === 'running' && (
                        <button
                          className="btn btn-outline btn-xs"
                          onClick={() => setModal({ action: 'stop', server: s })}
                          title="Force stop this server"
                        >
                          <StopCircle size={10} /> Stop
                        </button>
                      )}
                      {(s.state === 'suspended' || s.state === 'created') && (
                        <button
                          className="btn btn-outline btn-xs"
                          onClick={() => setModal({ action: 'start', server: s })}
                          title="Force start this server"
                        >
                          <PlayCircle size={10} /> Start
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={9} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 40 }}>
                    No servers match your filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* Confirm Modal */}
      {modal && (
        <Modal
          title={modal.action === 'stop'
            ? `Force Stop "${modal.server.world_name}"?`
            : `Force Start "${modal.server.world_name}"?`
          }
          message={modal.action === 'stop'
            ? `This will immediately kill the server process. The user's world data will NOT be lost but the server will go offline instantly.`
            : `This will attempt to start the server. If the node doesn't have capacity, this may fail.`
          }
          confirmText={modal.action === 'stop' ? 'Force Stop' : 'Force Start'}
          confirmDanger={modal.action === 'stop'}
          onConfirm={handleAction}
          onCancel={() => setModal(null)}
        />
      )}
    </div>
  );
}