/**
 * 外观与动态背景：把设置页那些项真正作用到界面上。
 *
 * 存哪：**服务器**（/api/config 里的 ui_* 键），本地只留一份镜像。
 * 2026-10-07 之前整份存 localStorage（as_appearance），老板报"换浏览器配置归 0"——
 * 换浏览器/换设备/清缓存就是一份新 localStorage，外观全没了。
 * 现在与 beginner_mode 一个做法：
 *   服务器给了值 → 以它为准（同时镜像到 localStorage，断网/未登录时还有得用）；
 *   服务器没值（老用户第一次上来）→ 用本地这份当值，登录后写上去一次（迁移，不丢设置）。
 * 草稿类（插件工坊 job_id、正在编辑的内容）故意留在本地，不上服务器。
 *
 * 强调色直接用 tokens.css 里已有的 [data-accent] 块（6 色调色板与桌面端 THEMES 一致）。
 */

export const ACCENT_KEYS = {
  深邃蓝: 'default',
  暗夜紫: 'midnight',
  赛博青: 'cyber',
  暖阳橙: 'sunset',
  薄荷绿: 'forest',
  '深空绿（新色板）': 'slate',
};

export const UI_THEME_KEYS = { 极光玻璃: 'glass', 瑞士极简: 'swiss', 深空终端: 'terminal' };
export const GLASS_KEYS = { '默认（原样）': 'default', 液态玻璃: 'liquid', 颗粒磨砂: 'frosted' };

/** 默认值与桌面端 settings 默认一致（遮罩 0.68 / 不透明度 70 / 亮度 0） */
export const APPEARANCE_DEFAULTS = {
  enabled: false,
  path: '',
  autoplay: false,
  loop: false,
  mask: 68,
  opacity: 70,
  bright: 0,
  animations: true,
  theme: '极光玻璃',
  accent: '深邃蓝',
  glass: '默认（原样）',
};

const STORE_KEY = 'as_appearance';

/* 本地外观 ↔ /api/config 的 ui_* 键的一一对应。
   为什么要两套键名：本地这份是 JS 里好用的短名，服务器那份要带 ui_ 前缀
   （避免和桌面端 settings.json 的通用键撞名，撞名会让控制台的外观改到桌面端去）。
   刻意**不含** any 密钥类字段：ui_* 全在 SAFE_KEYS 里，但都不是 SECRET_FIELDS。 */
const SERVER_KEY_MAP = {
  enabled: 'ui_bg_enabled',
  path: 'ui_bg_path',
  autoplay: 'ui_bg_autoplay',
  loop: 'ui_bg_loop',
  mask: 'ui_bg_mask',
  opacity: 'ui_bg_opacity',
  bright: 'ui_bg_bright',
  animations: 'ui_animations',
  theme: 'ui_theme',
  accent: 'ui_accent',
  glass: 'ui_glass',
};

/** 本地格式 → 服务器 payload（只带已知键，不把别的字段顺手传上去）。 */
export function appearanceToServer(ap = {}) {
  const out = {};
  for (const [local, remote] of Object.entries(SERVER_KEY_MAP)) {
    out[remote] = ap[local] === undefined ? APPEARANCE_DEFAULTS[local] : ap[local];
  }
  return out;
}

/** 服务器配置对象 → 本地格式。
 *  第二个返回字段 hasServer 表示"服务器上确实存过外观偏好"：
 *  /api/config 会把 SAFE_KEYS 里所有键都回一遍（没设过的键给 null/缺省），
 *  所以不能拿"有没有键"当判据 —— 得看有没有**任何一个** ui_* 键是真值。
 *  没有真值 = 老用户第一次上来 = 该把本地这份迁移上去。 */
export function appearanceFromServer(cfg) {
  if (!cfg || typeof cfg !== 'object') return { ap: null, hasServer: false };
  const ap = {};
  let hasServer = false;
  for (const [local, remote] of Object.entries(SERVER_KEY_MAP)) {
    const v = cfg[remote];
    if (v === undefined || v === null) continue;
    // 类型不对就当服务器没给（例如被人手改坏），别把字符串塞进滑杆
    if (typeof v !== typeof APPEARANCE_DEFAULTS[local]) continue;
    ap[local] = v;
    hasServer = true;
  }
  return { ap, hasServer };
}

export function loadAppearance() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return { ...APPEARANCE_DEFAULTS };
    return { ...APPEARANCE_DEFAULTS, ...JSON.parse(raw) };
  } catch {
    return { ...APPEARANCE_DEFAULTS };
  }
}

export function saveAppearance(ap) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(ap));
  } catch {
    /* 隐私模式/配额满时忽略：外观只是氛围层 */
  }
}

export function isVideoPath(p) {
  return /\.(mp4|webm|ogv|ogg|mov|m4v|mkv)$/i.test(String(p || '').trim());
}

/** 面板底色按「UI 不透明度」缩放：桌面端 70% 就是基准 1.0，范围 10~95 → 0.14~1.36 */
function panelAlphaScale(opacity) {
  const v = Number(opacity);
  const k = (Number.isFinite(v) ? v : 70) / 70;
  return Math.min(1.4, Math.max(0.15, k));
}

export function applyAppearance(ap) {
  const root = document.documentElement;
  root.dataset.accent = ACCENT_KEYS[ap.accent] || 'default';
  root.dataset.uiTheme = UI_THEME_KEYS[ap.theme] || 'glass';
  root.dataset.glass = GLASS_KEYS[ap.glass] || 'default';
  root.classList.toggle('reduce-motion', !ap.animations);

  const k = panelAlphaScale(ap.opacity);
  if ((UI_THEME_KEYS[ap.theme] || 'glass') === 'glass') {
    // 只有极光玻璃按「UI 不透明度」缩放面板 alpha。瑞士/终端的面板色由主题 CSS 决定，
    // 这里写内联变量会把主题色顶掉（内联样式优先级最高，之前就是这么被覆盖的）。
    root.style.setProperty('--as-panel', `rgba(17, 22, 34, ${(0.62 * k).toFixed(3)})`);
    root.style.setProperty('--as-panel-strong', `rgba(19, 26, 40, ${(0.78 * k).toFixed(3)})`);
  } else {
    root.style.removeProperty('--as-panel');
    root.style.removeProperty('--as-panel-strong');
  }
  root.style.setProperty('--as-bg-mask', String(Math.min(1, Math.max(0, Number(ap.mask) / 100))));
  root.style.setProperty('--as-bg-bright', String(1 + Number(ap.bright || 0) / 200));

  const hasBg = Boolean(ap.enabled && ap.path);
  document.body.classList.toggle('has-bg', hasBg);
}
