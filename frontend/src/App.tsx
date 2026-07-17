import { useState, useEffect, useCallback } from 'react';
import { api, type CookieStatus, type LoginStartResponse } from './api/client';
import './App.css';

type PlatformId = 'xueqiu';

interface PlatformInfo {
  id: PlatformId;
  name: string;
  icon: string;
  color: string;
}

const PLATFORMS: PlatformInfo[] = [
  { id: 'xueqiu', name: '雪球', icon: '❄️', color: '#2196F3' },
];

export default function App() {
  const [cookieStatus, setCookieStatus] = useState<CookieStatus | null>(null);
  const [loginActive, setLoginActive] = useState(false);
  const [qrImage, setQrImage] = useState('');
  const [loginMsg, setLoginMsg] = useState('');
  const [selectedPlatform, setSelectedPlatform] = useState<PlatformId>('xueqiu');

  const fetchCookieStatus = useCallback(async () => {
    try {
      const status = await api.cookieStatus();
      setCookieStatus(status);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    fetchCookieStatus();
  }, [fetchCookieStatus]);

  async function handleStartLogin() {
    setLoginActive(true);
    setQrImage('');
    setLoginMsg('正在启动浏览器...');

    try {
      // Cancel any existing session first
      await api.cancelLogin();
      await new Promise(r => setTimeout(r, 500));

      const resp: LoginStartResponse = await api.startLogin();

      if (resp.status === 'waiting_for_scan' && resp.qr_image) {
        setQrImage(resp.qr_image);
        setLoginMsg(resp.message || '请使用雪球APP扫描二维码登录');
        pollLoginStatus();
      } else if (resp.status === 'error') {
        setLoginMsg(`错误: ${resp.error || '未知错误'}`);
        setLoginActive(false);
      } else {
        setLoginMsg(resp.message || resp.status);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setLoginMsg(`错误: ${msg}`);
      setLoginActive(false);
    }
  }

  async function pollLoginStatus() {
    const maxAttempts = 120;
    for (let i = 0; i < maxAttempts; i++) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const data = await api.loginStatus();

        if (data.status === 'success') {
          setLoginMsg(`登录成功! 已保存 ${data.cookie_count} 个 Cookie`);
          setQrImage('');
          setLoginActive(false);
          fetchCookieStatus();
          return;
        } else if (data.status === 'timeout') {
          setLoginMsg('登录超时，请重试');
          setQrImage('');
          setLoginActive(false);
          return;
        } else if (data.status === 'error') {
          setLoginMsg(`错误: ${data.error || '未知错误'}`);
          setQrImage('');
          setLoginActive(false);
          return;
        }
      } catch {
        // ignore polling errors
      }
    }
    setLoginMsg('登录超时');
    setLoginActive(false);
  }

  async function handleCancelLogin() {
    try {
      await api.cancelLogin();
    } catch {
      // ignore
    }
    setLoginActive(false);
    setQrImage('');
    setLoginMsg('');
  }

  const platform = PLATFORMS.find(p => p.id === selectedPlatform)!;

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <span className="header-icon">📈</span>
          <h1>Stock Media AI Bot</h1>
        </div>
        <div className="header-right">
          {cookieStatus?.valid && (
            <span className="cookie-badge">
              <span className="cookie-dot" />
              已登录 · {cookieStatus.cookie_count} cookies
            </span>
          )}
        </div>
      </header>

      <main className="main">
        <section className="platforms-section">
          <h2>媒体平台</h2>
          <div className="platforms-grid">
            {PLATFORMS.map(p => (
              <div
                key={p.id}
                className={`platform-card ${selectedPlatform === p.id ? 'selected' : ''}`}
                onClick={() => setSelectedPlatform(p.id)}
                style={{ '--platform-color': p.color } as React.CSSProperties}
              >
                <span className="platform-icon">{p.icon}</span>
                <span className="platform-name">{p.name}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="login-section">
          <div className="login-card">
            <div className="login-header">
              <span className="login-platform-icon">{platform.icon}</span>
              <div>
                <h3>{platform.name} 账号管理</h3>
                <p className="login-sub">
                  {cookieStatus?.valid
                    ? `上次更新: ${new Date(cookieStatus.last_updated!).toLocaleString('zh-CN')} (${cookieStatus.cookie_count} cookies)`
                    : '尚未登录'}
                </p>
              </div>
            </div>

            <div className="login-body">
              {qrImage ? (
                <div className="qr-area">
                  <img src={qrImage} alt="QR Code" className="qr-image" />
                  <p className="qr-hint">{loginMsg}</p>
                  <div className="qr-actions">
                    <button className="btn btn-secondary" onClick={handleStartLogin}>
                      刷新二维码
                    </button>
                    <button className="btn btn-outline" onClick={handleCancelLogin}>
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <div className="login-action-area">
                  <p className="login-msg">{loginMsg || '点击下方按钮启动扫码登录'}</p>
                  <button
                    className="btn btn-primary"
                    onClick={handleStartLogin}
                    disabled={loginActive}
                  >
                    {loginActive ? '登录中...' : '开始扫码登录'}
                  </button>
                </div>
              )}
            </div>
          </div>
        </section>

        <section className="status-section">
          <h2>Cookie 状态</h2>
          <div className="status-card">
            <div className="status-row">
              <span className="status-label">平台</span>
              <span className="status-value">{platform.icon} {platform.name}</span>
            </div>
            <div className="status-row">
              <span className="status-label">状态</span>
              <span className={`status-value ${cookieStatus?.valid ? 'status-ok' : 'status-warn'}`}>
                {cookieStatus?.valid ? '有效' : '未登录'}
              </span>
            </div>
            {cookieStatus?.valid && (
              <>
                <div className="status-row">
                  <span className="status-label">Cookie 数量</span>
                  <span className="status-value">{cookieStatus.cookie_count}</span>
                </div>
                <div className="status-row">
                  <span className="status-label">更新时间</span>
                  <span className="status-value">
                    {new Date(cookieStatus.last_updated!).toLocaleString('zh-CN')}
                  </span>
                </div>
              </>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}
