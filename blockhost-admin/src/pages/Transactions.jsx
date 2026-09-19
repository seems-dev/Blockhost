import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { getTransactions } from '../api';
import StatusBadge from '../components/StatusBadge';
import { Search, RefreshCw, Filter } from 'lucide-react';

const fmtMoney = (amount, currency = 'USD') => {
  const symbol = currency === 'INR' ? '₹' : '$';
  return `${symbol}${Number(amount).toFixed(2)}`;
};

function TxRow({ tx }) {
  const isFailed = tx.status === 'failed';
  return (
    <tr style={{ background: isFailed ? 'rgba(248,81,73,0.04)' : undefined }}>
      <td>
        <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>
          {new Date(tx.created_at).toLocaleDateString()}
        </div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
          {new Date(tx.created_at).toLocaleTimeString()}
        </div>
      </td>
      <td style={{ fontSize: 12 }}>
        {tx.user_email}
        <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>{tx.user_id.slice(0, 8)}…</div>
      </td>
      <td style={{ fontWeight: 600, color: tx.status === 'paid' ? 'var(--green)' : 'var(--text-primary)', fontFamily: 'var(--mono)' }}>
        {fmtMoney(tx.amount, tx.currency)}
      </td>
      <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{tx.target_plan_id || '—'}</td>
      <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
        {tx.provider === 'paddle' ? '🏓 Paddle' :
         tx.provider === 'razorpay' ? '💳 Razorpay' :
         tx.provider}
      </td>
      <td><StatusBadge status={tx.status} /></td>
      <td style={{ textAlign: 'right' }}>
        {(tx.status === 'paid') && (
          <button className="btn btn-outline btn-xs" title="Issue refund via Paddle">
            Refund
          </button>
        )}
        {isFailed && (
          <button className="btn btn-outline btn-xs" title="Send reminder email to user">
            Remind
          </button>
        )}
      </td>
    </tr>
  );
}

export default function Transactions() {
  const [txs, setTxs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try { setTxs(await getTransactions()); } catch { }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    return txs.filter(t => {
      if (statusFilter && t.status !== statusFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        return t.user_email?.toLowerCase().includes(q) ||
               t.provider_order_id?.toLowerCase().includes(q);
      }
      return true;
    });
  }, [txs, search, statusFilter]);

  const totalPaid = txs.filter(t => t.status === 'paid').reduce((s, t) => s + t.amount, 0);
  const totalFailed = txs.filter(t => t.status === 'failed').length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h2 style={{ fontSize: 18, marginBottom: 2 }}>Billing & Transactions</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
            {txs.length} transactions · {fmtMoney(totalPaid)} collected ·{' '}
            <span style={{ color: totalFailed > 0 ? 'var(--red)' : 'var(--text-muted)' }}>
              {totalFailed} failed
            </span>
          </p>
        </div>
        <button className="btn btn-outline btn-sm" onClick={load}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Failed payments alert */}
      {totalFailed > 0 && (
        <div className="alert-item error" style={{ borderRadius: 6 }}>
          <span style={{ fontSize: 14 }}>⚠</span>
          <div>
            <strong>{totalFailed} payment{totalFailed > 1 ? 's' : ''} failed</strong> — review below and send reminders.
          </div>
        </div>
      )}

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 200, maxWidth: 360 }}>
          <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input className="search-input" placeholder="Search by email or order ID…" value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <select className="select-input" value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
          <option value="">All Statuses</option>
          <option value="paid">Paid</option>
          <option value="pending">Pending</option>
          <option value="failed">Failed</option>
          <option value="refunded">Refunded</option>
        </select>
      </div>

      {/* Table */}
      <div className="kpi-card" style={{ padding: 0, overflow: 'auto' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading transactions…</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>User</th>
                <th>Amount</th>
                <th>Plan</th>
                <th>Provider</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(tx => <TxRow key={tx.id} tx={tx} />)}
              {filtered.length === 0 && (
                <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 40 }}>
                  No transactions match filters.
                </td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}