import React from 'react';

export function Modal({ title, message, onConfirm, onCancel, confirmText = 'Confirm', confirmDanger = true, children }) {
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <h3 style={{ fontSize: 16, marginBottom: 8, color: 'var(--text-primary)' }}>{title}</h3>
        {message && <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 20, lineHeight: 1.6 }}>{message}</p>}
        {children}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
          <button className="btn btn-outline" onClick={onCancel}>Cancel</button>
          <button
            className={`btn ${confirmDanger ? 'btn-danger' : 'btn-primary'}`}
            onClick={onConfirm}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}

export function PromptModal({ title, label, placeholder, onConfirm, onCancel }) {
  const [value, setValue] = React.useState('');
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <h3 style={{ fontSize: 16, marginBottom: 16, color: 'var(--text-primary)' }}>{title}</h3>
        <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>{label}</label>
        <input
          autoFocus
          value={value}
          onChange={e => setValue(e.target.value)}
          placeholder={placeholder}
          className="search-input"
          style={{ paddingLeft: 12, marginBottom: 20 }}
          onKeyDown={e => e.key === 'Enter' && value && onConfirm(value)}
        />
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn btn-outline" onClick={onCancel}>Cancel</button>
          <button className="btn btn-primary" disabled={!value} onClick={() => onConfirm(value)}>Submit</button>
        </div>
      </div>
    </div>
  );
}
