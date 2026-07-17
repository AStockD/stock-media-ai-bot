import { useState, useEffect, useCallback } from 'react';
import { platformApi, type UserInfo, type PlatformAccount } from './api/client';
import Login from './pages/Login';
import Register from './pages/Register';
import './App.css';

type Page = 'login' | 'register' | 'dashboard';

interface PlatformInfo {
  id: string;
  name: string;
  icon: string;
  color: string;
}

const PLATFORMS: PlatformInfo[] = [
  { id: 'xueqiu', name: '雪球', icon: '❄️', color: '#2196F3' },
];

function loadAuth(): { token: string; user: UserInfo } | null {
  try {
    const t = localStorage.getItem('smab_token');
    const u = localStorage.getItem('smab_user');
    if (t && u) return { token: t, user: JSON.parse(u) };
  } catch { /* ignore */ }
  return null;
}

export default function App() {
  const [page, setPage] = useState<Page>(() => loadAuth() ? 'dashboard' : 'login');
  const [token, setToken] = useState<string>(() => loadAuth()?.token || '');
  const [user, setUser] = useState<UserInfo | null>(() => loadAuth()?.user || null);
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [selectedPlatform, setSelectedPlatform] = useState('xueqiu');
  const [qrImage, setQrImage] = useState('');
  const [loginMsg, setLoginMsg] = useState('');
  const [loginActive, setLoginActive] = useState(false);
  const [postContent, setPostContent] = useState('');
  const [postMsg, setPostMsg] = useState('');

  const handleLogin = (t: string, u: UserInfo) => {
    localStorage.setItem('smab_token', t);
    localStorage.setItem('smab_user', JSON.stringify(u));
    setToken(t);
    setUser(u);
    setPage('dashboard');
  };

  const handleLogout = () => {
    localStorage.removeItem('smab_token');
    localStorage.removeItem('smab_user');
    setToken('');
    setUser(null);
    setPage('login');
    setAccounts([]);
  };

  const fetchAccounts = useCallback(async () => {
    if (!token) return;
    try {
      const resp = await platformApi.getAccounts(token);
      setAccounts(resp.accounts);
    } catch { /* ignore */ }
  }, [token]);

  useEffect(() => {
    if (page === 'dashboard') fetchAccounts();
  }, [page, fetchAccounts]);

  function getAccountStatus(platform: string): PlatformAccount | undefined {
    return accounts.find(a => a.platform === platform);
  }

  async function handleStartLogin() {
    setLoginActive(true);
    setQrImage('');
    setLoginMsg('Starting browser...');
    try {
      await platformApi.cancelLogin(selectedPlatform, token);
      await new Promise(r => setTimeout(r, 500));
      const resp = await platformApi.startLogin(selectedPlatform, token);
      if (resp.status === 'waiting_for_scan' && resp.qr_image) {
        setQrImage(resp.qr_image);
        setLoginMsg(resp.message || 'Please scan QR code');
        pollLoginStatus();
      } else if (resp.status === 'error') {
        setLoginMsg(`Error: ${resp.error || 'Unknown'}`);
        setLoginActive(false);
      } else {
        setLoginMsg(resp.message || resp.status);
      }
    } catch (err: unknown) {
      setLoginMsg(`Error: ${err instanceof Error ? err.message : String(err)}`);
      setLoginActive(false);
    }
  }

  async function pollLoginStatus() {
    for (let i = 0; i < 120; i++) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const data = await platformApi.loginStatus(selectedPlatform, token);
        if (data.status === 'success') {
          setLoginMsg(`Login success! ${data.cookie_count} cookies saved`);
          setQrImage('');
          setLoginActive(false);
          fetchAccounts();
          return;
        } else if (data.status === 'timeout') {
          setLoginMsg('Login timeout, please retry');
          setQrImage('');
          setLoginActive(false);
          return;
        } else if (data.status === 'error') {
          setLoginMsg(`Error: ${data.error || 'Unknown'}`);
          setQrImage('');
          setLoginActive(false);
          return;
        }
      } catch { /* ignore */ }
    }
    setLoginMsg('Login timeout');
    setLoginActive(false);
  }

  async function handleCancelLogin() {
    try { await platformApi.cancelLogin(selectedPlatform, token); } catch { /* ignore */ }
    setLoginActive(false);
    setQrImage('');
    setLoginMsg('');
  }

  async function handlePost() {
    if (!postContent.trim()) return;
    setPostMsg('Posting...');
    try {
      const resp = await platformApi.createPost(selectedPlatform, postContent, token);
      setPostMsg(resp.success ? `Posted! ${resp.url || ''}` : `Failed: ${resp.error}`);
      if (resp.success) setPostContent('');
    } catch (err: unknown) {
      setPostMsg(`Error: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  if (page === 'login') {
    return <Login onLogin={handleLogin} onSwitchToRegister={() => setPage('register')} />;
  }
  if (page === 'register') {
    return <Register onLogin={handleLogin} onSwitchToLogin={() => setPage('login')} />;
  }

  const platform = PLATFORMS.find(p => p.id === selectedPlatform)!;
  const accountStatus = getAccountStatus(selectedPlatform);

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <span className="header-icon">📈</span>
          <h1>Stock Media AI Bot</h1>
        </div>
        <div className="header-right">
          <span className="user-badge">{user?.username}</span>
          <button className="btn btn-outline btn-sm" onClick={handleLogout}>Logout</button>
        </div>
      </header>

      <main className="main">
        <section className="platforms-section">
          <h2>Platforms</h2>
          <div className="platforms-grid">
            {PLATFORMS.map(p => {
              const acc = getAccountStatus(p.id);
              return (
                <div
                  key={p.id}
                  className={`platform-card ${selectedPlatform === p.id ? 'selected' : ''}`}
                  onClick={() => { setSelectedPlatform(p.id); setQrImage(''); setLoginMsg(''); }}
                  style={{ '--platform-color': p.color } as React.CSSProperties}
                >
                  <span className="platform-icon">{p.icon}</span>
                  <div>
                    <span className="platform-name">{p.name}</span>
                    <span className={`platform-status ${acc?.is_valid ? 'status-ok' : 'status-warn'}`}>
                      {acc?.is_valid ? 'Connected' : 'Not connected'}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="login-section">
          <div className="login-card">
            <div className="login-header">
              <span className="login-platform-icon">{platform.icon}</span>
              <div>
                <h3>{platform.name} Account</h3>
                <p className="login-sub">
                  {accountStatus?.is_valid
                    ? `Last updated: ${new Date(accountStatus.last_updated!).toLocaleString()} (${accountStatus.cookie_count} cookies)`
                    : 'Not connected'}
                </p>
              </div>
            </div>
            <div className="login-body">
              {qrImage ? (
                <div className="qr-area">
                  <img src={qrImage} alt="QR Code" className="qr-image" />
                  <p className="qr-hint">{loginMsg}</p>
                  <div className="qr-actions">
                    <button className="btn btn-secondary" onClick={handleStartLogin}>Refresh</button>
                    <button className="btn btn-outline" onClick={handleCancelLogin}>Cancel</button>
                  </div>
                </div>
              ) : (
                <div className="login-action-area">
                  <p className="login-msg">{loginMsg || 'Click to start QR code login'}</p>
                  <button className="btn btn-primary" onClick={handleStartLogin} disabled={loginActive}>
                    {loginActive ? 'Logging in...' : 'Scan QR to Login'}
                  </button>
                </div>
              )}
            </div>
          </div>
        </section>

        <section className="post-section">
          <h2>Create Post</h2>
          <div className="post-card">
            <textarea
              className="post-textarea"
              placeholder={`Write something to post on ${platform.name}...`}
              value={postContent}
              onChange={e => setPostContent(e.target.value)}
              rows={4}
            />
            {postMsg && <p className="post-msg">{postMsg}</p>}
            <button className="btn btn-primary" onClick={handlePost} disabled={!postContent.trim()}>
              Post to {platform.name}
            </button>
          </div>
        </section>
      </main>
    </div>
  );
}
