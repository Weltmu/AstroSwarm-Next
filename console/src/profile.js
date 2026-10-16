import { useEffect, useSyncExternalStore } from 'react';

import { avatarSrc } from './AccountAvatar.jsx';
import { get, post } from './api';

/* 账号资料（昵称 / 头像）的唯一数据源。
   为什么要一个模块级的共享 store：头像与昵称要出现在**所有**显示账号的地方
   （侧栏、瑞士顶栏、深空图标栏、设置页账号卡），各处各拉一次会出现
   「顶栏还是旧头像、设置页已经是新头像」这种不一致。
   store 很小：一份状态 + 订阅；改完立刻广播，所有用到的地方同一帧更新。

   接口（后端 console_ext.py，未登录回 401「本机还没登录星群账号」）：
     GET  /api/account/profile   → {email, nickname, signature, display_name, plan, avatar}
                                   avatar 是 data URL，没头像时是空串；本机没有昵称/头像时
                                   后端会**顺手去官网拉一次**，前端只管调它，不用先点同步。
     POST /api/account/profile   {nickname} 改昵称（空串 = 恢复默认显示邮箱前缀）
                                 {avatar}   换头像（data:image/... 的 data URL）
     POST /api/account/refresh   强制从官网个人中心同步一次 */

const EMPTY = {
  profile: null,
  loading: false,
  error: '',
  loaded: false,
};

let state = { ...EMPTY };
let avatarTick = 0;                    // 头像地址防缓存用的小计数（见 patch）
const listeners = new Set();

function emit() {
  listeners.forEach((fn) => fn());
}

