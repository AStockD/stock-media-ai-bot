import { useState } from 'react';
import { platformApi, type PlatformAccount } from '../api/client';

interface PlatformInfo {
  id: string;
  name: string;
  icon: string;
  color: string;
}

const PLATFORMS: PlatformInfo[] = [
  { id: 'xueqiu', name: '雪球', icon: '❄️', color: '#2196F3' },
];

interface Props {
  token: string;
  accounts: PlatformAccount[];
  onAccountsRefresh: () => void;
}

export default function Settings({ token, accounts, onAccountsRefresh }: Props) {
  const [selectedPlatform, setSelectedPlatform] = useState('xueqiu');
  const [qrImage, setQrImage] = useState('');
  const [loginMsg, setLoginMsg] = useState('');
  const [loginActive, setLoginActive] = useState(false);

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
          onAccountsRefresh();
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

  const platform = PLATFORMS.find(p => p.id === selectedPlatform)!;
  const accountStatus = getAccountStatus(selectedPlatform);

  return (
    <div className="settings-page">
      <section className="settings-section">
        <h3 className="section-title">Platforms</h3>
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

      <section className="settings-section">
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
    </div>
  );
}
