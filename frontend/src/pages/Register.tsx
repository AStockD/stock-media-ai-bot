import { useState } from 'react';
import { authApi, type UserInfo } from '../api/client';

interface Props {
  onLogin: (token: string, user: UserInfo) => void;
  onSwitchToLogin: () => void;
}

export default function Register({ onLogin, onSwitchToLogin }: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const resp = await authApi.register(username, password);
      onLogin(resp.token, resp.user);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Registration failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1>Stock Media AI Bot</h1>
        <h2>Register</h2>
        <form onSubmit={handleSubmit}>
          <input
            type="text"
            placeholder="Username (>= 3 chars)"
            value={username}
            onChange={e => setUsername(e.target.value)}
            required
            minLength={3}
          />
          <input
            type="password"
            placeholder="Password (>= 6 chars)"
            value={password}
            onChange={e => setPassword(e.target.value)}
            required
            minLength={6}
          />
          {error && <p className="auth-error">{error}</p>}
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? 'Registering...' : 'Register'}
          </button>
        </form>
        <p className="auth-switch">
          Already have an account? <a href="#" onClick={e => { e.preventDefault(); onSwitchToLogin(); }}>Login</a>
        </p>
      </div>
    </div>
  );
}
