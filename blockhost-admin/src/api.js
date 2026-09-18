import axios from 'axios';

const api = axios.create({
  baseURL: 'https://blockhost.sryze.cc',
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('admin_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  res => res,
  err => {
    if (err.response?.status === 401) {
      localStorage.removeItem('admin_token');
      window.location.reload();
    }
    return Promise.reject(err);
  }
);

export const loginAdmin = async (email, password) => {
  const res = await api.post('/api/auth/login', { email, password });
  localStorage.setItem('admin_token', res.data.access_token);
  return res.data;
};

export const logoutAdmin = () => {
  localStorage.removeItem('admin_token');
  window.location.reload();
};

// Dashboard
export const getStats = () => api.get('/api/admin/stats').then(r => r.data);

// Users
export const getUsers = (search = '') => api.get('/api/admin/users', { params: { search, limit: 200 } }).then(r => r.data);
export const banUser = (userId) => api.post(`/api/admin/users/${userId}/ban`).then(r => r.data);
export const unbanUser = (userId) => api.post(`/api/admin/users/${userId}/unban`).then(r => r.data);
export const grantBlockcoins = (userId, amount) =>
  api.post(`/api/admin/users/${userId}/grant-blockcoins`, null, { params: { amount } }).then(r => r.data);
export const impersonateUser = (userId) =>
  api.post(`/api/admin/users/${userId}/impersonate`).then(r => r.data);

// Servers
export const getServers = (params = {}) => api.get('/api/admin/servers', { params }).then(r => r.data);
export const forceStopServer = (serverId) => api.post(`/api/admin/servers/${serverId}/force-stop`).then(r => r.data);
export const forceStartServer = (serverId) => api.post(`/api/admin/servers/${serverId}/force-start`).then(r => r.data);

// Nodes
export const getNodes = () => api.get('/api/admin/nodes').then(r => r.data);
export const evacuateNode = (nodeId) => api.post(`/api/admin/nodes/${nodeId}/evacuate`).then(r => r.data);

// Transactions
export const getTransactions = (params = {}) => api.get('/api/admin/transactions', { params }).then(r => r.data);

// Audit logs
export const getAuditLogs = (params = {}) => api.get('/api/admin/audit-logs', { params }).then(r => r.data);

export default api;
