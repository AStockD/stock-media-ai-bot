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
  { id: 'joinquant', name: '聚宽', icon: '📊', color: '#4CAF50' },
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
  const [posterLocalPath, setPosterLocalPath] = useState('');
  const [customStockMode, setCustomStockMode] = useState(false);
  const [customStockName, setCustomStockName] = useState('');
  const [llmOptimize, setLlmOptimize] = useState(true);
  const [llmOptimizing, setLlmOptimizing] = useState(false);
  const [trendDirection, setTrendDirection] = useState<'auto' | 'bullish' | 'bearish'>('auto');
  const [jqPostUrl, setJqPostUrl] = useState('');

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
    setCustomStockMode(false);
    setCustomStockName('');
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

  function handleCustomStockClick() {
    setCustomStockMode(true);
    setSelectedStrategy('');
    setSelectionRecords([]);
    setPostContent('');
    setGeneratedStock('');
    setPosterUrl('');
    setPosterLocalPath('');
  }

  async function handleCustomGenerate() {
    const name = customStockName.trim();
    if (!name) return;
    setAnalyzingCode('custom');
    setPosterLoading(true);
    setPosterUrl('');
    setPosterLocalPath('');
    try {
      const resp = await stockSelectionApi.analyzeQuery(`看看${name}`, token, name);
      let content = resp.summary;

      if (llmOptimize) {
        setLlmOptimizing(true);
        try {
          const optResp = await stockSelectionApi.optimizeContent(resp.summary, name, token, trendDirection, resp.raw_summary);
          content = optResp.optimized_summary;
          if (optResp.poster_url) {
            setPosterUrl(optResp.poster_url);
          }
          if (optResp.poster_local_path) {
            setPosterLocalPath(optResp.poster_local_path);
          }
        } catch { /* fallback to original content */ }
        finally { setLlmOptimizing(false); }
      }

      setPostContent(content);
      setGeneratedStock(name);

      // Fallback poster generation if LLM optimization didn't provide one
      if (!posterUrl) {
        const today = new Date().toISOString().split('T')[0];
        const titleMatch = resp.summary.match(/^##\s+(.+)$/m);
        const title = titleMatch ? titleMatch[1] : `${name}（${today}）`;
        try {
          const posterResp = await posterApi.generate(
            title,
            `分析一下${name}`,
            resp.raw_summary,
            'https://www.astockd.com',
            token,
          );
          if (posterResp.code === 200 && posterResp.data?.url) {
            setPosterUrl(posterResp.data.url);
          }
        } catch { /* poster generation failed, continue without image */ }
      }
    } catch { /* ignore */ }
    finally { setAnalyzingCode(''); setPosterLoading(false); }
  }

  async function handleAnalyze(record: StockSelectionRecord) {
    setAnalyzingCode(record.code);
    setPosterLoading(true);
    setPosterUrl('');
    setPosterLocalPath('');
    try {
      const resp = await stockSelectionApi.analyzeQuery(`看看${record.name}`, token, record.name);
      let content = resp.summary;

      if (llmOptimize) {
        setLlmOptimizing(true);
        try {
          const optResp = await stockSelectionApi.optimizeContent(resp.summary, record.name, token, trendDirection, resp.raw_summary);
          content = optResp.optimized_summary;
          if (optResp.poster_url) {
            setPosterUrl(optResp.poster_url);
          }
          if (optResp.poster_local_path) {
            setPosterLocalPath(optResp.poster_local_path);
          }
        } catch { /* fallback to original content */ }
        finally { setLlmOptimizing(false); }
      }

      setPostContent(content);
      setGeneratedStock(record.name);

      // Fallback poster generation if LLM optimization didn't provide one
      if (!posterUrl) {
        const today = new Date().toISOString().split('T')[0];
        const titleMatch = resp.summary.match(/^##\s+(.+)$/m);
        const title = titleMatch ? titleMatch[1] : `${record.name}（${today}）`;
        try {
          const posterResp = await posterApi.generate(
            title,
            `分析一下${record.name}这只股票`,
            resp.raw_summary,
            'https://www.astockd.com',
            token,
          );
          if (posterResp.code === 200 && posterResp.data?.url) {
            setPosterUrl(posterResp.data.url);
          }
        } catch { /* poster generation failed, continue without image */ }
      }
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
        if (pid === 'joinquant') {
          const resp = await platformApi.createComment(pid, postContent, token, undefined, jqPostUrl || undefined, generatedStock || undefined);
          results.push({
            platform: info?.name || pid,
            success: resp.success,
            message: resp.success ? (resp.message || 'Success') : (resp.error || 'Failed'),
          });
        } else {
          const resp = await platformApi.createPost(pid, postContent, token, posterUrl || undefined, posterLocalPath || undefined);
          results.push({
            platform: info?.name || pid,
            success: resp.success,
            message: resp.success ? (resp.message || 'Success') : (resp.error || 'Failed'),
          });
        }
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
            <button
              className={`strategy-btn custom ${customStockMode ? 'active' : ''}`}
              onClick={handleCustomStockClick}
            >
              自定义发帖
            </button>
          </div>

          {!customStockMode && selectedStrategy && (
            <div className="llm-opt-row">
              <label className="llm-opt-toggle">
                <input
                  type="checkbox"
                  checked={llmOptimize}
                  onChange={e => setLlmOptimize(e.target.checked)}
                />
                <span>LLM 优化</span>
              </label>
              {llmOptimize && (
                <div className="trend-select">
                  <button className={`trend-btn ${trendDirection === 'auto' ? 'active' : ''}`} onClick={() => setTrendDirection('auto')}>自动</button>
                  <button className={`trend-btn bullish ${trendDirection === 'bullish' ? 'active' : ''}`} onClick={() => setTrendDirection('bullish')}>看多</button>
                  <button className={`trend-btn bearish ${trendDirection === 'bearish' ? 'active' : ''}`} onClick={() => setTrendDirection('bearish')}>看空</button>
                </div>
              )}
            </div>
          )}

          {customStockMode && (
            <div className="custom-stock-area">
              <div className="custom-stock-input-row">
                <input
                  type="text"
                  className="custom-stock-input"
                  placeholder="输入股票名称，如：贵州茅台"
                  value={customStockName}
                  onChange={e => setCustomStockName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') handleCustomGenerate(); }}
                />
                <button
                  className="btn-generate"
                  onClick={handleCustomGenerate}
                  disabled={!customStockName.trim() || analyzingCode !== ''}
                >
                  {analyzingCode === 'custom' ? '...' : '生成'}
                </button>
              </div>
              <div className="llm-opt-row">
                <label className="llm-opt-toggle">
                  <input
                    type="checkbox"
                    checked={llmOptimize}
                    onChange={e => setLlmOptimize(e.target.checked)}
                  />
                  <span>LLM 优化</span>
                </label>
                {llmOptimize && (
                  <div className="trend-select">
                    <button className={`trend-btn ${trendDirection === 'auto' ? 'active' : ''}`} onClick={() => setTrendDirection('auto')}>自动</button>
                    <button className={`trend-btn bullish ${trendDirection === 'bullish' ? 'active' : ''}`} onClick={() => setTrendDirection('bullish')}>看多</button>
                    <button className={`trend-btn bearish ${trendDirection === 'bearish' ? 'active' : ''}`} onClick={() => setTrendDirection('bearish')}>看空</button>
                  </div>
                )}
              </div>
            </div>
          )}

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

          {!selectionLoading && selectedStrategy && !customStockMode && selectionRecords.length === 0 && (
            <p className="selection-empty">No records for today</p>
          )}

          {postContent && (
            <div className="sticky-bottom">
              <div className="generated-hint">
                {generatedStock} content ready
                {llmOptimizing && <span className="poster-loading"> · LLM 优化中...</span>}
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
                  {p.id === 'joinquant' && <span className="pc-badge">评论</span>}
                </label>
              );
            })}
          </div>

          {selectedPlatforms.has('joinquant') && (
            <div className="jq-url-row">
              <input
                type="text"
                className="jq-post-url-input"
                placeholder="聚宽帖子URL，如 https://www.joinquant.com/view/community/detail/xxx"
                value={jqPostUrl}
                onChange={e => setJqPostUrl(e.target.value)}
              />
            </div>
          )}

          <div className="sticky-bottom">
            <button
              className="btn btn-primary btn-block"
              onClick={handleSend}
              disabled={!postContent.trim() || selectedPlatforms.size === 0 || sending || (selectedPlatforms.has('joinquant') && !jqPostUrl.trim())}
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
