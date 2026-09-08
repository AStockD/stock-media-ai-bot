import { useState } from 'react';
import { platformApi, type PlatformAccount, type ZsxqGroup } from '../api/client';
import SlidingCaptcha from '../components/SlidingCaptcha';

interface PlatformInfo {
  id: string;
  name: string;
  icon: string;
  color: string;
  loginType: 'qr' | 'password' | 'oauth_device';
}

const PLATFORMS: PlatformInfo[] = [
  { id: 'xueqiu', name: '雪球', icon: '❄️', color: '#2196F3', loginType: 'qr' },
  { id: 'joinquant', name: '聚宽', icon: '📊', color: '#FF9800', loginType: 'password' },
  { id: 'zsxq', name: '知识星球', icon: '🪐', color: '#7B68EE', loginType: 'oauth_device' },
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
  const [jqUsername, setJqUsername] = useState('');
  const [jqPassword, setJqPassword] = useState('');
  const [oauthUri, setOauthUri] = useState('');
  const [oauthCode, setOauthCode] = useState('');
  const [zsxqGroups, setZsxqGroups] = useState<ZsxqGroup[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState('');
  const [groupSaving, setGroupSaving] = useState(false);
  const [captchaData, setCaptchaData] = useState<{
    bgImg: string;
    hqImg: string;
    bgImgW: number;
    bgImgH: number;
    blockW: number;
    blockH: number;
    point: number[];
    axisY: number;
  } | null>(null);
  const [captchaLoading, setCaptchaLoading] = useState(false);

  function getAccountStatus(platform: string): PlatformAccount | undefined {
    return accounts.find(a => a.platform === platform);
  }

  function getPlatformInfo(id: string): PlatformInfo {
    return PLATFORMS.find(p => p.id === id)!;
  }

  async function loadZsxqGroups(preferredGroupId?: string) {
    try {
      const resp = await platformApi.getZsxqGroups(token);
      setZsxqGroups(resp.groups || []);
      if (preferredGroupId) {
        setSelectedGroupId(preferredGroupId);
      } else if (resp.groups?.length === 1) {
        setSelectedGroupId(resp.groups[0].group_id);
      }
    } catch { /* ignore */ }
  }

  async function handleStartLogin() {
    const platformInfo = getPlatformInfo(selectedPlatform);
    setLoginActive(true);
    setQrImage('');
    setOauthUri('');
    setOauthCode('');
    setLoginMsg(
      platformInfo.loginType === 'password'
        ? 'Logging in...'
        : platformInfo.loginType === 'oauth_device'
          ? 'Starting OAuth...'
          : 'Starting browser...',
    );
    try {
      await platformApi.cancelLogin(selectedPlatform, token);
      await new Promise(r => setTimeout(r, 500));

      const credentials = platformInfo.loginType === 'password'
        ? { username: jqUsername, password: jqPassword }
        : undefined;

      const resp = await platformApi.startLogin(selectedPlatform, token, credentials);
      if (resp.status === 'waiting_for_scan' && resp.qr_image) {
        setQrImage(resp.qr_image);
        setLoginMsg(resp.message || 'Please scan QR code');
        pollLoginStatus();
      } else if (resp.status === 'waiting_for_auth') {
        setOauthUri(resp.verification_uri || '');
        setOauthCode(resp.user_code || '');
        setLoginMsg(resp.message || '请在浏览器完成授权');
        pollLoginStatus();
      } else if (resp.status === 'captcha_required' && resp.captcha_data) {
        setCaptchaData(resp.captcha_data);
        setLoginMsg(resp.message || 'Please solve the CAPTCHA');
      } else if (resp.status === 'success') {
        setLoginMsg(`Login success! ${resp.message || ''}`);
        setLoginActive(false);
        onAccountsRefresh();
        if (selectedPlatform === 'zsxq') {
          await loadZsxqGroups(resp.group_id);
        }
      } else if (resp.status === 'error') {
        setLoginMsg(`Error: ${resp.error || 'Unknown'}`);
        setLoginActive(false);
      } else {
        setLoginMsg(resp.message || resp.status);
        if (resp.status === 'logging_in') {
          pollLoginStatus();
        } else {
          setLoginActive(false);
        }
      }
    } catch (err: unknown) {
      setLoginMsg(`Error: ${err instanceof Error ? err.message : String(err)}`);
      setLoginActive(false);
    }
  }

  async function pollLoginStatus() {
    for (let i = 0; i < 120; i++) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const data = await platformApi.loginStatus(selectedPlatform, token);
        if (data.status === 'success') {
          setLoginMsg(`Login success! ${data.cookie_count ?? ''} credentials saved`);
          setQrImage('');
          setOauthUri('');
          setOauthCode('');
          setLoginActive(false);
          onAccountsRefresh();
          if (selectedPlatform === 'zsxq') {
            if (data.groups?.length) {
              setZsxqGroups(data.groups);
              if (data.group_id) setSelectedGroupId(data.group_id);
              else if (data.groups.length === 1) setSelectedGroupId(data.groups[0].group_id);
            } else {
              await loadZsxqGroups(data.group_id);
            }
          }
          return;
        } else if (data.status === 'timeout') {
          setLoginMsg('Login timeout, please retry');
          setQrImage('');
          setOauthUri('');
          setLoginActive(false);
          return;
        } else if (data.status === 'error') {
          setLoginMsg(`Error: ${data.error || 'Unknown'}`);
          setQrImage('');
          setOauthUri('');
          setLoginActive(false);
          return;
        } else if (data.status === 'waiting_for_auth') {
          if (data.verification_uri) setOauthUri(data.verification_uri);
          if (data.user_code) setOauthCode(data.user_code);
          setLoginMsg(data.message || loginMsg);
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
    setOauthUri('');
    setOauthCode('');
    setCaptchaData(null);
    setLoginMsg('');
  }

  async function handleSaveGroup() {
    if (!selectedGroupId) return;
    setGroupSaving(true);
    try {
      const g = zsxqGroups.find(x => x.group_id === selectedGroupId);
      const resp = await platformApi.setZsxqGroup(token, selectedGroupId, g?.name);
      if (resp.success) {
        setLoginMsg(`已选择星球：${resp.group_name || selectedGroupId}`);
        onAccountsRefresh();
      } else {
        setLoginMsg(`Error: ${resp.error || '保存失败'}`);
      }
    } catch (err: unknown) {
      setLoginMsg(`Error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setGroupSaving(false);
    }
  }

  async function handleCaptchaSubmit(axisX: number) {
    if (!captchaData) return;
    setCaptchaLoading(true);
    setLoginMsg('Validating CAPTCHA...');
    try {
      const resp = await platformApi.validateCaptcha(token, axisX, 'login');
      if (resp.status === 'success') {
        setCaptchaData(null);
        setLoginMsg(`Login success! ${resp.cookie_count} cookies saved`);
        setLoginActive(false);
        onAccountsRefresh();
      } else if (resp.status === 'captcha_required') {
        if (resp.captcha_data) {
          setCaptchaData(resp.captcha_data);
        }
        setLoginMsg(resp.message || 'CAPTCHA failed, please try again');
      } else if (resp.status === 'error') {
        setCaptchaData(null);
        setLoginMsg(`Error: ${resp.error || 'Unknown'}`);
        setLoginActive(false);
      }
    } catch (err: unknown) {
      setLoginMsg(`Error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setCaptchaLoading(false);
    }
  }

  const platform = getPlatformInfo(selectedPlatform);
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
                onClick={() => {
                  setSelectedPlatform(p.id);
                  setQrImage('');
                  setOauthUri('');
                  setLoginMsg('');
                  if (p.id === 'zsxq' && getAccountStatus('zsxq')?.is_valid) {
                    loadZsxqGroups();
                  }
                }}
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
            {captchaData ? (
              <SlidingCaptcha
                bgImg={captchaData.bgImg}
                hqImg={captchaData.hqImg}
                bgImgW={captchaData.bgImgW}
                bgImgH={captchaData.bgImgH}
                axisY={captchaData.axisY}
                onSubmit={handleCaptchaSubmit}
                onCancel={handleCancelLogin}
                loading={captchaLoading}
                message={loginMsg || undefined}
                variant="inline"
                removeDarkPixels
              />
            ) : qrImage ? (
              <div className="qr-area">
                <img src={qrImage} alt="QR Code" className="qr-image" />
                <p className="qr-hint">{loginMsg}</p>
                <div className="qr-actions">
                  <button className="btn btn-secondary" onClick={handleStartLogin}>Refresh</button>
                  <button className="btn btn-outline" onClick={handleCancelLogin}>Cancel</button>
                </div>
              </div>
            ) : oauthUri ? (
              <div className="login-action-area">
                <p className="login-msg">{loginMsg}</p>
                <p className="login-msg">
                  确认码：<strong>{oauthCode}</strong>
                </p>
                <a href={oauthUri} target="_blank" rel="noreferrer" className="btn btn-primary">
                  打开授权页面
                </a>
                <button className="btn btn-outline" onClick={handleCancelLogin} style={{ marginLeft: 8 }}>
                  Cancel
                </button>
              </div>
            ) : platform.loginType === 'password' ? (
              <div className="login-action-area">
                <div className="login-form">
                  <input
                    type="text"
                    className="login-input"
                    placeholder="Username / Phone"
                    value={jqUsername}
                    onChange={e => setJqUsername(e.target.value)}
                    disabled={loginActive}
                  />
                  <input
                    type="password"
                    className="login-input"
                    placeholder="Password"
                    value={jqPassword}
                    onChange={e => setJqPassword(e.target.value)}
                    disabled={loginActive}
                  />
                </div>
                <p className="login-msg">{loginMsg || 'Enter credentials to login'}</p>
                <button
                  className="btn btn-primary"
                  onClick={handleStartLogin}
                  disabled={loginActive || !jqUsername || !jqPassword}
                >
                  {loginActive ? 'Logging in...' : 'Login'}
                </button>
              </div>
            ) : platform.loginType === 'oauth_device' ? (
              <div className="login-action-area">
                <p className="login-msg">{loginMsg || '点击开始 OAuth 设备码授权（官方 zsxq-cli）'}</p>
                <button className="btn btn-primary" onClick={handleStartLogin} disabled={loginActive}>
                  {loginActive ? 'Waiting for auth...' : '授权登录知识星球'}
                </button>
                {accountStatus?.is_valid && (
                  <div className="login-form" style={{ marginTop: 16 }}>
                    <select
                      className="login-input"
                      value={selectedGroupId}
                      onChange={e => setSelectedGroupId(e.target.value)}
                      onFocus={() => { if (!zsxqGroups.length) loadZsxqGroups(); }}
                    >
                      <option value="">选择默认星球</option>
                      {zsxqGroups.map(g => (
                        <option key={g.group_id} value={g.group_id}>{g.name}</option>
                      ))}
                    </select>
                    <button
                      className="btn btn-secondary"
                      onClick={handleSaveGroup}
                      disabled={!selectedGroupId || groupSaving}
                      style={{ marginTop: 8 }}
                    >
                      {groupSaving ? 'Saving...' : '保存默认星球'}
                    </button>
                  </div>
                )}
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
