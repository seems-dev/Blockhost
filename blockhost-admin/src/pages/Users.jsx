import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { getUsers, banUser, unbanUser, grantBlockcoins, impersonateUser } from '../api';
import StatusBadge from '../components/StatusBadge';
import { Modal, PromptModal } from '../components/Modal';
import { Search, RefreshCw, ExternalLink, Coins, ShieldOff, ShieldCheck } from 'lucide-react';

function timeAgo(iso) {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const d = Math.floor(diff / 86400000);
  if (d > 365) return `${Math.floor(d/365)}y ago`;
  if (d > 0) return `${d}d ago`;
  const h = Math.floor(diff / 3600000);
  if (h > 0) return `${h}h ago`;
  return 'Just now';
}

export default function UsersPage() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [modal, setModal] = useState(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);

  const showToast = (msg, ok = true) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 3000);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try { setUsers(await getUsers()); } catch { }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Client-side search (server also supports it, but client-side is instant)
  const filtered = useMemo(() => {
    if (!search.trim()) return users;
    const q = search.toLowerCase();
    return users.filter(u =>
      u.email?.toLowerCase().includes(q) ||
      u.nickname?.toLowerCase().includes(q)
    );
  }, [users, search]);

  const handleBan = async () => {
    setBusy(true);
    try {
      const u = modal.user;
      if (u.is_banned) await unbanUser(u.id);
      else await banUser(u.id);
      showToast(`User ${u.is_banned ? 'unbanned' : 'banned'}: ${u.email}`);
      load();
    } catch (err) {
      showToast(err.response?.data?.detail || 'Failed', false);
    } finally { setBusy(false); setModal(null); }
  };

  const handleGrant = async (amount) => {
    const parsed = parseFloat(amount);
    if (isNaN(parsed) || parsed <= 0) return showToast('Invalid amount', false);
    setBusy(true);
    try {
      await grantBlockcoins(modal.user.id, parsed);
      showToast(`Granted ${parsed} Blockcoins to ${modal.user.email}`);
      load();
    } catch (err) {
      showToast(err.response?.data?.detail || 'Failed', false);
    } finally { setBusy(false); setModal(null); }
  };

  const handleImpersonate = async (user) => {
    setBusy(true);
    try {
      const { access_token, expires_in } = await impersonateUser(user.id);
      // Open the user-facing app with this token pre-set
      const url = `https://blockhost.sryze.cc?__impersonate_token=${access_token}`;
      window.open(url, '_blank');
      showToast(`Opened session as ${user.email} (expires in ${expires_in}s)`);
    } catch (err) {
      showToast(err.response?.data?.detail || 'Impersonation not available', false);
    } finally { setBusy(false); }
  };

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

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h2 style={{ fontSize: 18, marginBottom: 2 }}>Users</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{filtered.length} of {users.length} users</p>
        </div>
        <button className="btn btn-outline btn-sm" onClick={load}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Search */}
      <div style={{ position: 'relative', maxWidth: 360 }}>
        <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
        <input
          className="search-input"
          placeholder="Search by email or nickname…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {/* Table */}
      <div className="kpi-card" style={{ padding: 0, overflow: 'auto' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading users…</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Nickname</th>
                <th>Servers</th>
                <th>Blockcoins</th>
                <th>Joined</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(u => (
                <tr key={u.id} style={{ opacity: u.is_banned ? 0.6 : 1 }}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{u.email}</div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>{u.id.slice(0, 12)}…</div>
                  </td>
                  <td style={{ color: 'var(--text-secondary)' }}>{u.nickname || '—'}</td>
                  <td style={{ fontFamily: 'var(--mono)', fontSize: 13 }}>{u.server_count}</td>
                  <td style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--accent)' }}>{u.blockcoin_balance}</td>
                  <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>{timeAgo(u.created_at)}</td>
                  <td>
                    {u.is_admin
                      ? <StatusBadge status="admin" />
                      : <StatusBadge status={u.is_banned ? 'banned' : 'active'} />
                    }
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'flex', gap: 5, justifyContent: 'flex-end' }}>
                      <button
                        className="btn btn-outline btn-xs"
                        title="Login as this user"
                        onClick={() => handleImpersonate(u)}
                      >
                        <ExternalLink size={10} /> Login As
                      </button>
                      <button
                        className="btn btn-outline btn-xs"
                        title="Grant Blockcoins"
                        onClick={() => setModal({ type: 'grant', user: u })}
                      >
                        <Coins size={10} /> Grant
                      </button>
                      <button
                        className="btn btn-outline btn-xs"
                        title={u.is_banned ? 'Unban user' : 'Ban user'}
                        onClick={() => setModal({ type: 'ban', user: u })}
                        disabled={u.is_admin}
                      >
                        {u.is_banned ? <ShieldCheck size={10} /> : <ShieldOff size={10} />}
                        {u.is_banned ? 'Unban' : 'Ban'}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 40 }}>No users found.</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* Ban Modal */}
      {modal?.type === 'ban' && (
        <Modal
          title={modal.user.is_banned ? `Unban "${modal.user.email}"?` : `Ban "${modal.user.email}"?`}
          message={
            modal.user.is_banned
              ? `This will restore the user's access to the platform.`
              : `This will soft-delete the user account. Their servers will remain but they will not be able to login. You can unban them later.`
          }
          confirmText={modal.user.is_banned ? 'Unban User' : 'Ban User'}
          confirmDanger={!modal.user.is_banned}
          onConfirm={handleBan}
          onCancel={() => setModal(null)}
        />
      )}

      {/* Grant Modal */}
      {modal?.type === 'grant' && (
        <PromptModal
          title={`Grant Blockcoins to "${modal.user.email}"`}
          label={`Amount (current balance: ${modal.user.blockcoin_balance})`}
          placeholder="e.g. 100"
          onConfirm={handleGrant}
          onCancel={() => setModal(null)}
        />
      )}
    </div>
  );
}
