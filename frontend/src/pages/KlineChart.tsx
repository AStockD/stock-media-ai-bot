import { useEffect, useRef, useState, useCallback } from 'react';

declare const echarts: any;

interface Props {
  code: string;
  name: string;
}

type ChartType = 'kline' | 'trend';

function toSecid(code: string): string {
  const prefix = code.split('.')[0].toUpperCase();
  const num = code.split('.')[1];
  return prefix === 'SH' ? `1.${num}` : `0.${num}`;
}

let _jsonpCounter = 0;
function fetchJsonp(url: string): Promise<any> {
  return new Promise((resolve, reject) => {
    const cbName = '_ekcb_' + (++_jsonpCounter) + '_' + Date.now();
    const script = document.createElement('script');
    const timer = setTimeout(() => { cleanup(); reject(new Error('timeout')); }, 10000);

    function cleanup() {
      clearTimeout(timer);
      delete (window as any)[cbName];
      if (script.parentNode) script.parentNode.removeChild(script);
    }

    (window as any)[cbName] = (data: any) => { cleanup(); resolve(data); };
    script.src = `${url}&cb=${cbName}`;
    script.onerror = () => { cleanup(); reject(new Error('load failed')); };
    document.head.appendChild(script);
  });
}

const commonTooltip = {
  trigger: 'axis' as const,
  axisPointer: { type: 'cross' as const },
  backgroundColor: '#1a1d27',
  borderColor: '#2a2d3a',
  textStyle: { color: '#e4e4e7', fontSize: 12 },
};

