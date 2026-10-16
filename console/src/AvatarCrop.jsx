import { useEffect, useRef, useState } from 'react';

import {
  CROP_MIN_SIDE, CROP_VIEW_H, CROP_VIEW_W, containSize, cropGeometry,
  fileToCroppedJpeg, initialCropBox, moveCropBox, resizeCropBox,
} from './profile.js';

/* 换头像前的方形裁剪框（老板 2026-10-07：上一版"图片铺满取景框、拖着图片跑"被否了）。
   这一版是大家都眼熟的那种：照片**整张完整显示**（contain，最多 80vw × 70vh，深色留白），
   在照片上拖一个方框选要保留的部分 —— 拖框内=移动，拖四角=等比缩放（恒为正方形），
   框外压暗；「确定」→ canvas 按 1:1 导出 512×512 JPEG 再交给上传，「取消」→ 一个请求都不发。
   只用现成的东西：原生 canvas、原生 <input type=file>、原生 pointer 事件，不引第三方库。 */

export const CROP_CORNERS = ['nw', 'ne', 'sw', 'se'];   // 四角手柄，同时也是四种拖拽模式
const CORNER_STYLE = {
  nw: { left: 0, top: 0, cursor: 'nwse-resize' },
  ne: { right: 0, top: 0, cursor: 'nesw-resize' },
  sw: { left: 0, bottom: 0, cursor: 'nesw-resize' },
  se: { right: 0, bottom: 0, cursor: 'nwse-resize' },
};

/** 弹窗里照片能用的地方：宽 80vw、高 70vh（跟 CSS 上的上限同一组数）。 */
function viewBoxSize() {
  const vw = (typeof window !== 'undefined' && window.innerWidth) || 1280;
  const vh = (typeof window !== 'undefined' && window.innerHeight) || 800;
  return { w: Math.round(vw * CROP_VIEW_W), h: Math.round(vh * CROP_VIEW_H) };
}

