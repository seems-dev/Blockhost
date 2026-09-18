import React, { useState, useEffect, useCallback } from 'react';
import { getNodes, evacuateNode } from '../api';
import StatusBadge from '../components/StatusBadge';
import { Modal } from '../components/Modal';
import { RefreshCw, Cpu, MemoryStick, Server, Activity } from 'lucide-react';

const fmt = (mb) => mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;

function RamBar({ used, total, suspended }) {
  const t = Math.max(1, total);
  const activePct = Math.min(100, (used / t) * 100);
  const suspPct = Math.min(100 - activePct, (suspended / t) * 100);
  return (
    <div title={`${fmt(used)} used / ${fmt(total)} total`}>
      <div className="ram-bar" style={{ width: 120 }}>
        <div className="ram-bar-segment" style={{ width: `${activePct}%`, background: activePct > 85 ? 'var(--red)' : activePct > 70 ? 'var(--amber)' : 'var(--green)' }} />
        <div className="ram-bar-segment" style={{ width: `${suspPct}%`, background: 'var(--amber)', opacity: 0.5 }} />
      </div>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2, fontFamily: 'var(--mono)' }}>
        {activePct.toFixed(0)}%
      </div>
    </div>
  );
}

const PROVIDER_LABEL = {
  oracle_free: 'Oracle Free',
  hetzner: 'Hetzner',
  aws: 'AWS',
  contabo: 'Contabo',
  local: 'Local',
  unknown: '—',
};

export default function Nodes() {
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState(null); // { type, node }
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);

  const showToast = (msg, ok = true) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 3000);
  };

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try { setNodes(await getNodes()); } catch { }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(() => load(true), 5000);
    return () => clearInterval(id);
  }, [load]);

  const handleEvacuate = async () => {
    setBusy(true);
    try {
      await evacuateNode(modal.node.id);
      showToast(`Node "${modal.node.name}" set to draining`);
      load(true);
    } catch (err) {
      showToast(err.response?.data?.detail || 'Failed', false);
    } finally { setBusy(false); setModal(null); }
  };

  if (loading) return <div style={{ color: 'var(--text-muted)' }}>Loading nodes…</div>;

  const totalRam = nodes.reduce((s, n) => s + n.total_ram_mb, 0);
  const usedRam = nodes.reduce((s, n) => s + n.used_ram_mb, 0);
  const onlineCount = nodes.filter(n => n.status === 'online').length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', top: 16, right: 16, zIndex: 999,
          background: toast.ok ? 'var(--green-bg)' : 'var(--red-bg)',
          border: `1px solid ${toast.ok ? 'var(--green)' : 'var(--red)'}`,
          color: toast.ok ? 'var(--green)' : 'var(--red)',
          padding: '10px 16px', borderRadius: 8, fontSize: 13, fontWeight: 500,
        }}>{toast.msg}</div>
      )}

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h2 style={{ fontSize: 18, marginBottom: 2 }}>Infrastructure Nodes</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
            {onlineCount}/{nodes.length} online · {fmt(usedRam)} / {fmt(totalRam)} RAM used globally
          </p>
        </div>
        <button className="btn btn-outline btn-sm" onClick={() => load(true)}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Table */}
      <div className="kpi-card" style={{ padding: 0, overflow: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Node</th>
              <th>IP Address</th>
              <th>Provider</th>
              <th>Status</th>
              <th>RAM</th>
              <th>CPU</th>
              <th>Servers</th>
              <th>Last Heartbeat</th>
              <th style={{ textAlign: 'right' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {nodes.map(node => (
              <tr key={node.id}>
                <td>
                  <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{node.name}</div>
                  {!node.approved && <div style={{ fontSize: 10, color: 'var(--amber)', marginTop: 2 }}>⚠ Not approved</div>}
                </td>
                <td className="cell-mono">{node.ip_address}:{node.agent_port}</td>
                <td>
                  <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {PROVIDER_LABEL[node.provider] || node.provider || '—'}
                  </span>
                </td>
                <td><StatusBadge status={node.status} /></td>
                <td>
                  <RamBar used={node.used_ram_mb} total={node.total_ram_mb} />
                  <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--mono)', marginTop: 2 }}>
                    {fmt(node.used_ram_mb)} / {fmt(node.total_ram_mb)}
                  </div>
                </td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <div className="ram-bar" style={{ width: 60 }}>
                      <div className="ram-bar-segment" style={{
                        width: `${node.cpu_usage_percent}%`,
                        background: node.cpu_usage_percent > 85 ? 'var(--red)' : 'var(--accent)',
                      }} />
                    </div>
                    <span style={{ fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--text-secondary)' }}>
                      {node.cpu_usage_percent.toFixed(1)}%
                    </span>
                  </div>
                </td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
                    <Server size={11} color="var(--text-muted)" />
                    <span>{node.running_count}</span>
                    <span style={{ color: 'var(--text-muted)' }}>/ {node.server_count}</span>
                  </div>
                </td>
                <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {node.last_heartbeat
                    ? new Date(node.last_heartbeat).toLocaleTimeString()
                    : <span style={{ color: 'var(--red)' }}>Never</span>}
                </td>
                <td style={{ textAlign: 'right' }}>
                  <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                    <button
                      className="btn btn-outline btn-xs"
                      onClick={() => setModal({ type: 'evacuate', node })}
                      disabled={node.status === 'draining'}
                      title="Move all servers off this node"
                    >
                      Evacuate
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {nodes.length === 0 && (
              <tr><td colSpan={9} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 40 }}>No nodes registered.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Confirm Modal */}
      {modal?.type === 'evacuate' && (
        <Modal
          title={`Evacuate Node "${modal.node.name}"`}
          message={`This will set the node to DRAINING mode and trigger the rebalancer to migrate all ${modal.node.server_count} server(s) to other nodes. The node will not receive new server assignments until reactivated.`}
          confirmText="Evacuate Node"
          confirmDanger={false}
          onConfirm={handleEvacuate}
          onCancel={() => setModal(null)}
        />
      )}
    </div>
  );
}
