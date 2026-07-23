import { useState, useEffect } from 'react';
import { platformApi, type PlatformAccount } from '../api/client';

interface PlatformInfo {
  id: string;
  name: string;
  icon: string;
  color: string;
  loginType: 'qr' | 'password';
}

const PLATFORMS: PlatformInfo[] = [
  { id: 'xueqiu', name: '雪球', icon: '❄️', color: '#2196F3', loginType: 'qr' },
  { id: 'joinquant', name: '聚宽', icon: '📊', color: '#FF9800', loginType: 'password' },
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
  const [processedPiece, setProcessedPiece] = useState<string | null>(null);
  const [captchaDragX, setCaptchaDragX] = useState(0);
  const [captchaDragging, setCaptchaDragging] = useState(false);

  useEffect(() => {
    if (!captchaData) {
      setProcessedPiece(null);
      return;
    }
    const { hqImg } = captchaData;
    const pieceImg = new Image();
    pieceImg.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = pieceImg.width;
      canvas.height = pieceImg.height;
      const ctx = canvas.getContext('2d')!;
      ctx.drawImage(pieceImg, 0, 0);
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const data = imageData.data;
      for (let j = 0; j < data.length; j += 4) {
        const brightness = (data[j] + data[j + 1] + data[j + 2]) / 3;
        if (brightness < 40) {
          data[j + 3] = 0;
        }
      }
      ctx.putImageData(imageData, 0, 0);
      setProcessedPiece(canvas.toDataURL());
    };
    pieceImg.src = hqImg;
  }, [captchaData]);

  function getAccountStatus(platform: string): PlatformAccount | undefined {
    return accounts.find(a => a.platform === platform);
  }

  function getPlatformInfo(id: string): PlatformInfo {
    return PLATFORMS.find(p => p.id === id)!;
  }

  async function handleStartLogin() {
    const platformInfo = getPlatformInfo(selectedPlatform);
    setLoginActive(true);
    setQrImage('');
    setLoginMsg(platformInfo.loginType === 'password' ? 'Logging in...' : 'Starting browser...');
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
      } else if (resp.status === 'captcha_required' && resp.captcha_data) {
        setCaptchaData(resp.captcha_data);
        setCaptchaDragX(0);
        setLoginMsg(resp.message || 'Please solve the CAPTCHA');
      } else if (resp.status === 'success') {
        setLoginMsg(`Login success! ${resp.message || ''}`);
        setLoginActive(false);
        onAccountsRefresh();
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
    setCaptchaData(null);
    setLoginMsg('');
  }

  async function handleCaptchaValidate() {
    if (!captchaData) return;
    setLoginMsg('Validating CAPTCHA...');
    try {
      const resp = await platformApi.validateLoginCaptcha(token, captchaDragX);
      if (resp.status === 'success') {
        setCaptchaData(null);
        setLoginMsg(`Login success! ${resp.cookie_count} cookies saved`);
        setLoginActive(false);
        onAccountsRefresh();
      } else if (resp.status === 'captcha_required') {
        if (resp.captcha_data) {
          setCaptchaData(resp.captcha_data);
          setCaptchaDragX(0);
        }
        setLoginMsg(resp.message || 'CAPTCHA failed, please try again');
      } else if (resp.status === 'error') {
        setCaptchaData(null);
        setLoginMsg(`Error: ${resp.error || 'Unknown'}`);
        setLoginActive(false);
      }
    } catch (err: unknown) {
      setLoginMsg(`Error: ${err instanceof Error ? err.message : String(err)}`);
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
            {captchaData ? (
              <div className="captcha-area">
                <p className="captcha-hint">{loginMsg || 'Drag the puzzle piece to the correct position'}</p>
                <div className="captcha-container" style={{ position: 'relative', display: 'inline-block' }}>
                  <img
                    src={captchaData.bgImg}
                    alt="CAPTCHA background"
                    className="captcha-bg"
                    draggable={false}
                  />
                  <img
                    src={processedPiece || captchaData.hqImg}
                    alt="CAPTCHA piece"
                    className="captcha-piece"
                    draggable={false}
                    style={{
                      position: 'absolute',
                      left: captchaDragX,
                      top: captchaData.axisY || 0,
                    }}
                  />
                </div>
                <div className="captcha-slider" style={{ width: captchaData.bgImgW }}>
                  <div className="captcha-slider-track">
                    <div
                      className="captcha-slider-handle"
                      style={{ left: captchaDragX }}
                      onMouseDown={(e) => {
                        e.preventDefault();
                        setCaptchaDragging(true);
                        const startX = e.clientX;
                        const startDragX = captchaDragX;
                        const maxDrag = captchaData.bgImgW - 56;

                        const onMouseMove = (ev: MouseEvent) => {
                          const dx = ev.clientX - startX;
                          setCaptchaDragX(Math.max(0, Math.min(maxDrag, startDragX + dx)));
                        };
                        const onMouseUp = () => {
                          setCaptchaDragging(false);
                          document.removeEventListener('mousemove', onMouseMove);
                          document.removeEventListener('mouseup', onMouseUp);
                        };
                        document.addEventListener('mousemove', onMouseMove);
                        document.addEventListener('mouseup', onMouseUp);
                      }}
                    />
                  </div>
                </div>
                <div className="captcha-actions">
                  <button
                    className="btn btn-primary"
                    onClick={handleCaptchaValidate}
                    disabled={captchaDragging || captchaDragX === 0}
                  >
                    Submit
                  </button>
                  <button className="btn btn-outline" onClick={handleCancelLogin}>Cancel</button>
                </div>
              </div>
            ) : qrImage ? (
              <div className="qr-area">
                <img src={qrImage} alt="QR Code" className="qr-image" />
                <p className="qr-hint">{loginMsg}</p>
                <div className="qr-actions">
                  <button className="btn btn-secondary" onClick={handleStartLogin}>Refresh</button>
                  <button className="btn btn-outline" onClick={handleCancelLogin}>Cancel</button>
                </div>
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