function patch(next) {
  /* 头像地址防缓存：后端给的是 data URL（内容换了 src 自然就换），
     万一是固定地址，就在写进 store 时补一个时间戳 —— 换完头像、从官网同步回来，
     界面上的图立刻是新的，不会拿着浏览器里那张旧的。 */
  if (next && next.profile) {
    const src = String(next.profile.avatar || '');
    if (src && !/^data:/i.test(src)) {
      avatarTick += 1;
      state = { ...state, ...next, profile: { ...next.profile, avatar: avatarSrc(src, `${Date.now()}-${avatarTick}`) } };
      emit();
      return;
    }
  }
  state = { ...state, ...next };
  emit();
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function snapshot() {
  return state;
}

/* 动作（改昵称/换头像/同步）失败时给用户看的话：后端给了中文原因就原样转达，
   其余（HTTP 500、Failed to fetch 这类）一律换成人话 —— 不把开发词甩到界面上。
   ⚠ 这里以前把带「登录」的错误一律翻成「先登录星群账号，再改昵称或头像。」，
   于是**已经登录**的人（顶栏写着已登录）看到账号卡上说"先登录"，自相矛盾。 */
function hintOf(e) {
  const text = String((e && e.message) || e || '').trim();
  if (/[\u4e00-\u9fff]/.test(text)) return text;
  if (/401|403|未授权|请先登录|未登录/.test(text)) return '登录状态过期了，重新登录一次。';
  return '刚才那步没成功，稍后再试一次。';
}

/* 读资料（GET /api/account/profile）失败时卡上那句话：中性、可重试，
   绝不跟顶栏的「已登录」打架（老板看到的就是这种自相矛盾）。 */
function readHint(e) {
  const text = String((e && e.message) || e || '').trim();
  if (/[\u4e00-\u9fff]/.test(text) && !/登录/.test(text)) return text;
  return '账号资料没读到，重新登录或点「从官网同步」再试一次。';
}

/* 「星群账号登录状态过期了」的通知口子（2026-10-10 修老板截图里那处自相矛盾）。
   为什么要有：本地 localStorage 里还留着 token（顶栏芯片据此说「已登录 · permanent」），
   但服务端那份已经失效 —— 读资料接口回 401。这时**必须把本地 token 清掉**，
   否则同一页上「已登录」和「未登录」会同时出现（账号卡写「未登录」、芯片写「已登录」）。
   App 挂一个回调进来：清 token + 给一句「登录状态已过期，请重新登录」。 */
let onAccountExpired = null;

export function setAccountExpiredHandler(fn) {
  onAccountExpired = typeof fn === 'function' ? fn : null;
}

/** 这个错是不是「星群账号 token 已失效」。
    只看 401，且**排除**控制台密码那一层（那是密码屏的事，不能顺手把账号 token 也清了）。 */
export function isAccountExpired(e) {
  if (!e || e.consoleLocked) return false;
  if (Number(e.status) === 401) return true;
  // 老后端/被中转过的响应可能没有 status：按后端那句中文认（api.js 会把 detail 包成 message）
  return /登录已过期|本机还没登录|请先登录/.test(String((e && e.message) || e));
}

/** 每个 catch 都先过一道：token 失效就地清掉，返回值原样给回调用方。 */
function noteExpired(e) {
  if (isAccountExpired(e) && onAccountExpired) onAccountExpired();
  return e;
}

/* 读一次资料。silent = 后台自己读的（焦点回来、每 60 秒那次）：
   失败就当没这回事 —— 不留错误提示、不摆「读取中」，上一次的资料原样留着，
   用户看到的最坏情况就是"还没变"，绝不会白屏或弹红字。
   ⚠ 唯一的例外是 401（token 失效）：silent 也必须处理，否则界面会一直顶着「已登录」。 */
export async function loadProfile({ silent = false } = {}) {
  if (!silent) patch({ loading: true });
  try {
    const data = await get('/api/account/profile');
    patch({ profile: data, loading: false, error: '', loaded: true });
    return data;
  } catch (e) {
    noteExpired(e);
    if (silent) return null;
    patch({ loading: false, error: readHint(e) });
    return null;
  }
}

export async function saveNickname(nickname) {
  try {
    const data = await post('/api/account/profile', { nickname: String(nickname || '') });
    patch({ profile: data, error: '' });
    await loadProfile({ silent: true });     // 存完立刻再读一次，界面钉在服务端那份上
    return { ok: true, message: String(nickname || '').trim() ? '昵称已改好' : '已恢复成默认显示（邮箱前缀）' };
  } catch (e) {
    noteExpired(e);
    const message = hintOf(e);
    patch({ error: message });
    return { ok: false, message };
  }
}

export async function sendAvatar(dataUrl) {
  try {
    /* 换头像按契约走 POST /api/account/profile 的 {avatar} 字段。
       两层保险（只为"点了必须真生效、且不误伤昵称"）：
       1) 把当前昵称一起带上 —— 后端若还停在"这个接口只认 nickname"那一版，
          传同样的昵称是空操作，不会把用户的昵称清空；
       2) 返回里没看到新头像（说明后端当没看见这个字段）就退回老的换头像接口，
          别让用户点了「换头像」却什么都没发生、界面还报"已换好"。 */
    const keepNick = String((state.profile && state.profile.nickname) || '');
    const before = String((state.profile && state.profile.avatar) || '');
    let data = null;
    try {
      data = await post('/api/account/profile', { avatar: dataUrl, nickname: keepNick });
    } catch {
      data = null;
    }
    if (!data || !String(data.avatar || '') || String(data.avatar) === before) {
      try {
        data = await post('/api/account/avatar', { avatar: dataUrl });
      } catch (e) {
        if (!data) throw e;          // 两条路都没成：如实报错
        /* 否则是主接口已经存下了（只是返回的图和旧的一样），这次退路失败不算数 */
      }
    }
    patch({ profile: data, error: '' });
    await loadProfile({ silent: true });     // 换完立刻再读一次：新头像当帧就换上去
    return { ok: true, message: '头像已换好' };
  } catch (e) {
    noteExpired(e);
    const message = hintOf(e);
    patch({ error: message });
    return { ok: false, message };
  }
}

export async function syncProfileFromSite() {
  patch({ loading: true });
  try {
    const data = await post('/api/account/refresh', {});
    patch({ profile: data, loading: false, error: '' });
    await loadProfile({ silent: true });     // 同步完立刻再读一次，官网那次改动不留尾巴
    return { ok: true, message: '已从官网同步最新的昵称与头像' };
  } catch (e) {
    noteExpired(e);
    const message = hintOf(e);
    patch({ loading: false, error: message });
    return { ok: false, message };
  }
}

/** 该不该现在去读一次资料：没 token 就清空（未登录去读只会白报 401，卡片改说引导话）；
    有 token 时，除非正在读，否则 force（进「设置 → 账号」、刚登录完）或还没读过就读一次。 */
export function ensureProfile(loggedIn, force = false) {
  if (!loggedIn) {
    if (state.profile || state.error || state.loaded) patch({ ...EMPTY });
    return;
  }
  if (state.loading) return;
  if (!force && state.profile && state.loaded) return;
  loadProfile();
}

/**
 * 把用户选的图片压成正方形 JPEG 的 data URL（和后端约定一致）。
 * 做法：按短边居中裁成正方形 → 缩到 512×512 → toDataURL('image/jpeg', 0.85)。
 * 512 的正方形 JPEG 一般 30~120KB，稳稳低于「建议 ≤512KB」这条线，当头像也够清楚。
 */
export function fileToSquareJpeg(file, size = 512, quality = 0.85) {
  return new Promise((resolve, reject) => {
    if (!file) {
      reject(new Error('没有选到图片'));
      return;
    }
    if (!String(file.type || '').startsWith('image/')) {
      reject(new Error('这个文件不是图片，换一张图片试试'));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('这张图片读不出来，换一张试试'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('这张图片打不开，换一张试试'));
      img.onload = () => {
        const side = Math.min(img.width || 0, img.height || 0);
        if (!side) {
          reject(new Error('这张图片尺寸读不到，换一张试试'));
          return;
        }
        const canvas = document.createElement('canvas');
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          reject(new Error('这台设备不支持压缩图片，换个浏览器试试'));
          return;
        }
        // 白底：PNG 透明区域压成 JPEG 会变黑，先铺白
        ctx.fillStyle = '#FFFFFF';
        ctx.fillRect(0, 0, size, size);
        const sx = (img.width - side) / 2;
        const sy = (img.height - side) / 2;
        ctx.drawImage(img, sx, sy, side, side, 0, 0, size, size);
        try {
          resolve(canvas.toDataURL('image/jpeg', quality));
        } catch (e) {
          reject(new Error(`图片压缩失败：${e.message}`));
        }
      };
      img.src = String(reader.result || '');
    };
    reader.readAsDataURL(file);
  });
}

