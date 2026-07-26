import React, { useEffect, useState } from 'react';
import { getStats } from '../api';

export default function Dashboard() {
    const [stats, setStats] = useState(null);

    useEffect(() => {
        getStats().then(setStats);
    }, []);

    if (!stats) return <div className="text-center mt-10">Loading stats...</div>;

    const cards = [
        { title: 'Total Users', value: stats.total_users, icon: '👥', color: 'bg-blue-600' },
        { title: 'Total Servers', value: stats.total_servers, icon: '🖥️', color: 'bg-purple-600' },
        { title: 'Running Now', value: stats.running_servers, icon: '⚡', color: 'bg-green-600' },
        { title: 'Total Revenue', value: `₹${stats.total_revenue}`, icon: '💰', color: 'bg-yellow-600' },
    ];

    return (
        <div>
            <h1 className="text-2xl font-bold mb-6">System Overview</h1>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                {cards.map((card) => (
                    <div key={card.title} className="bg-slate-800 p-6 rounded-lg shadow-lg border border-slate-700">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-slate-400 text-sm">{card.title}</p>
                                <p className="text-3xl font-bold mt-2">{card.value}</p>
                            </div>
                            <div className={`text-4xl p-4 rounded-full ${card.color} bg-opacity-20`}>
                                {card.icon}
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}