import React, { useState, useEffect } from 'react';
import { getUsers, banUser, grantBlockcoins } from '../api';

export default function Users() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadUsers();
  }, []);

  const loadUsers = async () => {
    setLoading(true);
    try {
      const data = await getUsers();
      setUsers(data);
    } catch (err) {
      console.error(err);
      alert('Failed to load users');
    }
    setLoading(false);
  };

  const handleBan = async (userId) => {
    if (!window.confirm('Are you sure you want to ban this user?')) return;
    try {
      await banUser(userId);
      alert('User banned successfully');
      loadUsers();
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to ban user');
    }
  };

  const handleGrant = async (userId) => {
    const amount = prompt('Enter amount of Blockcoins to grant (e.g., 5.00):');
    if (!amount) return;
    const amountFloat = parseFloat(amount);
    if (isNaN(amountFloat) || amountFloat <= 0) {
      alert('Invalid amount');
      return;
    }
    try {
      await grantBlockcoins(userId, amountFloat);
      alert(`Granted ${amountFloat} Blockcoins successfully`);
      loadUsers();
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to grant Blockcoins');
    }
  };

  if (loading) return <div className="text-white">Loading users...</div>;

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6 text-white">Users</h2>
      
      <div className="bg-slate-800 rounded-lg overflow-hidden border border-slate-700">
        <table className="w-full text-left">
          <thead className="bg-slate-900 border-b border-slate-700 text-slate-300">
            <tr>
              <th className="p-4">ID</th>
              <th className="p-4">Email</th>
              <th className="p-4">Nickname</th>
              <th className="p-4">Joined</th>
              <th className="p-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700 text-slate-100">
            {users.map(u => (
              <tr key={u.id} className="hover:bg-slate-750 transition-colors">
                <td className="p-4 text-xs font-mono text-slate-400 truncate max-w-[120px]">{u.id}</td>
                <td className="p-4">{u.email}</td>
                <td className="p-4">{u.nickname || '-'}</td>
                <td className="p-4">{new Date(u.created_at).toLocaleDateString()}</td>
                <td className="p-4 text-right">
                  <button 
                    onClick={() => handleGrant(u.id)}
                    className="bg-emerald-600 hover:bg-emerald-500 text-white px-3 py-1 rounded text-sm mr-2"
                  >
                    Grant 💰
                  </button>
                  <button 
                    onClick={() => handleBan(u.id)}
                    className="bg-red-600 hover:bg-red-500 text-white px-3 py-1 rounded text-sm"
                  >
                    Ban ⛔
                  </button>
                </td>
              </tr>
            ))}
            {users.length === 0 && (
              <tr>
                <td colSpan="5" className="p-6 text-center text-slate-500">No users found.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