/* ---------------- 照片上的方形裁剪框（老板 2026-10-07：上一版"图片铺满取景框、
   拖着图片跑"太丑，也不是他习惯的那种）----------------
   这一版：弹窗里**照片整张完整显示**（contain，最多 80vw × 70vh，深色留白），
   用户在照片上拖一个方框选要保留的部分 —— 拖框内=移动，拖四角=等比缩放（恒为正方形），
   框外压暗；确认后 canvas 按 1:1 导出 512×512 JPEG（质量 0.9）。
   下面几个函数是纯计算 / 纯导出，冒烟里可以直接喂一张 600×1300 的长图，
   验「初始方框是正方形且在照片里」「移动、缩放后仍是正方形且不越界」「导出的图宽高相等」。 */
export const CROP_SIZE = 512;          // 导出边长：正好是显示尺寸的 4 倍，够清楚
export const CROP_QUALITY = 0.9;
export const CROP_MAX_BYTES = 512 * 1024;
export const CROP_INIT_RATIO = 0.8;    // 初始方框边长 = 照片短边的 80%
export const CROP_MIN_SIDE = 40;       // 方框最小边长（屏幕像素：别缩成一个点）
export const CROP_VIEW_W = 0.8;        // 照片显示上限：宽 80vw
export const CROP_VIEW_H = 0.7;        // 照片显示上限：高 70vh

/** 照片在弹窗里的显示尺寸：整张按 contain 缩，最多 maxW × maxH —— 一条 6:13 的长图
    就是一条竖着的**完整**照片，任何一边都不会被裁掉；比上限小的小图最多放大 3 倍（不糊成马赛克）。 */
export function containSize(iw, ih, maxW, maxH, upscale = 3) {
  const w = Math.max(1, Number(iw) || 0);
  const h = Math.max(1, Number(ih) || 0);
  const mw = Math.max(1, Number(maxW) || 0);
  const mh = Math.max(1, Number(maxH) || 0);
  const k = Math.min(mw / w, mh / h, upscale > 0 ? upscale : 1);
  return { w: Math.round(w * k), h: Math.round(h * k), k };
}

/** 初始方框：正方形，边长 = 照片短边的 80%，居中（单位：照片像素）。 */
export function initialCropBox(iw, ih) {
  const w = Math.max(1, Math.round(Number(iw) || 0));
  const h = Math.max(1, Math.round(Number(ih) || 0));
  const side = Math.max(1, Math.round(Math.min(w, h) * CROP_INIT_RATIO));
  return { x: Math.round((w - side) / 2), y: Math.round((h - side) / 2), side };
}

