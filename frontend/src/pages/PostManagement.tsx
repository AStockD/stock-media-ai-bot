import { useState, useEffect, useCallback } from 'react';
import { platformApi, stockSelectionApi, posterApi, type PlatformAccount, type Strategy, type StockSelectionRecord } from '../api/client';
import KlineChart from './KlineChart';

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
}

interface PostResult {
  platform: string;
  success: boolean;
  message: string;
}

export default function PostManagement({ token }: Props) {
  const [step, setStep] = useState<1 | 2>(1);
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [selectedPlatforms, setSelectedPlatforms] = useState<Set<string>>(new Set());
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selectedStrategy, setSelectedStrategy] = useState('');
  const [selectionRecords, setSelectionRecords] = useState<StockSelectionRecord[]>([]);
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [postContent, setPostContent] = useState('');
  const [sending, setSending] = useState(false);
  const [postResults, setPostResults] = useState<PostResult[]>([]);
  const [analyzingCode, setAnalyzingCode] = useState('');
  const [generatedStock, setGeneratedStock] = useState('');
  const [expandedChart, setExpandedChart] = useState<string | null>(null);
  const [posterUrl, setPosterUrl] = useState('');
  const [posterLoading, setPosterLoading] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [accResp, stratResp] = await Promise.all([
        platformApi.getAccounts(token),
        stockSelectionApi.getStrategies(token),
      ]);
      setAccounts(accResp.accounts);
      setStrategies(stratResp.strategies);
    } catch { /* ignore */ }
  }, [token]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const validAccounts = accounts.filter(a => a.is_valid);

  function togglePlatform(id: string) {
    setSelectedPlatforms(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    setPostResults([]);
  }

  async function handleStrategyClick(strategyId: string) {
    setSelectedStrategy(strategyId);
    setSelectionLoading(true);
    setSelectionRecords([]);
    setPostContent('');
    setGeneratedStock('');
    try {
      const resp = await stockSelectionApi.getRecords(strategyId, token);
      setSelectionRecords(resp.records);
    } catch { /* ignore */ }
    finally { setSelectionLoading(false); }
  }

  async function handleAnalyze(record: StockSelectionRecord) {
    setAnalyzingCode(record.code);
    setPosterLoading(true);
    setPosterUrl('');
    try {
      const resp = await stockSelectionApi.analyzeQuery(`看看${record.name}`, token, record.name);
      setPostContent(resp.summary);
      setGeneratedStock(record.name);

      // Generate poster
      const today = new Date().toISOString().split('T')[0];
      const titleMatch = resp.summary.match(/^##\s+(.+)$/m);
      const title = titleMatch ? titleMatch[1] : `${record.name}（${today}）`;
      try {
        const posterResp = await posterApi.generate(
          title,
          `分析一下${record.name}这只股票`,
          resp.summary,
          'https://www.astockd.com',
          token,
        );
        if (posterResp.code === 200 && posterResp.data?.url) {
          setPosterUrl(posterResp.data.url);
        }
      } catch { /* poster generation failed, continue without image */ }
    } catch { /* ignore */ }
    finally { setAnalyzingCode(''); setPosterLoading(false); }
  }

  async function handleSend() {
    if (!postContent.trim() || selectedPlatforms.size === 0) return;
    setSending(true);
    setPostResults([]);
    const results: PostResult[] = [];
    for (const pid of selectedPlatforms) {
      const info = PLATFORMS.find(p => p.id === pid);
      try {
        const resp = await platformApi.createPost(pid, postContent, token, posterUrl || undefined);
        results.push({
          platform: info?.name || pid,
          success: resp.success,
          message: resp.success ? (resp.message || 'Success') : (resp.error || 'Failed'),
        });
      } catch (err: unknown) {
        results.push({
          platform: info?.name || pid,
          success: false,
          message: err instanceof Error ? err.message : String(err),
        });
      }
    }
    setPostResults(results);
    setSending(false);
    if (results.every(r => r.success)) {
      setPostContent('');
      setGeneratedStock('');
    }
  }

  return (
    <div className="post-mgmt">
      <div className="step-bar">
        <div className={`step-item ${step >= 1 ? 'active' : ''}`} onClick={() => setStep(1)}>
          <span className="step-num">1</span>
          <span className="step-label">选股生成</span>
        </div>
        <div className="step-line" />
        <div className={`step-item ${step >= 2 ? 'active' : ''}`}>
          <span className="step-num">2</span>
          <span className="step-label">确认发布</span>
        </div>
      </div>

      {step === 1 && (
        <div className="step-content">
          <div className="strategy-scroll">
            {strategies.map(s => (
              <button
                key={s.id}
                className={`strategy-btn ${selectedStrategy === s.id ? 'active' : ''}`}
                onClick={() => handleStrategyClick(s.id)}
              >
                {s.name}
              </button>
            ))}
          </div>

          {selectionLoading && <p className="selection-loading">Loading...</p>}

          {!selectionLoading && selectionRecords.length > 0 && (
            <div className="stock-cards">
              {selectionRecords.map((r, i) => (
                <div key={r.code + i} className="stock-card">
                  <div className="stock-card-top">
                    <div className="stock-info">
                      <span className="stock-name">{r.name}</span>
                      <span className="stock-code">{r.code}</span>
                    </div>
                    <div className="stock-price">
                      <button 
                        className={`btn-chart ${expandedChart === r.code ? 'active' : ''}`} 
                        onClick={() => setExpandedChart(expandedChart === r.code ? null : r.code)}
                      >K</button>
                      <span className="stock-price-val">{r.price}</span>
                      <span className={r.pct_change >= 0 ? 'pct-up' : 'pct-down'}>
                        {r.pct_change > 0 ? '+' : ''}{r.pct_change}%
                      </span>
                    </div>
                  </div>
                  <div className="stock-card-bottom">
                    <div className="stock-scores">
                      <span className="score-tag">Score <b>{r.overall_score}</b></span>
                      <span className="score-tag">Sent <b>{r.sentiment_norm}</b></span>
                      <span className="score-tag">Tick <b>{r.tick_norm}</b></span>
                      <span className="score-tag">Flow <b>{r.flow_norm}</b></span>
                    </div>
                    <button
                      className={`btn-generate ${analyzingCode === r.code ? 'loading' : ''}`}
                      onClick={() => handleAnalyze(r)}
                      disabled={analyzingCode !== ''}
                    >
                      {analyzingCode === r.code ? '...' : '生成'}
                    </button>
                  </div>
                  {expandedChart === r.code && (
                    <div className="stock-card-chart">
                      <KlineChart code={r.code} name={r.name} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {!selectionLoading && selectedStrategy && selectionRecords.length === 0 && (
            <p className="selection-empty">No records for today</p>
          )}

          {postContent && (
            <div className="sticky-bottom">
              <div className="generated-hint">
                {generatedStock} content ready
                {posterLoading && <span className="poster-loading"> · 生成海报中...</span>}
              </div>
              <button className="btn btn-primary btn-block" onClick={() => setStep(2)}>
                下一步
              </button>
            </div>
          )}
        </div>
      )}

      {step === 2 && (
        <div className="step-content">
          <div className="compose-area">
            <textarea
              className="compose-textarea"
              placeholder="Post content..."
              value={postContent}
              onChange={e => setPostContent(e.target.value)}
              rows={12}
            />
            <div className="compose-footer">
              <span className="char-count">{postContent.length} chars</span>
              <button className="btn-link" onClick={() => { setStep(1); setPosterUrl(''); }}>返回修改</button>
            </div>
          </div>

          {posterUrl && (
            <div className="poster-preview">
              <div className="poster-preview-header">海报预览</div>
              <img src={posterUrl} alt="Poster" className="poster-preview-image" />
            </div>
          )}

          <div className="platform-select-row">
            {PLATFORMS.map(p => {
              const acc = validAccounts.find(a => a.platform === p.id);
              const checked = selectedPlatforms.has(p.id);
              return (
                <label
                  key={p.id}
                  className={`platform-check-card ${checked ? 'checked' : ''} ${acc ? '' : 'disabled'}`}
                  style={{ '--platform-color': p.color } as React.CSSProperties}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={!acc}
                    onChange={() => togglePlatform(p.id)}
                  />
                  <span className="pc-icon">{p.icon}</span>
                  <span className="pc-name">{p.name}</span>
                </label>
              );
            })}
          </div>

          <div className="sticky-bottom">
            <button
              className="btn btn-primary btn-block"
              onClick={handleSend}
              disabled={!postContent.trim() || selectedPlatforms.size === 0 || sending}
            >
              {sending ? 'Sending...' : `发布到 ${selectedPlatforms.size} 个平台`}
            </button>
          </div>

          {postResults.length > 0 && (
            <div className="post-results">
              {postResults.map((r, i) => (
                <div key={i} className={`result-item ${r.success ? 'result-ok' : 'result-fail'}`}>
                  <span className="result-platform">{r.platform}</span>
                  <span className="result-msg">{r.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
