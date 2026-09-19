import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { getDeployments, forceStopDeployment, forceStartDeployment } from '../api';
import StatusBadge from '../components/StatusBadge';
import { Modal } from '../components/Modal';
import { Search, RefreshCw, StopCircle, PlayCircle } from 'lucide-react';

export default function Deployments() {
  const [deployments, setDeployments] = useState([]);
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
    try { setDeployments(await getDeployments()); } catch { }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(() => load(true), 5000);
    return () => clearInterval(id);
  }, [load]);

  const filtered = useMemo(() => {
    return deployments.filter(d => {
      if (stateFilter && d.state !== stateFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        return d.name?.toLowerCase().includes(q) ||
          d.owner_email?.toLowerCase().includes(q) ||
          d.node_name?.toLowerCase().includes(q);
      }
      return true;
    });
  }, [deployments, search, stateFilter]);

  const handleAction = async () => {
    setBusy(true);
    try {
      const { action, deployment } = modal;
      if (action === 'stop') await forceStopDeployment(deployment.id);
      else await forceStartDeployment(deployment.id);
      showToast(`App "${deployment.name}" ${action === 'stop' ? 'stopped' : 'started'}`);
      load(true);
    } catch (err) {
      showToast(err.response?.data?.detail || 'Action failed', false);
    } finally { setBusy(false); setModal(null); }
  };

  const runningCount = deployments.filter(d => d.state === 'running').length;
  const suspendedCount = deployments.filter(d => d.state === 'suspended' || d.state === 'stopped').length;

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
          <h2 style={{ fontSize: 18, marginBottom: 2 }}>Apps & DBs Monitor</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
            {runningCount} running · {suspendedCount} stopped · {deployments.length} total
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
            placeholder="Search by app name, owner, or node…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <select className="select-input" value={stateFilter} onChange={e => setStateFilter(e.target.value)}>
          <option value="">All States</option>
          <option value="running">Running</option>
          <option value="suspended">Suspended</option>
          <option value="building">Building</option>
          <option value="error">Error</option>
        </select>
      </div>

      {/* Table */}
      <div className="kpi-card" style={{ padding: 0, overflow: 'auto' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading apps…</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>App Name</th>
                <th>Owner</th>
                <th>Node</th>
                <th>Image / Repo</th>
                <th>Ports (Int → Ext)</th>
                <th>Limits (RAM/CPU)</th>
                <th>State</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(d => (
                <tr key={d.id} style={{
                  background: d.state === 'running' ? 'rgba(63,185,80,0.02)' : undefined,
                }}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{d.name}</div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>{d.id.slice(0, 8)}…</div>
                  </td>
                  <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{d.owner_email}</td>
                  <td className="cell-mono" style={{ fontSize: 12 }}>{d.node_name || <span style={{ color: 'var(--text-muted)' }}>—</span>}</td>
                  <td className="cell-mono" style={{ fontSize: 12, maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {d.docker_image || <span style={{ color: 'var(--text-muted)' }}>—</span>}
                  </td>
                  <td className="cell-mono" style={{ fontSize: 12 }}>
                    {d.internal_port || '—'} <span style={{ color: 'var(--text-muted)' }}>→</span> {d.host_port || '—'}
                  </td>
                  <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {d.ram_limit_mb}MB / {d.cpu_limit}c
                  </td>
                  <td><StatusBadge status={d.state} /></td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'flex', gap: 5, justifyContent: 'flex-end' }}>
                      {d.state === 'running' && (
                        <button
                          className="btn btn-outline btn-xs"
                          onClick={() => setModal({ action: 'stop', deployment: d })}
                          title="Force stop this app"
                        >
                          <StopCircle size={10} /> Stop
                        </button>
                      )}
                      {(d.state === 'suspended' || d.state === 'error' || d.state === 'stopped') && (
                        <button
                          className="btn btn-outline btn-xs"
                          onClick={() => setModal({ action: 'start', deployment: d })}
                          title="Force start this app"
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
                  <td colSpan={8} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 40 }}>
                    No apps match your filters.
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
            ? `Force Stop "${modal.deployment.name}"?`
            : `Force Start "${modal.deployment.name}"?`
          }
          message={modal.action === 'stop'
            ? `This will immediately kill the app container. Traffic to dynamic domains will fail.`
            : `This will attempt to start the app container and provision a node if needed.`
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
