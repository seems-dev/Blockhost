import React, { useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import Servers from './pages/Servers';
import Transactions from './pages/Transactions';
import { loginAdmin, logoutAdmin } from './api';

function AdminLayout({ children }) {
  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <div className="w-64 bg-slate-900 border-r border-slate-700 p-6">
        <h1 className="text-xl font-bold text-cyan-400 mb-10">BlockHost Admin</h1>
        <nav className="flex flex-col gap-4">
          <NavLink to="/" className={({ isActive }) => isActive ? "text-cyan-400 font-bold" : "text-slate-400 hover:text-white"}>📊 Dashboard</NavLink>
          <NavLink to="/servers" className={({ isActive }) => isActive ? "text-cyan-400 font-bold" : "text-slate-400 hover:text-white"}>🖥️ Servers</NavLink>
          <NavLink to="/transactions" className={({ isActive }) => isActive ? "text-cyan-400 font-bold" : "text-slate-400 hover:text-white"}>
            💰 Transactions
          </NavLink>
          <button onClick={logoutAdmin} className="text-left text-red-400 hover:text-red-300 mt-10">🚪 Logout</button>
        </nav>
      </div>

      {/* Main Content */}
      <div className="flex-1 p-8 overflow-y-auto">
        {children}
      </div>
    </div>
  );
}

export default function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(!!localStorage.getItem('admin_token'));

  const handleLogin = async (e) => {
    e.preventDefault();
    const email = e.target.email.value;
    const password = e.target.password.value;
    await loginAdmin(email, password);
    setIsLoggedIn(true);
  };

  if (!isLoggedIn) {
    return (
      <div className="flex h-screen items-center justify-center">
        <form onSubmit={handleLogin} className="bg-slate-800 p-8 rounded-lg border border-slate-700 w-96">
          <h2 className="text-2xl font-bold mb-6 text-center">Admin Login</h2>
          <input name="email" type="email" placeholder="Admin Email" className="w-full p-3 mb-4 bg-slate-700 rounded outline-none" required />
          <input name="password" type="password" placeholder="Password" className="w-full p-3 mb-6 bg-slate-700 rounded outline-none" required />
          <button type="submit" className="w-full bg-cyan-600 hover:bg-cyan-700 text-white font-bold p-3 rounded">Login</button>
        </form>
      </div>
    );
  }

  return (
    <BrowserRouter>
      <AdminLayout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/servers" element={<Servers />} />
          <Route path="/transactions" element={<Transactions />} />
          {/* Add routes for /users and /transactions later */}
        </Routes>
      </AdminLayout>
    </BrowserRouter>
  );
}