export default function KlineChart({ code, name }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState<ChartType>('kline');
  const klineDataRef = useRef<string[] | null>(null);
  const trendDataRef = useRef<{ trends: string[]; preClose: number } | null>(null);

  const disposeChart = useCallback(() => {
    if (chartInstance.current) {
      chartInstance.current.dispose();
      chartInstance.current = null;
    }
  }, []);

  useEffect(() => {
    loadKlineData();
    loadTrendData();
    return () => disposeChart();
  }, [code]);

  useEffect(() => {
    if (activeTab === 'kline' && klineDataRef.current) {
      setLoading(false);
      setError('');
      renderKline(klineDataRef.current);
    } else if (activeTab === 'trend' && trendDataRef.current) {
      setLoading(false);
      setError('');
      renderTrend(trendDataRef.current.trends, trendDataRef.current.preClose);
    } else if (activeTab === 'kline' && !klineDataRef.current) {
      setLoading(true);
    } else if (activeTab === 'trend' && !trendDataRef.current) {
      setLoading(true);
    }
  }, [activeTab]);

  function loadKlineData() {
    const secid = toSecid(code);
    const url = `https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=${secid}&klt=101&fqt=1&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58&ut=b2884a393a59ad64002292a3e90d46a5&beg=20250101&end=20991231`;
    fetchJsonp(url)
      .then(data => {
        const klines: string[] = data?.data?.klines || [];
        klineDataRef.current = klines;
        if (activeTab === 'kline') {
          if (klines.length === 0) { setError('No data'); }
          else { renderKline(klines); }
          setLoading(false);
        }
      })
      .catch(() => { if (activeTab === 'kline') { setError('Failed to load'); setLoading(false); } });
  }

  function loadTrendData() {
    const secid = toSecid(code);
    const url = `https://push2his.eastmoney.com/api/qt/stock/trends2/get?secid=${secid}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&ut=7eea3edcaed734bea9cbfc24409ed989&ndays=1&iscr=0`;
    fetchJsonp(url)
      .then(data => {
        const trends: string[] = data?.data?.trends || [];
        const preClose = data?.data?.preClose ?? data?.data?.prePrice ?? 0;
        trendDataRef.current = { trends, preClose };
        if (activeTab === 'trend') {
          if (trends.length === 0) { setError('No data'); }
          else { renderTrend(trends, preClose); }
          setLoading(false);
        }
      })
      .catch(() => { if (activeTab === 'trend') { setError('Failed to load'); setLoading(false); } });
  }

  function renderKline(klines: string[]) {
    disposeChart();
    if (!chartRef.current || typeof echarts === 'undefined') return;

    const dates: string[] = [];
    const ohlc: number[][] = [];
    const closes: number[] = [];
    const volumes: number[] = [];

    for (const line of klines) {
      const [date, open, close, high, low, vol] = line.split(',').map((v, i) =>
        i === 0 ? v : parseFloat(v)
      );
      dates.push(date as string);
      ohlc.push([+open, +close, +low, +high]);
      closes.push(+close);
      volumes.push(+vol);
    }

    const calcMA = (period: number) => {
      const result: (number | null)[] = [];
      for (let i = 0; i < closes.length; i++) {
        if (i < period - 1) { result.push(null); continue; }
        let sum = 0;
        for (let j = 0; j < period; j++) sum += closes[i - j];
        result.push(+(sum / period).toFixed(2));
      }
      return result;
    };

    const ma5 = calcMA(5);
    const ma10 = calcMA(10);
    const ma20 = calcMA(20);

    const chart = echarts.init(chartRef.current, 'dark');
    chartInstance.current = chart;
    chart.setOption({
      backgroundColor: '#0f1117',
      animation: false,
      legend: {
        data: ['MA5', 'MA10', 'MA20'],
        top: 0,
        right: 60,
        textStyle: { color: '#71717a', fontSize: 10 },
        itemWidth: 14,
        itemHeight: 2,
      },
      tooltip: commonTooltip,
      grid: [
        { left: 50, right: 16, top: 24, height: '50%' },
        { left: 50, right: 16, top: '76%', height: '16%' },
      ],
      xAxis: [
        { type: 'category', data: dates, boundaryGap: true, axisLine: { lineStyle: { color: '#2a2d3a' } }, axisLabel: { color: '#71717a', fontSize: 10 }, splitLine: { show: false }, gridIndex: 0 },
        { type: 'category', data: dates, gridIndex: 1, axisLine: { lineStyle: { color: '#2a2d3a' } }, axisLabel: { show: false }, splitLine: { show: false } },
      ],
      yAxis: [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: '#2a2d3a', type: 'dashed' } }, axisLabel: { color: '#71717a', fontSize: 10 }, axisLine: { show: false } },
        { scale: true, gridIndex: 1, splitNumber: 2, splitLine: { show: false }, axisLabel: { color: '#71717a', fontSize: 10 }, axisLine: { show: false } },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
        { type: 'slider', xAxisIndex: [0, 1], bottom: 4, height: 16, borderColor: '#2a2d3a', fillerColor: 'rgba(59,130,246,0.15)', handleStyle: { color: '#3b82f6' }, textStyle: { color: '#71717a', fontSize: 10 } },
      ],
      series: [
        { name: 'K线', type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0, itemStyle: { color: '#ef4444', color0: '#22c55e', borderColor: '#ef4444', borderColor0: '#22c55e' } },
        { name: 'MA5', type: 'line', data: ma5, xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', lineStyle: { width: 2, color: '#f59e0b' }, smooth: true },
        { name: 'MA10', type: 'line', data: ma10, xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', lineStyle: { width: 2, color: '#3b82f6' }, smooth: true },
        { name: 'MA20', type: 'line', data: ma20, xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', lineStyle: { width: 2, color: '#a855f7' }, smooth: true },
        { name: '成交量', type: 'bar', data: volumes, xAxisIndex: 1, yAxisIndex: 1, itemStyle: { color: (p: any) => ohlc[p.dataIndex][1] >= ohlc[p.dataIndex][0] ? '#ef4444' : '#22c55e' } },
      ],
    });
    window.addEventListener('resize', chart.resize);
  }

  function renderTrend(trends: string[], preClose: number) {
    disposeChart();
    if (!chartRef.current || typeof echarts === 'undefined') return;

    const times: string[] = [];
    const prices: number[] = [];
    const avgPrices: number[] = [];
    const volumes: number[] = [];

    for (const t of trends) {
      const parts = t.split(',');
      if (parts.length < 7) continue;
      const datetime = parts[0];
      const time = datetime.includes(' ') ? datetime.split(' ')[1] : datetime;
      const price = parseFloat(parts[1]);
      const vol = parseFloat(parts[5]);
      const turnover = parseFloat(parts[6]);
      const apiVwap = parts.length > 7 ? parseFloat(parts[7]) : NaN;
      if (isNaN(price)) continue;
      times.push(time);
      prices.push(price);
      const vwap = !isNaN(apiVwap) ? apiVwap : (vol > 0 ? turnover / (vol * 100) : price);
      avgPrices.push(isNaN(vwap) ? price : vwap);
      volumes.push(isNaN(vol) ? 0 : vol);
    }

    if (times.length === 0) {
      setError('No trend data');
      setLoading(false);
      return;
    }

    const chart = echarts.init(chartRef.current, 'dark');
    chartInstance.current = chart;

    chart.setOption({
      backgroundColor: '#0f1117',
      animation: false,
      legend: {
        data: ['价格', '均价'],
        top: 0,
        right: 60,
        textStyle: { color: '#71717a', fontSize: 10 },
        itemWidth: 14,
        itemHeight: 2,
      },
      tooltip: {
        trigger: 'axis' as const,
        backgroundColor: '#1a1d27',
        borderColor: '#2a2d3a',
        textStyle: { color: '#e4e4e7', fontSize: 12 },
        formatter: (params: any) => {
          const idx = params[0]?.dataIndex;
          if (idx == null) return '';
          const p = prices[idx];
          const chg = preClose > 0 ? ((p - preClose) / preClose * 100).toFixed(2) : '-';
          const color = preClose > 0 ? (p >= preClose ? '#ef4444' : '#22c55e') : '#3b82f6';
          return `<div style="font-size:12px">
            <div>${times[idx]}</div>
            <div style="color:${color}">价格: ${p.toFixed(2)} (${chg}%)</div>
            <div>均价: ${avgPrices[idx].toFixed(2)}</div>
          </div>`;
        },
      },
      grid: [
        { left: 50, right: 16, top: 24, height: '50%' },
        { left: 50, right: 16, top: '76%', height: '16%' },
      ],
      xAxis: [
        { type: 'category', data: times, boundaryGap: false, axisLine: { lineStyle: { color: '#2a2d3a' } }, axisLabel: { color: '#71717a', fontSize: 10, interval: Math.floor(times.length / 4) }, splitLine: { show: false }, gridIndex: 0 },
        { type: 'category', data: times, gridIndex: 1, axisLine: { lineStyle: { color: '#2a2d3a' } }, axisLabel: { show: false }, splitLine: { show: false } },
      ],
      yAxis: [
        {
          scale: true, gridIndex: 0,
          splitLine: { lineStyle: { color: '#2a2d3a', type: 'dashed' } },
          axisLabel: { color: '#71717a', fontSize: 10, formatter: (v: number) => v.toFixed(2) },
          axisLine: { show: false },
        },
        { scale: true, gridIndex: 1, splitNumber: 2, splitLine: { show: false }, axisLabel: { color: '#71717a', fontSize: 10 }, axisLine: { show: false } },
      ],
      series: [
        {
          name: '价格', type: 'line', data: prices, xAxisIndex: 0, yAxisIndex: 0,
          symbol: 'none', lineStyle: { width: 1.5, color: '#3b82f6' },
          areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(59,130,246,0.15)' }, { offset: 1, color: 'rgba(59,130,246,0)' }] } },
          markLine: preClose > 0 ? {
            silent: true, symbol: 'none',
            lineStyle: { color: '#f59e0b', type: 'dashed', width: 1 },
            data: [{ yAxis: preClose }],
            label: { formatter: `昨收 ${preClose.toFixed(2)}`, color: '#f59e0b', fontSize: 10 },
          } : undefined,
        },
        {
          name: '均价', type: 'line', data: avgPrices, xAxisIndex: 0, yAxisIndex: 0,
          symbol: 'none', lineStyle: { width: 2, color: '#f59e0b', type: 'dashed' },
        },
        {
          name: '成交量', type: 'bar', data: volumes.map((v, i) => ({
            value: v,
            itemStyle: { color: i > 0 ? (prices[i] >= prices[i - 1] ? '#ef4444' : '#22c55e') : '#3b82f6' },
          })),
          xAxisIndex: 1, yAxisIndex: 1,
        },
      ],
    });
    window.addEventListener('resize', chart.resize);
  }

  return (
    <div className="kline-inline">
      <div className="kline-inline-header">
        <span className="kline-inline-title">{name} ({code})</span>
        <div className="kline-tabs">
          <button className={`kline-tab ${activeTab === 'kline' ? 'active' : ''}`} onClick={() => setActiveTab('kline')}>日K</button>
          <button className={`kline-tab ${activeTab === 'trend' ? 'active' : ''}`} onClick={() => setActiveTab('trend')}>分时</button>
        </div>
      </div>
      <div className="kline-inline-body">
        {loading && <p className="selection-loading">Loading...</p>}
        {error && <p className="selection-empty">{error}</p>}
        <div ref={chartRef} className="kline-inline-chart" />
      </div>
    </div>
  );
}