/** 把方框夹回照片里：边长先卡在 [1, 短边]，再整体挪进照片 —— 框永远不会超出照片边界。 */
export function clampCropBox(box, iw, ih) {
  const w = Math.max(1, Number(iw) || 0);
  const h = Math.max(1, Number(ih) || 0);
  const short = Math.min(w, h);
  const want = Number(box && box.side);
  const side = Math.min(short, Math.max(1, Number.isFinite(want) && want > 0 ? want : short * CROP_INIT_RATIO));
  const bx = Number(box && box.x);
  const by = Number(box && box.y);
  const x = Number.isFinite(bx) ? bx : (w - side) / 2;
  const y = Number.isFinite(by) ? by : (h - side) / 2;
  return { x: Math.min(Math.max(0, x), w - side), y: Math.min(Math.max(0, y), h - side), side };
}

/** 拖框内：整框跟着指针走（dx/dy 单位是照片像素），越界就贴在照片边上，不会跑出去。 */
export function moveCropBox(box, dx, dy, iw, ih) {
  const b = clampCropBox(box, iw, ih);
  return clampCropBox({ x: b.x + (Number(dx) || 0), y: b.y + (Number(dy) || 0), side: b.side }, iw, ih);
}

/** 拖四角：等比缩放，恒为正方形；对角钉在原地不动，最小 minSide、最大不超过照片。
    corner = 'nw' | 'ne' | 'sw' | 'se'（拖哪个角，就由那个角带大 / 带小）。 */
export function resizeCropBox(box, corner, dx, dy, iw, ih, minSide = 1) {
  const w = Math.max(1, Number(iw) || 0);
  const h = Math.max(1, Number(ih) || 0);
  const b = clampCropBox(box, iw, ih);
  const ddx = Number(dx) || 0;
  const ddy = Number(dy) || 0;
  // 沿对角方向取位移：拖角是"斜着走"的，单看某一轴会让手感变得很怪
  const grow = { se: (ddx + ddy) / 2, nw: -(ddx + ddy) / 2,
    ne: (ddx - ddy) / 2, sw: (ddy - ddx) / 2 }[corner];
  if (!Number.isFinite(grow)) return b;
  const right = b.x + b.side;
  const bottom = b.y + b.side;
  // 对角固定 → 能长到多大由"固定角到照片边"决定
  const maxSide = Math.min(Math.min(w, h), {
    se: Math.min(w - b.x, h - b.y),
    nw: Math.min(right, bottom),
    ne: Math.min(w - b.x, bottom),
    sw: Math.min(right, h - b.y),
  }[corner]);
  const lo = Math.min(maxSide, Math.max(1, Number(minSide) || 1));
  const side = Math.min(maxSide, Math.max(lo, b.side + grow));
  const left = (corner === 'nw' || corner === 'sw') ? right - side : b.x;
  const top = (corner === 'nw' || corner === 'ne') ? bottom - side : b.y;
  return clampCropBox({ x: left, y: top, side }, w, h);
}

/** 方框 → canvas 的源矩形。sw 恒等于 sh —— 这是「1:1」的唯一来源。
    没给方框就退化成"居中、短边 80%"的初始方框；越界一律夹回照片里，整块必然落在图上。 */
export function cropGeometry(iw, ih, box) {
  const w = Math.max(1, Math.round(Number(iw) || 0));
  const h = Math.max(1, Math.round(Number(ih) || 0));
  const b = clampCropBox(box && Number(box.side) > 0 ? box : initialCropBox(w, h), w, h);
  const side = Math.max(1, Math.min(Math.round(b.side), w, h));
  return {
    sx: Math.min(Math.max(0, Math.round(b.x)), w - side),
    sy: Math.min(Math.max(0, Math.round(b.y)), h - side),
    sw: side,
    sh: side,
  };
}

