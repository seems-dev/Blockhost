import React, { useEffect, useState } from 'react';
import { getTransactions } from '../api';

export default function Transactions() {
    const [transactions, setTransactions] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        getTransactions()
            .then(setTransactions)
            .finally(() => setLoading(false));
    }, []);

    const getStatusBadge = (status) => {
        switch (status) {
            case 'paid':
                return 'bg-green-900 text-green-300 border-green-700';
            case 'pending':
                return 'bg-yellow-900 text-yellow-300 border-yellow-700';
            case 'failed':
            case 'refunded':
                return 'bg-red-900 text-red-300 border-red-700';
            default:
                return 'bg-gray-800 text-gray-400 border-gray-600';
        }
    };

    if (loading) return <div className="text-center mt-10 text-slate-400">Loading transactions...</div>;

    return (
        <div>
            <div className="flex justify-between items-center mb-6">
                <h1 className="text-2xl font-bold">Transactions</h1>
                <div className="text-sm text-slate-400">
                    Showing {transactions.length} records
                </div>
            </div>

            <div className="bg-slate-800 rounded-lg overflow-hidden border border-slate-700">
                <div className="overflow-x-auto">
                    <table className="w-full text-left border-collapse">
                        <thead>
                            <tr className="border-b border-slate-700 text-slate-400 text-sm uppercase tracking-wider">
                                <th className="p-4">Date</th>
                                <th className="p-4">User ID</th>
                                <th className="p-4">Server ID</th>
                                <th className="p-4">Amount</th>
                                <th className="p-4">Provider</th>
                                <th className="p-4">Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            {transactions.length === 0 ? (
                                <tr>
                                    <td colSpan="6" className="p-8 text-center text-slate-500">
                                        No transactions found.
                                    </td>
                                </tr>
                            ) : (
                                transactions.map((tx) => (
                                    <tr key={tx.id} className="border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors">
                                        <td className="p-4 text-sm text-slate-300 whitespace-nowrap">
                                            {new Date(tx.created_at).toLocaleDateString()} <br />
                                            <span className="text-xs text-slate-500">
                                                {new Date(tx.created_at).toLocaleTimeString()}
                                            </span>
                                        </td>
                                        <td className="p-4 font-mono text-xs text-blue-400" title={tx.user_id}>
                                            {tx.user_id.substring(0, 8)}...
                                        </td>
                                        <td className="p-4 font-mono text-xs text-purple-400" title={tx.server_id}>
                                            {tx.server_id.substring(0, 8)}...
                                        </td>
                                        <td className="p-4 font-bold text-white">
                                            ₹{tx.amount}
                                        </td>
                                        <td className="p-4 text-sm text-slate-400">
                                            {tx.provider === 'razorpay' ? '💳 Razorpay' : tx.provider}
                                        </td>
                                        <td className="p-4">
                                            <span className={`px-3 py-1 rounded-full text-xs font-bold border ${getStatusBadge(tx.status)}`}>
                                                {tx.status.toUpperCase()}
                                            </span>
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}