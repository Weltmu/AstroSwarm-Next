// 无头端控制台默认挂在根路径（FastAPI 直接托管 console-dist）。
// 预览版挂子路径时（nginx /preview/ → 反代 7860），构建时注入 VITE_API_BASE=/preview，
// 生产构建不带这个变量 → BASE 为空，行为与原来完全一致。
const BASE = (import.meta.env && import.meta.env.VITE_API_BASE) || '';

/* ── 控制台会话（2026-10-07 加的密码登录）──────────────────────────────────
   与控制台密码对应的会话 token。存两个地方是**故意的**：
     · localStorage   —— 勾了「记住这台设备 15 天」时用（关掉浏览器再开还在）；
     · sessionStorage —— 没勾时用（关掉浏览器就没了，等于 12 小时的短会话）。
   读的时候先 localStorage 再 sessionStorage；服务端才是过期时间的唯一裁判 ——
   这里存着但服务端说过期了，一样要重新输密码。

   ⚠ sessionStorage 在某些环境（Node 冒烟测试）里没有，所有访问都包 try。 */
const CONSOLE_TOKEN_KEY = 'as_console_token';

function readStore(store, key) {
  try {
    return store ? store.getItem(key) : null;
  } catch {
    return null;                 // 隐私模式/被禁用时不要抛，退化成"未登录"
  }
}

function writeStore(store, key, value) {
  try {
    if (store) store.setItem(key, value);
  } catch {
    /* 配额满/隐私模式：忽略，会话只在内存里活着也不致命 */
  }
}

function clearStore(store, key) {
  try {
    if (store) store.removeItem(key);
  } catch {
    /* 同上 */
  }
}

export function getConsoleToken() {
  return readStore(globalThis.localStorage, CONSOLE_TOKEN_KEY)
    || readStore(globalThis.sessionStorage, CONSOLE_TOKEN_KEY)
    || '';
}

/** 记住会话 token。remember=true 写 localStorage（15 天），否则只写 sessionStorage。 */
export function setConsoleToken(token, remember = true) {
  if (!token) {
    clearConsoleToken();
    return;
  }
  if (remember) {
    writeStore(globalThis.localStorage, CONSOLE_TOKEN_KEY, token);
    clearStore(globalThis.sessionStorage, CONSOLE_TOKEN_KEY);
  } else {
    writeStore(globalThis.sessionStorage, CONSOLE_TOKEN_KEY, token);
    clearStore(globalThis.localStorage, CONSOLE_TOKEN_KEY);
  }
}

export function clearConsoleToken() {
  clearStore(globalThis.localStorage, CONSOLE_TOKEN_KEY);
  clearStore(globalThis.sessionStorage, CONSOLE_TOKEN_KEY);
}

/* 「会话被服务端拒了」的通知口子。
   为什么要有：密码会话可能在**任何**一次请求上过期（服务端说了算），
   这时前端要立刻切回密码屏，而不是让每个页面各自显示"读不到"。
   App 挂一个回调进来，api.js 在收到 console_locked 时喊一声。 */
let onConsoleLocked = null;

export function setConsoleLockedHandler(fn) {
  onConsoleLocked = typeof fn === 'function' ? fn : null;
}

/** 这个错是不是「需要控制台密码」。 */
export function isConsoleLocked(err) {
  return Boolean(err && err.consoleLocked);
}

async function request(path, options = {}) {
  const token = localStorage.getItem('astroswarm_token');
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const consoleToken = getConsoleToken();
  if (consoleToken) headers['X-Console-Token'] = consoleToken;
  const res = await fetch(BASE + path, {
    headers,
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || data.error || `HTTP ${res.status}`);
    // 带上状态码：调用方要能分开处理「401 = 星群账号 token 已失效」与别的错
    // （控制台密码那一层另有 console_locked 标记，两条路不能混）。
    err.status = res.status;
    if (data.console_locked) {
      // 服务端说"要控制台密码"：立刻通知 App 切回密码屏（只在没验证过时才清 token，
      // 免得一个偶然的 401 把好端端的 15 天会话删掉）
      err.consoleLocked = true;
      if (onConsoleLocked) onConsoleLocked();
    }
    throw err;
  }
  return data;
}

export const get = (path) => request(path);
export const post = (path, body) =>
  request(path, { method: 'POST', body: JSON.stringify(body) });
export const put = (path, body) =>
  request(path, { method: 'PUT', body: JSON.stringify(body) });
// SSE（EventSource 不能自定义 header，也不吃 fetch 的封装）要自己拼完整地址
export const apiUrl = (path) => BASE + path;

/* 接口出错时给用户看的话（全站一套口径）。
   为什么要单独抽出来：后端还在同时补接口，网页先按契约写好了 ——
   这时请求会拿到 404（接口还没有）或 500（接口在但没数据）。
   直接把 "HTTP 500" / "Not Found" 甩到页面上是开发词，用户看不懂，
   也分不清"功能没了"和"服务暂时没回"。这里统一翻成人话：
     401/403 → 去登录
     404     → 服务器还没这个接口
     其它码  → 服务器暂时没回这条数据，稍后重试
   页面上一律显示成「读取中…」或「暂不可用…」，不白屏。 */
export function apiErrText(e, what = '暂不可用') {
  const m = String((e && e.message) || e || '').trim();
  if (isConsoleLocked(e)) {
    // 密码会话过期/没登录控制台：这句话要指向密码屏，不是星群账号
    return '控制台已锁定：请在密码屏重新输入控制台密码。';
  }
  if (/401|403|未授权|请先登录|未登录|token/i.test(m)) {
    return '需要先登录星群账号才读得到（去「设置」页登录）。';
  }
  if (/404|not found/i.test(m)) return `${what}：服务器还没这个功能。`;
  if (/^HTTP \d+$/.test(m)) return `${what}：服务器暂时没回这条数据。`;
  return `${what}：${m}`;
}

// 给「浏览器不能带 header」的请求用（EventSource 的日志流、<img> 的微信二维码）：
// 把登录 token 拼到查询串上。后端这两个接口同时接受 header 和 ?token=，
// 目的只是不让同一局域网里的其他人直接读到日志/扫码接管微信。
// 2026-10-07：控制台会话 token 同样要带上（后端这一层对 /api/** 全都要）。
export const authUrl = (path) => {
  const token = localStorage.getItem('astroswarm_token');
  const full = BASE + path;
  const params = [];
  if (token) params.push(`token=${encodeURIComponent(token)}`);
  const consoleToken = getConsoleToken();
  if (consoleToken) params.push(`console=${encodeURIComponent(consoleToken)}`);
  if (!params.length) return full;
  return `${full}${full.includes('?') ? '&' : '?'}${params.join('&')}`;
};
