
import React, { useEffect, useState } from 'react';
import { getServers, forceStopServer } from '../api';

export default function Servers() {
    const [servers, setServers] = useState([]);

    const fetchServers = () => getServers().then(setServers);

    useEffect(() => {
        fetchServers();
        const interval = setInterval(fetchServers, 10000); // Auto-refresh every 10s
        return () => clearInterval(interval);
    }, []);

    const handleForceStop = async (id) => {
        if (!window.confirm('Are you sure? This will instantly kill the server process.')) return;
        await forceStopServer(id);
        fetchServers(); // Refresh list
    };

    const getStateColor = (state) => {
        switch (state) {
            case 'running': return 'bg-green-500';
            case 'suspended': return 'bg-red-500';
            default: return 'bg-gray-500';
        }
    };

    return (
        <div>
            <h1 className="text-2xl font-bold mb-6">Server Monitor</h1>
            <div className="bg-slate-800 rounded-lg overflow-hidden border border-slate-700">
                <table className="w-full text-left border-collapse">
                    <thead>
                        <tr className="border-b border-slate-700 text-slate-400 text-sm">
                            <th className="p-4">Server ID</th>
                            <th className="p-4">Owner ID</th>
                            <th className="p-4">Name</th>
                            <th className="p-4">Status</th>
                            <th className="p-4">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {servers.map((s) => (
                            <tr key={s.id} className="border-b border-slate-700 hover:bg-slate-700/50">
                                <td className="p-4 font-mono text-xs text-blue-400">{s.id.substring(0, 8)}...</td>
                                <td className="p-4 font-mono text-xs">{s.owner_id.substring(0, 8)}...</td>
                                <td className="p-4">{s.world_name}</td>
                                <td className="p-4">
                                    <span className={`px-2 py-1 rounded-full text-xs text-white ${getStateColor(s.state)}`}>
                                        {s.state}
                                    </span>
                                </td>
                                <td className="p-4">
                                    {s.state === 'running' && (
                                        <button
                                            onClick={() => handleForceStop(s.id)}
                                            className="bg-red-600 hover:bg-red-700 text-white text-xs px-3 py-1 rounded"
                                        >
                                            Force Stop
                                        </button>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}