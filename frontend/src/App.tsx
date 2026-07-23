import { useState, useEffect, useCallback } from 'react';
import { platformApi, type UserInfo, type PlatformAccount } from './api/client';
import Login from './pages/Login';
import Register from './pages/Register';
import PostManagement from './pages/PostManagement';
import MyPosts from './pages/MyPosts';
import Settings from './pages/Settings';
import './App.css';

type AuthPage = 'login' | 'register';
type Page = 'post' | 'myposts' | 'settings';

const PAGE_TITLES: Record<Page, string> = {
  post: '发帖管理',
  myposts: '我的帖子',
  settings: '设置',
};

function loadAuth(): { token: string; user: UserInfo } | null {
  try {
    const t = localStorage.getItem('smab_token');
    const u = localStorage.getItem('smab_user');
    if (t && u) return { token: t, user: JSON.parse(u) };
  } catch { /* ignore */ }
  return null;
}

export default function App() {
  const [authPage, setAuthPage] = useState<AuthPage | null>(() => loadAuth() ? null : 'login');
  const [token, setToken] = useState<string>(() => loadAuth()?.token || '');
  const [user, setUser] = useState<UserInfo | null>(() => loadAuth()?.user || null);
  const [currentPage, setCurrentPage] = useState<Page>('post');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);

  const handleLogin = (t: string, u: UserInfo) => {
    localStorage.setItem('smab_token', t);
    localStorage.setItem('smab_user', JSON.stringify(u));
    setToken(t);
    setUser(u);
    setAuthPage(null);
  };

  const handleLogout = () => {
    localStorage.removeItem('smab_token');
    localStorage.removeItem('smab_user');
    setToken('');
    setUser(null);
    setAuthPage('login');
    setAccounts([]);
    setDrawerOpen(false);
  };

  const fetchAccounts = useCallback(async () => {
    if (!token) return;
    try {
      const resp = await platformApi.getAccounts(token);
      setAccounts(resp.accounts);
    } catch { /* ignore */ }
  }, [token]);

  useEffect(() => {
    if (authPage === null) fetchAccounts();
  }, [authPage, fetchAccounts]);

  function navigateTo(page: Page) {
    setCurrentPage(page);
    setDrawerOpen(false);
  }

  if (authPage === 'login') {
    return <Login onLogin={handleLogin} onSwitchToRegister={() => setAuthPage('register')} />;
  }
  if (authPage === 'register') {
    return <Register onLogin={handleLogin} onSwitchToLogin={() => setAuthPage('login')} />;
  }

  return (
    <div className="app">
      <header className="top-bar">
        <button className="hamburger" onClick={() => setDrawerOpen(true)}>
          <span /><span /><span />
        </button>
        <h1 className="top-bar-title">{PAGE_TITLES[currentPage]}</h1>
        <span className="user-avatar">{user?.username?.[0]?.toUpperCase() || '?'}</span>
      </header>

      {drawerOpen && (
        <div className="drawer-overlay" onClick={() => setDrawerOpen(false)}>
          <nav className="drawer" onClick={e => e.stopPropagation()}>
            <div className="drawer-header">
              <span className="drawer-avatar">{user?.username?.[0]?.toUpperCase() || '?'}</span>
              <span className="drawer-username">{user?.username}</span>
            </div>
            <div className="drawer-items">
              <button
                className={`drawer-item ${currentPage === 'post' ? 'active' : ''}`}
                onClick={() => navigateTo('post')}
              >
                <span className="drawer-icon">📝</span>
                发帖管理
              </button>
              <button
                className={`drawer-item ${currentPage === 'myposts' ? 'active' : ''}`}
                onClick={() => navigateTo('myposts')}
              >
                <span className="drawer-icon">📋</span>
                我的帖子
              </button>
              <button
                className={`drawer-item ${currentPage === 'settings' ? 'active' : ''}`}
                onClick={() => navigateTo('settings')}
              >
                <span className="drawer-icon">⚙️</span>
                设置
              </button>
            </div>
            <div className="drawer-footer">
              <button className="drawer-item logout" onClick={handleLogout}>
                <span className="drawer-icon">🚪</span>
                退出登录
              </button>
            </div>
          </nav>
        </div>
      )}

      <main className="main">
        {currentPage === 'post' && <PostManagement token={token} />}
        {currentPage === 'myposts' && <MyPosts token={token} />}
        {currentPage === 'settings' && (
          <Settings token={token} accounts={accounts} onAccountsRefresh={fetchAccounts} />
        )}
      </main>
    </div>
  );
}