/** 按方框把图片导成正方形 JPEG 的 data URL。没给方框就退化成"居中、短边 80%"的初始方框。 */
export function fileToCroppedJpeg(file, box, size = CROP_SIZE, quality = CROP_QUALITY) {
  return new Promise((resolve, reject) => {
    if (!file) {
      reject(new Error('没有选到图片'));
      return;
    }
    if (!String(file.type || '').startsWith('image/')) {
      reject(new Error('这个文件不是图片，换一张图片试试'));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('这张图片读不出来，换一张试试'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('这张图片打不开，换一张试试'));
      img.onload = () => {
        const iw = img.naturalWidth || img.width || 0;
        const ih = img.naturalHeight || img.height || 0;
        if (!iw || !ih) {
          reject(new Error('这张图片尺寸读不到，换一张试试'));
          return;
        }
        const want = Number(box && box.sw) || 0;
        const g = want > 0
          ? { sx: Number(box.sx) || 0, sy: Number(box.sy) || 0, sw: want, sh: Number(box.sh) || want }
          : cropGeometry(iw, ih);
        // 边长再跟图片边界取一次交：保证 drawImage 的源矩形整块都在图里、且一定是正方形
        const side = Math.max(1, Math.min(g.sw, g.sh, iw, ih));
        const sx = Math.min(Math.max(0, g.sx), iw - side);
        const sy = Math.min(Math.max(0, g.sy), ih - side);
        const canvas = document.createElement('canvas');
        canvas.width = size;              // 宽高同值 —— 导出的图必然 1:1
        canvas.height = size;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          reject(new Error('这台设备不支持裁剪图片，换个浏览器试试'));
          return;
        }
        ctx.fillStyle = '#FFFFFF';        // 白底：PNG 透明区域压成 JPEG 会变黑
        ctx.fillRect(0, 0, size, size);
        ctx.imageSmoothingEnabled = true;
        try { ctx.imageSmoothingQuality = 'high'; } catch (e) { /* 老浏览器不认就随它去 */ }
        ctx.drawImage(img, sx, sy, side, side, 0, 0, size, size);
        try {
          let out = canvas.toDataURL('image/jpeg', quality);
          // 万一这张图特别"花"，压一档再来，保证不超过 512KB 这条线
          if (out.length * 0.75 > CROP_MAX_BYTES) out = canvas.toDataURL('image/jpeg', 0.72);
          resolve(out);
        } catch (e) {
          reject(new Error('图片裁剪失败，换一张试试'));
        }
      };
      img.src = String(reader.result || '');
    };
    reader.readAsDataURL(file);
  });
}

export function useProfile() {
  const snap = useSyncExternalStore(subscribe, snapshot, snapshot);
  return { ...snap, reload: () => loadProfile() };
}

/* ---------------- 自动同步（老板 2026-10-06：任意一端改完，另一端别手动刷新） ----------------
   三个时机去读一次 GET /api/account/profile：
     1) 窗口重新获得焦点（刚从浏览器别处/官网切回来）；
     2) 标签页切回可见；
     3) 每 60 秒一跳。
   ⚠ 只在「已登录 + 页面可见」时跑：没登录一次都不打，页面一藏起来就停表 ——
     不在后台空转，也不把浏览器拖卡。读的时候一律 silent，失败不打扰用户。
   返回一个「停止」函数：组件卸载时调用，定时器与两个监听一起摘干净。 */
export const PROFILE_POLL_MS = 60000;

export function watchProfile(loggedIn, intervalMs = PROFILE_POLL_MS) {
  if (typeof window === 'undefined' || typeof document === 'undefined') return () => {};
  let timer = 0;
  const stopTimer = () => { if (timer) { clearInterval(timer); timer = 0; } };
  const visible = () => document.visibilityState !== 'hidden';
  const read = () => {
    if (!loggedIn || !visible()) return;
    loadProfile({ silent: true });
  };
  const startTimer = () => {
    if (!loggedIn || timer || !visible()) return;
    timer = setInterval(read, intervalMs);
  };
  const onFocus = () => read();
  const onVisible = () => {
    if (!visible()) { stopTimer(); return; }   // 切走 → 停表
    read();                                    // 切回来 → 立刻读一次
    startTimer();
  };
  window.addEventListener('focus', onFocus);
  document.addEventListener('visibilitychange', onVisible);
  startTimer();
  return () => {
    stopTimer();
    window.removeEventListener('focus', onFocus);
    document.removeEventListener('visibilitychange', onVisible);
  };
}

/** 把 watcher 挂到组件上：登录态一变就重挂，卸载自动清定时器（不漏内存、不留后台请求）。 */
export function useProfileAutoSync(loggedIn) {
  useEffect(() => watchProfile(Boolean(loggedIn)), [loggedIn]);
}
