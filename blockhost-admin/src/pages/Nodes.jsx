import React, { useState, useEffect } from 'react';
import { getNodes } from '../api';

export default function Nodes() {
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadNodes();
  }, []);

  const loadNodes = async () => {
    setLoading(true);
    try {
      const data = await getNodes();
      setNodes(data);
    } catch (err) {
      console.error(err);
      alert('Failed to load nodes');
    }
    setLoading(false);
  };

  if (loading) return <div className="text-white">Loading nodes...</div>;

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6 text-white">Worker Nodes</h2>
      
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {nodes.map(node => (
          <div key={node.id} className="bg-slate-800 rounded-lg p-6 border border-slate-700">
            <div className="flex justify-between items-start mb-4">
              <h3 className="text-lg font-bold text-white">{node.name}</h3>
              <span className={`px-2 py-1 text-xs rounded font-bold ${
                node.status === 'online' ? 'bg-emerald-900 text-emerald-400' : 'bg-red-900 text-red-400'
              }`}>
                {node.status.toUpperCase()}
              </span>
            </div>
            
            <div className="space-y-4 text-sm text-slate-300">
              <div className="flex justify-between">
                <span className="text-slate-500">IP Address:</span>
                <span className="font-mono text-cyan-400">{node.ip_address}</span>
              </div>
              
              <div>
                <div className="flex justify-between mb-1">
                  <span className="text-slate-500">RAM Usage:</span>
                  <span>{Math.round(node.used_ram_mb / 1024)}GB / {Math.round(node.total_ram_mb / 1024)}GB</span>
                </div>
                <div className="w-full bg-slate-700 rounded-full h-2">
                  <div 
                    className="bg-cyan-500 h-2 rounded-full" 
                    style={{ width: `${Math.min(100, (node.used_ram_mb / Math.max(1, node.total_ram_mb)) * 100)}%` }}
                  ></div>
                </div>
              </div>

              <div>
                <div className="flex justify-between mb-1">
                  <span className="text-slate-500">CPU Usage:</span>
                  <span>{node.cpu_usage_percent.toFixed(1)}%</span>
                </div>
                <div className="w-full bg-slate-700 rounded-full h-2">
                  <div 
                    className="bg-purple-500 h-2 rounded-full" 
                    style={{ width: `${Math.min(100, node.cpu_usage_percent)}%` }}
                  ></div>
                </div>
              </div>
            </div>
          </div>
        ))}
        {nodes.length === 0 && (
          <div className="col-span-full p-12 text-center text-slate-500 bg-slate-800 rounded-lg border border-slate-700">
            No worker nodes registered.
          </div>
        )}
      </div>
    </div>
  );
}
