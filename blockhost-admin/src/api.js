import axios from 'axios';

const api = axios.create({
    baseURL: 'https://blockhost.sryze.cc',
});

// Automatically attach JWT token if logged in
api.interceptors.request.use((config) => {
    const token = localStorage.getItem('admin_token');
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

export const loginAdmin = async (email, password) => {
    const res = await api.post('/api/auth/login', { email, password });
    localStorage.setItem('admin_token', res.data.access_token);
    return res.data;
};

export const logoutAdmin = () => localStorage.removeItem('admin_token');

export const getStats = () => api.get('/api/admin/stats').then(r => r.data);
export const getUsers = () => api.get('/api/admin/users').then(r => r.data);
export const getServers = () => api.get('/api/admin/servers').then(r => r.data);
export const getTransactions = () => api.get('/api/admin/transactions').then(r => r.data);

export const forceStopServer = (serverId) =>
    api.post(`/api/admin/servers/${serverId}/force-stop`).then(r => r.data);

export const banUser = (userId) =>
    api.post(`/api/admin/users/${userId}/ban`).then(r => r.data);

export const grantBlockcoins = (userId, amount) =>
    api.post(`/api/admin/users/${userId}/grant-blockcoins`, null, { params: { amount } }).then(r => r.data);

export const getNodes = () => api.get('/api/admin/nodes').then(r => r.data);

export default api;
