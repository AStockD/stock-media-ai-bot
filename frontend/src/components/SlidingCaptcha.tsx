import { useState, useEffect, useRef } from 'react';

interface SlidingCaptchaProps {
  bgImg: string;
  hqImg: string;
  bgImgW: number;
  bgImgH?: number;
  axisY?: number;
  onSubmit: (axisX: number) => void;
  onCancel?: () => void;
  onRefresh?: () => void;
  loading?: boolean;
  message?: string;
  variant?: 'inline' | 'modal';
  removeDarkPixels?: boolean;
}

export default function SlidingCaptcha({
  bgImg,
  hqImg,
  bgImgW,
  bgImgH = 142,
  axisY = 0,
  onSubmit,
  onCancel,
  onRefresh,
  loading = false,
  message,
  variant = 'inline',
  removeDarkPixels = false,
}: SlidingCaptchaProps) {
  const [dragX, setDragX] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [processedPiece, setProcessedPiece] = useState<string | null>(null);
  const startXRef = useRef(0);
  const startDragRef = useRef(0);

  useEffect(() => {
    if (!removeDarkPixels || !hqImg) {
      setProcessedPiece(null);
      return;
    }
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = img.width;
      canvas.height = img.height;
      const ctx = canvas.getContext('2d')!;
      ctx.drawImage(img, 0, 0);
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const d = imageData.data;
      for (let j = 0; j < d.length; j += 4) {
        const brightness = (d[j] + d[j + 1] + d[j + 2]) / 3;
        if (brightness < 40) {
          d[j + 3] = 0;
        }
      }
      ctx.putImageData(imageData, 0, 0);
      setProcessedPiece(canvas.toDataURL());
    };
    img.src = hqImg;
  }, [hqImg, removeDarkPixels]);

  useEffect(() => {
    setDragX(0);
  }, [bgImg, hqImg]);

  const maxDrag = bgImgW - 56;

  function handleMouseDown(e: React.MouseEvent) {
    e.preventDefault();
    setDragging(true);
    startXRef.current = e.clientX;
    startDragRef.current = dragX;

    const onMouseMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startXRef.current;
      setDragX(Math.max(0, Math.min(maxDrag, startDragRef.current + dx)));
    };
    const onMouseUp = () => {
      setDragging(false);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }

  function handleSubmit() {
    if (dragX > 0) {
      onSubmit(dragX);
    }
  }

  const isModal = variant === 'modal';

  const content = (
    <div className={`captcha-wrapper ${isModal ? 'captcha-modal' : 'captcha-inline'}`}>
      {isModal && <div className="captcha-overlay-bg" />}
      <div className={isModal ? 'captcha-modal-content' : 'captcha-area'}>
        {isModal && <h3 className="captcha-modal-title">请拖动滑块完成验证</h3>}
        {message && <p className="captcha-hint">{message}</p>}
        {!message && !isModal && <p className="captcha-hint">Drag the puzzle piece to the correct position</p>}

        <div className="captcha-container" style={{ position: 'relative', display: 'inline-block' }}>
          <img
            src={bgImg}
            alt="CAPTCHA background"
            className="captcha-bg"
            draggable={false}
            style={{ width: bgImgW, height: bgImgH }}
          />
          <img
            src={processedPiece || hqImg}
            alt="CAPTCHA piece"
            className="captcha-piece"
            draggable={false}
            style={{
              position: 'absolute',
              left: dragX,
              top: axisY,
            }}
          />
        </div>

        <div className="captcha-slider" style={{ width: bgImgW }}>
          <div className="captcha-slider-track">
            <div
              className="captcha-slider-handle"
              style={{ left: dragX }}
              onMouseDown={handleMouseDown}
            />
          </div>
        </div>

        <div className="captcha-actions">
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={dragging || dragX === 0 || loading}
          >
            {loading ? '...' : 'Submit'}
          </button>
          {onRefresh && (
            <button className="btn btn-outline" onClick={onRefresh} disabled={loading}>
              Refresh
            </button>
          )}
          {onCancel && (
            <button className="btn btn-outline" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
      </div>
    </div>
  );

  return content;
}