export default function AvatarCrop({ file, busy = false, onCancel = () => {}, onConfirm = () => {} }) {
  const rootRef = useRef(null);
  const dragRef = useRef(null);
  const [url, setUrl] = useState('');
  const [imgSize, setImgSize] = useState(null);      // 照片原始像素 { w, h }
  const [box, setBox] = useState(null);              // 方框（单位：照片像素）
  const [vp, setVp] = useState(viewBoxSize);         // 照片能用的地方（屏幕像素）
  const [err, setErr] = useState('');
  const [working, setWorking] = useState(false);
  const lock = working || busy;

  /* 选中的文件 → 一个只在内存里的地址；组件一撤就把地址还回去，不留在页面上。
     照片一读出来就把方框摆好：正方形、短边 80%、居中。 */
  useEffect(() => {
    if (!file) {
      setUrl('');
      setImgSize(null);
      setBox(null);
      return undefined;
    }
    let objUrl = '';
    try { objUrl = URL.createObjectURL(file); } catch (e) { objUrl = ''; }
    setUrl(objUrl);
    setImgSize(null);
    setBox(null);
    setErr('');
    const probe = new Image();
    probe.onload = () => {
      const w = probe.naturalWidth || probe.width || 0;
      const h = probe.naturalHeight || probe.height || 0;
      setImgSize({ w, h });
      setBox(initialCropBox(w, h));
    };
    probe.onerror = () => setErr('这张图片打不开，换一张试试');
    probe.src = objUrl;
    return () => { if (objUrl) URL.revokeObjectURL(objUrl); };
  }, [file]);

  /* 打开时把焦点收进弹窗，好让 Esc / 回车管用；顺便别让背后的页面跟着滚。 */
  useEffect(() => {
    if (rootRef.current) {
      try { rootRef.current.focus(); } catch (e) { /* 拿不到焦点不影响用鼠标 */ }
    }
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  /* 窗口大小变了 → 照片重算显示尺寸；方框存在照片坐标系里，不用跟着改。 */
  useEffect(() => {
    const onResize = () => setVp(viewBoxSize());
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  /* 照片显示尺寸（整张 contain 进 80vw × 70vh）+ 方框对应的源矩形。
     disp.k = 屏幕上 1 像素等于照片的多少像素；两个都从方框算出来，画的就是要导出的那一块。 */
  const disp = imgSize ? containSize(imgSize.w, imgSize.h, vp.w, vp.h) : null;
  const view = imgSize && box ? cropGeometry(imgSize.w, imgSize.h, box) : null;

  /* 拖框内 = 移动；拖四角 = 等比缩放。都用 Pointer Events，鼠标和触屏同一套。 */
  const startDrag = (mode) => (e) => {
    if (!imgSize || !box || lock) return;
    e.preventDefault();
    e.stopPropagation();
    dragRef.current = { mode, px: e.clientX, py: e.clientY, box };
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err2) { /* 不支持就算了 */ }
  };

  const moveDrag = (e) => {
    const d = dragRef.current;
    if (!d || !imgSize || !disp) return;
    const dx = (e.clientX - d.px) / disp.k;          // 屏幕位移 → 照片像素
    const dy = (e.clientY - d.py) / disp.k;
    const minSide = CROP_MIN_SIDE / disp.k;          // 屏幕上最小 40px
    setBox(d.mode === 'move'
      ? moveCropBox(d.box, dx, dy, imgSize.w, imgSize.h)
      : resizeCropBox(d.box, d.mode, dx, dy, imgSize.w, imgSize.h, minSide));
  };

  const endDrag = () => { dragRef.current = null; };

  const doReset = () => {
    if (imgSize) setBox(initialCropBox(imgSize.w, imgSize.h));
  };

  const doConfirm = async () => {
    if (!file || !imgSize || !box || working || busy) return;
    setWorking(true);
    setErr('');
    try {
      const dataUrl = await fileToCroppedJpeg(file, cropGeometry(imgSize.w, imgSize.h, box));
      onConfirm(dataUrl);
    } catch (e) {
      setErr(String((e && e.message) || e || '这张图片裁不了，换一张试试'));
      setWorking(false);
    }
  };

  /* 方框在屏幕上的位置 / 边长（照片像素 → CSS 像素） */
  const k = disp ? disp.k : 1;
  const rl = view ? view.sx * k : 0;
  const rt = view ? view.sy * k : 0;
  const rs = view ? view.sw * k : 0;

  const onKey = (e) => {
    if (e.key === 'Escape') onCancel();
    if (e.key === 'Enter') doConfirm();
  };

  return (
    <div
      className="cropmodal"
      role="dialog"
      aria-modal="true"
      aria-label="裁剪头像"
      tabIndex={-1}
      ref={rootRef}
      onKeyDown={onKey}
    >
      <div className="cropmodal__mask" onClick={lock ? undefined : onCancel} />
      <div className="cropmodal__box card card--strong">
        <b className="card__title">裁剪头像</b>
        {/* 照片整张完整显示（contain，最多 80vw × 70vh）；方框就画在照片上，框外压暗 */}
        <div
          className={`cropmodal__stage${disp ? '' : ' cropmodal__stage--wait'}`}
          style={disp ? { width: `${disp.w}px`, height: `${disp.h}px` } : undefined}
        >
          <div className="cropmodal__photo">
            <img
              className="cropmodal__img"
              src={url || undefined}
              alt={url ? '选中的图片' : ''}
              draggable={false}
              style={{
                width: disp ? `${disp.w}px` : '100%',
                height: disp ? `${disp.h}px` : '100%',
                maxWidth: `${CROP_VIEW_W * 100}vw`,
                maxHeight: `${CROP_VIEW_H * 100}vh`,
              }}
            />
            {!url && <span className="cropmodal__wait">正在打开这张图片…</span>}
            {disp && view && (
              <>
                <span className="cropmodal__dim" style={{ left: 0, top: 0, right: 0, height: `${rt}px` }} />
                <span className="cropmodal__dim" style={{ left: 0, top: `${rt + rs}px`, right: 0, bottom: 0 }} />
                <span className="cropmodal__dim" style={{ left: 0, top: `${rt}px`, width: `${rl}px`, height: `${rs}px` }} />
                <span className="cropmodal__dim" style={{ left: `${rl + rs}px`, top: `${rt}px`, right: 0, height: `${rs}px` }} />
              </>
            )}
          </div>
          {/* 方框本身：拖里面=移动，拖四角的圆点=等比缩放 */}
          {disp && view && (
            <div
              className="cropmodal__rect"
              style={{ left: `${rl}px`, top: `${rt}px`, width: `${rs}px`, height: `${rs}px` }}
              onPointerDown={startDrag('move')}
              onPointerMove={moveDrag}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
            >
              {CROP_CORNERS.map((key) => (
                <span
                  key={key}
                  className={`cropmodal__dot cropmodal__dot--${key}`}
                  style={CORNER_STYLE[key]}
                  onPointerDown={startDrag(key)}
                />
              ))}
            </div>
          )}
        </div>
        <div className="cropmodal__bar">
          <span className="cropmodal__hint">拖动方框选择要保留的部分，拖四角可以放大缩小</span>
          <div className="cropmodal__acts">
            <button type="button" className="btn btn--ghost" disabled={lock || !imgSize} onClick={doReset}>
              重置
            </button>
            <button type="button" className="btn btn--ghost" disabled={lock} onClick={onCancel}>
              取消
            </button>
            <button
              type="button"
              className="btn btn--primary"
              disabled={lock || !imgSize}
              onClick={doConfirm}
            >
              {lock ? '处理中…' : '确定'}
            </button>
          </div>
        </div>
        {err && <span className="cropmodal__err">{err}</span>}
      </div>
    </div>
  );
}
