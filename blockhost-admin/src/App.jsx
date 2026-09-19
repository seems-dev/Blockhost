import React, { useState, useEffect, useCallback } from 'react';
import { BrowserRouter, Routes, Route, NavLink, useLocation } from 'react-router-dom';
import {
  LayoutDashboard, Server, Users, Network, CreditCard,
  ScrollText, LogOut, Menu, X, RefreshCw, Shield,
  ChevronLeft, AppWindow
} from 'lucide-react';
import Dashboard from './pages/Dashboard';
import Servers from './pages/Servers';
import Transactions from './pages/Transactions';
import UsersPage from './pages/Users';
import Nodes from './pages/Nodes';
import AuditLogs from './pages/AuditLogs';
import Deployments from './pages/Deployments';
import { loginAdmin, logoutAdmin } from './api';

const NAV = [
  { to: '/',            icon: LayoutDashboard, label: 'Overview' },
  { to: '/nodes',       icon: Network,         label: 'Nodes' },
  { to: '/users',       icon: Users,           label: 'Users' },
  { to: '/servers',     icon: Server,          label: 'Minecraft' },
  { to: '/deployments', icon: AppWindow,       label: 'Apps & DBs' },
  { to: '/transactions',icon: CreditCard,      label: 'Billing' },
  { to: '/audit',       icon: ScrollText,      label: 'Audit Logs' },
];

function Sidebar({ collapsed, onCollapse, mobileOpen, onMobileClose }) {
  const loc = useLocation();
  return (
    <>
      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 99 }}
          onClick={onMobileClose}
        />
      )}
      <aside
        className={`sidebar ${collapsed ? 'collapsed' : ''} ${mobileOpen ? 'open' : ''}`}
        style={{ zIndex: 100 }}
      >
        {/* Logo */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: collapsed ? 'center' : 'space-between',
          padding: '14px 14px 10px', borderBottom: '1px solid var(--border)', flexShrink: 0,
        }}>
          {!collapsed && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Shield size={18} color="var(--accent)" />
              <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
                Erex Admin
              </span>
            </div>
          )}
          <button
            className="btn btn-ghost btn-xs"
            onClick={onCollapse}
            style={{ padding: 4 }}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <ChevronLeft size={14} style={{ transform: collapsed ? 'rotate(180deg)' : 'none', transition: '0.2s' }} />
          </button>
        </div>

        {/* Nav */}
        <nav style={{ padding: '10px 8px', flex: 1, overflow: 'hidden' }}>
          {NAV.map(({ to, icon: Icon, label }) => {
            const isActive = to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(to);
            return (
              <NavLink
                key={to} to={to}
                className={`nav-item ${isActive ? 'active' : ''}`}
                onClick={onMobileClose}
                title={collapsed ? label : undefined}
              >
                <Icon size={16} className="nav-icon" style={{ flexShrink: 0 }} />
                {!collapsed && <span>{label}</span>}
              </NavLink>
            );
          })}
        </nav>

        {/* Logout */}
        <div style={{ padding: '10px 8px', borderTop: '1px solid var(--border)' }}>
          <button
            className="nav-item"
            onClick={logoutAdmin}
            style={{ width: '100%', background: 'none', border: 'none', justifyContent: collapsed ? 'center' : 'flex-start' }}
            title={collapsed ? 'Logout' : undefined}
          >
            <LogOut size={15} style={{ color: 'var(--red)', flexShrink: 0 }} />
            {!collapsed && <span style={{ color: 'var(--red)' }}>Logout</span>}
          </button>
        </div>
      </aside>
    </>
  );
}

function Topbar({ onMobileMenuOpen, lastRefresh }) {
  const loc = useLocation();
  const page = NAV.find(n => n.to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(n.to));
  const fmtTime = lastRefresh ? new Date(lastRefresh).toLocaleTimeString() : null;

  return (
    <div className="topbar">
      <button className="btn btn-ghost btn-sm" style={{ display: 'none' }} id="mobile-menu-btn" onClick={onMobileMenuOpen}>
        <Menu size={16} />
      </button>
      <button
        className="btn btn-ghost btn-sm"
        style={{ padding: 6 }}
        onClick={onMobileMenuOpen}
      >
        <Menu size={15} />
      </button>
      {page && (
        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
          {page.label}
        </span>
      )}
      <div style={{ flex: 1 }} />
      {fmtTime && (
        <span style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
          <RefreshCw size={11} /> Updated {fmtTime}
        </span>
      )}
    </div>
  );
}

function AdminLayout({ children }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [lastRefresh] = useState(Date.now());

  return (
    <div className="admin-layout">
      <Sidebar
        collapsed={collapsed}
        onCollapse={() => setCollapsed(c => !c)}
        mobileOpen={mobileOpen}
        onMobileClose={() => setMobileOpen(false)}
      />
      <div className="main-content">
        <Topbar onMobileMenuOpen={() => setMobileOpen(true)} lastRefresh={lastRefresh} />
        <div className="page-content animate-fade-in">
          {children}
        </div>
      </div>
    </div>
  );
}

function LoginPage({ onLogin }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true); setError('');
    try {
      await loginAdmin(e.target.email.value, e.target.password.value);
      onLogin();
    } catch (err) {
      setError(err.response?.data?.detail || 'Login failed. Check credentials.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      width: '100vw', height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg-base)',
    }}>
      <div style={{
        background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 12,
        padding: 36, width: 360, boxShadow: '0 16px 48px rgba(0,0,0,0.4)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 28 }}>
          <Shield size={22} color="var(--accent)" />
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)' }}>Erex Admin</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Mission Control</div>
          </div>
        </div>
        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 5 }}>Email</label>
            <input name="email" type="email" required className="search-input" style={{ paddingLeft: 12 }} placeholder="admin@erex.gg" />
          </div>
          <div style={{ marginBottom: 20 }}>
            <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 5 }}>Password</label>
            <input name="password" type="password" required className="search-input" style={{ paddingLeft: 12 }} placeholder="••••••••" />
          </div>
          {error && (
            <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 14, padding: '8px 12px', background: 'var(--red-bg)', borderRadius: 6 }}>
              {error}
            </div>
          )}
          <button type="submit" className="btn btn-primary" disabled={loading} style={{ width: '100%', padding: '9px 16px', justifyContent: 'center' }}>
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
}

export default function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(!!localStorage.getItem('admin_token'));

  if (!isLoggedIn) return <LoginPage onLogin={() => setIsLoggedIn(true)} />;

  return (
    <BrowserRouter>
      <AdminLayout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/servers" element={<Servers />} />
          <Route path="/deployments" element={<Deployments />} />
          <Route path="/transactions" element={<Transactions />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="/nodes" element={<Nodes />} />
          <Route path="/audit" element={<AuditLogs />} />
        </Routes>
      </AdminLayout>
    </BrowserRouter>
  );
}