// Status badge component with color coding
const STATE_MAP = {
  // Server states
  running:     { cls: 'badge-green', label: 'Running' },
  suspended:   { cls: 'badge-amber', label: 'Suspended' },
  provisioning:{ cls: 'badge-blue',  label: 'Provisioning' },
  syncing:     { cls: 'badge-blue',  label: 'Syncing' },
  suspending:  { cls: 'badge-amber', label: 'Suspending' },
  migrating:   { cls: 'badge-blue',  label: 'Migrating' },
  created:     { cls: 'badge-slate', label: 'Created' },
  // Node states
  online:      { cls: 'badge-green', label: 'Online' },
  offline:     { cls: 'badge-red',   label: 'Offline' },
  draining:    { cls: 'badge-amber', label: 'Draining' },
  starting:    { cls: 'badge-blue',  label: 'Starting' },
  // Billing states
  paid:        { cls: 'badge-green', label: 'Paid' },
  pending:     { cls: 'badge-amber', label: 'Pending' },
  failed:      { cls: 'badge-red',   label: 'Failed' },
  refunded:    { cls: 'badge-slate', label: 'Refunded' },
  // User states
  active:      { cls: 'badge-green', label: 'Active' },
  banned:      { cls: 'badge-red',   label: 'Banned' },
  grace_period:{ cls: 'badge-amber', label: 'Grace Period' },
  cancelled:   { cls: 'badge-slate', label: 'Cancelled' },
};

export default function StatusBadge({ status, noDot = false }) {
  const cfg = STATE_MAP[status?.toLowerCase()] || { cls: 'badge-slate', label: status || 'Unknown' };
  return (
    <span className={`badge ${cfg.cls}`}>
      {!noDot && <span className="badge-dot" />}
      {cfg.label}
    </span>
  );
}
