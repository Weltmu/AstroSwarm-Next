import { useCallback, useEffect, useRef, useState } from 'react';
import { apiErrText, apiUrl, authUrl, get, post, put,
  clearConsoleToken, isConsoleLocked, setConsoleLockedHandler } from './api.js';
import MemoryTimeline from './MemoryTimeline.jsx';
import KnowledgeBase from './KnowledgeBase.jsx';
import { RecentMessages, StatsPanel, TodayStats } from './BrainStats.jsx';
import AccountAvatar from './AccountAvatar.jsx';
import AccountProfileBlock from './AccountProfileBlock.jsx';
import BotAiCard from './BotAiCard.jsx';
import WorkshopPage from './WorkshopPage.jsx';
import ConsolePasswordCard from './ConsolePasswordCard.jsx';
import { setupSteps } from './SetupSteps.jsx';
import { ADVANCED_PAGES, NAV_GROUPS } from './nav.js';
import { versionLabel } from './version.js';
import { setIdea } from './workshopIdea.js';
import { ensureProfile, setAccountExpiredHandler, useProfile, useProfileAutoSync } from './profile.js';
import QqChannelCard from './QqChannelCard.jsx';
import QqOfficialCard from './QqOfficialCard.jsx';
import FeishuCard, { feishuBadge } from './FeishuCard.jsx';
import TelegramCard from './TelegramCard.jsx';
import LogRetention from './LogRetention.jsx';
import LoginBanner from './LoginBanner.jsx';
import ConsoleLogin from './ConsoleLogin.jsx';
import LoginModal, { LoginForm } from './LoginModal.jsx';
/* 统一控件库（2026-10-12 老板要求：开关不像 iOS、数值太乱、按钮要胶囊）
   —— 全部零依赖，纯 React + 原生 CSS，见 src/components/ 与 src/components.css */
import Toggle, { ToggleRow } from './components/Toggle.jsx';
import PillButton from './components/PillButton.jsx';
import Collapse from './components/Collapse.jsx';
import NumberField from './components/NumberField.jsx';
import MarketPanel from './MarketPanel.jsx';
import MachineCapabilities from './MachineCapabilities.jsx';
import ZipInstall from './ZipInstall.jsx';
import {
  APPEARANCE_DEFAULTS,
  applyAppearance,
  appearanceFromServer,
  appearanceToServer,
  isVideoPath,
  loadAppearance,
  saveAppearance,
} from './appearance.js';

/* 统一线性图标集：24×24 viewBox、stroke-width 1.5、round 端点/拐角、fill:none、stroke:currentColor。
   每个 name 对应「若干段 path」，一个图形拆成几笔，线条粗细与视觉比重才一致
   （之前每个图标一条长度不等的复杂路径，粗细/圆角/比重各不相同，看着就是「瞎做的」）。
   语义分配（任何两个导航项都不共用图标）：
   home 首页 · plug 接入（把通道插进来）· brain AI 大脑 · puzzle 插件管理 ·
   server 全局管理（服务/进程机架）· chat 消息中心 · file-text 日志（文档）·
   package 依赖（包裹箱）· gear 设置（齿轮）· user 星群账号（人像）；
   ⚠ 账号 ≠ 设置：「星群账号」入口（侧栏 .sidebar-account / 瑞士顶栏 .topnav__account /
   深空图标栏 .rail__account）一律用 user，gear 只留给「设置」那一条，任何两项都不共用图标。
   通道图标：qq QQ（企鹅）· wechat 微信（双气泡）· plane 飞书 · planeTilt 纸飞机 ·
   terminal 终端窗口 / check 完成勾（供其它页面调用，保持可用）。 */
const ICONS = {
  home: [
    'M3.5 10.6 12 3.6l8.5 7',
    'M5.8 9.4V19a1.4 1.4 0 0 0 1.4 1.4h9.6A1.4 1.4 0 0 0 18.2 19V9.4',
    'M9.8 20.4v-5.2h4.4v5.2',
  ],
  plug: [
    'M9 3v5',
    'M15 3v5',
    'M6 8h12v6.4a3.6 3.6 0 0 1-3.6 3.6h-4.8A3.6 3.6 0 0 1 6 14.4V8Z',
    'M12 18v3.4',
  ],
  brain: [
    'M12 4.6C10.6 2.6 7.6 3 7 5 4.6 4.8 3.2 6.6 3.6 8.6 2.2 9.8 2.4 12.2 4.2 13.2 4 15.6 5.8 17.4 8 17 8.4 19 10.6 19.8 12 19.2',
    'M12 4.6C13.4 2.6 16.4 3 17 5 19.4 4.8 20.8 6.6 20.4 8.6 21.8 9.8 21.6 12.2 19.8 13.2 20 15.6 18.2 17.4 16 17 15.6 19 13.4 19.8 12 19.2',
    'M12 4.6v14.6',
    'M9.6 8.9c-1.3 0-2 1.5-1.1 2.5',
    'M14.4 8.9c1.3 0 2 1.5 1.1 2.5',
  ],
  chat: [
    'M21 15.6a2 2 0 0 1-2 2H8.4L4 21.2V5.6a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2Z',
    'M8.6 9.4h7.4',
    'M8.6 13h4.4',
  ],
  wechat: [
    'M12 10.6a2 2 0 0 1-2 2H6.6L3.6 15.2V5.6a2 2 0 0 1 2-2h4.4a2 2 0 0 1 2 2Z',
    'M14.4 16.8a1.6 1.6 0 0 1 1.6 1.6h3.6L21.2 21V11.4a1.6 1.6 0 0 1-1.6-1.6H16a1.6 1.6 0 0 1-1.6 1.6Z',
  ],
  qq: [
    'M12 3.6C15.6 3.6 18.4 7 18.4 11.2c0 3-1 6.2-2.6 8.2-.5.6-1.2.9-2 .9H10.2c-.8 0-1.5-.3-2-.9-1.6-2-2.6-5.2-2.6-8.2C5.6 7 8.4 3.6 12 3.6Z',
    'M12 11.4c1.8 0 3.3 1.5 3.3 3.4 0 1.6-.7 3-1.6 4.2H10.3c-.9-1.2-1.6-2.6-1.6-4.2C8.7 12.9 10.2 11.4 12 11.4Z',
    'M11.3 9.6 12 10.4 12.7 9.6Z',
    'M9.9 7.45a0.75 0.75 0 0 1 0 1.5 0.75 0.75 0 0 1 0-1.5Z',
    'M14.1 7.45a0.75 0.75 0 0 1 0 1.5 0.75 0.75 0 0 1 0-1.5Z',
  ],
  puzzle: [
    'M9.6 4a2.4 2.4 0 0 1 4.8 0H19a1 1 0 0 1 1 1v4.6a2.4 2.4 0 0 0 0 4.8V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4.6a2.4 2.4 0 0 0 0-4.8V5a1 1 0 0 1 1-1h4.6Z',
  ],
  /* sparkle：四角星 + 一颗小星（工坊：让 AI 生成东西）。与其它图标同规格：
     24×24、stroke 1.5、round、fill:none、currentColor。 */
  sparkle: [
    'M11 3.5 12.6 8.4 17.5 10 12.6 11.6 11 16.5 9.4 11.6 4.5 10 9.4 8.4 11 3.5Z',
    'M17.6 15.2 18.4 17.6 20.8 18.4 18.4 19.2 17.6 21.6 16.8 19.2 14.4 18.4 16.8 17.6 17.6 15.2Z',
  ],
  server: [
    'M5 4.5h14A1.5 1.5 0 0 1 20.5 6v2.5a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 8.5V6A1.5 1.5 0 0 1 5 4.5Z',
    'M5 14.5h14a1.5 1.5 0 0 1 1.5 1.5v2.5a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18.5V16A1.5 1.5 0 0 1 5 14.5Z',
    'M5.9 6.2a1 1 0 0 1 0 2 1 1 0 0 1 0-2Z',
    'M5.9 16.2a1 1 0 0 1 0 2 1 1 0 0 1 0-2Z',
  ],
  'file-text': [
    'M6.5 3.5h7L17 7v12a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 5 19V5a1.5 1.5 0 0 1 1.5-1.5Z',
    'M13.5 3.5V7H17',
    'M8 12h8',
    'M8 15.6h5',
  ],
  package: [
    'M20 8.4 12 4 4 8.4v7.2L12 20l8-4.4V8.4Z',
    'M4 8.4 12 12.8l8-4.4',
    'M12 12.8V20',
  ],
  terminal: [
    'M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z',
    'M3 8.6h18',
    'M7 12.4l2.4 2.2-2.4 2.2',
    'M12.6 16.8h4',
  ],
  /* 账号：一个头 + 一段肩弧（最外层落在 4.25–19.75，1–23 安全区内不溢出） */
  user: [
    'M12 4.6a3.5 3.5 0 0 1 0 7 3.5 3.5 0 0 1 0-7Z',
    'M5 19.4a7 7 0 0 1 14 0',
  ],
  gear: [
    'M8.4 12a3.6 3.6 0 0 1 7.2 0 3.6 3.6 0 0 1-7.2 0Z',
    'M17.6 12h2',
    'M14.8 16.85 15.8 18.58',
    'M9.2 16.85 8.2 18.58',
    'M6.4 12h-2',
    'M9.2 7.15 8.2 5.42',
    'M14.8 7.15 15.8 5.42',
  ],
  plane: [
    'M21 3 14.7 21 11.1 12.9 3 9.3 21 3Z',
    'M21 3 11.1 12.9',
  ],
  planeTilt: [
    'M21 21 14.7 3 11.1 11.1 3 14.7 21 21Z',
    'M21 21 11.1 11.1',
  ],
  check: ['M4.5 12.5 9.6 17.6 19.5 6.6'],
};

/* 页面右上角的版本号：从 GET /api/status 现取（Linux 无头端自己的版本，0.x 那一串），
   取不到再看 /api/config，两处都没有就写「版本读取中」。
   逻辑在 version.js 里（冒烟要直接 import 它，见 _smoke/smoke.jsx）。 */

/* ---------------------------------------------------------------- 依赖任务
   后台扫描 / 安装由无头端的 console_ext 跑在子线程里（一个请求挂几十秒会被 nginx 掐），
   所以控制台这边用 1.2s 轮询 /api/deps/status 拿进度；返回最终状态对象。 */
const DEPS_POLL_MS = 1200;
const DEPS_POLL_MAX = 300; // 最多 6 分钟

/** 写接口（扫描/安装/重启）要鉴权：未登录时后端回「请先登录」，这里补一句去哪儿登录 */
function withAuthHint(err) {
  const m = String((err && err.message) || err);
  return m.includes('登录') ? `${m}：请先在「设置 → 星群账号」登录，扫描/安装/重启都需要鉴权` : m;
}

/* 未登录 / 无权限的统一判据。
   为什么要把 403 也算进来：公开预览路径（/pv<token>/）下 nginx 对 /api/tools/status、
   /api/plugins/installed 直接 return 403（不落到后端），未登录时这两个请求拿到的是
   「HTTP 403」而不是后端的「请先登录」—— 只看 401 会把它们当成普通故障弹红字。 */
function isAuthError(err) {
  const m = String((err && err.message) || '');
  return m.includes('请先登录') || m.includes('未登录') || m.includes('401') || m.includes('403');
}

/** 「读不到真实数据」时的统一说法。
    ⚠ 不要把话说死成「你没登录」：公开预览路径 /pv<token>/ 下 nginx 会对
    /api/tools/status、/api/plugins/installed 直接 return 403（不落到后端），
    已经登录也会拿到 403 —— 所以这里说的是「读不到 + 先去登录」，
    并把「登录了还读不到」这个第二种可能一起写出来，不让用户白折腾。 */
const LOGIN_HINT =
  '读不到真实数据：请先登录（侧栏「星群账号」或 设置 → 账号）；登录后仍然读不到，就是这个访问地址没放开该接口';

async function runDeps(kind, onStatus) {
  try {
    await post(`/api/deps/${kind}`, {});
  } catch (e) {
    throw new Error(withAuthHint(e));
  }
  for (let i = 0; i < DEPS_POLL_MAX; i += 1) {
    const st = await get('/api/deps/status');
    if (onStatus) onStatus(st);
    if (!st.running) return st;
    await new Promise((r) => setTimeout(r, DEPS_POLL_MS));
  }
  throw new Error('依赖任务超时（超过 6 分钟）');
}
/* ── 权益字段（后端 /api/auth/status 与 /api/plugins/entitlement）────
     member      = 账号档位（月费 / 季费 / 年费 / 永久都算）
     all_plugins = 历史字段（能力包已全部免费，运行时不再按它放行）
     full        = 旧字段，语义已收窄成「解锁全部付费包」= all_plugins，保留兼容旧后端

   2026-10-01 起：**插件、能力包、微信通道全部免费**，前端不再用这些字段决定
   "某个功能能不能用"，只拿 plan 在账号卡片上显示一个档位名字。
   判「登录了没有」一律看 auth.email —— 不要再用 member / full 当功能开关。 */
const isMember = (a) => Boolean(a?.member) || Boolean(a?.all_plugins) || Boolean(a?.full);
const isFull = (a) => Boolean(a?.all_plugins) || Boolean(a?.full);

/* ── 「登录后才有真值」的两个显示助手（2026-10-07 老板报的 bug）─────────────
   老板原话："换浏览器会出现先前配置归 0……有的页面甚至把默认值当成已配置，属于误导"。
   机理：/api/config 要登录才给，未登录时 config 是 null，而这些地方原来写的是
   `config?.bot_port || health?.bot_port || 12113` —— 于是**未登录的界面顶着
   一个像模像样的端口号**，用户以为那就是他的配置。

   规矩（两条）：能读到服务器值 → 显示真值；读不到 → 显示「登录后显示」，
   绝不摆一个默认值冒充真值。端口这种"看起来像真的"的数字尤其不能编。 */
const NEED_LOGIN = '登录后显示';

/** 端口：只有拿到 config/health 里的值才显示数字，否则如实说"登录后显示"。 */
function portText(config, health) {
  const p = config?.bot_port ?? health?.bot_port;
  return (p === 0 || p) ? String(p) : NEED_LOGIN;
}

/** 某个配置项是"已配置 / 未配置"：没登录就没有资格下这个结论 —— 说"登录后显示"。 */
function configuredText(loggedIn, value) {
  if (!loggedIn) return NEED_LOGIN;
  return String(value || '').trim() ? '已配置' : '未配置';
}

/** 某个配置项的字面值；没登录时同样不摆空值（空值会被读成"我没填"）。 */
function configText(loggedIn, value, fallback = '未填') {
  if (!loggedIn) return NEED_LOGIN;
  return String(value || '').trim() || fallback;
}

/** 写剪贴板：clipboard API 在非安全上下文（http 且非 localhost）不可用，兜底用 execCommand。 */async function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand('copy');
  document.body.removeChild(ta);
  if (!ok) throw new Error('浏览器不给写剪贴板');
}

/**
 * 无头端的「打开 XX」一律改成「复制路径」：浏览器里没有服务器文件管理器，
 * 但把路径粘到 SSH / SFTP 里就是最顺手的做法。makePath 里现取现拼，保证是最新值。
 */
async function copyServerPath(setMsg, what, makePath) {
  try {
    const p = await makePath();
    await copyToClipboard(p);
    setMsg(`已复制${what}：${p}（服务器上的路径，粘到终端或 SFTP 里用）`);
  } catch (e) {
    setMsg(`复制${what}失败：${e.message}`);
  }
}

/** bot 的 .env 路径：/api/services/status 里带着 bot_dir。 */
async function botEnvPath() {
  const s = await get('/api/services/status');
  const dir = s && s.bot && s.bot.bot_dir;
  if (!dir) throw new Error('没拿到 bot 目录（机器人还没部署过）');
  return `${dir}/.env`;
}

/** 插件目录：/api/plugins/installed 里带着 dir。 */
async function pluginsDirPath() {
  const d = await get('/api/plugins/installed');
  if (!d || !d.dir) throw new Error('没拿到插件目录');
  return d.dir;
}

/* 导航按角色分组（不再按「平台 / 全局」分层）：
   运行 = 连通道看健康；能力 = 人格与工具；系统 = 服务、记录与设置。
   组标题小字次要色；条目沿用原来的图标与 .nav-item 样式。
   图标语义一一对应、互不重复：首页 home / 接入 plug（多通道合并页，不再用微信图标）
   / AI 大脑 brain / 插件管理 puzzle / 全局管理 server（服务与进程）/ 消息中心 chat
   / 日志 file-text（文档，不再和全局管理共用 terminal）/ 依赖 package / 设置 gear。 */
/* 导航结构（4 组）在 nav.js 里 —— 与桌面端一一对应，测试直接读那份。 */
const BEGINNER_KEY = 'as_beginner_mode';

/* UI v2 重排后，styles.css 自动区那批「按页钉死」的定位规则（.page > section:nth-child(n) …）
   是按旧 DOM 的层级数出来的，新 DOM 一旦命中就会把高度/位移钉错地方。
   换一个页名，让旧选择器整体不命中 —— 自动区一个字节都不用改，旧页面的版式也不受影响。 */
const PAGE_KEY = {
  home: 'home-v2',
  access: 'access-v2',
  services: 'services-v2',
  plugins: 'plugins-v2',
  workshop: 'workshop-v2',
  settings: 'settings-v2',
};

/* 图标渲染：尺寸只有两档 —— 侧栏/内容区 18、顶栏/标题栏 16（调用点显式传 size）。
   规格钉死：24×24 viewBox、stroke 1.5、round、fill:none、currentColor。
   className="icon" 让 svg 变块级并在 flex 里 align-self 居中（inline svg 默认带基线偏移，
   看起来会偏下 1–2px），保证同一排按钮里的图标光学对齐。 */
function Icon({ name, size = 18 }) {
  const paths = ICONS[name] || ICONS.package;
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {paths.map((d, i) => (
        <path key={i} d={d} />
      ))}
    </svg>
  );
}

function StatusChip({ ok, warn, children }) {
  const cls = warn ? 'chip chip--warn' : ok ? 'chip chip--ok' : 'chip chip--err';
  return <span className={cls}>{children}</span>;
}

/** 桌面端 StatusBadge：9px 色点（带同色辉光）+ 13px 文案 */
function StatusBadge({ tone = 'unknown', children }) {
  return (
    <span className={`badge badge--${tone}`}>
      <i className="badge__dot" />
      <span className="badge__label">{children}</span>
    </span>
  );
}

function Group({ title, desc, children }) {
  return (
    <section className="group">
      <header className="group__head">
        <h2>{title}</h2>
        {desc && <p>{desc}</p>}
      </header>
      <div className="group__body">{children}</div>
    </section>
  );
}

function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

export default function App() {
  const [view, setView] = useState('home');
  // 导航 4 组的展开状态：默认全收起（子项收在组里，点标题才展开）；
  // 当前页所在的组永远展开，见 groupOpen()。
  const [openGroups, setOpenGroups] = useState({});
  const [detailPid, setDetailPid] = useState('');
  const [detailEntry, setDetailEntry] = useState(null);
  const [beginner, setBeginner] = useState(() => localStorage.getItem(BEGINNER_KEY) !== '0');
  // 真正的「已登录」判据是本地有没有 token：/api/auth/status 无论登录与否都会返回本机账号邮箱，
  // 拿 auth.email 当登录标志会让设置页的端口一直空着（/api/config 需要 token）。
  const [token, setToken] = useState(() => localStorage.getItem('astroswarm_token') || '');
  const [health, setHealth] = useState(null);
  const [auth, setAuth] = useState(null);
  const [services, setServices] = useState(null);
  const [config, setConfig] = useState(null);
  /* 无头端版本号（GET /api/status）。未登录也回 {ok, version}，所以这个请求不用鉴权、
     失败也不影响别的：读不到就显示「版本读取中」，绝不退回某个写死的数字。 */
  const [statusInfo, setStatusInfo] = useState(null);
  // 设置页当前页签由 App 拿着（「设置 → 账号」页签仍在：有人习惯从那里登录）
  const [settingsTab, setSettingsTab] = useState('appearance');
  /* 登录弹窗（2026-10-06 老板要求）：账号入口一律开它，不再跳「设置 → 账号」页签。
     loginReturnRef 记住是哪个按钮点开的 —— 关掉之后焦点要还回去。 */
  const [loginOpen, setLoginOpen] = useState(false);
  const loginReturnRef = useRef(null);
  /* 顶层提示：插件工坊自己不带提示条，它把结果交给 App 显示。
     ⚠ 这里以前传的是 setMsg，而 App 里根本没有 setMsg —— 一点「插件工坊」整页就
     被 ErrorBoundary 接住显示「页面出错了：setMsg is not defined」。现在补上真状态。 */
  const [shellMsg, setShellMsg] = useState('');
  // 外观（背景/遮罩/不透明度/主题/强调色/质感）：改完写服务器，本地只留镜像
  const [appearance, setAppearance] = useState(loadAppearance);

  /* ── 控制台密码会话（2026-10-07 老板要求）──────────────────────────────
     控制台是 http://IP:7860 上的网页，同一局域网谁打开都能改配置 —— 所以在
     星群账号之外再加一道只属于这台服务器的密码。
     状态机（consoleGate）：
       'checking' —— 刚打开，正在问 /api/console/session（这一屏什么都别显示，别闪）
       'setup'    —— 这台服务器还没设过密码 → 显示「设置控制台密码」
       'locked'   —— 设过了但本浏览器没会话 → 显示「输入控制台密码」
       'open'     —— 会话有效 → 正常显示控制台
     会话过期由**服务端**判定：api.js 收到 console_locked 时会喊一声，这里切回 'locked'。

     ⚠ 这两个 state 必须**声明在下面那些 useEffect 之前**：拉数据的 effect 会读
     consoleGate 做判断，声明晚了就是 TDZ（编译后 "Cannot access before initialization"，
     整页被 ErrorBoundary 接住显示「页面出错了」—— 2026-10-07 踩过这一次）。 */
  const [consoleGate, setConsoleGate] = useState('checking');
  const [consoleNeedSetup, setConsoleNeedSetup] = useState(false);

  useEffect(() => {
    // 会话被服务端拒了（过期/被别处改密码作废）：立刻退回密码屏
    setConsoleLockedHandler(() => {
      clearConsoleToken();
      setConsoleGate((prev) => (prev === 'open' ? 'locked' : prev));
    });
    (async () => {
      try {
        const s = await get('/api/console/session');
        if (s && s.authed) {
          setConsoleGate('open');
          return;
        }
        setConsoleNeedSetup(Boolean(s && s.need_setup));
        setConsoleGate(s && s.need_setup ? 'setup' : 'locked');
      } catch {
        /* 老后端没有 /api/console/*（404）：不能把用户永远关在这一屏 ——
           放行进控制台，用原有的"未登录提示条"兜住。前后端版本不一致时也不白屏。 */
        setConsoleGate('open');
      }
    })();
    return () => setConsoleLockedHandler(null);
  }, []);

  useEffect(() => {
    applyAppearance(appearance);
    saveAppearance(appearance);
  }, [appearance]);

  // 当前页 id 写进 <html data-page="xxx">：styles.css 里「自动钉死 / 自动求解」那两段规则
  // 是按页作用域的（html[data-page='xxx'][data-ui-theme='yyy'] .page > …）。
  // UI v2 重排过的页面走 PAGE_KEY 里的新页名：旧规则整体不命中，自动区一个字节都不用改；
  // 没重排的页面（brain / logs / deps / messages / plugin-detail）仍用原名，版式不变。
  useEffect(() => {
    document.documentElement.dataset.page = PAGE_KEY[view] || view;
  }, [view]);

  const refresh = async () => {
    try {
      const [h, a, s] = await Promise.all([
        get('/health'),
        get('/api/auth/status'),
        get('/api/services/status'),
      ]);
      setHealth(h);
      setAuth(a);
      setServices(s);
    } catch {
      /* 服务未就绪时保持旧状态 */
    }
  };

  const loadConfig = async () => {
    try {
      setConfig(await get('/api/config'));
    } catch {
      /* 保持旧配置（未登录星群账号时就是这条路；界面偏好另有 /api/ui/prefs，不受影响） */
    }
  };

  const loadStatus = async () => {
    try {
      setStatusInfo(await get('/api/status'));
    } catch {
      /* 保持旧值：标题栏会显示「版本读取中」 */
    }
  };

  /* 拉数据这条只在**控制台会话通了**之后才跑（2026-10-07）。
     为什么：密码屏还没过的时候，/api/** 一律 401 —— 照样发请求的话，
     浏览器控制台会刷出一串红色 401（老板看到就以为坏了），而且这些请求本来
     一个字节都拿不到。gate 一变成 'open' 就立刻拉真数据（登录后所有页面马上有值）。 */
  useEffect(() => {
    if (consoleGate !== 'open') return undefined;
    loadConfig();
    loadStatus();
    loadUiPrefs();       // 界面偏好只认控制台会话，账号没登录也读得到
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [consoleGate]);

  // 顶层提示 3 秒后自己消失，和各个页面里的 toast 一个节奏
  useEffect(() => {
    if (!shellMsg) return undefined;
    const t = setTimeout(() => setShellMsg(''), 3200);
    return () => clearTimeout(t);
  }, [shellMsg]);

  /* 小白模式是**服务器上的设置**（桌面端读的也是它，settings.json 那一份），
     不能只存浏览器 localStorage —— 两边存的地方不同，老板桌面端关了、控制台还开着，
     侧栏一个 10 项一个 6 项，看着就是"没对齐"。
     规则：服务器给了 beginner_mode 就以它为准（同时镜像到 localStorage，断网时也有个默认）；
     切换时写回服务器；写不进去（没登录等）就只在本机生效，并如实说一句。 */
  const beginnerFromServer = useRef(false);
  useEffect(() => {
    if (beginnerFromServer.current) return;
    if (!config || typeof config.beginner_mode !== 'boolean') return;
    beginnerFromServer.current = true;
    setBeginner(config.beginner_mode);
    localStorage.setItem(BEGINNER_KEY, config.beginner_mode ? '1' : '0');
  }, [config]);

  const setBeginnerMode = (on) => {
    const v = Boolean(on);
    localStorage.setItem(BEGINNER_KEY, v ? '1' : '0');
    setBeginner(v);
    put('/api/config', { beginner_mode: v })
      .then(() => setShellMsg(v ? '小白模式已开启，已同步到服务器' : '小白模式已关闭，已同步到服务器'))
      .catch((e) => setShellMsg(isAuthError(e)
        ? '已在本机生效；登录后会同步到服务器'
        : `已在本机生效，但没同步到服务器：${e.message}`));
  };

  /* ── 外观/界面偏好（背景、遮罩、不透明度、主题、强调色、质感）──────────────
     2026-10-07 起是**服务器设置**（键名 ui_*），本地只留镜像。
     老板原话："换浏览器会出现先前配置归 0" —— 原因就是这份原来只存 localStorage。

     ⚠ 走 /api/ui/prefs，**不是 /api/config**：后者属于"星群账号"那一层，
     没登录账号就 401；而界面偏好跟账号权益无关，只跟"进不进得了这个控制台"有关。
     早先接在 /api/config 上时实测踩到：控制台密码过了、账号那层没登录 →
     偏好读不到 → 界面又是默认值，等于没修（老板要的正是"换浏览器也在"）。
     规则：
       服务器有值 → 以服务器为准，并镜像回 localStorage（断网时还有得用）；
       服务器没值 → 说明是老用户第一次上来，把本地这份**写上去一次**（迁移，不丢设置）；
     写入失败（会话过期等）静默回退本地并给一句中文提示，不弹英文报错、不白屏。 */
  const appearanceSynced = useRef(false);

  const pushAppearance = (ap) => put('/api/ui/prefs', appearanceToServer(ap))
    .catch((e) => {
      setShellMsg(isConsoleLocked(e)
        ? '外观已在本机生效；控制台会话过期了，重新输密码后会同步到服务器'
        : `外观已在本机生效，但没同步到服务器：${e.message}`);
    });

  const applyServerAppearance = (payload) => {
    const { ap, hasServer } = appearanceFromServer(payload);
    if (hasServer && ap) {
      appearanceSynced.current = true;
      setAppearance((prev) => {
        const next = { ...prev, ...ap };
        // 与本地一致时返回原对象引用：避免每次拉偏好都白重渲染一遍
        const same = Object.keys(next).every((k) => next[k] === prev[k]);
        return same ? prev : next;
      });
      return;
    }
    if (!appearanceSynced.current) {
      appearanceSynced.current = true;      // 一次会话只迁移一次，别每次拉偏好都写
      pushAppearance(appearance);
    }
  };

  const loadUiPrefs = async () => {
    try {
      applyServerAppearance(await get('/api/ui/prefs'));
    } catch {
      /* 读不到（会话过期 / 老后端没这条接口）：继续用本地镜像，不影响界面可用 */
    }
  };

  // 改动即时生效 + 写本地镜像 + 同步服务器（会话过期时那次失败会被上面静默吞掉）
  const onAppearanceChange = (next) => {
    setAppearance(next);
    pushAppearance(next);
  };

  // 登录/认领/退出之后配置才有权限拉：一起刷新，否则设置页端口一直是空的
  const reloadAll = async () => {
    await Promise.all([refresh(), loadConfig(), loadStatus(), loadUiPrefs()]);
  };

  const onToken = (value) => {
    if (value) localStorage.setItem('astroswarm_token', value);
    else localStorage.removeItem('astroswarm_token');
    setToken(value || '');
  };

  /* 星群账号登录状态过期（读资料接口回 401，2026-10-10 修老板截图里那处自相矛盾）：
     本地 token 这时已经没有意义了 —— **立刻清掉**，让顶栏芯片、侧栏、账号卡、
     设置页那几行字一起回到「未登录」，并给一句实话。
     以前不清 token：芯片按"本地有 token"说「已登录」，账号卡却因为读不到资料说
     「未登录」，同一页两处自相矛盾。判别标准就一条：**页面上任何两处
     "登录/未登录"的说法都必须一致** —— 所以下面所有登录态文案统一看本地 token。 */
  useEffect(() => {
    setAccountExpiredHandler(() => {
      if (!localStorage.getItem('astroswarm_token')) return;   // 已经清过：别重复提示
      onToken('');
      setShellMsg('登录状态已过期，请重新登录');
      reloadAll();                                             // 顺手把账号状态重读一遍
    });
    return () => setAccountExpiredHandler(null);
  }, []);

  /* 打开登录弹窗。传入点击事件时用它的 currentTarget 记住触发按钮；
     没有事件（提示条那种 onLogin() 不传参的用法）就退回当前焦点元素。
     先写 ref 再 setState：紧接着那次重渲染才拿得到最新的「焦点还回哪儿」。 */
  const openLogin = (ev) => {
    loginReturnRef.current = (ev && ev.currentTarget) || document.activeElement || null;
    setLoginOpen(true);
  };
  const closeLogin = () => setLoginOpen(false);

  // 账号资料（昵称/头像）：登录后立刻读一次，之后由 profile.js 那个共享 store 广播，
  // 侧栏 / 顶栏 / 图标栏 / 设置页账号卡用的是同一份数据，不会一处新一处旧。
  // ⚠ 判据用本地 token（登录成功的真凭据），不用 auth.email —— /api/auth/status
  //   无论登录与否都回本机邮箱，拿它当判据会在没登录时也去拉资料、白报 401。
  const profileState = useProfile();
  useEffect(() => {
    ensureProfile(Boolean(token), true);
  }, [token]);
  /* 官网那边改完昵称/头像，这边不用手动刷新：焦点回来、标签页切回来、
     以及每 60 秒各读一次（页面藏起来就停，卸载时清定时器）。 */
  useProfileAutoSync(Boolean(token));

  const accountName = profileState.profile?.display_name
    || profileState.profile?.nickname
    || '';
  /* 登录态的判据只有本地 token 一个（/api/auth/status 回的本机邮箱只是资料，
     而且它要 Authorization 对得上才给 —— 拿它当登录标志会让同一页出现
     "芯片说已登录、这行说未登录"这种自相矛盾）。 */
  const accountTip = token
    ? `星群账号 · 已登录：${accountName || auth?.email || '已登录'}`
    : '星群账号 · 未登录，点击登录';

  // 标题栏的版本号：/api/status → /api/config → 「版本读取中」（绝不写死数字）
  const versionText = versionLabel(statusInfo, config);

  /* 小白模式隐藏 messages / plugins / logs / deps 四个入口。但首页「打开消息中心」、
     接入页「打开消息中心」、设置页「实时日志 / 依赖管理」都会把用户直接送到这些页 ——
     那时侧栏里没有对应条目，用户会以为「进到了导航上不存在的地方」，也找不到回去的路。
     所以：当前页永远保留自己的入口。只多不少，不新增任何功能。 */
  const visibleNav = (items) =>
    items.filter(([id]) => !(beginner && ADVANCED_PAGES.includes(id)) || id === view);

  /* 统一导航的 4 组（2026-10-02）：机器人 / 工坊 / 人格 / 设置 —— 与桌面端一一对应。
     子项收在组里、点组标题才展开；当前页所在的组永远展开，收起时组标题后面带上当前页名字，
     免得收起来后一屏只剩四个组名，看不出自己在哪。 */
  const visibleGroups = NAV_GROUPS
    .map((g) => ({ ...g, items: visibleNav(g.items) }))
    .filter((g) => g.items.length);

  const toggleGroup = (id) => setOpenGroups((prev) => ({ ...prev, [id]: !prev[id] }));
  const groupOpen = (g) =>
    (g.items.some(([id]) => id === view) ? true : Boolean(openGroups[g.id]));

  /* 侧边栏（极光玻璃）：4 组；组内是一个面板，组与组之间留空；
     列表自带 overflow，窄屏只在组内滚动、不横向溢出。 */
  const renderNav = () => (
    <nav className="navgroups" aria-label="主导航">
      {visibleGroups.map((g) => {
        const open = groupOpen(g);
        const current = g.items.find(([id]) => id === view);
        return (
          <div className="navgrp" key={g.id}>
            <button
              type="button"
              className={open ? 'navgrp__title navgrp__title--open' : 'navgrp__title'}
              onClick={() => toggleGroup(g.id)}
              aria-expanded={open}
              title={open ? `收起「${g.title}」` : `展开「${g.title}」`}
            >
              <span>{g.title}</span>
              {!open && current && <span className="navgrp__now">{current[1]}</span>}
              <span className="navgrp__chev">{open ? '▾' : '▸'}</span>
            </button>
            {open && (
              <div className="navgrp__list">
                {g.items.map(([id, label, icon]) => (
                  <button
                    key={id}
                    type="button"
                    className={view === id ? 'nav-item nav-item--on' : 'nav-item'}
                    onClick={() => setView(id)}
                    aria-current={view === id ? 'page' : undefined}
                  >
                    <Icon name={icon} size={18} />
                    <span>{label}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );

  /* 瑞士极简：顶栏导航（带图标，选中 = 深色文字 + 2px 强调色下划线）。
     账号按钮 → 三组（组间竖分隔线）；窄屏由 .navflow 允许换行，不横向溢出。 */
  const renderTopnav = () => (
    <nav className="topnav navflow" aria-label="顶部导航">
      {/* 顶栏那个「星群账号」与侧栏是同一个入口（瑞士极简主题），一起改成开弹窗 */}
      <button type="button" className="topnav__account" title={accountTip} onClick={openLogin}>
        <AccountAvatar profile={profileState.profile} size={18} />
        <span>{accountName || '星群账号'}</span>
      </button>
      {visibleGroups.flatMap((g, gi) => {
        const items = g.items;
        if (!items.length) return [];
        return [
          ...(gi > 0 ? [<span className="topnav__sep" key={`sep-${g.id}`} />] : []),
          ...items.map(([id, label, icon]) => (
            <button
              key={id}
              type="button"
              className={view === id ? 'topnav__item topnav__item--on' : 'topnav__item'}
              onClick={() => setView(id)}
              aria-current={view === id ? 'page' : undefined}
            >
              <Icon name={icon} size={16} />
              <span>{label}</span>
            </button>
          )),
        ];
      })}
    </nav>
  );

  /* 深空终端：54 宽的图标栏（图标 20），选中 = 强调色底 + 左侧 2px 强调色边；
     组与组之间用一条极细分隔线（图标模式下没有文字，只能靠线分层）。 */
  const renderRail = () => (
    <nav className="rail" aria-label="图标导航">
      {/* 图标栏那个账号按钮（深空终端主题）同理：开弹窗，不跳设置 */}
      <button type="button" className="rail__account" title={accountTip} onClick={openLogin}>
        <AccountAvatar profile={profileState.profile} size={22} />
      </button>
      {visibleGroups.flatMap((g, gi) => {
        const items = g.items;
        if (!items.length) return [];
        return [
          ...(gi > 0 ? [<span className="railsep" key={`sep-${g.id}`} />] : []),
          ...items.map(([id, label, icon]) => (
            <button
              key={id}
              type="button"
              className={view === id ? 'rail__item rail__item--on' : 'rail__item'}
              title={label}
              aria-label={label}
              onClick={() => setView(id)}
              aria-current={view === id ? 'page' : undefined}
            >
              <Icon name={icon} size={18} />
            </button>
          )),
        ];
      })}
    </nav>
  );

  /* 「锁定」= 退出控制台会话。刻意**不动星群账号**（老板要的是锁屏，
     顺手退账号会害他重走一遍邮箱登录）。 */
  const lockConsole = async () => {
    try {
      await post('/api/console/logout', {});
    } catch {
      /* 会话可能已经过期了：本地清掉照样回密码屏 */
    }
    clearConsoleToken();
    setConsoleGate('locked');
    setShellMsg('控制台已锁定（星群账号仍保持登录）');
  };

  if (consoleGate !== 'open') {
    if (consoleGate === 'checking') {
      // 正在问服务器：给一屏安静的中文加载，不要闪默认值
      return <div className="cl-screen"><div className="cl-card"><p className="cl-hint">正在读取控制台状态…</p></div></div>;
    }
    return (
      <ConsoleLogin
        needSetup={consoleGate === 'setup' || consoleNeedSetup}
        onMsg={setShellMsg}
        onAuthed={() => {
          setConsoleGate('open');
          // 拿到会话立刻拉真实数据：配置、状态、外观偏好（换浏览器后设置全在这儿回来）
          reloadAll();
        }}
      />
    );
  }

  return (
    <>
      {/* 动态背景层（图片/视频 + 遮罩强度），永远在最底层且不挡点击 */}
      <div className="bg-media" aria-hidden="true">
        {appearance.enabled && appearance.path ? (
          isVideoPath(appearance.path) ? (
            <video src={appearance.path} autoPlay={appearance.autoplay} loop={appearance.loop} muted playsInline />
          ) : (
            <img src={appearance.path} alt="" />
          )
        ) : null}
        <div className="bg-media__mask" />
      </div>
      <div className="shell">
      <header className="titlebar">
        {/* 顶栏品牌 = 软件名（2026-10-10 老板定名：软件叫「星巢 Nest」；
            「AstroSwarm 星群」是网站/账号服务的品牌，只留在账号相关文案里）。 */}
        <span className="titlebar__title">星巢 Nest</span>
        <span className="titlebar__ver" title="Linux 无头端自己的版本号（取自服务器）">
          {versionText} · 星巢 Nest
        </span>
        <div className="titlebar__right">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setView('logs')}>
            实时日志
          </button>
          {/* 「锁定」= 退出控制台会话（回密码屏），**不动星群账号**。
              为什么放在标题栏最顺手的位置：老板离开电脑时要一键锁屏，
              找三层菜单再锁等于不会锁。 */}
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            title="锁定控制台：回到密码屏。星群账号不会退出。"
            onClick={lockConsole}
          >
            锁定
          </button>
          {/* 账号只表示"登录了没有"。插件与通道全部免费，不再有"免费版/会员"之分。
              ⚠ 判据要和别处一样用本地 token，不能用 auth.email ——
              /api/auth/status 无论登录与否都会回本机账号邮箱，用它会让"未登录"的机器
              顶着「已登录」，和上面那条未登录提示条自相矛盾。 */}
          <StatusChip ok={Boolean(token)} warn={!token}>
            {token ? (
              <>
                {/* 和桌面端一致：这块也带头像，名字优先用官网昵称，没读到就还是「已登录」 */}
                <AccountAvatar profile={profileState.profile} size={14} />
                <span>{accountName || '已登录'}</span>
                {auth?.plan ? <span>{` · ${auth.plan}`}</span> : null}
              </>
            ) : '未登录'}
          </StatusChip>
        </div>
      </header>
      {/* 未登录时第一眼就要知道该干什么：一条带「去登录」的提示条，登录后自己消失。
          以前只有卡片里的小字（LOGIN_HINT），不翻到对应卡片根本看不见。
          「去登录」开的是登录弹窗（2026-10-06）：就地填完就能继续看这一页，不跳走。 */}
      <LoginBanner show={!token} onLogin={openLogin} />
      {renderTopnav()}
      <div className="app">
        <aside className="sidebar-wrap">
          {/* 「星群账号」入口保留（老板明确要求不删）：点它开登录弹窗，
              不再跳到「设置 → 账号」页签 */}
          <button
            type="button"
            className="sidebar-account"
            title={accountTip}
            onClick={openLogin}
          >
            <AccountAvatar profile={profileState.profile} size={22} />
            <span>{accountName || '星群账号'}</span>
          </button>
          {renderNav()}
        </aside>
        {renderRail()}
        <main className="main" key={view}>
          <div className={PAGE_KEY[view] ? 'page page--v2' : 'page'}>
            {view === 'home' && (
              <Overview
                health={health}
                auth={auth}
                services={services}
                config={config}
                loggedIn={Boolean(token)}
                onAccess={() => setView('access')}
                onMessages={() => setView('messages')}
                onServices={() => setView('services')}
                onGo={(v) => setView(v)}
              />
            )}
            {view === 'access' && (
              <AccessPage
                auth={auth}
                config={config}
                health={health}
                services={services}
                onSaved={loadConfig}
                onMessages={() => setView('messages')}
                loggedIn={Boolean(token)}
              />
            )}
            {view === 'services' && (
              <ServicesPage
                config={config}
                health={health}
                services={services}
                onAccess={() => setView('access')}
                onLogs={() => setView('logs')}
                onDeps={() => setView('deps')}
                loggedIn={Boolean(token)}
              />
            )}
            {view === 'brain' && (
              <AiBrain
                config={config}
                onSaved={loadConfig}
                beginner={beginner}
                loggedIn={Boolean(token)}
              />
            )}
            {view === 'messages' && <MessageCenter loggedIn={Boolean(token)} />}
            {view === 'workshop' && (
              <WorkshopPage
                onMsg={setShellMsg}
                onGoPlugins={() => setView('plugins')}
              />
            )}
            {view === 'plugins' && (
              <Plugins
                auth={auth}
                onDetail={(pid, entry) => {
                  setDetailPid(pid);
                  setDetailEntry(entry || null);
                  setView('plugin-detail');
                }}
              />
            )}
            {view === 'plugin-detail' && (
              <PluginDetail
                pid={detailPid}
                entry={detailEntry}
                auth={auth}
                onBack={() => setView('plugins')}
              />
            )}
            {view === 'logs' && <Logs loggedIn={Boolean(token)} />}
            {view === 'settings' && (
              <Settings
                config={config}
                services={services}
                onSaved={loadConfig}
                beginner={beginner}
                onBeginner={setBeginnerMode}
                loggedIn={Boolean(token)}
                health={health}
                auth={auth}
                onChanged={reloadAll}
                onToken={onToken}
                appearance={appearance}
                onAppearance={onAppearanceChange}
                onGo={(v) => setView(v)}
                version={versionText}
                tab={settingsTab}
                onTab={setSettingsTab}
                onLockConsole={lockConsole}
              />
            )}
            {view === 'deps' && <Deps />}
          </div>
        </main>
      </div>
      {shellMsg && <div className="toast">{shellMsg}</div>}
      </div>
      {/* 登录弹窗挂在 .shell **外面**：遮罩要盖住整窗（含标题栏与侧栏），
          也避开 .shell 那个 z-index:1 的层叠上下文，不必和里面的卡片比层级。
          登录成功后 LoginForm 自己回调 closeLogin，紧接着 reloadAll 让首页四卡与
          今日数据立刻拿到真实值（不用手动刷新）。 */}
      <LoginModal
        open={loginOpen}
        onClose={closeLogin}
        returnFocusTo={loginReturnRef.current}
        loggedIn={Boolean(token)}
        email={auth?.email || ''}
        onToken={onToken}
        onChanged={reloadAll}
        onMsg={setShellMsg}
        onGoAccount={() => { closeLogin(); setSettingsTab('account'); setView('settings'); }}
      />
    </>
  );
}

function QWeatherCard() {
  const [cfg, setCfg] = useState(null);
  const [pub, setPub] = useState('');
  const [apiHost, setApiHost] = useState('');
  const [projectId, setProjectId] = useState('');
  const [credId, setCredId] = useState('');
  const [msg, setMsg] = useState('');

  const refresh = () => {
    get('/api/qweather/config')
      .then((d) => {
        setCfg(d);
        setApiHost(d.api_host || '');
        setProjectId(d.project_id || '');
        setCredId(d.credential_id || '');
      })
      .catch((e) => setMsg(String(e.message)));
  };

  useEffect(refresh, []);

  const generate = async () => {
    try {
      const d = await post('/api/qweather/generate-key');
      setPub(d.public_key || '');
      setMsg('密钥已生成并保存在服务器，把公钥贴到和风控制台');
      refresh();
    } catch (e) {
      setMsg(String(e.message));
    }
  };

  const save = async () => {
    try {
      await post('/api/qweather/save', {
        api_host: apiHost,
        project_id: projectId,
        credential_id: credId,
      });
      setMsg('已保存，重启机器人生效');
      refresh();
    } catch (e) {
      setMsg(String(e.message));
    }
  };

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <h3 className="subhead">和风天气配置</h3>
      {cfg && (
        <p className="row-item__desc">
          状态：
          {cfg.configured ? '已配置 ✓' : '未完成（需要公钥 + 凭据ID）'}
          {cfg.private_key_path ? (
            <span className="mono"> · 私钥 {cfg.private_key_path}</span>
          ) : null}
        </p>
      )}
      <div className="field">
        <label>API Host</label>
        <input
          value={apiHost}
          onChange={(e) => setApiHost(e.target.value)}
          placeholder="控制台-设置里的专属 API Host，如 xxx.qweatherapi.com"
        />
      </div>
      <div className="field">
        <label>项目ID</label>
        <input
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
          placeholder="控制台-项目管理里的项目ID"
        />
      </div>
      <div className="field">
        <label>凭据ID</label>
        <input
          value={credId}
          onChange={(e) => setCredId(e.target.value)}
          placeholder="创建 JWT 凭据后显示的 ID"
        />
      </div>
      <div className="wake-row" style={{ gap: 8, marginTop: 8 }}>
        <button type="button" className="btn" onClick={generate}>
          生成密钥对
        </button>
        <button type="button" className="btn btn--primary" onClick={save}>
          保存配置
        </button>
      </div>
      {pub && (
        <pre
          className="mono"
          style={{
            whiteSpace: 'pre-wrap',
            background: 'rgba(255,255,255,0.05)',
            padding: 10,
            borderRadius: 8,
            marginTop: 10,
          }}
        >
          {pub}
        </pre>
      )}
      <p className="msg msg--in" style={{ marginTop: 10 }}>
        控制台：dev.qweather.com → 项目管理 → 你的项目 → 添加凭据 →
        身份认证方式选「JSON Web Token」→ 粘贴上方公钥 → 保存 →
        把「设置」里的 API Host 和「凭据ID」填到上面 → 保存配置 → 重启机器人。
      </p>
      {msg && <p className="msg msg--in">{msg}</p>}
    </div>
  );
}

/* 插件管理：与桌面端 plugins_page.py 1:1（高级页，小白模式下入口被隐藏）。
   实测：左右两张面板各 679×598（行内间距 14）→ 全宽「本地插件」169 → 底部「安装任务」48
   （只露标题，任务面板自身隐藏）。面板内所有子行间距 = 面板自身间距 6
   （Qt 的子布局会继承父布局 spacing，所以这里不能用页面的 14）。
   开发者模式关闭时：PyPI / zip 的输入与按钮禁用、「NoneBot 插件市场」整块隐藏。
   列表项格式取自 _refresh_market_list：`[免费|会员|单买] 名字  v版本  [分类]`。 */
/* 插件列表现已全部免费：「插件管理」页的商店改成了卡片 + 搜索 + 分类标签，
   每张卡自己带「安装 / ✓已装 + 配置」按钮，这里不再按 tier 区分文案与颜色。 */

/** 插件详情页：介绍 / 功能 / 可调参数（写 <data_home>/tool_packs/<id>/config.json）。 */
function PluginDetail({ pid, entry, auth, onBack }) {
  const [d, setD] = useState(null);
  const [ent, setEnt] = useState(null);      // /api/plugins/entitlement
  const [code, setCode] = useState('');
  const [form, setForm] = useState({});
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const load = () => {
    if (!pid) return;
    get(`/api/tools/${pid}/detail`)
      .then((res) => {
        setD(res);
        setForm({ ...(res.values || {}) });
      })
      .catch((e) => setMsg(`读取插件详情失败：${e.message}`));
  };
  /* ⚠ 这两行以前写的是 `useEffect(load, [pid])` / `useEffect(loadEnt, [pid])` ——
     直接把"会返回 Promise 的函数"当 effect 传进去了。React 会把 effect 的返回值
     当成清理函数，于是**离开插件详情页时**去调 promise.destroy() →
     "destroy is not a function" → 整页被 ErrorBoundary 接住显示「页面出错了」。
     这是 2026-10-12 截图验收时才暴露的既有缺陷（2016 年那次恢复版本就在），
     不是本轮改动引入的。修法：包一层不返回值的箭头函数（只改写法，语义不变）。 */
  useEffect(() => { load(); }, [pid]);

  const loadEnt = () =>
    get('/api/plugins/entitlement')
      .then(setEnt)
      .catch(() => {});
  useEffect(() => { loadEnt(); }, [pid]);

  const price = entry?.price;
  const buyUrl = entry?.buy_url || entry?.url || '';   // 市场清单里的旧购买链接已不再使用
  const owned = Boolean(ent && (ent.owned_plugins || []).includes(pid));
  // 能力包已全部免费开源：这里只做「装了就能用」的兜底判断，
  // 旧清单里带 tier 的条目按后端口径（all_plugins / 已登记）放行。
  const locked = Boolean(entry && entry.tier && entry.tier !== 'free') && !owned && !isFull(ent);

  const redeem = async () => {
    const v = code.trim();
    if (!v) {
      setMsg('先填兑换码');
      return;
    }
    setBusy(true);
    try {
      const res = await post('/api/plugins/redeem', { code: v });
      setCode('');
      setEnt(res);
      setMsg(
        (res.owned_plugins || []).includes(pid)
          ? `兑换成功：已永久拥有「${d?.name || pid}」`
          : `兑换成功（当前已购：${(res.owned_plugins || []).join('、') || '无'}）`,
      );
    } catch (e) {
      setMsg(withAuthHint(e));
    } finally {
      setBusy(false);
    }
  };

  const refreshEnt = async () => {
    setBusy(true);
    try {
      const res = await post('/api/plugins/refresh-entitlement', {});
      setEnt(res);
      setMsg(
        (res.owned_plugins || []).includes(pid)
          ? '权益已刷新：这个插件已经是你的了 ✓'
          : '权益已刷新，但还没查到这笔购买（如果你刚付款，等 1~5 分钟再点一次）',
      );
    } catch (e) {
      setMsg(withAuthHint(e));
    } finally {
      setBusy(false);
    }
  };

  const fields = d?.fields || [];
  const installed = Boolean(d?.installed);
  const changed = fields.filter((f) => form[f.key] !== (d?.values || {})[f.key]);
  const set = (k, v) => setForm((s) => ({ ...s, [k]: v }));

  const save = async () => {
    if (!changed.length) {
      setMsg('没有改动');
      return;
    }
    setBusy(true);
    try {
      const patch = {};
      changed.forEach((f) => {
        patch[f.key] = f.type === 'number' ? Number(form[f.key]) : form[f.key];
      });
      const res = await put(`/api/tools/${pid}/config`, patch);
      setMsg(`已保存：${Object.keys(res.changed || {}).join('、') || '无变化'}（参数立刻生效，下次主动消息就按新值判断）`);
      load();
    } catch (e) {
      setMsg(withAuthHint(e));
    } finally {
      setBusy(false);
    }
  };

  const installPack = async () => {
    try {
      await post('/api/tools/install', { id: pid });
      setMsg('已安装');
      load();
    } catch (e) {
      setMsg(withAuthHint(e));
    }
  };

  /* 插件参数渲染（2026-10-12 换统一控件）：
     · bool   → 开关行（带一句说明），不再是一个裸复选框
     · number → NumberField（标签 + 数值 + 单位后缀 + 步进 + 范围提示），
                不再是裸 input[type=number]；档位少（step=1 且跨度 ≤ 20）时自动变滑杆
     · text   → 保持输入框，但右侧说明统一走 hint 行 */
  const field = (f) => {
    const v = form[f.key];
    if (f.type === 'bool') {
      return (
        <ToggleRow
          key={f.key}
          checked={Boolean(v)}
          onChange={(next) => set(f.key, next)}
          label={f.label || f.key}
          desc={f.help || ''}
        />
      );
    }
    if (f.type === 'number') {
      return (
        <NumberField
          key={f.key}
          label={f.label || f.key}
          value={v}
          min={f.min}
          max={f.max}
          step={f.step || 1}
          unit={f.unit || ''}
          hint={f.help || ''}
          onChange={(next) => set(f.key, next)}
        />
      );
    }
    return (
      <div className="param-row" key={f.key}>
        <span>{f.label || f.key}</span>
        <input className="p-input" value={v ?? ''} spellCheck={false} onChange={(e) => set(f.key, e.target.value)} />
        <b>{(f.help || '') + (f.unit || '')}</b>
      </div>
    );
  };

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{d?.name || '插件详情'}</h1>
          <p>
            {d?.installed ? `已安装 v${d.version || '—'}` : '尚未安装'}
            {d?.kind ? ` · ${d.kind}` : ''}
            {d?.adapters?.length ? ` · 支持 ${d.adapters.join(' / ')}` : ''}
          </p>
        </div>
        <div className="page-head__right">
          <button type="button" className="btn btn--ghost" onClick={onBack}>
            返回插件列表
          </button>
        </div>
      </header>

      <section className="card card--wide">
        <h2 className="card__title">介绍</h2>
        <div className="stat-row">
          <span>价格</span>
          <b>{price == null ? '免费' : `¥${price}`}</b>
        </div>
        <div className="stat-row">
          <span>拥有状态</span>
          <b>{price == null || owned || isFull(ent) ? '可以直接安装' : '需要先开通'}</b>
        </div>
        <p className="card__note">{d?.description || '（这个插件没有写介绍）'}</p>
        {price == null ? (
          <p className="card__note">
            这是<b>免费插件</b>：点下面「安装这个插件」直接装，装完重启一次机器人即可用。
            也可以在官网「插件市场」（astroswarm.cn/plugins.html）下载 zip。
          </p>
        ) : owned ? (
          <p className="card__note">
            这个插件已经登记在你账号下：点「安装这个插件」即可，重启机器人后生效；
            官网「插件市场」里它也会显示「已拥有」，随时能重新下载。
          </p>
        ) : (
          <>
            <div className="p-row">
              {buyUrl && (
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => window.open(buyUrl, '_blank')}
                >
                  {'去账号中心开通'}
                </button>
              )}
              <button type="button" className="btn" disabled={busy} onClick={refreshEnt}>
                已开通，刷新权益
              </button>
            </div>
            <p className="card__note">
              开通入口在账号中心（astroswarm.cn）：填<b>你的星群账号邮箱</b>，
              开通后点「已开通，刷新权益」，一般几秒内生效。
            </p>
            <div className="p-row">
              <input
                className="p-input"
                value={code}
                placeholder="兑换码（XXXX-XXXX-XXXX-XXXX）"
                spellCheck={false}
                onChange={(e) => setCode(e.target.value)}
              />
              <button type="button" className="btn" disabled={busy} onClick={redeem}>
                兑换
              </button>
            </div>
            <p className="card__note">
              兑换码是<b>人工兜底</b>用的：开通后权益没自动同步过来（比如账号邮箱填错了），
              找我要一个码填在这里兑换即可。
            </p>
          </>
        )}
        {!d?.installed && (
          <div className="p-row">
            <button type="button" className="btn btn--primary" onClick={installPack}>
              安装这个插件
            </button>
          </div>
        )}
      </section>

      {Boolean(d?.features?.length) && (
        <section className="card card--wide">
          <h2 className="card__title">功能</h2>
          <div className="p-list">
            {d.features.map((f) => (
              <div className="p-item param-feature" key={f.title}>
                {`${f.title}：${f.desc}`}
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="card card--wide">
        <h2 className="card__title">参数设置</h2>
        <p className="card__note">
          {d?.installed
            ? '改完点保存，立刻写进服务器；主动消息按这些数值判断（概率 / 间隔 / 互动窗口 / 未回上限 / 免打扰 / 限速）。'
            : '这个插件还没安装，安装后才能改参数。'}
        </p>
        {fields.length === 0 && <p className="card__note">这个插件没有可调参数。</p>}
        {/* 2026-10-12 信息层级：开关（功能开关）常显 —— 藏起来会让人以为"这个功能没了"；
            数值项（概率/间隔/冷却/限速/时段这类配一次就不动的）收进折叠区，
            标题行给一句当前值摘要，不用展开也知道现在配的是什么。 */}
        {fields.filter((f) => f.type === 'bool').map((f) => field(f))}
        {(() => {
          const nums = fields.filter((f) => f.type === 'number');
          if (!nums.length) return null;
          const summary = nums.slice(0, 3)
            .map((f) => `${f.label || f.key} ${form[f.key] ?? '—'}${f.unit || ''}`)
            .join(' · ');
          const dirtyNums = changed.filter((k) => nums.some((f) => f.key === k)).length;
          return (
            <Collapse
              storageKey={`plugin-${pid}-numbers`}
              title={`数值参数（${nums.length} 项）`}
              summary={nums.length > 3 ? `${summary} …` : summary}
              dirty={dirtyNums > 0}
              dirtyText={`已改 ${dirtyNums} 项`}
            >
              {nums.map((f) => field(f))}
            </Collapse>
          );
        })()}
        {fields.filter((f) => f.type !== 'bool' && f.type !== 'number').map((f) => (
          <div key={f.key}>{field(f)}</div>
        ))}
        {fields.length > 0 && (
          <div className="p-row">
            <PillButton
              tone="primary"
              disabled={!installed || busy || !changed.length}
              loading={busy}
              onClick={save}
            >
              {busy ? '保存中…' : changed.length ? `保存参数（${changed.length} 项改动）` : '保存参数'}
            </PillButton>
            <PillButton tone="ghost" disabled={busy} onClick={load}>
              重新读取
            </PillButton>
          </div>
        )}
      </section>

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}

function Plugins({ auth, onDetail = () => {} }) {
  const [entries, setEntries] = useState([]);
  const [packs, setPacks] = useState([]);
  const [status, setStatus] = useState('正在加载插件商店...');
  const [category, setCategory] = useState('');
  const [query, setQuery] = useState('');
  const [picked, setPicked] = useState('');
  const [packId, setPackId] = useState('');
  const [pyPackage, setPyPackage] = useState('');
  const [zipPath, setZipPath] = useState('');
  const [qweather, setQweather] = useState(false);
  const [msg, setMsg] = useState('');
  // 开发者模式（后端 /api/console/dev-mode，存服务器 config_home/console.json）：
  // 关着时 PyPI 那行输入与按钮禁用，跟桌面端 plugin 页的规则一致。
  const [devMode, setDevMode] = useState(false);
  const [pkgBusy, setPkgBusy] = useState(false);
  const [pkgStage, setPkgStage] = useState('');
  // /api/plugins/installed：已装插件、本地插件目录、pyproject 记录的商店插件
  const [localInfo, setLocalInfo] = useState({ installed: [], local: [], store: [], dir: '' });
  const [deps, setDeps] = useState(null);
  // 兑换码 / 权益（桌面端 plugins_page 的「兑换 + 刷新权益」那一行）
  const [redeemCode, setRedeemCode] = useState('');
  const [redeeming, setRedeeming] = useState(false);
  const [ent, setEnt] = useState(null);
  // 官网市场正在装哪一个（按钮显示「正在安装…」并禁用）
  const [marketBusyId, setMarketBusyId] = useState('');

  const loadLocal = () => {
    get('/api/plugins/installed')
      // 逐项兜底：服务器回了 200 但少字段时（或换了一版接口），
      // 直接 setLocalInfo(d) 会让 store.map / local.map 抛错，整页被 ErrorBoundary 接住。
      .then((d) => setLocalInfo({
        installed: Array.isArray(d.installed) ? d.installed : [],
        local: Array.isArray(d.local) ? d.local : [],
        store: Array.isArray(d.store) ? d.store : [],
        dir: d.dir || '',
      }))
      .catch((e) => fail(e, '本地插件清单'));
  };

  /* 未登录时这一页的四块数据全都读不到（/api/tools/status、/api/plugins/entitlement、
     /api/plugins/installed 都鉴权）。以前每处都是 .catch(() => {})，静默吞掉：
     表现是「已装能力包空列表」「付费插件全显示未购买」，用户以为功能没了。
     现在统一记一句实话，并在对应卡片上显示，不再把「没登录」装成「没有数据」。 */
  const [needLogin, setNeedLogin] = useState(false);
  const fail = (e, what) => {
    if (isAuthError(e)) {
      setNeedLogin(true);
      return;
    }
    setMsg(`${what}读取失败：${e.message}`);
  };

  const loadPacks = () => {
    get('/api/tools/status')
      .then((d) => setPacks((d.installed || []).filter((p) => p.kind !== 'persona-pack')))
      .catch((e) => fail(e, '已装能力包'));
  };

  const load = () => {
    setStatus('正在加载插件商店...');
    Promise.all([
      get('/api/plugins').then((d) => d.plugins || []).catch(() => []),
      get('/api/tools/market').then((d) => d.packs || []).catch(() => []),
    ])
      .then(([plugins, toolPacks]) => {
        const list = [...plugins, ...toolPacks];
        setEntries(list);
        if (!list.length) {
          setStatus('插件商店暂不可用或暂无内容，可稍后点「刷新」重试');
          return;
        }
        // 插件与能力包全部免费、微信通道也不需要开通 —— 这里只说数量，不提会员/付费
        setStatus(`插件商店已加载：${list.length} 个（全部免费，装上就能用）`);
      })
      .catch((e) => setStatus(`插件商店加载失败：${e.message}（可点「刷新」重试）`));
  };

  useEffect(() => {
    load();
    loadPacks();
    loadLocal();
    get('/api/console/dev-mode')
      .then((d) => setDevMode(Boolean(d.developer_mode)))
      .catch((e) => fail(e, '开发者模式'));
    get('/api/plugins/entitlement')
      .then((d) => setEnt(d))
      .catch(() => setEnt(null));      // 未登录时读不到：上面那行会说"先登录"
  }, []);

  /* 兑换码：成功与否都由后端说了算（它才知道码是真的）。
     成功后后端会把新权益存本机，这里顺手再读一次权益，界面上立刻能看到变化。 */
  const redeem = async () => {
    const code = redeemCode.trim();
    if (!code) {
      setMsg('先把兑换码填上');
      return;
    }
    setRedeeming(true);
    try {
      const r = await post('/api/plugins/redeem', { code });
      setRedeemCode('');
      setEnt(r || null);
      setMsg('兑换成功，权益已更新');
      loadPacks();
    } catch (e) {
      setMsg(withAuthHint(e));
    } finally {
      setRedeeming(false);
    }
  };

  const refreshEnt = async () => {
    try {
      setEnt(await post('/api/plugins/refresh-entitlement', {}));
      setMsg('权益已刷新');
    } catch (e) {
      setMsg(withAuthHint(e));
    }
  };

  // 插件与能力包现在全部免费（后端 _entitled 恒为 True）——界面上不再出现
  // "会员/单买/未解锁"这类字样，也不需要再按 tier 判断能不能装。
  const categories = [...new Set(entries.map((e) => e.category || '未分类'))].sort();

  // 已装：能力包看 /api/tools/status，普通插件看 /api/plugins/installed
  const installedIds = new Set([
    ...packs.map((p) => String(p.id)),
    ...(localInfo.installed || []).map((p) => String(p.id)),
  ]);
  const isInstalled = (e) => installedIds.has(String(e.id));

  const kw = query.trim().toLowerCase();
  const shown = entries.filter((e) => {
    if (category && (e.category || '未分类') !== category) return false;
    if (!kw) return true;
    const hay = `${e.name || ''} ${e.description || ''} ${e.id || ''}`.toLowerCase();
    return hay.includes(kw);
  });
  // 默认按分类分组（分组只在"全部 + 没搜索"时才有意义）
  const grouped = !category && !kw;
  const groups = [];
  if (grouped) {
    shown
      .slice()
      .sort((a, b) => String(a.category || '未分类').localeCompare(String(b.category || '未分类')))
      .forEach((e) => {
        const cat = e.category || '未分类';
        const last = groups[groups.length - 1];
        if (last && last.cat === cat) last.items.push(e);
        else groups.push({ cat, items: [e] });
      });
  }

  const installEntry = async (entry) => {
    if (!entry) {
      setMsg('请先在插件商店选择一个插件');
      return;
    }
    try {
      // 能力包走 /api/tools/install，普通插件走 /api/plugins/install（桌面端是同一份列表）
      await post(entry.kind && entry.kind !== 'plugin' ? '/api/tools/install' : '/api/plugins/install', {
        id: entry.id,
      });
      setMsg(`「${entry.name}」已安装，重启机器人生效`);
      loadPacks();
      loadLocal();
    } catch (e) {
      setMsg(withAuthHint(e));
    }
  };

  const install = () => installEntry(entries.find((e) => String(e.id) === picked));

  /* 官网市场那一条的「安装」：还是走同一个商店插件安装接口 /api/plugins/install，
     但市场接口是白名单字段，**只有 name、没有 id**。所以：
       1) 先在已经拉到的 /api/plugins 清单里按名字找回真 id（同一份清单，能对上）；
       2) 对不上就把名字当 id 发过去（后端按 id 匹配，匹配不到会回 404，
          那时把后端的原因照实显示，不假装装好了）。
     两个键一起发，后端按哪个都能认。 */
  const installMarket = async (entry) => {
    const name = String((entry && entry.name) || '');
    if (!name) {
      setMsg('市场条目没有名字，装不了');
      return;
    }
    const hit = entries.find((e) => String(e.name || '') === name);
    setMarketBusyId(name);
    try {
      await post('/api/plugins/install', { id: hit ? String(hit.id) : name, name });
      setMsg(`「${name}」已安装，重启机器人生效`);
      load();
      loadPacks();
      loadLocal();
    } catch (e) {
      setMsg(withAuthHint(e));
    } finally {
      setMarketBusyId('');
    }
  };

  // 开发者模式：从 PyPI 装插件。后端是后台线程 + 轮询（pip 可能跑几分钟），
  // 所以这里提交完就轮询 /api/deps/status，把最后一行输出当进度显示。
  const installPyPI = async () => {
    const name = pyPackage.trim();
    if (!name) {
      setMsg('先填 PyPI 包名，例如 nonebot-plugin-status');
      return;
    }
    if (!devMode) {
      setMsg('开发者模式没开：先去「设置 → 启动行为」打开（后果自负）');
      return;
    }
    setPkgBusy(true);
    setPkgStage('正在提交安装…');
    setMsg(`正在安装 ${name}…`);
    try {
      await post('/api/plugins/install-pypi', { package: name });
      for (let i = 0; i < 400; i += 1) {
        const st = await get('/api/deps/status');
        const last = (st.log || []).slice(-1)[0];
        if (last) setPkgStage(last);
        if (!st.running) {
          if (st.error) {
            setMsg(`${name} 安装失败，下面是命令输出：${st.error}`);
          } else {
            setMsg(`${name} 装好了（已写进机器人 pyproject），重启机器人后生效`);
            setPyPackage('');
            loadLocal();
          }
          break;
        }
        await new Promise((r) => setTimeout(r, 1500));
      }
    } catch (e) {
      setMsg(withAuthHint(e));
    } finally {
      setPkgBusy(false);
      setPkgStage('');
    }
  };

  const uninstallPack = async () => {
    if (!packId) {
      setMsg('请先在「已装能力包」里选一个');
      return;
    }
    // 卸载不可撤销：删掉的是 <数据目录>/tool_packs/<id>，连同用户调过的参数一起没了
    if (!window.confirm(`确定卸载「${packId}」？\n该能力包的配置与参数会一起删除，不能撤销。`)) return;
    try {
      await post('/api/tools/uninstall', { id: packId });
      setMsg('已卸载');
      loadPacks();
    } catch (e) {
      setMsg(withAuthHint(e));
    }
  };

  const restartBot = async () => {
    if (!window.confirm('确定重启机器人？\n大约 10 秒内 QQ / 微信 会短暂离线，正在进行的对话会中断。')) return;
    try {
      // 走 /api/bot/restart：它会等到反向 WS 真连上再回，并把每步耗时写进日志，
      // 避免"以为没重启"（设置类改动不需要它，只有改了代码/装了插件才用）。
      const r = await post('/api/bot/restart', {});
      const secs = (r && r.seconds) || {};
      setMsg(
        `机器人已重启（停止 ${secs.stop ?? '?'}s｜启动 ${secs.start ?? '?'}s｜` +
        `反向 WS 重连 ${secs.reconnect ?? '?'}s）` +
        (r && r.qq_connected === false ? '：协议端稍后会自动重连，看日志确认' : '')
      );
    } catch (e) {
      setMsg(withAuthHint(e));
    }
  };

  // 桌面端「重装缺失依赖」：先扫一遍，真有缺失再装
  const reinstallDeps = async () => {
    setMsg('正在扫描插件依赖…');
    try {
      const scanned = await runDeps('scan', (st) => setDeps(st));
      if (!scanned.missing || !scanned.missing.length) {
        setMsg(`依赖齐全（共检查 ${scanned.checked} 项）`);
        return;
      }
      setMsg(`缺 ${scanned.missing.length} 项，开始安装…`);
      const done = await runDeps('install', (st) => setDeps(st));
      setMsg(`依赖安装完成（成功 ${(done.installed || []).length} 项），建议重新扫描验证`);
    } catch (e) {
      setMsg(withAuthHint(e));
    }
  };

  const disabled = '无头端暂不支持，请先在桌面端操作';
  const todo = (what) => setMsg(`${what}：${disabled}`);

  return (
    <>
      <header className="page-head">
        <div>
          <h1>插件管理</h1>
          <p>装插件、管插件都在这里；第三方插件安装默认关闭，可在「设置 → 开发者模式」开启（后果自负）。</p>
        </div>
      </header>

      {/* 本机能力（可搜索 · 可开关）：与桌面端 plugins_page.py::_build_capability_panel
          同一位置（标题下面、插件商店上面）。2026-10-12 老板：「插件页面看不见好友
          申请那个功能」「加一个能搜索还带开关的」。 */}
      <MachineCapabilities onMsg={setMsg} />

      <div className="pl-row">
        <section className="card pl-panel">
          <h2 className="card__title">插件商店</h2>
          <p className="card__note">
            插件与能力包全部免费，安装前会自动校验兼容性。
          </p>

          {/* 搜索：名字或说明里的字都能搜（插件一多，靠翻列表找太慢） */}
          <div className="p-row">
            <input
              className="p-input mkt-search"
              value={query}
              placeholder="搜索插件：输名字或说明里的字都行"
              spellCheck={false}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button type="button" className="btn btn--ghost" onClick={load}>
              刷新
            </button>
            <button
              type="button"
              className="btn btn--primary"
              title="先点卡片上的「安装」，或先选中一行再点这里"
              onClick={install}
            >
              安装选中的
            </button>
          </div>

          {/* 分类用横排小标签：一共有哪几类一眼看全（比下拉直观） */}
          <div className="mkt-tags">
            {['', ...categories].map((c) => (
              <button
                key={c || '__all__'}
                type="button"
                className={category === c ? 'mkt-tag mkt-tag--on' : 'mkt-tag'}
                onClick={() => setCategory(c)}
              >
                {c || '全部'}
              </button>
            ))}
          </div>

          {shown.length === 0 && (
            <p className="card__note">没有匹配的插件。换个词，或点「全部」看所有插件。</p>
          )}

          {(grouped ? groups : [{ cat: '', items: shown }]).map((group) => (
            <div className="mkt-group" key={group.cat || '__flat__'}>
              {group.cat && (
                <p className="mkt-group__title">{`${group.cat} · ${group.items.length} 个`}</p>
              )}
              <div className="mkt-cards">
                {group.items.map((e) => {
                  const inst = isInstalled(e);
                  const cat = e.category || '未分类';
                  return (
                    <div
                      className={picked === String(e.id) ? 'mkt-card mkt-card--on' : 'mkt-card'}
                      key={String(e.id)}
                      onClick={() => setPicked(String(e.id))}
                    >
                      <span className="mkt-card__icon" data-cat={cat}>
                        {cat.slice(0, 1)}
                      </span>
                      <div className="mkt-card__body">
                        <div className="mkt-card__head">
                          <b className="mkt-card__name">{e.name}</b>
                          <span className="mkt-card__ver">{`v${e.version || ''}`}</span>
                          <span className="mkt-badge">{cat}</span>
                          <span className="mkt-badge mkt-badge--free">免费</span>
                        </div>
                        <div className="mkt-card__desc">
                          {e.description || `${cat}：装进机器人就能用`}
                        </div>
                      </div>
                      <div className="mkt-card__actions">
                        <button
                          type="button"
                          className="btn btn--ghost"
                          title="看介绍、功能与可调参数"
                          onClick={(ev) => {
                            ev.stopPropagation();
                            onDetail(String(e.id), e);
                          }}
                        >
                          详情
                        </button>
                        {inst ? (
                          <>
                            <span className="mkt-installed">✓ 已装</span>
                            <button
                              type="button"
                              className="btn btn--ghost"
                              title="打开它的可调参数"
                              onClick={(ev) => {
                                ev.stopPropagation();
                                onDetail(String(e.id), e);
                              }}
                            >
                              配置
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            className="btn btn--primary"
                            onClick={(ev) => {
                              ev.stopPropagation();
                              installEntry(e);
                            }}
                          >
                            安装
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}

          <p className="card__note">{status}</p>
          {needLogin && (
            <p className="card__note">
              {LOGIN_HINT}：下面「已装能力包 / 已安装 / 本地插件」三块都会是空的 ——
              不是这些插件不存在。
            </p>
          )}
          <h3 className="card__subtitle">已装能力包</h3>

          <div className="p-row">
            <div className="p-list p-list--72">
              {packs.map((p) => (
                <div className="p-item-row" key={String(p.id)}>
                  <button
                    type="button"
                    className={packId === String(p.id) ? 'p-item p-item--on' : 'p-item'}
                    onClick={() => setPackId(String(p.id))}
                  >
                    {`${p.name}  v${p.version}`}
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost p-item__more"
                    title="看介绍、功能与可调参数"
                    onClick={() => onDetail(String(p.id))}
                  >
                    详情
                  </button>
                </div>
              ))}
            </div>
            {/* 这个「配置」原来在没选中任何能力包时会 onDetail('')：详情页 pid 为空 →
                 load() 里 if (!pid) return → 进到一个只有标题、价格「免费」和
                「这个插件没有写介绍」的空白页，没有任何错误提示。
                现在先拦住，并说清楚要先选一个。 */}
            <button
              type="button"
              className="btn btn--ghost"
              title="先在左边选一个能力包，再点这里调参数。"
              onClick={() => {
                if (!packId) {
                  setMsg('先在左边「已装能力包」里选一个，再点「配置」');
                  return;
                }
                if (packId === 'qweather') {
                  setQweather((v) => !v);
                  return;
                }
                onDetail(packId);
              }}
            >
              配置
            </button>
            <button type="button" className="btn btn--ghost" onClick={uninstallPack}>
              卸载
            </button>
          </div>

          {/* 开发者高级区：默认收起（桌面端 plugins_page 的 _ADVANCED_TITLE 那一段），
              第三方 zip / PyPI / 在线插件市场都收在里面 —— 平时不占版面，也不诱导点。 */}
          <details className="fold">
            <summary className="fold__summary">开发者高级区（第三方 zip / PyPI）</summary>
            <div className="fold__body">
              <p className="card__note">
                要先开「设置 → 开发者模式」；风险自负。
              </p>

              <h3 className="card__subtitle">第三方 zip 安装</h3>
              <ZipInstall />

              <h3 className="card__subtitle">从 PyPI 安装</h3>
              <div className="p-row">
                <input
                  className="p-input"
                  value={pkgBusy && pkgStage ? pkgStage : pyPackage}
                  placeholder={devMode ? 'PyPI 包名，如 nonebot-plugin-status' : 'PyPI 包名（需先开开发者模式）'}
                  spellCheck={false}
                  disabled={!devMode || pkgBusy}
                  onChange={(e) => setPyPackage(e.target.value)}
                />
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={!devMode || pkgBusy}
                  onClick={installPyPI}
                >
                  安装
                </button>
              </div>
              <p className="card__note">不推荐：第三方插件风险自负。</p>

              <h3 className="card__subtitle">在线插件市场</h3>
              <p className="card__note">
                在页面下方「官网市场」里逛，那是对外开放的区域。
              </p>
            </div>
          </details>

          <h3 className="card__subtitle">已安装（pyproject 记录）</h3>
          <div className="p-list p-list--115">
            {localInfo.store.map((name) => (
              <div className="p-item" key={name}>
                {name}
              </div>
            ))}
          </div>
        </section>

        <section className="card pl-panel">
          <h2 className="card__title">兑换与配置</h2>

          {/* 兑换码：桌面端 plugins_page 的「兑换」那一行。走 /api/plugins/redeem，
              兑换成功后后端会把新权益存到本机，所以顺手再刷一次权益。 */}
          <h3 className="card__subtitle">兑换码</h3>
          <div className="p-row">
            <input
              className="p-input"
              value={redeemCode}
              placeholder="把兑换码粘在这里"
              spellCheck={false}
              disabled={redeeming}
              onChange={(e) => setRedeemCode(e.target.value)}
            />
            <button type="button" className="btn btn--primary" disabled={redeeming} onClick={redeem}>
              {redeeming ? '正在兑换…' : '兑换'}
            </button>
          </div>
          <p className="card__note">
            {ent && ent.plan
              ? `当前账号：${ent.email || '已登录'} · ${ent.plan}`
              : ent && ent.email
                ? `当前账号：${ent.email}`
                : '兑换要先登录（设置 → 星群账号）。'}
          </p>
          <div className="p-row">
            <button type="button" className="btn btn--ghost btn--sm" onClick={refreshEnt}>
              刷新权益
            </button>
          </div>

          <h3 className="card__subtitle">和风天气</h3>
          <p className="card__note">
            天气插件要用：先生成密钥对，再贴到和风控制台。
          </p>
          <div className="p-row">
            <button
              type="button"
              className={qweather ? 'btn btn--primary' : 'btn'}
              onClick={() => setQweather((v) => !v)}
            >
              {qweather ? '收起和风天气配置' : '配置和风天气'}
            </button>
          </div>

          <h3 className="card__subtitle">已装列表</h3>
          <p className="card__note">
            「已装能力包」就是清单：选中后「配置」或「卸载」；装上即启用。
          </p>
        </section>
      </div>

      {/* 官网插件市场：和桌面端「插件」页同一份清单（/api/plugins/market）。
          单独一张全宽卡，不去挤上面那两栏的版面。 */}
      <MarketPanel
        busyId={marketBusyId}
        onInstall={installMarket}
        onMsg={setMsg}
      />

      {qweather && <QWeatherCard />}

      <section className="card card--strong pl-local">
        <h2 className="card__title">本地插件（src/plugins）</h2>
        <div className="p-list p-list--72">
          {localInfo.local.map((name) => (
            <div className="p-item" key={name}>
              {`${name}${localInfo.installed.some((p) => p.id === name) ? '' : '（无 plugin.json）'}`}
            </div>
          ))}
        </div>
        {needLogin && <p className="card__note">{LOGIN_HINT}：本地插件清单读不到。</p>}
        <div className="p-row">
          <button type="button" className="btn" onClick={reinstallDeps}>
            重装缺失依赖
          </button>
          <button type="button" className="btn" onClick={loadLocal}>
            刷新列表
          </button>
          <button
            type="button"
            className="btn"
            title={localInfo.dir || '插件目录'}
            onClick={() => copyServerPath(setMsg, '插件目录', pluginsDirPath)}
          >
            复制插件目录
          </button>
          <span className="p-gap" />
          <button
            type="button"
            className="btn btn--primary"
            onClick={restartBot}
            title="装完插件必须重启一次机器人（插件代码不做热重载）；点一下就好，重启日志会写清每步耗时。"
          >
            重启机器人（装完插件点这里）
          </button>
        </div>
      </section>

      {/* 「安装任务」原来是一张只有标题的空卡片。改成本地/后台任务的真状态：
          这一页发起的「重装缺失依赖」与「PyPI 安装」都会写 /api/deps/status，
          进度与最后一行输出就在这里显示，不用再去「依赖」页看。 */}
      <section className="card pl-tasks">
        <h2 className="card__title">安装任务</h2>
        <p className="card__note">
          {pkgBusy
            ? `正在从 PyPI 安装：${pkgStage || '准备中…'}`
            : deps && deps.running
              ? `后台任务进行中：${deps.stage || '执行中…'}`
              : deps && deps.error
                ? `上次任务失败：${deps.error}`
                : deps && Array.isArray(deps.missing) && deps.checked
                  ? `上次检查 ${deps.checked} 项依赖，缺失 ${deps.missing.length} 项`
                  : '当前没有安装任务。'}
        </p>
      </section>

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}

/* 日志：与桌面端 logs_page.py 1:1。
   实测：玻璃面板内边距 12/16/14、间距 6；过滤行 = 来源标签/下拉(100) + addSpacing(12) +
   级别标签/下拉(88) + 撑开 + 打开日志目录(108) + 清空视图(82)（按钮间 6）。
   日志视图：等宽 12px、底 rgba(8,11,18,.72)、1px rgba(255,255,255,.08)、圆角 10、内边距 10、色 #C6D0E0。
   行格式与桌面端一致：`[HH:MM:SS] [来源][级别] 正文`；级别按桌面端 _infer_level 的同一套关键词推断。
   数据源：桌面端是「任务/管理器信号 + NoneBot 日志文件尾部」，无头端一条 SSE 对一个文件
   （/api/logs/stream?name=headless 跟无头端自己的进程访问日志，?name=nonebot 跟机器人日志）。
   来源下拉原来有「任务 / 管理器 / NoneBot」三项，但流里每一行都被硬编码成
   「管理器」，选「任务」「NoneBot」永远是空面板 —— 属于点了没用的假筛选项。
   后端已补上 ?name=nonebot（logs.LOG_FILES 白名单，只收名字不收路径），
   于是「NoneBot」这一项现在有真实数据了，重新加回来。 */
const LOG_SOURCES = ['全部', '管理器', '机器人'];
/* 来源 → 后端日志名（logs.LOG_FILES 的键）。名字写死在这里，页面上永远给不出路径。 */
const LOG_STREAM_NAMES = { 管理器: 'headless', 机器人: 'nonebot' };
const LOG_LEVELS = ['全部', 'INFO', 'OK', 'WARN', 'ERROR'];

function inferLevel(line) {
  const low = line.toLowerCase();
  if (low.includes('error') || low.includes('traceback') || low.includes('exception') || low.includes('failed')) {
    return 'ERROR';
  }
  if (low.includes('warn')) return 'WARN';
  if (low.includes('success') || low.includes('ok') || line.includes('完成')) return 'OK';
  return 'INFO';
}

function Logs({ loggedIn = true }) {
  const [entries, setEntries] = useState([]);
  const [source, setSource] = useState('全部');
  const [level, setLevel] = useState('全部');
  const [msg, setMsg] = useState('');
  // 关键字过滤：日志一多，靠眼睛翻是不现实的（桌面端也有这一条）
  const [keyword, setKeyword] = useState('');
  // 实时跟随：默认开（新日志来就贴底）；关掉之后可以安心往回翻，不会被拽下去
  const [follow, setFollow] = useState(true);
  // 流的状态：'' 正常；否则是一句给用户看的实话（未登录 / 断开）
  const [streamNote, setStreamNote] = useState('');
  const boxRef = useRef(null);

  useEffect(() => {
    const stamp = () => new Date().toTimeString().slice(0, 8);
    const push = (msgs, src) => {
      const rows = msgs
        .map((m) => String(m).replace(/\x1b\[[0-9;]*m/g, '').trimEnd())
        .filter((m) => m.trim())
        .map((m) => ({ t: stamp(), source: src, level: inferLevel(m), msg: m }));
      if (!rows.length) return;
      setEntries((prev) => [...prev, ...rows].slice(-8000));
    };

    // 未登录时后端 _require_auth 直接 401：EventSource 会静默失败，
    // 页面上只剩一个永远空的黑框。这里先判登录态，别白试一次。
    if (!loggedIn) {
      setStreamNote(`${LOGIN_HINT}。实时日志带 AI key / 微信 token / 对话内容，必须登录才能看。`);
      return undefined;
    }

    /* 每个来源一条 SSE：「全部」= 两条流都在跑，每行按自己那条流打来源标记，
       于是「来源」筛选变成纯前端过滤，选哪一项都有真数据。
       后端用 ?name= 选文件（白名单，只收名字不收路径），这里传的就是上面那张表里的常量。 */
    const streams = Object.entries(LOG_STREAM_NAMES).map(([src, name]) => {
      const es = new EventSource(authUrl(`/api/logs/stream?name=${encodeURIComponent(name)}`));
      es.onopen = () => setStreamNote('');
      es.onmessage = (ev) => push(String(ev.data || '').split('\n'), src);
      /* 没有 onerror 时用户看到的就是「卡住的黑框」：token 过期（401）、服务重启、
         或后端 10 分钟主动结束连接（logs.afollow 的 max_seconds）都会走到这里。
         浏览器会自己按 EventSource 的规则重连，所以文案说「正在自动重连」。 */
      es.onerror = () => {
        setStreamNote(
          `${src} 日志流已断开，正在自动重连。一直连不上就重新登录。`,
        );
      };
      return es;
    });
    return () => streams.forEach((es) => es.close());
  }, [loggedIn]);

  useEffect(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [entries, follow]);

  const kw = keyword.trim().toLowerCase();
  const shown = entries.filter(
    (e) => (source === '全部' || e.source === source)
      && (level === '全部' || e.level === level)
      && (!kw || e.msg.toLowerCase().includes(kw)),
  );

  return (
    <>
      <header className="page-head">
        <div>
          <h1>日志</h1>
          <p>管理器与机器人两路日志实时推送，可按来源与级别筛选。</p>
        </div>
      </header>

      <section className="card card--strong log-panel">
        <div className="log-bar">
          <span>来源</span>
          <select className="log-select log-select--src" value={source} onChange={(e) => setSource(e.target.value)}>
            {LOG_SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <span className="log-bar__gap">级别</span>
          <select className="log-select log-select--lv" value={level} onChange={(e) => setLevel(e.target.value)}>
            {LOG_LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
          <input
            className="p-input log-kw"
            value={keyword}
            placeholder="按关键字过滤，如 错误 / 微信"
            spellCheck={false}
            title="只看含这几个字的行；清空就恢复全部"
            onChange={(e) => setKeyword(e.target.value)}
          />
          <span className="tgl-inline log-follow" title="关掉后新日志不会再把你拽到底部">
            <span>实时跟随</span>
            <Toggle
              checked={follow}
              ariaLabel="实时跟随日志"
              onChange={(v) => setFollow(v)}
            />
          </span>
          <button
            type="button"
            className="btn"
            title="拉到最新一行"
            onClick={() => {
              setFollow(true);
              if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
            }}
          >
            跳到最新
          </button>
          <span className="log-bar__spacer" />
          <button
            type="button"
            className="btn"
            title="无头端没有本地文件管理器：点这里把日志文件下载下来（按当前「来源」选文件；来源为「全部」时下载管理器的日志）"
            onClick={() => {
              // 下载接口同样 _require_auth：未登录时开新标签页只会得到一个 401 空白页
              if (!loggedIn) {
                setMsg(`${LOGIN_HINT}（下载日志文件也需要鉴权）`);
                return;
              }
              const name = LOG_STREAM_NAMES[source] || LOG_STREAM_NAMES['管理器'];
              window.open(authUrl(`/api/logs/download?name=${encodeURIComponent(name)}`), '_blank');
            }}
          >
            下载日志文件
          </button>
          <button type="button" className="btn" onClick={() => setEntries([])}>
            清空视图
          </button>
        </div>

        {streamNote && <p className="card__note log-note">{streamNote}</p>}

        <textarea
          className="log-view"
          ref={boxRef}
          readOnly
          spellCheck={false}
          value={shown.map((e) => `[${e.t}] [${e.source}][${e.level}] ${e.msg}`).join('\n')}
        />

        {/* 有日志但筛没了，要说清楚是筛选的问题，不是「日志没了」 */}
        {!streamNote && entries.length > 0 && shown.length === 0 && (
          <p className="card__note log-note">
            当前筛选没有匹配的日志，放宽试试。
          </p>
        )}
        {follow && shown.length > 0 && (
          <p className="card__note log-note">
            {`实时跟随中，共 ${shown.length} 行。`}
          </p>
        )}
      </section>

      {/* 日志保留的入口就放在日志页里：看完日志顺手就能改保留天数 / 清旧日志，
          不用再跑去「全局管理」找。默认收起，不抢日志面板的版面。 */}
      <details className="fold">
        <summary className="fold__summary">日志保留与清理</summary>
        <div className="fold__body">
          <LogRetention onMsg={setMsg} />
        </div>
      </details>

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}

const PERSONA_PRESETS = {
  星群助手:
    '你是星群 AI 助手，回答简洁直接，只给关键信息。',
};

/* 人设卡片「谁在生效」那一行的文案（导出是为了让冒烟直接断言，不用渲染）。
   优先级与后端一致：本地人设 > 预设档案 > 手写人格文本（本地人设启用时后端会把档案关掉，二者互斥）。 */
export function effectivePersonaText(form, activePersona, presetName) {
  if (activePersona) return `当前生效：本地人设《${activePersona.name || activePersona.id}》`;
  if (form && form.agent_profile_enabled) return `当前生效：预设档案 ${presetName || '星群助手'}`;
  return '当前生效：手写人设';
}

/* ------------------------------------------------------------------ AI 大脑
   与桌面端 brain_page.py 1:1：
   - 小白模式（默认）只显示「接口配置 + 人设与主动聊天 + 底部空态」；
     桌面端 BrainPage.set_beginner() 同样把 智能体档案 / 人设工坊 / 记忆与 MCP
     三块 setVisible(False)，并隐藏「飞书 / 纸飞机」两个平台开关。
   - 卡片内间距实测 10（不是 6），行内「标签→控件」间距 10，标签宽度按文字自适应。
   - 保存：桌面端一个「保存 AI 配置」按钮统一写接口 + 人设 + 主动聊天；
     无头端 /api/config 目前只认 ai_provider / ai_base_url / ai_model / ai_api_key，
     人设走 /api/ai/personality，其余项等后端补齐（先按 UI 对齐）。 */
const AI_PROVIDERS = [
  ['custom', '自定义接口', '', ''],
  ['deepseek', 'DeepSeek', 'https://api.deepseek.com', 'deepseek-v4-flash'],
  ['dashscope', '通义千问（百炼）', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen3.7-flash'],
  ['zhipu', '智谱 GLM', 'https://open.bigmodel.cn/api/paas/v4', 'glm-5.2'],
  ['moonshot', 'Kimi（Moonshot）', 'https://api.moonshot.cn/v1', 'kimi-k3'],
  ['openai', 'OpenAI', 'https://api.openai.com/v1', 'gpt-5.6'],
];
const AGENT_PROFILES = [
  ['star_helper', '星群助手'],
  ['liqinghan', '李清菡'],
];
// 桌面端 _spin()：label 在上、QSpinBox 在下的定宽（118）小格，3 列 × 2 行
const SPIN_LABELS = {
  ai_interval_min: '间隔最小(分)',
  ai_interval_max: '间隔最大(分)',
  ai_cooldown: '冷却(分)',
  ai_quiet_start: '免打扰开始(时)',
  ai_quiet_end: '免打扰结束(时)',
};
/* 数值项的**纯标签 + 单位**（给 NumberField 用：标签写「间隔最小」、单位后缀写「分」，
   不再把单位塞在括号里 —— 单位是控件的一部分，不该混在标签文字里）。 */
const SPIN_META = {
  ai_interval_min: { label: '间隔最小', unit: '分' },
  ai_interval_max: { label: '间隔最大', unit: '分' },
  ai_cooldown: { label: '冷却', unit: '分' },
  ai_quiet_start: { label: '免打扰开始', unit: '时' },
  ai_quiet_end: { label: '免打扰结束', unit: '时' },
};
// 内置记忆三档（与后端 qbotmanager.core.ai_config 的 MEMORY_LEVEL_* 逐字一致）。
// 「无」= 不记不读；「基础」= 现在线上的行为（只记事实和日记、带最近 3 条）；
// 「智能」= 再加高权重与承诺必带、时间线、印象档合并、隐私过滤、可查可删。
const MEMORY_LEVELS = [
  ['off', '无'],
  ['basic', '基础'],
  ['smart', '智能'],
];
const MEMORY_LEVEL_LABELS = { off: '无', basic: '基础', smart: '智能' };
const MEMORY_LEVEL_HINTS = {
  off: '无：完全不记也不读，等于回到没有记忆的状态。',
  basic: '基础：只记事实和日记，每次带上最近 3 条记忆——现在线上就是这一档。',
  smart: '智能：在基础之上加权重/承诺必带、时间线、印象档合并、隐私过滤与可查可删。',
};
// 桌面端 Settings 里已有、但无头端 /api/config 还没接的项：先给同款默认值，UI 与桌面端一致
const BRAIN_DEFAULTS = {  ai_provider: 'custom',
  ai_base_url: '',
  ai_model: '',
  ai_api_key: '',
  ai_platforms: { qq: true, wechat: true, feishu: true, telegram: true },
  agent_profile_enabled: false,
  agent_profile_id: 'star_helper',
  agent_wake_words: '',
  agent_wake_auto: true,
  agent_owner_openid: '',
  vision_api_url: '',
  vision_model: '',
  vision_api_key: '',
  ai_proactive: false,
  ai_proactive_targets: '',
  ai_proactive_groups: '',
  ai_interval_min: 30,
  ai_interval_max: 90,
  ai_cooldown: 30,
  ai_quiet_start: 0,
  ai_quiet_end: 8,
  memory_enabled: true,
  /* 内置记忆三档（无/基础/智能）。真实值从 /api/ai/settings 读（见下面的 useEffect），
     这里只是首帧兜底：桌面端与控制台默认都是「基础」= 现在线上的行为。 */
  memory_level: 'basic',
  mcp_enabled: false,
  mcp_servers: {},
};

// 导出是为了让冒烟把它真渲染一遍并点一次胶囊（人设整合后必须锁住：一张卡、两段、互斥、状态行）
// profiles 可注入：生产环境就是内置的那份清单；冒烟传空数组验「还没有下载档案」那一句。
export function AiBrain({ config, onSaved, beginner, loggedIn = true, profiles = AGENT_PROFILES }) {
  const [form, setForm] = useState(null);
  const [personality, setPersonality] = useState('');
  const [msg, setMsg] = useState('');
  const [installed, setInstalled] = useState(false);
  /* 记忆 / 身份绑定的真实数量。以前这一块三行写死 0 条 / 0 个用户 / 0 组绑定，
     无论机器人里有多少记忆都显示 0 —— 数据其实一直有：GET /api/stats/summary 返回
     memory_global / memory_users / identity_binds（首页「运行数据」用的就是它）。
     未登录时该接口 401，退回「—」并给一句实话，不编数字。 */
  const [stats, setStats] = useState(null);
  const [personas, setPersonas] = useState([]);
  const [personaSel, setPersonaSel] = useState('');
  const [personaBusy, setPersonaBusy] = useState(false);
  const [guide, setGuide] = useState('');            // 非空 = 就地展开模板说明
  const [importPanel, setImportPanel] = useState(false);
  const [importText, setImportText] = useState('');
  const [importName, setImportName] = useState('');
  /* 人设卡片的「胶囊」两态与本卡片内就地提示。
     personaPill：滑块滑到哪一段（'preset' / 'custom'），纯视觉；
     personaHint：切换时改了什么的实话（例如「已切到自定义：预设档案已停用」）。 */
  const [personaPill, setPersonaPill] = useState('preset');
  const [personaHint, setPersonaHint] = useState('');
  const segWrapRef = useRef(null);
  const segPresetRef = useRef(null);
  const segCustomRef = useRef(null);
  const [mcpText, setMcpText] = useState('{}');
  /* 「现在记住了什么」：读内置记忆库（GET /api/memory/builtin）。
     peek=null 表示还没点过；peekMsg 是读不到时的一句实话。 */
  const [peek, setPeek] = useState(null);
  const [peekMsg, setPeekMsg] = useState('');
  /* MCP 状态必须走专用接口 /api/mcp/config（返回 {ok, mcp:{enabled,servers}}）。
     以前从 /api/config 读 mcp_enabled / mcp_servers，而后端 headless_config 的
     DEFAULTS / SAFE_KEYS 根本没有这两个键 → 永远 undefined → 保存时 !!undefined 恒 false，
     用户只是改了模型名，已开启的 MCP 就被静默关掉（界面上的服务器框也永远是 {}）。
     mcpBase：null = 还没读到；false = 读不到（未登录/接口不可用）—— 读不到就绝不写 MCP。 */
  const [mcpBase, setMcpBase] = useState(null);
  // 用户是否真的动过 MCP 控件（勾选框 / JSON 框）；没动过就不提交，避免无谓写入
  const [mcpTouched, setMcpTouched] = useState(false);
  const [wakeBusy, setWakeBusy] = useState(false);
  /* 「获取模型」：按当前服务商地址 + 密钥拉一次真实模型列表（桌面端 brain_page 同一个按钮）。
     models = 服务商回的模型名；modelMsg = 一句人话结果（拉不到也能手填模型名）。 */
  const [models, setModels] = useState([]);
  const [modelBusy, setModelBusy] = useState(false);
  const [modelMsg, setModelMsg] = useState('');
  const personaFileRef = useRef(null);

  useEffect(() => {
    get('/api/ai/personality')
      .then((d) => setPersonality(d.personality || ''))
      .catch((e) => setMsg(String(e.message)));
  }, []);

  useEffect(() => {
    get('/api/mcp/config')
      .then((d) => {
        const m = (d && d.mcp) || {};
        setMcpBase({
          enabled: Boolean(m.enabled),
          servers: (m.servers && typeof m.servers === 'object') ? m.servers : {},
        });
      })
      .catch(() => setMcpBase(false));
  }, []);

  /* 内置记忆三档 + 空 @ / 上文窗口：真实值存在机器人侧 aichat_manager.json，
     只有 /api/ai/settings 读得到（/api/config 是「星群账号」那一层，不带这些键）。
     读不到就保持兜底值，保存时也不会把这个键写坏——保存只提交用户点过的档位。 */
  /* 机器人侧「上文/超长」这一组（2026-10-12 补界面）：
       empty_at_action      只 @ 不写字时怎么办（轻唤一句 / 不回复）
       context_window_minutes 上文回溯上限（分钟，1~1440）
       max_incoming_chars   单条正文上限（字，200~100000，0 = 不限制）
     这三个以前**只有后端、没有输入框**，用户在控制台上根本改不到。
     「只提交真改过的」：把读到的原值记在 baseline 里，保存时只 PUT 变了的那些 ——
     既不会把没读到的值写成默认，也不会把用户没动过的项反复写一遍。 */
  const robotBaseline = useRef(null);
  useEffect(() => {
    get('/api/ai/settings')
      .then((d) => {
        if (!d) return;
        robotBaseline.current = {
          empty_at_action: d.empty_at_action !== undefined ? String(d.empty_at_action) : '',
          context_window_minutes: typeof d.context_window_minutes === 'number'
            ? d.context_window_minutes : null,
          max_incoming_chars: typeof d.max_incoming_chars === 'number'
            ? d.max_incoming_chars : null,
        };
        setForm((prev) => (prev === null ? prev : {
          ...prev,
          memory_level: d.memory_level === undefined
            ? prev.memory_level : String(d.memory_level || 'basic'),
          memory_enabled: d.memory_level === undefined
            ? prev.memory_enabled : String(d.memory_level) !== 'off',
          memory_level_note: d.memory_level_note || prev.memory_level_note,
          empty_at_action: d.empty_at_action !== undefined
            ? String(d.empty_at_action) : prev.empty_at_action,
          empty_at_info: d.empty_at_info || prev.empty_at_info,
          context_window_minutes: typeof d.context_window_minutes === 'number'
            ? d.context_window_minutes : prev.context_window_minutes,
          context_window_note: d.context_window_note || prev.context_window_note,
          max_incoming_chars: typeof d.max_incoming_chars === 'number'
            ? d.max_incoming_chars : prev.max_incoming_chars,
        }));
      })
      .catch(() => { /* 未登录 / 接口不可用：不提示，保持兜底值 */ });
  }, []);

  useEffect(() => {
    if (form === null && config) {
      const merged = { ...BRAIN_DEFAULTS, ...config };
      setForm(merged);
      setMcpText(JSON.stringify(merged.mcp_servers || {}, null, 2));
    }
  }, [config, form]);

  // MCP 基线读到后回填表单（用户已经动过 MCP 控件就不覆盖他改的值）
  const formReady = form !== null;

  /* 胶囊滑块回填：按已保存的配置把滑块挪到正确那一段（只挪视觉，不改配置 —— silent）。
     首次拿到配置才做一次；之后用户点切换就是显式改配置，不再被回填覆盖。 */
  const pillInitedRef = useRef(false);
  useEffect(() => {
    if (pillInitedRef.current || form === null || !config) return;
    pillInitedRef.current = true;
    const preset = Boolean(({ ...BRAIN_DEFAULTS, ...config }).agent_profile_enabled);
    setPersonaPill(preset ? 'preset' : 'custom');
  }, [config, form]);
  useEffect(() => {
    if (!formReady || !mcpBase || mcpTouched) return;
    setForm((cur) => ({ ...cur, mcp_enabled: mcpBase.enabled, mcp_servers: mcpBase.servers }));
    setMcpText(JSON.stringify(mcpBase.servers || {}, null, 2));
  }, [formReady, mcpBase, mcpTouched]);

  useEffect(() => {
    get('/api/tools/status')
      .then((d) => {
        const list = d.installed || [];
        // 桌面端判据是 (plugins_dir / "ai").is_dir()；无头端取工具包列表里的 ai
        setInstalled(list.some((p) => String(p.id || '').toLowerCase() === 'ai'));
      })
      .catch(() => {});
  }, []);

  // 记忆 / 身份绑定数量：跟首页「运行数据」同一份真数据（未登录就别发这个必 401 的请求）
  useEffect(() => {
    if (!loggedIn) {
      setStats(null);
      return undefined;
    }
    let alive = true;
    get('/api/stats/summary')
      .then((d) => { if (alive) setStats(d); })
      .catch(() => { if (alive) setStats(null); });
    return () => { alive = false; };
  }, [loggedIn]);

  /* -------------------------------------------------------- 人设工坊（本地）
     后端 /api/persona/* 直接包桌面端的 qbotmanager.core.persona_workshop：
     同一个 <数据目录>/personas，同一套人格卡格式，启用后写机器人人格 + 关智能体档案。
     列表的增删改都走真接口，不再弹「桌面端功能」。 */
  const refreshPersonas = async (keepSel) => {
    try {
      const d = await get('/api/persona/list');
      const list = d.items || [];
      setPersonas(list);
      setPersonaSel((cur) => {
        const want = keepSel || cur;
        if (want && list.some((p) => p.id === want)) return want;
        const active = list.find((p) => p.active);
        return (active || list[0] || {}).id || '';
      });
      return list;
    } catch (e) {
      setMsg(apiErrText(e, '人设列表'));
      return [];
    }
  };

  useEffect(() => { refreshPersonas(); }, []);

  const personaName = (p) => (p && (p.name || p.id)) || '';

  /* 胶囊的键盘操作：Tab 能聚焦到胶囊上，左右方向键在两段之间切换（原生分段控件的手感）。
     Tab 焦点跟着选中项走（选中项 tabIndex=0，另一段 -1），所以一次 Tab 进一次就够。 */
  const segOrder = ['preset', 'custom'];
  const onSegKey = (e, key, other, otherRef) => {
    const k = e.key;
    if (k === 'ArrowLeft' || k === 'ArrowRight' || k === 'ArrowUp' || k === 'ArrowDown') {
      e.preventDefault();
      onPersonaPill(other);
      if (otherRef && otherRef.current) otherRef.current.focus();
      return;
    }
    if (k === 'Home' || k === 'End') {
      e.preventDefault();
      const want = k === 'Home' ? segOrder[0] : segOrder[segOrder.length - 1];
      if (want !== key) {
        onPersonaPill(want);
        const ref = want === 'preset' ? segPresetRef : segCustomRef;
        if (ref.current) ref.current.focus();
      }
      return;
    }
    if (k === 'Enter' || k === ' ') {
      e.preventDefault();
      onPersonaPill(key);
    }
  };

  /* 只保存人格设定这一项（自定义/手写人设用）。不碰接口配置、不碰 MCP。 */
  const savePersonality = async () => {
    setPersonaBusy(true);
    try {
      const d = await put('/api/ai/personality', { personality });
      if (d && d.personality !== undefined) setPersonality(d.personality);
      onSaved();
      setMsg('人格设定已保存，重启机器人生效。');
    } catch (e) {
      setMsg(`保存人格设定失败：${e.message}`);
    } finally {
      setPersonaBusy(false);
    }
  };

  /* ------------------------------------------------------------ 谁在生效
     三块并成一张卡片后，用户第一眼要看到「现在到底是谁在说话」。优先级与后端一致：
     本地人设 > 预设档案 > 手写人格文本（本地人设启用时后端会把档案开关关掉，二者互斥）。
     取值放在下面 f 定义之后（effectivePersonaLabel），这里只留切换逻辑。 */

  /* 切换胶囊 = 互斥切换：切到「自定义」就关掉预设档案；切回「预设」就停用本地人设。
     两条路径都会把结果写回服务器（/api/config 的 agent_profile_enabled + local_persona_id），
     并且**一定**就地说明改了什么，绝不静默改状态。 */
  const persistPersonaMode = async (patch) => {
    try {
      const saved = await put('/api/config', patch);
      setForm((cur) => ({ ...(cur || BRAIN_DEFAULTS), ...patch, ...(saved || {}) }));
      onSaved();
      return true;
    } catch (e) {
      setMsg(`保存人设来源失败：${e.message}`);
      return false;
    }
  };

  const onPersonaPill = async (key, opts) => {
    if (key !== 'preset' && key !== 'custom') return;
    if (key === personaPill) return;
    setPersonaPill(key);
    if (opts && opts.silent) return;            // 初始化回填：只挪滑块，不动配置
    if (key === 'preset') {
      // 启用档案 = 停用本地人设（后端 persona/activate 也是这么互斥的）
      const had = Boolean(activePersona);
      if (had) {
        try {
          const d = await post('/api/persona/deactivate', {});
          if (d && d.personality !== undefined) setPersonality(d.personality);
        } catch (e) {
          setMsg(`停用本地人设失败：${e.message}`);
        }
      }
      const ok = await persistPersonaMode({ agent_profile_enabled: true, local_persona_id: '' });
      if (had) await refreshPersonas();     // 列表里的「启用中」标记要跟着掉，状态行才会改口
      setPersonaHint(had
        ? '已切到预设：本地人设已停用，改用档案里的人格。'
        : '已切到预设：按档案走，本地人设保持停用。');
      if (ok) setMsg('已切到预设档案（重启机器人生效）');
      return;
    }
    // key === 'custom'：用本地人设或手写人格文本 —— 预设档案必须让位
    const hadProfile = Boolean(f.agent_profile_enabled);
    const ok = await persistPersonaMode({ agent_profile_enabled: false });
    setPersonaHint(hadProfile
      ? '已切到自定义：预设档案已停用，人格以本地人设 / 下面这段文字为准。'
      : '已切到自定义：预设档案本来就是关的，人格以本地人设 / 下面这段文字为准。');
    if (ok) setMsg('已切到自定义（本地人设或手写人格，重启机器人生效）');
  };

  const importPersonaPayload = async (payload) => {
    setPersonaBusy(true);
    try {
      const d = await post('/api/persona/import', payload);
      setImportPanel(false);
      setImportText('');
      setImportName('');
      await refreshPersonas(d.id);
      setMsg(`人设已导入：${d.name || d.id}（${d.id}），选中后点「启用」；启用后重启机器人生效。`);
    } catch (e) {
      setMsg(`导入失败：${e.message}`);
    } finally {
      setPersonaBusy(false);
    }
  };

  const onPickPersonaFile = (file) => {
    if (!file) return;
    // 浏览器读文件：文本类（json/md）直接读文本，zip 读成 base64 ——
    // 后端不用 multipart（无头端依赖里没有 python-multipart），统一收 JSON 正文。
    const isZip = /\.zip$/i.test(file.name);
    const reader = new FileReader();
    const done = new Promise((resolve, reject) => {
      reader.onerror = () => reject(new Error('文件读取失败'));
      reader.onload = () => resolve(String(reader.result || ''));
    });
    if (isZip) reader.readAsDataURL(file);
    else reader.readAsText(file, 'utf-8');
    done.then((raw) => importPersonaPayload(isZip
      ? { filename: file.name, content_b64: raw.split(',')[1] || '' }
      : { filename: file.name, content: raw, name: importName }))
      .catch((e) => setMsg(`导入失败：${e.message}`));
  };

  const activatePersona = async () => {
    if (!personaSel) {
      setMsg('请先在人设列表里选中一个本地人设');
      return;
    }
    setPersonaBusy(true);
    try {
      const d = await post('/api/persona/activate', { id: personaSel });
      if (d.personality !== undefined) setPersonality(d.personality);
      set({ agent_profile_enabled: false });   // 与桌面端一致：启用本地人设会关掉硬编码档案
      await refreshPersonas(personaSel);
      onSaved();
      setMsg(`已启用人设：${personaName(d) || personaSel}，重启机器人后生效。`);
    } catch (e) {
      setMsg(`启用失败：${e.message}`);
    } finally {
      setPersonaBusy(false);
    }
  };

  const deactivatePersona = async () => {
    setPersonaBusy(true);
    try {
      const d = await post('/api/persona/deactivate', {});
      if (d.personality !== undefined) setPersonality(d.personality);
      await refreshPersonas();
      setMsg('已停用本地人设，重启机器人后生效。');
    } catch (e) {
      setMsg(`停用失败：${e.message}`);
    } finally {
      setPersonaBusy(false);
    }
  };

  const uninstallPersona = async () => {
    const item = personas.find((p) => p.id === personaSel);
    if (!item) {
      setMsg('请先在人设列表里选中要卸载的人设');
      return;
    }
    // 卸载不可撤销：删的是 <数据目录>/personas/<id>，连同自带工具一起没
    if (!window.confirm(
      `确定卸载人设「${personaName(item)}」吗？\n该人设的文件与自带工具会被删除，不能撤销。`
    )) return;
    setPersonaBusy(true);
    try {
      await post('/api/persona/uninstall', { id: item.id, confirm: true });
      await refreshPersonas();
      setMsg(`人设已卸载：${personaName(item)}`);
    } catch (e) {
      setMsg(`卸载失败：${e.message}`);
    } finally {
      setPersonaBusy(false);
    }
  };

  /* 「现在记住了什么」：拉一次内置记忆库。读不到就说读不到（不显示成「没有记忆」）。 */
  const peekBuiltinMemory = async () => {
    setPeekMsg('');
    try {
      const d = await get('/api/memory/builtin');
      setPeek(d);
      if (!d || !d.rows || !d.rows.length) setPeekMsg('还没有记忆：聊过天之后这里会出现它记住的事。');
    } catch (e) {
      setPeek({ level_label: '—', rows: [] });
      setPeekMsg(`读记忆库失败：${e.message}`);
    }
  };

  const togglePersonaGuide = async () => {    if (guide) {                 // 再点一次收起
      setGuide('');
      return;
    }
    try {
      const d = await get('/api/persona/guide');
      setGuide(d.text || '');
    } catch (e) {
      setMsg(`读取模板说明失败：${e.message}`);
    }
  };

  // 还没拿到配置时的兜底也带上 config（服务端渲染 / 首帧就能说对「谁在生效」，不会先闪一句错的）
  const f = form || { ...BRAIN_DEFAULTS, ...(config || {}) };
  const set = (patch) => setForm({ ...f, ...patch });
  /* 谁在生效：本地人设 > 预设档案 > 手写人格文本（f / personas 都在上面了，顺序不能提前） */
  const activePersona = personas.find((p) => p.active) || null;
  const presetName = (profiles.find(([k]) => k === (f.agent_profile_id || 'star_helper')) || [])
    .slice(1)[0] || '星群助手';
  const effectivePersonaLabel = effectivePersonaText(f, activePersona, presetName);
  const onProvider = (key) => {
    const p = AI_PROVIDERS.find((x) => x[0] === key) || AI_PROVIDERS[0];
    const patch = { ai_provider: key };
    if (p[2]) patch.ai_base_url = p[2];
    if (p[3]) patch.ai_model = p[3];
    set(patch);
  };


  /* 获取模型：后端拿接口地址 + 密钥去问服务商要模型列表（密钥留空/脱敏时用服务器上那把）。
     拉不到不算错：提示一句「可以手填」，不挡保存。 */
  const fetchModels = async () => {
    if (modelBusy) return;
    setModelBusy(true);
    setModelMsg('正在获取模型列表…');
    try {
      const r = await post('/api/ai/models', {
        api_url: f.ai_base_url || '',
        api_key: f.ai_api_key || '',
        protocol: 'openai',
      });
      const list = (r && r.models) || [];
      setModels(list);
      setModelMsg(list.length
        ? `拉到 ${r.count || list.length} 个模型，下面选一个就填进「模型」`
        : '服务商没回模型列表 —— 直接手填模型名即可');
    } catch (e) {
      setModels([]);
      setModelMsg(`没拉到（${e.message}）—— 可以直接手填模型名，保存不受影响`);
    } finally {
      setModelBusy(false);
    }
  };

  const genWake = async () => {
    setWakeBusy(true);
    try {
      // 后端按人格文本判定并提取称呼（不调用外部模型，不产生费用，也不会再 404）。
      // 把界面上正在编辑的人格一起带过去，改完人格不用先保存也能生成。
      const d = await post('/api/ai/wake-words', {
        persona_mode: f.agent_persona_mode || '',
        personality,
      });
      if (d.words && d.words.length) set({ agent_wake_words: d.words.join(',') });
      setMsg(d.source === 'default'
        ? '人格文本里没提到称呼，已用默认唤醒词，可手动修改（保存后重启机器人生效）'
        : '唤醒词已生成，可手动修改（保存后重启机器人生效）');
    } catch (e) {
      setMsg(`唤醒词生成失败：${e.message}`);
    } finally {
      setWakeBusy(false);
    }
  };

  const save = async () => {
    try {
      let servers = f.mcp_servers || {};
      try {
        servers = JSON.parse(mcpText || '{}');
      } catch {
        setMsg('MCP 配置格式错误: MCP 配置必须是 JSON 对象，例如 {"名称": {...}}');
        return;
      }
      // 无头端 /api/config 只接这些键（与后端 SAFE_KEYS 对齐）。
      // 以前只 PUT 4 项，界面上改了的平台开关/智能体档案等 15+ 项被静默丢弃却提示「已保存」。
      const HEADLESS_CFG_KEYS = [
        'ai_provider', 'ai_base_url', 'ai_model', 'ai_api_key',
        'ai_platforms',
        'agent_profile_enabled', 'agent_profile_id', 'agent_owner_qq',
        'agent_groups', 'agent_address_words', 'agent_wake_words', 'agent_wake_auto',
        'vision_api_url', 'vision_api_key', 'vision_model',
      ];
      const payload = {};
      HEADLESS_CFG_KEYS.forEach((k) => {
        if (f[k] !== undefined) payload[k] = f[k];
      });
      const savedCfg = await put('/api/config', payload);
      setForm({ ...f, ...savedCfg, mcp_servers: servers });
      await put('/api/ai/personality', { personality });
      /* 内置记忆三档走专用接口（与桌面端「AI 大脑」页写的是同一份
         aichat_manager.json）。机器人侧**每条消息现读**这份配置
         （core/memory/policy.py::level()），所以改完立即生效，不用重启。 */
      let memMsg = '';
      /* 「上文 / 超长 / 只 @ 不写字」三项目前和记忆档位走同一个接口（/api/ai/settings），
         打包成一次 PUT；只提交**相对读到的基线真的变了**的键。
         没读到基线（未登录）时一个都不提交 —— 免得把服务器上的值写成界面默认值。 */
      const extra = {};
      const baseLine = robotBaseline.current;
      if (baseLine) {
        if (baseLine.empty_at_action
          && String(f.empty_at_action || '') !== baseLine.empty_at_action) {
          extra.empty_at_action = String(f.empty_at_action);
        }
        if (baseLine.context_window_minutes !== null
          && Number(f.context_window_minutes) !== baseLine.context_window_minutes) {
          extra.context_window_minutes = Number(f.context_window_minutes);
        }
        if (baseLine.max_incoming_chars !== null
          && Number(f.max_incoming_chars) !== baseLine.max_incoming_chars) {
          extra.max_incoming_chars = Number(f.max_incoming_chars);
        }
      }
      const wantSettings = Boolean(f.memory_level) || Object.keys(extra).length > 0;
      if (wantSettings) {
        const body = { ...extra };
        if (f.memory_level) body.memory_level = String(f.memory_level);
        try {
          const r = await put('/api/ai/settings', body);
          const changed = (r && r.changed) || [];
          const bits = [];
          if (body.memory_level) {
            bits.push(changed.includes('memory_level')
              ? `记忆档位：${MEMORY_LEVEL_LABELS[f.memory_level] || f.memory_level}`
              : '记忆档位没变');
          }
          if (extra.empty_at_action) bits.push('只 @ 不写字的处理方式');
          if (extra.context_window_minutes !== undefined) bits.push('上文回溯上限');
          if (extra.max_incoming_chars !== undefined) bits.push('单条正文上限');
          // 后端还没接 max_incoming_chars 时不能装成存上了：看 changed 里有没有它
          if (extra.max_incoming_chars !== undefined && !changed.includes('max_incoming_chars')) {
            bits.push('单条正文上限**服务器还没接这个接口，本次没写进去**');
          }
          if (bits.length) memMsg = `；${bits.join('、')}`;
          if (r && typeof r.max_incoming_chars === 'number') {
            robotBaseline.current = {
              ...(robotBaseline.current || {}),
              max_incoming_chars: r.max_incoming_chars,
            };
          }
        } catch (e) {
          memMsg = `；上文/超长那一组保存失败：${e.message}`;
        }
      }
      /* MCP 走专用接口（存进机器人侧读的那份 manager 文件）。
         写之前必须确认「读到了基线」：读不到（未登录 / 接口不可用）时一个字节都不写，
         否则就会重现「不写 MCP 却把写成 enabled:false」这种把配置改坏的问题。
         读到基线时也要真变了（或用户确实动过控件）才提交，保证保存不动服务器上的 MCP。 */
      const base = mcpBase || null;
      const serversChanged = !base || JSON.stringify(servers) !== JSON.stringify(base.servers || {});
      const enabledChanged = !base || Boolean(f.mcp_enabled) !== base.enabled;
      let mcpMsg = '';
      if (base) {
        if (mcpTouched || serversChanged || enabledChanged) {
          try {
            const r = await post('/api/mcp/save', { enabled: Boolean(f.mcp_enabled), servers });
            mcpMsg = `；MCP 已保存 ${r.servers} 个服务器`;
            setMcpBase({ enabled: Boolean(f.mcp_enabled), servers });
            setMcpTouched(false);
          } catch (e) {
            mcpMsg = `；MCP 保存失败：${e.message}`;
          }
        }
      } else if (mcpTouched) {
        mcpMsg = '；MCP 状态没读到，本次未改动服务器上的 MCP 配置（登录后重试）';
      }
      setMsg(
        `AI 配置已保存 ${Object.keys(payload).length} 项（模型 / 智能体档案 / 视觉）${mcpMsg}${memMsg}，` +
        '**改完立即生效（不用重启机器人）**；' +
        '主动聊天与记忆等数值在「插件详情页」里改。'
      );
      setMcpText(JSON.stringify(servers, null, 2));
      onSaved();
    } catch (e) {
      setMsg(String(e.message));
    }
  };

  const headlessTodo = (what) => setMsg(
    `${what}：这是桌面端（Windows 版）的功能，无头端暂未接入 —— 记忆时间线、本地知识库请在桌面版里操作。`
  );
  /* 数值项（2026-10-12）：原来是一排裸 input[type=number]（.num-grid），
     现在统一走 NumberField：标签 + −/+ 步进 + 范围提示；跨度 ≤ 20 档的（免打扰时段 0~23）
     自动变滑杆。这些项**低频**，由下面那两处 Collapse 收起来，标题行给当前值摘要。 */
  const num = (key, lo, hi) => {
    const meta = SPIN_META[key] || { label: SPIN_LABELS[key] || key, unit: '' };
    return (
      <NumberField
        key={key}
        label={meta.label}
        value={f[key]}
        min={lo}
        max={hi}
        unit={meta.unit}
        onChange={(v) => set({ [key]: v })}
      />
    );
  };

  return (
    <>
      <header className="page-head">
        <div>
          <h1>AI 大脑</h1>
          <p>QQ / 微信 / 飞书 / 纸飞机共用一个 AI 大脑。</p>
        </div>
      </header>

      {/* 顶部状态摘要：先回答"现在什么状态、下一步做什么"，细节都在下面的分区里 */}
      <section
        className="card"
        style={{
          marginBottom: 18,
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '10px 16px',
          padding: '12px 16px',
        }}
      >
        <StatusBadge tone={f.ai_api_key ? 'running' : 'warn'}>
          {f.ai_api_key ? '模型已配置' : '还没填 API 密钥'}
        </StatusBadge>
        <span className="card__note" style={{ margin: 0 }}>
          模型 <b>{f.ai_model || '未配置'}</b>
          {' · 平台 '}
          {[
            ['qq', 'QQ'],
            ['wechat', '微信'],
            ['feishu', '飞书'],
            ['telegram', '纸飞机'],
          ]
            .map(([k, label]) => `${label}${f.ai_platforms?.[k] === false ? '✗' : '✓'}`)
            .join(' / ')}
          {' · 人格 '}
          <b>
            {activePersona
              ? `本地人设《${personaName(activePersona)}》`
              : (f.agent_profile_enabled
                ? `智能体档案（${f.agent_profile_id || '默认'}）`
                : '内置人格')}
          </b>
        </span>
        {!f.ai_api_key && (
          <span className="card__note" style={{ margin: 0, color: '#f0b429' }}>
            → 先在下面「连接模型」里填 API 密钥，保存后重启机器人
          </span>
        )}
      </section>

      <section className="card card--strong card--brain">
        <h2 className="card__title">接口配置</h2>
        <p className="card__note">
          选择服务商自动填接口地址与默认模型（预设已按官方文档核对）；平台开关决定 AI
          在哪些平台生效，<b>保存后立即生效，不用重启机器人</b>。
        </p>

        {/* 2026-10-12：原来四个平台是一排 16px 的裸复选框（老板说的"开关太 low"就是这个）。
            换成同一套开关：整块可点、键盘 Tab 可达、空格切换、有焦点环。
            这四个是**功能开关**，不折进折叠区 —— 藏起来会让人以为"这个平台不支持了"。 */}
        <div className="kv-row kv-row--platform">
          <span>平台启用</span>
          {[
            ['qq', 'QQ'],
            ['wechat', '微信'],
          ].map(([key, label]) => (
            <span className="tgl-inline" key={key} title="关掉后 AI 大脑不再处理该平台的消息。">
              <span>{label}</span>
              <Toggle
                checked={Boolean((f.ai_platforms || {})[key])}
                ariaLabel={`${label} 平台启用`}
                onChange={(v) => set({ ai_platforms: { ...(f.ai_platforms || {}), [key]: v } })}
              />
            </span>
          ))}
          {!beginner &&
            [
              ['feishu', '飞书'],
              ['telegram', '纸飞机'],
            ].map(([key, label]) => (
              <span className="tgl-inline" key={key}>
                <span>{label}</span>
                <Toggle
                  checked={Boolean((f.ai_platforms || {})[key])}
                  ariaLabel={`${label} 平台启用`}
                  onChange={(v) => set({ ai_platforms: { ...(f.ai_platforms || {}), [key]: v } })}
                />
              </span>
            ))}
        </div>

        <div className="kv-row kv-row--wide">
          <span>服务商</span>
          <select
            value={f.ai_provider || 'custom'}
            onChange={(e) => onProvider(e.target.value)}
            title="选大厂服务商自动填地址与模型，也可自定义。"
          >
            {AI_PROVIDERS.map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </div>

        <div className="kv-row kv-row--wide">
          <span>接口地址</span>
          <input
            value={f.ai_base_url ?? ''}
            placeholder="https://api.deepseek.com"
            spellCheck={false}
            onChange={(e) => set({ ai_base_url: e.target.value })}
          />
        </div>

        <div className="kv-row kv-row--wide">
          <span>模型</span>
          <input
            value={f.ai_model ?? ''}
            placeholder="deepseek-v4-flash"
            spellCheck={false}
            onChange={(e) => set({ ai_model: e.target.value })}
          />
        </div>

        <div className="kv-row kv-row--wide">
          <span>API 密钥</span>
          <input
            type="password"
            value={f.ai_api_key ?? ''}
            placeholder="sk-..."
            spellCheck={false}
            onChange={(e) => set({ ai_api_key: e.target.value })}
          />
        </div>

        <div className="btn-row">
          <PillButton
            disabled={modelBusy || !String(f.ai_base_url || '').trim()}
            loading={modelBusy}
            title="按上面的接口地址和密钥，问服务商要一次真实模型列表"
            onClick={fetchModels}
          >
            {modelBusy ? '获取中…' : '获取模型'}
          </PillButton>
          <PillButton tone="primary" onClick={save}>
            保存 AI 配置
          </PillButton>
        </div>
        {modelMsg && <p className="card__note">{modelMsg}</p>}
        {models.length > 0 && (
          <div className="kv-row kv-row--wide">
            <span>模型列表</span>
            <select
              value=""
              title="选一个就填到上面的「模型」里"
              onChange={(e) => { if (e.target.value) set({ ai_model: e.target.value }); }}
            >
              <option value="">选一个填进「模型」</option>
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
        )}
      </section>

      {/* 人设：档案（预设）/ 本地人设（自定义）/ 人格文本 / 主动聊天 —— 合成一张卡片。
          以前这是三张卡（智能体档案 / 人设工坊（本地）/ 人设与主动聊天），功能重合、
          设置框离得远，用户看不出「到底谁在生效」。现在顶部一个胶囊切换来源，
          下面固定放主动聊天这几项（跟人设来源无关）。
          小白模式也显示这张卡：与桌面端 set_beginner() 一致（它只藏档案/工坊/记忆，
          人设与主动聊天这一块始终在）。 */}
      <section className="card card--strong card--brain">
        <h2 className="card__title">人设与主动聊天</h2>
        <p className="card__note">
          先选人设从哪来：用下载好的档案（预设），还是自己写 / 导入一份（自定义）；
          <b>人设与主动聊天参数改完立即生效，不用重启机器人</b>。
        </p>

        <div className="seg" role="tablist" aria-label="人设来源" ref={segWrapRef}>
          <span className="seg__thumb" data-pos={personaPill} aria-hidden="true" />
          <button
            type="button"
            ref={segPresetRef}
            className={personaPill === 'preset' ? 'seg__btn seg__btn--on' : 'seg__btn'}
            role="tab"
            aria-selected={personaPill === 'preset'}
            tabIndex={personaPill === 'preset' ? 0 : -1}
            title="用下载好的智能体档案（会自动停用本地人设）"
            onClick={() => onPersonaPill('preset')}
            onKeyDown={(e) => onSegKey(e, 'preset', 'custom', segCustomRef)}
          >
            预设
          </button>
          <button
            type="button"
            ref={segCustomRef}
            className={personaPill === 'custom' ? 'seg__btn seg__btn--on' : 'seg__btn'}
            role="tab"
            aria-selected={personaPill === 'custom'}
            tabIndex={personaPill === 'custom' ? 0 : -1}
            title="用本地人设或自己写的人格文本（会自动停用预设档案）"
            onClick={() => onPersonaPill('custom')}
            onKeyDown={(e) => onSegKey(e, 'custom', 'preset', segPresetRef)}
          >
            自定义
          </button>
        </div>
        <p className="card__note" style={{ marginTop: 0 }}>
          预设 = 下载好的智能体档案；自定义 = 自己写或导入的人设。两段互斥，切换会告诉你改了什么。
        </p>

        <p
          className={activePersona
            ? 'brain-status brain-status--local'
            : (f.agent_profile_enabled ? 'brain-status brain-status--profile' : 'brain-status')}
        >
          {effectivePersonaLabel}
        </p>
        {personaHint && <p className="card__note brain-pill-hint">{personaHint}</p>}

        {personaPill === 'preset' ? (
          <div className="persona-pane" role="tabpanel" aria-label="预设">
            <p className="card__note">
              挑个档案打开开关，就有群管、表情、游戏等功能。
            </p>
            <ToggleRow
              checked={Boolean(f.agent_profile_enabled)}
              label="启用档案"
              desc="开启后按档案启用群管、语音、图片、游戏。"
              onChange={(on) => {
                set({ agent_profile_enabled: on });
                setPersonaHint(on
                  ? '已启用预设档案：保存后按档案走（人格设定仍以上面那段文字为准）。'
                  : '已关掉预设档案：改成手写人格或本地人设。');
              }}
            />

            {profiles.length ? (
              <div className="kv-row kv-row--wide">
                <span>档案选择</span>
                <select
                  value={f.agent_profile_id || 'star_helper'}
                  onChange={(e) => set({ agent_profile_id: e.target.value })}
                  title="星群助手 = 默认行为；李清菡 = 完整功能层预设"
                >
                  {profiles.map(([key, name]) => (
                    <option key={key} value={key}>
                      {name}
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <p className="brain-list__empty">还没有下载档案，去「插件工坊」看看</p>
            )}

            <details style={{ marginTop: 12 }}>
              <summary
                style={{
                  cursor: 'pointer',
                  fontSize: 13,
                  opacity: 0.7,
                  padding: '6px 0',
                }}
              >
                高级设置：唤醒词 · 绑定账号 · 视觉模型（默认收起）
              </summary>
              <p className="card__note" style={{ marginTop: 8 }}>
                唤醒词用于群聊，不用 @ 也能叫醒；私聊直接说。
              </p>

              <div className="kv-row kv-row--wide">
                <span>唤醒词</span>
                <input
                  value={f.agent_wake_words ?? ''}
                  placeholder="逗号分隔，如：李清菡,学姐,菡姐,菡菡（留空 = 档案默认）"
                  spellCheck={false}
                  onChange={(e) => set({ agent_wake_words: e.target.value })}
                />
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={genWake}
                  disabled={wakeBusy}
                  title="按人格文本生成唤醒词；没配模型时按名字提取。"
                >
                  {wakeBusy ? '生成中…' : '按人格生成'}
                </button>
              </div>

              <ToggleRow
                checked={Boolean(f.agent_wake_auto)}
                label="人格变化时自动重生成唤醒词"
                onChange={(v) => set({ agent_wake_auto: v })}
              />

              <div className="kv-row kv-row--wide">
                <span>你的会话标识</span>
                <input
                  value={f.agent_owner_openid ?? ''}
                  placeholder="从「消息中心」复制你的会话标识贴这里"
                  spellCheck={false}
                  onChange={(e) => set({ agent_owner_openid: e.target.value })}
                />
              </div>

              <p className="card__note">视觉模型（图片描述/生成；密钥留空 = 不启用）</p>
              <div className="kv-row kv-row--wide">
                <span>视觉接口地址</span>
                <input
                  value={f.vision_api_url ?? ''}
                  placeholder="https://api.siliconflow.cn/v1"
                  spellCheck={false}
                  onChange={(e) => set({ vision_api_url: e.target.value })}
                />
              </div>
              <div className="kv-row kv-row--wide">
                <span>视觉模型</span>
                <input
                  value={f.vision_model ?? ''}
                  placeholder="Qwen/Qwen3-Omni-30B-A3B-Instruct"
                  spellCheck={false}
                  onChange={(e) => set({ vision_model: e.target.value })}
                />
              </div>
              <div className="kv-row kv-row--wide">
                <span>视觉 API 密钥</span>
                <input
                  type="password"
                  value={f.vision_api_key ?? ''}
                  placeholder="sk-..."
                  spellCheck={false}
                  onChange={(e) => set({ vision_api_key: e.target.value })}
                />
              </div>
            </details>
          </div>
        ) : (
          <div className="persona-pane" role="tabpanel" aria-label="自定义">
          <p className="card__note">
            导入人设包：先下载模板填写，再导入。
            也可以直接粘一段人设文字；启用后会自动关闭预设档案。
          </p>
          <div className="btn-row btn-row--6">
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => window.open(authUrl('/api/persona/template'), '_blank')}
            >
              下载模板
            </button>
            <button type="button" className="btn btn--ghost" onClick={togglePersonaGuide}>
              {guide ? '收起说明' : '模板说明'}
            </button>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => setImportPanel((v) => !v)}
            >
              导入人设
            </button>
            <button type="button" className="btn btn--ghost" onClick={() => refreshPersonas()}>
              刷新
            </button>
          </div>

          {importPanel && (
            <div className="kv-row kv-row--wide kv-row--top">
              <span>导入</span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1 }}>
                <div className="btn-row btn-row--6">
                  <button
                    type="button"
                    className="btn btn--ghost"
                    disabled={personaBusy}
                    onClick={() => personaFileRef.current && personaFileRef.current.click()}
                  >
                    选择文件（.json / .zip）
                  </button>
                  <input
                    ref={personaFileRef}
                    type="file"
                    accept=".json,.zip,.md,.markdown,.txt"
                    style={{ display: 'none' }}
                    onChange={(e) => {
                      const file = e.target.files && e.target.files[0];
                      e.target.value = '';           // 同一个文件能连续选两次
                      onPickPersonaFile(file);
                    }}
                  />
                  <input
                    value={importName}
                    placeholder="人设名称（粘贴文本时用，可留空）"
                    spellCheck={false}
                    onChange={(e) => setImportName(e.target.value)}
                  />
                </div>
                <textarea
                  className="brain-area"
                  value={importText}
                  placeholder="也可以直接粘一段人设文字，再点「导入」。"
                  spellCheck={false}
                  onChange={(e) => setImportText(e.target.value)}
                />
                <div className="btn-row btn-row--6">
                  <button
                    type="button"
                    className="btn btn--primary"
                    disabled={personaBusy || !importText.trim()}
                    onClick={() => importPersonaPayload({ filename: 'pasted.md', content: importText, name: importName })}
                  >
                    导入粘贴内容
                  </button>
                  <button type="button" className="btn btn--ghost" onClick={() => setImportPanel(false)}>
                    取消
                  </button>
                </div>
              </div>
            </div>
          )}

          {guide && (
            <div className="kv-row kv-row--wide kv-row--top">
              <span>模板说明</span>
              <pre
                className="brain-area brain-area--mcp"
                style={{ flex: 1, height: 220, overflow: 'auto', whiteSpace: 'pre-wrap' }}
              >
                {guide}
              </pre>
            </div>
          )}

          <div className="brain-list brain-list--110">
            {personas.length ? (
              personas.map((p) => (
                <div
                  className="brain-list__row"
                  key={p.id}
                  title={`${p.summary || ''}${p.tools && p.tools.length ? `　工具：${p.tools.join('、')}` : ''}`}
                  style={{
                    cursor: 'pointer',
                    background: p.id === personaSel ? 'rgba(88, 152, 255, 0.18)' : 'transparent',
                  }}
                  onClick={() => setPersonaSel(p.id)}
                >
                  {p.name} v{p.version}{p.active ? '（启用中）' : ''}　[工具：{(p.tools || []).join('、') || '无'}]
                  　{p.source}{p.updated_at ? ` · ${p.updated_at}` : ''}
                </div>
              ))
            ) : (
              <p className="brain-list__empty">本机未安装本地人设包</p>
            )}
          </div>
          <div className="btn-row btn-row--6">
            <button
              type="button"
              className="btn btn--ghost"
              disabled={personaBusy}
              onClick={activatePersona}
            >
              启用
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={personaBusy}
              onClick={deactivatePersona}
            >
              停用本地人设
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={personaBusy}
              onClick={uninstallPersona}
            >
              卸载
            </button>
          </div>

          {/* 人格设定 = 最终生效的人设文本：档案 / 本地人设最终都以文本形式落到这里，
              也可以什么都不选、直接手写一段。保存按钮只写这一项（/api/ai/personality）。 */}
          <div className="kv-row kv-row--wide kv-row--top">
            <span>人格设定</span>
            <textarea
              className="brain-area"
              value={personality}
              placeholder="例如：回答简洁直接，不废话…"
              spellCheck={false}
              onChange={(e) => setPersonality(e.target.value)}
            />
          </div>
          <div className="btn-row btn-row--6">
            <button
              type="button"
              className="btn btn--primary"
              disabled={personaBusy}
              title="只保存这一项人格设定，不动接口配置"
              onClick={savePersonality}
            >
              {personaBusy ? '保存中…' : '保存人设'}
            </button>
            <span className="card__note" style={{ margin: 0 }}>
              这里写什么，机器人就按什么演；留空 = 内置默认。
            </span>
          </div>
          </div>
        )}

        {/* 主动聊天：跟人设从哪来无关，两种形态都显示。
            「启用主动聊天」是**关键开关**（开了它会自己找人说话）—— 常显，不折。
            下面那些间隔/冷却/免打扰时段是低频数值 —— 收进折叠区，标题行给当前值摘要。 */}
        <ToggleRow
          checked={Boolean(f.ai_proactive)}
          label="启用主动聊天（机器人会不定时主动找用户聊天）"
          desc="关掉后下面这些频率参数都不生效，它只在被 @ 或私聊时回话。"
          onChange={(v) => set({ ai_proactive: v })}
        />

        <div className="kv-row kv-row--wide">
          <span>目标用户</span>
          <input
            value={f.ai_proactive_targets ?? ''}
            placeholder="多个用逗号分隔；留空 = 不主动找人"
            spellCheck={false}
            aria-label="目标用户"
            onChange={(e) => set({ ai_proactive_targets: e.target.value })}
          />
        </div>

        <div className="kv-row kv-row--wide">
          <span>搭话群聊</span>
          <input
            value={f.ai_proactive_groups ?? ''}
            placeholder="多个用逗号分隔；留空 = 不搭话"
            spellCheck={false}
            aria-label="搭话群聊"
            onChange={(e) => set({ ai_proactive_groups: e.target.value })}
          />
        </div>

        <Collapse
          storageKey="brain-proactive-numbers"
          title="主动聊天频率（5 项）"
          summary={`间隔 ${f.ai_interval_min ?? '—'}–${f.ai_interval_max ?? '—'} 分 · 冷却 ${f.ai_cooldown ?? '—'} 分 · 免打扰 ${f.ai_quiet_start ?? '—'}–${f.ai_quiet_end ?? '—'} 时`}
        >
          <p className="card__note">
            间隔 = 两次主动搭话之间至少/至多隔多久；冷却 = 刚聊完这段时间内不再主动；
            免打扰时段内它不会主动开口（按服务器本地时间）。
          </p>
          {num('ai_interval_min', 1, 1440)}
          {num('ai_interval_max', 1, 1440)}
          {num('ai_cooldown', 1, 1440)}
          {num('ai_quiet_start', 0, 23)}
          {num('ai_quiet_end', 0, 23)}
        </Collapse>
      </section>

      {!beginner && (
        <section className="card card--strong card--brain">
          <h2 className="card__title">记忆与 MCP</h2>
          {/* 内置记忆三档（2026-10-11 老板拍板）：无 / 基础 / 智能。
              存的是机器人侧 aichat_manager.json 的 memory_level。
              **2026-10-11 热重载**：机器人侧每次调用都现读这份配置
              （core/memory/policy.py::level()，带 mtime+size 缓存），
              所以改完立即生效，不用重启机器人。 */}
          <div className="seg seg--3" role="group" aria-label="记忆档位">
            <span
              className="seg__thumb"
              style={{
                transform: `translateX(${Math.max(0, MEMORY_LEVELS.findIndex(([v]) => v === f.memory_level)) * 100}%)`,
              }}
              aria-hidden="true"
            />
            {MEMORY_LEVELS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`seg__btn${f.memory_level === value ? ' seg__btn--on' : ''}`}
                aria-pressed={f.memory_level === value}
                title={MEMORY_LEVEL_HINTS[value]}
                onClick={() => set({
                  memory_level: value,
                  // 「无」= 不记不读：把旧开关一起摆对，避免两处状态互相打架
                  memory_enabled: value !== 'off',
                })}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="card__note">
            记忆档位：<b>{MEMORY_LEVEL_LABELS[f.memory_level] || '基础'}</b> ——
            {MEMORY_LEVEL_HINTS[f.memory_level] || MEMORY_LEVEL_HINTS.basic}
            <b>改完立即生效，不用重启机器人。</b>
          </p>

          {/* 「只 @ 不写字时怎么办」—— 能选档位的就不让人填数字（档位胶囊）。
              默认「轻唤一句」。这是行为开关，不是低频数值，所以放在折叠区**外面**。 */}
          {f.empty_at_info && Array.isArray(f.empty_at_info.options) && (
            <NumberField
              className="numf--wide"
              label={f.empty_at_info.label || '只 @ 不写字时'}
              value={String(f.empty_at_action || (f.empty_at_info && f.empty_at_info.default) || 'greet')}
              options={(f.empty_at_info.options || []).map((o) => ({ value: o.value, label: o.label }))}
              hint={f.empty_at_info.note || ''}
              onChange={(v) => set({ empty_at_action: v })}
            />
          )}

          {/* 上文回溯上限 + 单条正文上限：都是"配一次就不动"的低频数值 → 收进折叠区，
              标题行给当前值摘要。这两个以前**只有后端、没有输入框**，这次补上。 */}
          <Collapse
            storageKey="brain-context-limits"
            title="上文与超长消息（2 项）"
            summary={`上文回溯 ${f.context_window_minutes ?? '—'} 分钟 · 单条上限 ${Number(f.max_incoming_chars) === 0 ? '不限' : `${f.max_incoming_chars ?? '—'} 字`}`}
          >
            <p className="card__note">
              {f.context_window_note || '上文回溯上限：群里旧消息超过这个时间就不进提示词。被显式引用（回复某条）时不受这个限制。'}
            </p>
            <NumberField
              label="上文回溯上限"
              value={f.context_window_minutes ?? 5}
              min={1}
              max={1440}
              unit="分"
              hint="1~1440 分钟；默认 5 分钟"
              onChange={(v) => set({ context_window_minutes: v })}
            />
            <NumberField
              label="单条正文上限"
              value={f.max_incoming_chars ?? 4000}
              min={0}
              max={100000}
              step={100}
              unit="字"
              hint="粘进来的长文会被截到这个长度，免得一次请求太大；填 0 = 不限制"
              onChange={(v) => set({ max_incoming_chars: v })}
            />
          </Collapse>
          <div className="btn-row btn--row-6">
            <button type="button" className="btn" onClick={peekBuiltinMemory}>
              现在记住了什么
            </button>
          </div>
          {peek && (
            <div className="card__note">
              <div>
                档位 {peek.level_label || peek.level || '—'}
                {typeof peek.total === 'number' ? `｜共 ${peek.total} 条` : ''}
                {peek.promises ? `｜承诺 ${peek.promises}` : ''}
                {peek.impressions ? `｜印象档 ${peek.impressions}` : ''}
                {peek.expired ? `｜已过期 ${peek.expired}` : ''}
              </div>
              {Array.isArray(peek.rows) && peek.rows.length > 0 ? (
                <ul className="brain-list">
                  {peek.rows.slice(0, 40).map((r) => (
                    <li key={`${r.qq}-${r.id}`}>
                      [{r.kind}/权重{r.weight}] {String(r.fact).slice(0, 80)}
                      <span className="card__note">（{r.qq}）</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div>{peekMsg || '还没有记忆：聊过天之后这里会出现它记住的事。'}</div>
              )}
              {peek.rows && peek.rows.length > 40 && (
                <div>（只显示最近 40 条，共 {peek.rows.length} 条）</div>
              )}
            </div>
          )}
          {/* 记忆与 MCP：这两个是**隐私相关**的开关（记忆 = 长期留存聊天内容；
              MCP = 让 AI 调外部服务器），一律常显，绝不折进折叠区。 */}
          <ToggleRow
            checked={Boolean(f.memory_enabled)}
            label="启用全局记忆（默认开启，AI 会记住长期事项）"
            desc="关掉后不再写入新记忆，已有的记忆仍在下面「现在记住了什么」里可查可删。"
            onChange={(v) => set({
              memory_enabled: v,
              memory_level: v
                ? (f.memory_level === 'off' ? 'basic' : f.memory_level)
                : 'off',
            })}
          />
          <ToggleRow
            checked={Boolean(f.mcp_enabled)}
            label="启用 MCP 工具（供 AI 调用外部服务器）"
            desc="开启后 AI 可以按下面的地址调用外部 MCP 服务器，等于把外部工具接进对话。"
            onChange={(v) => {
              setMcpTouched(true);
              set({ mcp_enabled: v });
            }}
          />
          <div className="kv-row kv-row--wide kv-row--top">
            <span>MCP 服务器</span>
            <textarea
              className="brain-area brain-area--mcp"
              value={mcpText}
              placeholder={'{\n  "服务器名": {"type": "sse", "enabled": true, "url": "http://127.0.0.1:8000/sse", "headers": {}}\n}'}
              spellCheck={false}
              onChange={(e) => {
                setMcpTouched(true);
                setMcpText(e.target.value);
              }}
            />
          </div>
          <p className="card__note">JSON 格式：sse 用 url/headers；stdio 用 command/args/env；保存后重启机器人生效。</p>
          {mcpBase === false && (
            <p className="card__note">
              MCP 状态没读到，保存时不会改动服务器上的配置。
            </p>
          )}
          <MemoryTimeline />
          <KnowledgeBase />
        </section>
      )}

      {!installed ? (
        <div className="empty-state empty-state--brain">
          <div className="empty-state__title">未检测到内置 AI 插件</div>
          <div className="empty-state__hint">
            重启一次机器人就会自动装好。
          </div>
        </div>
      ) : (
        <section className="card card--brain">
          {[
            ['当前模型', f.ai_model || '未配置'],
            ['MCP', `${f.mcp_enabled ? '已开启' : '未开启'} · ${Object.keys(f.mcp_servers || {}).length} 个服务器`],
            ['全局记忆', stats ? `${stats.memory_global ?? 0} 条` : '—'],
            ['用户记忆', stats ? `${stats.memory_users ?? 0} 个用户` : '—'],
            ['身份绑定', stats ? `${stats.identity_binds ?? 0} 组绑定` : '—'],
          ].map(([label, value]) => (
            <div className="kv-row kv-row--wide" key={label}>
              <span>{label}</span>
              <b>{value}</b>
            </div>
          ))}
          {!stats && <p className="card__note">{LOGIN_HINT}（记忆与身份绑定的数量需要登录后才能读）</p>}
          <div className="btn-row btn-row--6">
            <button
              type="button"
              className="btn"
              title="点这里复制配置文件路径"
              onClick={() => copyServerPath(setMsg, '配置路径', botEnvPath)}
            >
              复制配置路径
            </button>
            <button
              type="button"
              className="btn"
              title="无头端没有本地文件管理器：点这里复制插件目录"
              onClick={() => copyServerPath(setMsg, '插件目录', pluginsDirPath)}
            >
              复制插件目录
            </button>
          </div>
        </section>
      )}

      {/* 机器人 AI（群聊）：群聊回话、引用解析、群里搭话、允许回话的群、免@连续对话。
          和桌面端「AI 大脑 → 人设与主动聊天」同一批设置，数据在机器人侧 aichat_manager.json。
          onOpenPack：「去「连续对话能力包」配置」直接走插件详情那条路由 ——
          这个控制台里详情页就是配置页（参数按 manifest schema 渲染，保存写
          tool_packs/<id>/config.json），所以不用另做跳转/滚动/高亮。 */}
      <BotAiCard
        onMsg={setMsg}
        onOpenPack={(pid) => {
          if (!pid) return;
          setDetailPid(String(pid));
          setDetailEntry(null);
          setView('plugin-detail');
        }}
      />

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}


/* 首页那条横排三步条上的短名（桌面端首页写的就是「1 连 AI / 2 接 QQ / 3 启动」）。
   步骤状态与按钮仍由 setupSteps() 算（那个纯函数的 title 是给竖排视图用的，别动）。 */
const STEP_SHORT = { ai: '连 AI', qq: '接 QQ', bot: '启动' };

/* 首页（导出是为了让冒烟能直接把它渲染一遍：区块顺序 / 标题用词要有断言锁着）。
   结构固定（与桌面端 home_page.py 一一对应）：
     标题区（副标题 + 更新于 + 服务状态点）
     → 横排三步条（右侧带「你想要什么功能」输入框）
     → 平台四卡（一行四张）
     → 今日数据 / 最近消息（两列）
     → 机器人 / 当前任务（两列）
     → 折叠区（本机信息 / 运行数据 / 运维入口） */
export function Overview({ health, auth, services, config, loggedIn, onAccess, onMessages, onServices, onGo = () => {} }) {
  const [msg, setMsg] = useState('');
  const [now, setNow] = useState(() => new Date());
  const [idea, setIdeaText] = useState('');
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!msg) return undefined;
    const timer = setTimeout(() => setMsg(''), 3200);
    return () => clearTimeout(timer);
  }, [msg]);

  /* 微信卡的状态（桌面端首页那张卡同源）：/api/wechat/status。
     SSR（冒烟）不跑 effect，初始就是「未登录」，与桌面端「没登录就写未登录」一致。 */
  const [wxLoggedIn, setWxLoggedIn] = useState(false);
  useEffect(() => {
    get('/api/wechat/status')
      .then((d) => setWxLoggedIn(Boolean(d && d.logged_in)))
      .catch(() => {});
  }, []);

  const act = async (action) => {
    const label = { start: '启动', stop: '停止', restart: '重启' }[action] || action;
    try {
      await post(`/api/services/${action}`, { service: 'bot' });
      setMsg(`${label}指令已发送`);
    } catch (e) {
      setMsg(String(e.message));
    }
  };

  const stopAll = () => {
    if (!window.confirm('确定停止全部？\nQQ / 微信 会离线，机器人不再回复任何消息。')) return;
    act('stop');
  };

  /* 重启不是无害动作：进程重启期间 QQ / 微信 会离线约 10 秒，协议端要重新连回来，
     正在进行的对话会被打断。首页三个分支里的「重启机器人」都走这里，统一加二次确认。 */
  const restartBot = () => {
    if (!window.confirm('确定重启机器人？\n大约 10 秒内 QQ / 微信 会短暂离线，正在进行的对话会中断。')) return;
    act('restart');
  };

  const botState = services?.bot?.state;
  const botOn = botState === 'running';
  const deployed = botState === 'running' || botState === 'installed';
  // 通道连接状态：进程在跑 ≠ QQ 已连上。以前只看进程，协议端没连也显示绿色「运行中」，
  // 用户会以为能收消息，实际一条都收不到 —— 这里拆成三态。
  const qqRaw = String(services?.bot?.qq || '').toLowerCase();
  const qqLinked = qqRaw === 'connected';
  const qqMap = {
    connected: '已连接',
    connecting: '连接中',
    unauthorized: '白名单未通过',
    stopped: '等待协议端连接',
    unknown: '状态未知',
  };
  const qqReason = qqMap[qqRaw] || '等待协议端连接';
  const qqTone = !botOn ? 'stopped' : qqLinked ? 'running' : 'warn';
  const qqLabel = !botOn ? 'QQ · 未连接' : qqLinked ? 'QQ · 已连接' : 'QQ · 未连接';
  // 微信通道已免费开放（2026-10-01）：不再看账号档位，这里恒为可用。
  const wxOn = true;
  const botTone = services == null ? 'unknown' : botOn ? 'running' : deployed ? 'stopped' : 'unknown';
  const botLabel = services == null ? '机器人 · 状态未知' : botOn ? '机器人 · 运行中' : deployed ? '机器人 · 已停止' : '机器人 · 未部署';
  // 标题区右侧那个状态点：桌面端是「任何一个通道在跑就算运行中」
  const anyChannelOn = Boolean(botOn || qqLinked || wxLoggedIn);
  // 「机器人现在好不好」= 三件事都成立才算正常；任何一项不成立都给出人话原因 + 该做什么
  const healthy = botOn && qqLinked && wxOn;
  const problems = [];
  if (!botOn) problems.push(deployed ? '机器人进程没在跑：点下面的按钮启动。' : '机器人还没部署过：先启动一次。');
  else if (!qqLinked) problems.push(`QQ 通道未连接（当前：${qqReason}）：协议端没连上，QQ 现在收不到消息，去「接入 → QQ」核对反向 WS 地址。`);
  // 未登录时 config 是 null：这里**不能**回落成 12113，否则界面会顶着一个假的端口号
  const botPort = portText(config, health);
  const clock = now.toTimeString().slice(0, 8);
  const wsUrl = `ws://${window.location.hostname || '127.0.0.1'}:${botPort}/onebot/v11/ws`;

  // 主操作：一屏只有一个「该做的事」。正常时不给实心主按钮（没事情要做），
  // 只留一个次级的「重启机器人」；出问题就把对应动作顶上来。
  const mainAction = !botOn
    ? {
        title: deployed ? '机器人没在跑' : '机器人还没部署',
        hint: deployed
          ? '启动后 QQ / 微信 通道才会开始收消息。'
          : '第一次启动会自动装运行环境（几分钟），装完就能用。',
        primary: { label: '启动全部', onClick: () => act('start') },
        secondary: [],
      }
    : !qqLinked
      ? {
          title: 'QQ 通道没连上',
          hint: `进程在跑，但协议端还没连过来（当前：${qqReason}），QQ 现在收不到消息。`,
          primary: { label: '去接入', onClick: onAccess },
          secondary: [{ label: '重启机器人', onClick: restartBot }],
        }
      : !wxOn
        ? {
            title: '微信通道当前不可用',
            hint: '微信通道已免费开放：重启一次机器人，再去「接入 → 微信」扫码登录即可。',
            primary: { label: '去接入', onClick: onAccess },
            secondary: [{ label: '重启机器人', onClick: restartBot }],
          }
        : {
            title: '一切正常',
            hint: 'QQ 已连接，正在收消息。',
            primary: null,
            secondary: [
              { label: '重启机器人', onClick: restartBot },
              { label: '去接入', onClick: onAccess },
            ],
          };

  return (
    <>
      <header className="page-head">
        <div>
          <h1>首页</h1>
          <p>各平台服务状态总览，一键进入管理</p>
        </div>
        <div className="page-head__right">
          <span className="home-time">更新于 {clock}</span>
          <StatusBadge tone={services == null ? 'unknown' : anyChannelOn ? 'running' : 'stopped'}>
            {services == null ? '服务状态未知' : anyChannelOn ? '服务运行中' : '服务已停止'}
          </StatusBadge>
        </div>
      </header>

      {/* 0) 三步开箱（**一条横排**）：1 连 AI → 2 接 QQ → 3 启动，每步一个状态 chip + 小按钮；
             同一行最右侧是「你想要什么功能」输入框 + 去做插件。
             状态由 setupSteps() 这个纯函数判断（AI 有没有模型名 / QQ 有没有凭证或已连 /
             机器人有没有在跑），完成绿、当前蓝、待办灰 —— 与桌面端首页同一条结构。 */}
      <section className="card steps-bar">
        {setupSteps({
          aiModel: config?.ai_model,
          qqReady: Boolean(String(config?.qq_app_id || '').trim()) || qqLinked,
          botRunning: botOn,
        }).map((step, i) => (
          <span className="steps-bar__seg" key={step.id}>
            {i > 0 && <span className="steps-bar__arrow">→</span>}
            <span className={`steps-bar__name steps-bar__name--${step.tone}`}>
              {`${step.no} ${STEP_SHORT[step.id] || step.title}`}
            </span>
            <span className={`steps-bar__chip steps-bar__chip--${step.tone}`}>{step.status}</span>
            <button
              type="button"
              className="btn btn--sm"
              disabled={starting && step.id === 'bot'}
              onClick={() => {
                if (step.id === 'ai') {
                  onGo('brain');
                  setMsg('去「AI 大脑」填接口地址与 Key，填完保存');
                  return;
                }
                if (step.id === 'qq') {
                  onGo('access');
                  return;
                }
                setStarting(true);
                act('start').finally(() => setStarting(false));
              }}
            >
              {step.id === 'bot' && starting ? '正在启动…' : step.button}
            </button>
          </span>
        ))}

        <span className="steps-bar__gap" />
        <input
          className="p-input steps-bar__wish"
          value={idea}
          placeholder="你想要什么功能？一句话，我让 AI 给你做一个插件"
          spellCheck={false}
          onChange={(e) => setIdeaText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter') return;
            setIdea(idea);
            onGo('workshop');
          }}
        />
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => {
            setIdea(idea);
            onGo('workshop');
          }}
        >
          去做插件
        </button>
      </section>

      {/* 2) 平台四卡（桌面端首页同一套，一行四张）：点任意一张进「接入」看细节。
          「一键启停全部通道」在下面的「机器人」卡里。 */}
      <div className="hp-cards">
        <button type="button" className="hp-card" onClick={onAccess} title="去「接入 → QQ」看详细设置">
          <StatusBadge tone={qqTone}>{botOn ? 'QQ · 运行中' : 'QQ · 未运行'}</StatusBadge>
          <span className="hp-card__line">{`AppID ${configText(loggedIn, config?.qq_app_id)}`}</span>
          <span className="hp-card__line">
            {`官方通道 ${configuredText(loggedIn, config?.qq_app_id)}`}
          </span>
        </button>
        <button type="button" className="hp-card" onClick={onAccess} title="去「接入 → 微信」扫码登录">
          <StatusBadge tone={wxLoggedIn ? 'success' : 'stopped'}>
            {wxLoggedIn ? '微信 · 已连接' : '微信 · 未登录'}
          </StatusBadge>
          <span className="hp-card__line">iLink 私聊个人助手</span>
        </button>
        <button type="button" className="hp-card" onClick={onAccess} title="去「接入 → 飞书」填凭证、装适配器">
          <StatusBadge tone="stopped">飞书 · 未接入</StatusBadge>
          <span className="hp-card__line">规划中</span>
        </button>
        <button type="button" className="hp-card" onClick={onAccess} title="纸飞机通道还在规划中">
          <StatusBadge tone="stopped">纸飞机 · 未接入</StatusBadge>
          <span className="hp-card__line">规划中</span>
        </button>
      </div>
      {problems.length > 0 && (
        <ul className="hp-health__list">
          {problems.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      )}

      {/* 3) 今日数据 / 最近消息（两列）：今日数据照桌面端 home_page.py 的行项目 */}
      <div className="hp-grid">
        <section className="card hp-today">
          <h2 className="card__title">今日数据</h2>
          <TodayStats loggedIn={loggedIn} />
        </section>

        <section className="card hp-recent">
          <h2 className="card__title">最近消息</h2>
          <RecentMessages onOpen={onMessages} loggedIn={loggedIn} />
          <p className="card__note">点上面任意一条 → 进入消息中心</p>
          <div className="hp-linkrow">
            <button type="button" className="hp-link" onClick={onMessages}>
              打开消息中心 ›
            </button>
          </div>
        </section>
      </div>

      {/* 4) 机器人 / 当前任务（两列）：机器卡里**只有一个切换按钮**（桌面端 self.btn_toggle
            同一个做法：运行中显示「停止全部」，否则显示「启动全部」），右侧是任务空态。
            首页不放「重启」—— 重启在「全局管理」，写在那行说明的 tooltip 里。 */}
      <div className="hp-grid">
        <section className="card hp-act">
          <h2 className="card__title">机器人</h2>
          <button
            type="button"
            className="btn btn--primary btn--block"
            disabled={starting}
            onClick={() => {
              if (botOn) {
                stopAll();
                return;
              }
              setStarting(true);
              act('start').finally(() => setStarting(false));
            }}
          >
            {botOn ? '停止全部' : '启动全部'}
          </button>
          <p className="card__note" title="重启在「全局管理」。">一键启停全部通道。</p>
        </section>

        <section className="card">
          <h2 className="card__title">当前任务</h2>
          {/* 空闲时就是两行说明，不留空态大块；有任务时这里换成任务列表 */}
          <p className="card__note">当前没有正在执行的任务</p>
          <p className="card__note">点击「启动全部」开始，或在插件/依赖页发起安装任务</p>
        </section>
      </div>

      {/* 4) 折叠区只留运维入口、路径细节与原始数据 */}
      <details className="fold">
        <summary className="fold__summary">详情与运维：运行数据 · 路径与日志入口</summary>
        <div className="fold__body">
          <section className="card">
            <h2 className="card__title">本机信息</h2>
            <div className="stat-rows">
              {[
                // 同类信息合并成行：版本/机器码一行、目录一行、服务与端口一行，不再一项一行撑满屏
                ['版本 / 机器码',
                  `${health?.version ? `v${String(health.version).replace(/^v/, '')}` : '版本读取中'} · ${health?.machine_id ? String(health.machine_id).slice(0, 8) + '…' : '登录后可见'}`],
                ['机器人目录', services?.bot?.bot_dir || '—'],
                ['服务 / 端口', `${health?.ok ? '后端正常' : '后端未连通'} · ${botPort}`],
              ].map(([label, value]) => (
                <div className="stat-row" key={label}>
                  <span>{label}</span>
                  <b className="hp-nowrap">{value ?? '—'}</b>
                </div>
              ))}
            </div>
          </section>

          <section className="card">
            <h2 className="card__title">运行数据</h2>
            <StatsPanel loggedIn={loggedIn} />
          </section>

          <section className="card">
            <h2 className="card__title">运维入口</h2>
            <div className="stat-rows">
              <div className="stat-row">
                <span>QQ 反向地址</span>
                <b className="hp-nowrap">{wsUrl}</b>
              </div>
              <div className="stat-row">
                <span>日志文件</span>
                <b className="hp-nowrap">{services?.bot?.log || '—'}</b>
              </div>
            </div>
            <div className="btn-row">
              <button type="button" className="btn" onClick={onServices}>
                看服务详情
              </button>
              <button type="button" className="btn btn--ghost" onClick={onMessages}>
                打开消息中心
              </button>
            </div>
          </section>
        </div>
      </details>

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}

function WeChatBody({ auth, onMessages }) {
  // 微信通道已免费开放（2026-10-01）：不再按账号档位判断，扫码登录就能用。
  const member = true;
  // 微信通道（iLink）：状态和二维码都由后端从适配器落的文件里读，前端只做展示与触发
  const [wx, setWx] = useState(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [wxErr, setWxErr] = useState('');
  const codeRef = useRef(null);

  // /api/wechat/status 也要鉴权：未登录时以前是 .catch(() => {})，卡片会显示成
  // 「未登录 / 还没有二维码」—— 把「读不到」说成了「没有」。这里记下来如实展示。
  const loadWx = () =>
    get('/api/wechat/status')
      .then((d) => { setWx(d); setWxErr(''); })
      .catch((e) => {
        setWx(null);
        setWxErr(isAuthError(e) ? 'login' : apiErrText(e, '微信状态读取中'));
      });
  useEffect(() => {
    loadWx();
    const t = setInterval(loadWx, 3000);
    return () => clearInterval(t);
  }, []);

  const qrUrl = wx?.qrcode_url || '';
  const qrFresh = qrUrl && (wx?.qrcode_age ?? 999) < 110;   // 二维码 2 分钟一换
  const loggedIn = Boolean(wx?.logged_in);
  const tone = loggedIn ? 'success' : member && qrUrl ? 'warn' : 'stopped';
  const stateText = loggedIn ? '已连接' : qrUrl ? '等待扫码' : '未登录';

  /* 「重新扫码登录」的按钮名看起来只是刷新一张图，后端实现却是 /api/wechat/relogin
     = 清登录态 + 重启机器人（console_ext 的 relogin），会打断正在进行的对话、
     微信在几秒内离线。所以加二次确认，并在确认框与提示里把真实后果说清楚。 */
  const doRelogin = async () => {
    if (!window.confirm(
      '确定重新扫码登录？\n这会清除微信登录态并重启机器人：\n'
      + '· 大约 10 秒内微信/QQ 会离线，正在进行的对话会中断；\n'
      + '· 之后要用微信重新扫一次码才能恢复。',
    )) return;
    setBusy(true);
    setMsg('正在重新扫码：清登录态并重启机器人…');
    try {
      await post('/api/wechat/relogin', {});
      setMsg('已重启机器人，几秒后这里会出现新的二维码');
      setTimeout(loadWx, 4000);
    } catch (e) {
      setMsg(withAuthHint(e));
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async () => {
    const v = code.trim();
    if (!v) {
      setMsg('先填配对码');
      return;
    }
    try {
      await post('/api/wechat/verify-code', { code: v });
      setCode('');
      setMsg('配对码已提交，确认后会自动变成「已连接」。');
      loadWx();
    } catch (e) {
      setMsg(withAuthHint(e));
    }
  };

  const restartBot = async () => {
    if (!window.confirm('确定重启机器人？\n大约 10 秒内 QQ / 微信 会短暂离线，正在进行扫码会中断。')) return;
    try {
      await post('/api/services/restart', { service: 'bot' });
      setMsg('已发送重启指令，机器人起来后会继续扫码流程');
    } catch (e) {
      setMsg(withAuthHint(e));
    }
  };

  const openQrLink = () => {
    if (!qrUrl) {
      setMsg('还没有二维码，先点「重新扫码登录」');
      return;
    }
    window.open(qrUrl, '_blank');
    setMsg('已打开二维码链接：用微信打开它即可授权');
  };

  return (
    <>
      <section className="card card--wx">
        <StatusBadge tone={wxErr ? 'unknown' : tone}>
          微信 iLink · {wxErr ? '状态读不到' : stateText}
        </StatusBadge>
        {wxErr === 'login' && (
          <p className="card__note">{LOGIN_HINT}：微信状态读不到，下面显示的不是真实状态。</p>
        )}
        {wxErr && wxErr !== 'login' && (
          <p className="card__note">微信状态读取失败：{wxErr}（可点「刷新二维码」重试）</p>
        )}
        <div className="stat-row">
          <span>Bot ID</span>
          <b>{wx?.bot_id || '—'}</b>
        </div>
        <div className="stat-row">
          <span>登录时间</span>
          <b>{loggedIn ? '已登录' : wx?.login_status ? `扫码状态：${wx.login_status}` : '—'}</b>
        </div>
        <div className="qr-box">
          {loggedIn ? (
            '微信通道已连接，收到私聊会由 Agent 直接回复'
          ) : qrFresh ? (
            <img
              src={authUrl('/api/wechat/qrcode.png?t=' + Math.floor(qrUrl.length + (wx?.qrcode_ts || 0)))}
              alt="微信二维码"
              style={{ width: '100%', height: '100%', objectFit: 'contain' }}
            />
          ) : (
            '暂无有效二维码\n点「重新扫码登录」后这里会自动显示'
          )}
        </div>
        {(wx?.login_extra || qrUrl) && !loggedIn && (
          <p className="card__note">
            {wx?.login_extra ? `微信：${wx.login_extra}　` : ''}
            {qrUrl ? `二维码链接：${qrUrl}` : ''}
          </p>
        )}
        <p className="card__note">
          说明：iLink 是私密个人助手通道，不支持群聊。
        </p>
        <div className="p-row">
          <input
            className="p-input"
            ref={codeRef}
            value={code}
            placeholder="配对码（微信里提示时填这里）"
            spellCheck={false}
            onChange={(e) => setCode(e.target.value)}
          />
          <button type="button" className="btn btn--primary" onClick={submitCode}>
            提交配对码
          </button>
        </div>
        <div className="btn-row">
          <button type="button" className="btn" disabled={!member || busy} onClick={doRelogin}>
            重新扫码登录
          </button>
          <button type="button" className="btn" onClick={loadWx}>
            刷新二维码
          </button>
          <button type="button" className="btn" onClick={openQrLink}>
            打开扫码链接
          </button>
          <button type="button" className="btn" onClick={() => codeRef.current && codeRef.current.focus()}>
            输入配对码
          </button>
          <button type="button" className="btn" onClick={restartBot}>
            重启机器人
          </button>
        </div>
      </section>

      <section className="card card--wide card--fill">
        <h2 className="card__title">最近微信会话</h2>
        {/* 这里以前是一个写死的「—」占位。会话本身在消息中心（真接口），
            这里如实说明去哪儿看，不再摆一个没有意义的横杠。 */}
        <p className="card__note">
          {wxErr === 'login'
            ? `${LOGIN_HINT}：登录后才能判断微信有没有会话。`
            : loggedIn
              ? '已连接。会话在「消息中心」里看。'
              : '微信还没登录：扫码并提交配对码后才有会话。'}
        </p>
        <button type="button" className="btn btn--block" onClick={onMessages}>
          打开消息中心
        </button>
      </section>

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}

function QQBody({ config, health, services, onSaved, officialApiOk = true,
                  loggedIn = true, wsUrl = '' }) {
  const [form, setForm] = useState(null);
  const [msg, setMsg] = useState('');
  const [tutorial, setTutorial] = useState(false);
  /* QQ 官方凭证（AppID / AppSecret / AppToken）在重排版时从前端丢了，后端 SAFE_KEYS 一直支持。
     这里补回来：secret / token 是后端脱敏字段（/api/config 回 ****1234），
     只有用户确实改过（edited 里标记过）才提交，未修改时整条键都不送 ——
     否则就是把打码值当成真值写回去（后端虽然也会拦 **** 开头的值，前端不该依赖那一层）。 */
  const [edited, setEdited] = useState({});
  useEffect(() => {
    if (form === null && config) setForm(config);
  }, [config, form]);
  const f = form || {};
  const host = window.location.hostname || '127.0.0.1';
  // 未登录时 wsUrl 由 AccessPage 传空串进来：**不编一个端口**给用户看，
  // 否则他会拿一个假地址去填协议端（原来这里回落 12113，正是"配置归 0"的观感来源之一）。
  const botPort = portText(form, health);
  const showWs = Boolean(wsUrl);
  const botOn = services?.bot?.state === 'running';
  const qq = services?.bot?.qq;
  const qqText =
    {
      connected: '已连接（协议端在线）',
      connecting: '连接中',
      unauthorized: '白名单未通过',
    }[qq] || '等待协议端反向连接';

  const act = async (action) => {
    const label = { start: '启动', stop: '停止', restart: '重启' }[action] || action;
    try {
      await post(`/api/services/${action}`, { service: 'bot' });
      setMsg(`${label}指令已发送`);
    } catch (e) {
      setMsg(String(e.message));
    }
  };

  const editField = (key, value) => {
    setForm({ ...f, [key]: value });
    setEdited((prev) => ({ ...prev, [key]: true }));
  };

  const save = async () => {
    try {
      // ⚠ 这里必须取 f.bot_port（表单里的真值），**不能**取上面那个给界面看的 botPort ——
      // 它未登录时是「登录后显示」这句文案，Number() 之后会变成 NaN → 回落 12113，
      // 等于把用户真实端口悄悄改成默认值（这正是"配置归 0"那一类 bug 的写法）。
      const payload = { bot_port: Number(f.bot_port) || 12113 };
      // AppID 不是脱敏字段（后端原样返回），可以一直提交；
      // AppSecret / AppToken / 访问令牌只在用户改过时提交。
      // 官方凭证只在这张卡还管着它的时候提交（新接口没上时的备用入口）。
      if (!officialApiOk && f.qq_app_id !== undefined) payload.qq_app_id = String(f.qq_app_id || '');
      ['qq_app_secret', 'qq_app_token', 'qq_onebot_token'].forEach((k) => {
        if (edited[k]) payload[k] = String(f[k] || '');
      });
      const saved = await put('/api/config', payload);
      setForm({ ...f, ...saved });
      setEdited({});
      setMsg('已保存，重启机器人后生效');
      onSaved();
    } catch (e) {
      setMsg(String(e.message));
    }
  };

  const copyUrl = async () => {
    if (!showWs) {
      setMsg('先登录星群账号：反向 WS 地址里的端口存在服务器上，读到了才复制得对');
      return;
    }
    try {
      await navigator.clipboard.writeText(wsUrl);
      setMsg(`已复制：${wsUrl}`);
    } catch (e) {
      setMsg('浏览器不给写剪贴板，请手动选中复制');
    }
  };

  return (
    <>
      <div className="btn-row">
        <button type="button" className="btn btn--primary" onClick={() => act('start')}>
          启动全部
        </button>
        <button
          type="button"
          className="btn btn--danger"
          onClick={() => {
            if (!window.confirm('确定停止全部？\nQQ / 微信 会离线，机器人不再回复任何消息。')) return;
            act('stop');
          }}
        >
          停止全部
        </button>
        <button type="button" className="btn btn--ghost" onClick={() => act('restart')}>
          重启机器人
        </button>
      </div>

      <div className="btn-row">
        <button
          type="button"
          className="btn btn--ghost"
          onClick={() => copyServerPath(setMsg, '配置路径', botEnvPath)}
          title="点这里复制配置文件路径"
        >
          复制配置路径
        </button>
        <button type="button" className="btn btn--ghost" onClick={() => setTutorial((v) => !v)}>
          接入教程（LLOneBot）
        </button>
      </div>

      {tutorial && (
        <section className="card card--wide">
          <h2 className="card__title">接入教程：把协议端连过来</h2>
          <p className="card__note">
            {`1. 在任意一台设备安装协议端（LLOneBot / NapCat / Lagrange.OneBot）并扫码登录 QQ 小号；
2. 打开协议端的「网络配置」→ 新建「WebSocket 服务端 / 反向 WebSocket」；
3. 地址填 ${showWs ? wsUrl : '（登录后这里会显示你这台服务器的真实地址）'}，启用并保存；
4. 回到本页，上面「状态」变成「已连接」即接入成功。`}
          </p>
        </section>
      )}

      {/* 群消息模式 + 官方通道体检：和桌面端 QQ 页那张状态卡同一套结论 */}
      <QqOfficialCard onMsg={setMsg} />

      <div className="home-row home-row--cards">
        <section className="card card--kv card--wide">
          <StatusBadge tone={botOn ? 'running' : 'stopped'}>
            机器人 · {botOn ? '运行中' : '已停止'}
          </StatusBadge>
          <div className="stat-row">
            <span>端口</span>
            <b>{botPort}</b>
          </div>
          <div className="stat-row">
            <span>Python</span>
            <b className="mono">—</b>
          </div>
          <div className="stat-row">
            <span>运行记录</span>
            <b>—</b>
          </div>
        </section>

        <section className="card card--kv card--wide">
          <StatusBadge tone={qq === 'connected' ? 'success' : (botOn ? 'warn' : 'stopped')}>
            QQ OneBot · {qq === 'connected' ? '已连接' : '已停止'}
          </StatusBadge>
          <div className="stat-row">
            <span>反向地址</span>
            <b className="mono">{showWs ? wsUrl : NEED_LOGIN}</b>
          </div>
          <div className="stat-row">
            <span>通道</span>
            <b>第三方 OneBot（反向 WS）</b>
          </div>
          <div className="stat-row">
            <span>状态</span>
            <b>{qqText}</b>
          </div>
          <p className="card__note">
            协议端由你自行安装并扫码登录（例如 LLOneBot / Lagrange），星巢 Nest
            只负责连接；第三方协议请自备小号。
          </p>
        </section>
      </div>

      <section className="card card--kv card--wide">
        <h2 className="card__title">第三方 OneBot（协议端）</h2>
        {/* 这两个「下拉」各自只有一个选项、也没有 onChange —— 点了不会变，是假控件。
            无头端固定只走第三方 OneBot + 反向 WS（后端的 qq_channel / qq_onebot_mode 虽然
            在白名单里，但界面上没有第二种可选值），所以改成只读文字，不再做成下拉。 */}
        <div className="stat-row">
          <span>接入方式</span>
          <b>第三方 OneBot 协议（协议端自行安装）</b>
        </div>
        <p className="card__note">
          协议端需你自己安装（例如 LLOneBot、Lagrange.OneBot、NapCat）并扫码登录，星巢
          Nest 不提供协议端；第三方协议请自备小号。
        </p>

        <div className="kv-group">
          <div className="stat-row">
            <span>连接方式</span>
            <b>反向 WS（推荐）：协议端来连星巢 Nest</b>
          </div>

          <div className="kv-group">
            <p className="card__note">
              {`把地址复制到协议端的「反向 WebSocket」配置里，保存后会自动连回来。
NapCat：网络配置 → 新建 WebSocket 服务端；LLOneBot：网络配置 → 反向 WebSocket 客户端。
协议端在另一台设备时：监听地址填 0.0.0.0，并把下面地址里的 ${host} 换成这台电脑的局域网 IP。`}
            </p>

            <div className="kv-row">
              <span>监听地址</span>
              <input className="mono" value="0.0.0.0" readOnly title="无头端固定监听全部网卡" />
            </div>
            <div className="kv-row">
              <span>监听端口</span>
              <input
                className="mono"
                value={botPort}
                spellCheck={false}
                onChange={(e) => setForm({ ...f, bot_port: e.target.value })}
              />
            </div>
            <div className="kv-row kv-row--url">
              {/* 未登录时这里原来是 ws://…:12113 这个**编出来的**地址，
                  用户会照着填到协议端去。现在如实说"登录后显示"。 */}
              <input
                className="mono"
                value={showWs ? wsUrl : '登录后显示（端口存在服务器上）'}
                readOnly
              />
              <button type="button" className="btn btn--primary" onClick={copyUrl}>
                复制地址
              </button>
            </div>
          </div>

          {/* 「访问令牌」原来是一个永远空白、永远不可填的 disabled 输入框：
              它既没有值也没有入口，看着就像坏了。而后端 headless_config.SAFE_KEYS 里
              qq_onebot_token 一直是可读可写的（且不在 SECRET_FIELDS，/api/config 会回真值），
              所以这里做成真正能填的输入：填了就跟机器人端口一起保存（见 save()）。
              留空 = 机器人不校验令牌，此时协议端也留空即可。 */}
          <div className="kv-row">
            <span>访问令牌</span>
            <input
              className="mono"
              value={f.qq_onebot_token ?? ''}
              spellCheck={false}
              placeholder="留空 = 不校验；两边要填成同一个值"
              onChange={(e) => editField('qq_onebot_token', e.target.value)}
            />
          </div>
          <p className="card__note">
            两边填一样的口令，
            否则 QQ 会连不上。改完要重启机器人生效。
          </p>
        </div>

        <button type="button" className="btn btn--primary btn--self" onClick={save}>
          保存通道配置
        </button>
      </section>

      {/* QQ 官方机器人（QQ 开放平台）凭证：正常归上面那张「QQ 官方机器人」卡管
          （走 /api/qq/official）。只有那个接口还没上（404）时才退回这里的旧入口 ——
          写的是 /api/config 的 qq_app_id / qq_app_secret / qq_app_token，
          headless_config 的 SAFE_KEYS 一直支持。后端补上后这块自动消失，不留两份表单。 */}
      {!officialApiOk && (
        <section className="card card--kv card--wide">
          <h2 className="card__title">QQ 官方机器人（备用入口）</h2>
          <p className="card__note">
            官方那套接口还没上，先在这里填 AppID / AppSecret / AppToken；
            密钥框里的 ****xxxx 是脱敏显示，不重新输入就保持服务器上的原值。
          </p>
          <div className="kv-row">
            <span>AppID</span>
            <input
              className="mono"
              value={f.qq_app_id ?? ''}
              spellCheck={false}
              placeholder="QQ 开放平台的 AppID"
              onChange={(e) => editField('qq_app_id', e.target.value)}
            />
          </div>
          <div className="kv-row">
            <span>AppSecret</span>
            <input
              className="mono"
              type="password"
              value={f.qq_app_secret ?? ''}
              spellCheck={false}
              placeholder="QQ 开放平台的 AppSecret"
              onChange={(e) => editField('qq_app_secret', e.target.value)}
            />
          </div>
          <div className="kv-row">
            <span>AppToken</span>
            <input
              className="mono"
              type="password"
              value={f.qq_app_token ?? ''}
              spellCheck={false}
              placeholder="QQ 开放平台事件推送的 Token"
              onChange={(e) => editField('qq_app_token', e.target.value)}
            />
          </div>
          <button type="button" className="btn btn--primary btn--self" onClick={save}>
            保存官方凭证
          </button>
        </section>
      )}

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}

/* 接入页：通道列表，两组（已上线通道 / 规划中通道）。
   每行结构固定：左边图标 + 信息列（名称 + 状态徽标 / 一句说明 / 2–3 行关键事实），
   右边一个固定宽度的操作槽（只有「配置 ›」一个主操作，margin-left:auto 右对齐）——
   状态文字挪进信息列后，按钮横向位置就不再随状态文字长短漂移。
   复制地址 / 重新扫码这类就地快捷动作放在信息列自己的行里，不影响右侧操作槽对齐。
   展开后才出现该通道原来的全部设置（QQBody / WeChatBody 原样保留，功能一个没删）。 */
function AccessPage({ auth, config, health, services, onSaved, onMessages,
                      loggedIn = true }) {
  const [open, setOpen] = useState('');
  const [wx, setWx] = useState(null);
  const [msg, setMsg] = useState('');

  const botOn = services?.bot?.state === 'running';
  const qqRaw = String(services?.bot?.qq || '').toLowerCase();
  const qqLinked = qqRaw === 'connected';
  const qqTone = !botOn ? 'stopped' : qqLinked ? 'running' : 'warn';
  const qqLabel = !botOn ? '未连接（机器人未运行）' : qqLinked ? '已连接（协议端在线）' : '未连接（等待协议端）';
  // 微信通道已免费开放（2026-10-01）：不再看账号档位，恒为可用。
  const wxOn = true;
  const toggle = (id) => setOpen((v) => (v === id ? '' : id));

  // 折叠状态下也要有信息：微信通道摘要自己拉一次 /api/wechat/status（和展开后的 WeChatBody 同源）
  // 未登录时这个接口 401：以前 .catch(() => {}) 会让摘要显示成「还没有二维码，点重新扫码生成」，
  // 把「读不到」说成「没有」。这里记下来，摘要里如实写。
  const [wxErr, setWxErr] = useState('');
  const loadWx = () =>
    get('/api/wechat/status')
      .then((d) => { setWx(d); setWxErr(''); })
      .catch((e) => {
        setWx(null);
        setWxErr(isAuthError(e) ? 'login' : apiErrText(e, '微信状态读取中'));
      });
  useEffect(() => {
    loadWx();
    const t = setInterval(loadWx, 5000);
    return () => clearInterval(t);
  }, []);
  /* 飞书通道摘要：折叠状态下也得有信息，和展开后的 FeishuCard 同源（同一个接口、
     同一套徽标口径 feishuBadge）。未登录/接口没上时如实写，不装成"没填凭证"。 */
  const [feishu, setFeishu] = useState(null);
  const [feishuErr, setFeishuErr] = useState('');
  const loadFeishu = () =>
    get('/api/feishu/status')
      .then((d) => { setFeishu(d); setFeishuErr(''); })
      .catch((e) => {
        setFeishu(null);
        setFeishuErr(apiErrText(e, '飞书状态读取中'));
      });
  useEffect(() => {
    loadFeishu();
  }, []);
  const feishuInfo = feishuBadge(feishu, feishuErr);
  const feishuSummary = feishuErr
    ? feishuErr
    : feishu
      ? (feishu.ready
        ? '已就绪，重启机器人后连上'
        : (feishu.why || '还差一步：看上面的徽标'))
      : '读取中…';
  /* QQ 官方机器人摘要（折叠状态用）+ 判断新接口在不在：
     在 → 官方凭证归 QqOfficialCard 管；不在（404）→ QQBody 里保留旧入口兜底，
     免得后端还没补上时用户连凭证都没地方填。
     注意只有 404 才算「接口没有」；500 之类的服务端故障不该把备用入口放出来。 */
  const [official, setOfficial] = useState(null);
  const [officialErr, setOfficialErr] = useState('');
  const [officialMissing, setOfficialMissing] = useState(false);
  useEffect(() => {
    get('/api/qq/official')
      .then((d) => { setOfficial(d); setOfficialErr(''); setOfficialMissing(false); })
      .catch((e) => {
        setOfficial(null);
        setOfficialMissing(/404|not found/i.test(String(e.message)));
        setOfficialErr(apiErrText(e, 'QQ 官方状态读取中'));
      });
  }, []);
  const officialApiOk = !officialMissing;
  useEffect(() => {
    if (!msg) return undefined;
    const t = setTimeout(() => setMsg(''), 3200);
    return () => clearTimeout(t);
  }, [msg]);

  // 未登录时 config 是 null：**不编端口**，wsUrl 留空（QQBody 会显示"登录后显示"）
  const wsUrl = config?.bot_port || health?.bot_port
    ? `ws://${window.location.hostname || '127.0.0.1'}:${portText(config, health)}/onebot/v11/ws`
    : '';

  const copyWs = async () => {
    if (!wsUrl) {
      setMsg('先登录星群账号：反向地址里的端口存在服务器上，读到了才复制得对');
      return;
    }
    try {
      await navigator.clipboard.writeText(wsUrl);
      setMsg(`已复制：${wsUrl}`);
    } catch {
      setMsg('浏览器不给写剪贴板，请手动选中复制');
    }
  };

  /* 「重新扫码」不只是刷新二维码：后端 /api/wechat/relogin = 清登录态 + 重启机器人，
     会让微信在几秒内离线并打断正在进行的对话。这个按钮还是折叠行里的小 ghost 按钮，
     很容易误点 —— 加二次确认，并在提示里写清真实后果。 */
  const relogin = async () => {
    if (!window.confirm(
      '确定重新扫码？\n这会清除微信登录态并重启机器人：\n'
      + '· 大约 10 秒内微信/QQ 会离线，正在进行的对话会中断；\n'
      + '· 之后要用微信重新扫一次码才能恢复。',
    )) return;
    try {
      await post('/api/wechat/relogin', {});
      setMsg('已重启机器人，几秒后会出现新二维码。');
      setTimeout(loadWx, 4000);
    } catch (e) {
      setMsg(withAuthHint(e));
    }
  };

  const wxSummary = !wxOn
    ? '微信通道当前不可用：重启一次机器人再试'
    : wxErr === 'login'
      ? '微信 · 状态读不到'
      : wxErr
        ? `微信状态读取失败：${wxErr}`
        : wx?.logged_in
          ? `已登录 iLink${wx?.bot_id ? ` · Bot ${wx.bot_id}` : ''}，私聊会由机器人直接回复`
          : wx?.qrcode_url
            ? `二维码待扫（${wx?.qrcode_age ?? 0}s 前生成，2 分钟一换）`
            : wx?.login_status
              ? `扫码状态：${wx.login_status}`
              : '还没有二维码，点「重新扫码」生成';

  const channels = [
    {
      id: 'qq',
      icon: 'qq',
      name: 'QQ',
      meta: 'QQ 官方机器人 / 第三方 OneBot 两种接法都在这张卡里',
      tone: qqTone,
      label: qqLabel,
      body: (
        <QQBody
          config={config}
          health={health}
          services={services}
          onSaved={onSaved}
          officialApiOk={officialApiOk}
          loggedIn={loggedIn}
          wsUrl={wsUrl}
        />
      ),
      facts: (
        <>
          <div className="chan__fact">
            <span className="chan__k">官方机器人</span>
            <span className="chan__v">
              {official
                ? (official.appid_set && official.secret_set
                  ? `AppID / AppSecret 已填${official.enabled ? '，开关已开' : '，开关没开'}`
                  : '还没填 AppID / AppSecret')
                : (officialErr || '读取中…')}
            </span>
          </div>
          <div className="chan__fact">
            <span className="chan__k">第三方 OneBot</span>
            <code className="chan__v mono">{wsUrl || NEED_LOGIN}</code>
            <button type="button" className="btn btn--ghost btn--sm" onClick={copyWs}>
              复制地址
            </button>
          </div>
          <p className="chan__hint">
            {qqLinked
              ? '已连上：QQ 消息现在会经这条通道进 Agent。'
              : '未连上：官方填凭证；第三方让协议端连上面的地址。'}
          </p>
        </>
      ),
    },
    {
      id: 'wx',
      icon: 'wechat',
      name: '微信',
      meta: 'iLink 通道：个人私聊助手，不支持群聊',
      tone: wxOn ? (wx?.logged_in ? 'success' : 'warn') : 'warn',
      label: wxErr && wxOn
        ? '微信 · 状态读不到'
        : wxOn ? (wx?.logged_in ? '微信 · 已连接' : '微信 · 未登录') : '微信 · 不可用',
      body: <WeChatBody auth={auth} onMessages={onMessages} />,
      facts: (
        <>
          <div className="chan__fact">
            <span className="chan__k">通道状态</span>
            <span className="chan__v">{wxSummary}</span>
            {/* 按钮名保持「重新扫码」是因为这是桌面端/用户习惯的叫法，但它的真实行为
                （清登录态 + 重启机器人）写在 title 里，点击还会有二次确认。 */}
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={!wxOn}
              title="会清除登录态并重启机器人，之后要重新扫码。"
              onClick={relogin}
            >
              重新扫码
            </button>
          </div>
          <p className="chan__hint">
            {!wxOn
              ? '微信通道不可用：先重启一次机器人。'
              : wxErr === 'login'
                ? `${LOGIN_HINT}：登录后这里才会显示真实的微信通道状态。`
                : wx?.logged_in
                  ? '已连上。出问题先点「重新扫码」。'
                  : '未连上：点「重新扫码」，再展开这张卡扫码。'}
          </p>
        </>
      ),
    },
    {
      id: 'feishu',
      icon: 'plane',
      name: '飞书',
      meta: '飞书自建应用：长连接收消息，不用公网地址',
      tone: feishuInfo.tone,
      label: feishuInfo.label,
      body: <FeishuCard />,
      facts: (
        <>
          <div className="chan__fact">
            <span className="chan__k">通道状态</span>
            <span className="chan__v">{feishuSummary}</span>
          </div>
          <p className="chan__hint">
            点「配置 ›」填凭证；缺适配器就点「装适配器」。
          </p>
        </>
      ),
    },
    {
      id: 'telegram',
      icon: 'planeTilt',
      name: '纸飞机',
      planned: true,
      label: '暂未开放 · 正在规划中',
      meta: '还在规划中：现在没有接入方式',
      body: <TelegramCard />,
      facts: (
        <p className="chan__hint">还在规划中，不用管它，点「说明 ›」看看怎么回事。</p>
      ),
    },
  ];

  /* 一行通道卡：右侧操作槽固定宽度，所有通道的「配置 ›」落在同一条竖线上。
     planned = 规划中的通道（纸飞机）：徽标固定写「暂未开放 · 正在规划中」，
     但**仍然能展开**看说明 —— 展开后那张卡里一个能填的控件都没有，不会装成能用。 */
  const renderChan = (ch) => (
    <div className="chan__item" key={ch.id}>
      <section className={ch.planned ? 'card chan chan--closed' : 'card chan'}>
        <span className="chan__icon">
          <Icon name={ch.icon} size={18} />
        </span>
        <div className="chan__main">
          <div className="chan__head">
            <span className="chan__name">{ch.name}</span>
            {ch.planned ? (
              <span className="chan__closed">{ch.label}</span>
            ) : (
              <StatusBadge tone={ch.tone}>{ch.label}</StatusBadge>
            )}
          </div>
          <div className="chan__meta">{ch.meta}</div>
          <div className="chan__facts">{ch.facts}</div>
        </div>
        <div className="chan__side">
          <button
            type="button"
            className="btn btn--ghost btn--sm chan__act"
            aria-expanded={open === ch.id}
            onClick={() => toggle(ch.id)}
          >
            {open === ch.id ? '收起' : ch.planned ? '说明 ›' : '配置 ›'}
          </button>
        </div>
      </section>
      {open === ch.id && <div className="chan__body">{ch.body}</div>}
    </div>
  );

  return (
    <>
      <header className="page-head">
        <div>
          <h1>接入</h1>
          <p>想让机器人在哪个平台说话，就在这里接上；每张卡直接给出关键信息，点「配置 ›」展开该通道的全部设置</p>
        </div>
      </header>

      {/* 功能分组：组内 14px，组间 24px（组外 > 组内），不靠藏内容换留白 */}
      <div className="chan-grp">
        {channels.filter((c) => !c.planned).map(renderChan)}
      </div>

      <div className="chan-grp chan-grp--plan">
        <div className="chan-grp__title">规划中通道</div>
        {channels.filter((c) => c.planned).map(renderChan)}
      </div>

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}

/* 消息中心：与桌面端 message_center_page.py 1:1。
   实测：页头 → 过滤行（平台 24×35 / 下拉 85×35 / 刷新 56×33，行内间距 14）→ 玻璃面板
   （内边距 10/12/12，flex 撑满）→ 空态 60 高（9+18+6+18+9）。
   空态提示在桌面端是「236 宽、16 行高」的标签里放下两行（Qt 的 QLabel 只报 18 高，
   实际绘制溢出了第二行），所以这里固定 236×18 + overflow:visible，绘制和度量都对得上。
   这一页原来是整页空壳：rows 恒为 []、刷新按钮只弹一句
   「无头端还没有会话接口」，而接口其实早就有（GET /api/messages/list），首页「最近消息」
   （BrainStats.jsx）一直在用它。现在按真实接口接线，字段也换成后端真有的那几个。

   ⚠ 后端 messages_list 以前只透出白名单字段 key/title/name/platform/ts/last/last_text，
   而 message_store.load_conversations 造出来的字典里只有 platform/scene/room/count/last
   —— scene/room 被白名单丢掉，多个 QQ 会话在界面上就都显示成「QQ 会话」，分不清谁是谁。
   本轮后端已把 scene/room（以及按 platform+scene+room 还原出来的 key）加进返回，
   所以这里用 scene+room 拼出「QQ 群 123456 / QQ 好友 123456 / 微信 好友 xxx」这种可区分的标题。 */
const MESSAGE_PLATFORMS = [
  ['', '全部'],
  ['qq', 'QQ'],
  ['wechat', '微信'],
  ['feishu', '飞书'],
  ['telegram', '纸飞机'],
];
const PLATFORM_LABELS = { qq: 'QQ', wechat: '微信', feishu: '飞书', telegram: '纸飞机' };
const SCENE_LABELS = { group: '群', private: '好友' };

function MessageCenter({ loggedIn = true }) {
  const [rows, setRows] = useState(null);      // null = 还没读到；[] = 真的没有会话
  const [dsh, setDsh] = useState([]);
  const [needLogin, setNeedLogin] = useState(false);
  const [err, setErr] = useState('');
  const [platform, setPlatform] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  /* 点一条会话 → 右侧/下面看它的内容（桌面端只列一行文本，控制台把这条会话有的字段
     全部摊开：平台、群/好友、会话标识、条数、最近时间、最后一条消息）。
     正文接口还没有（/api/messages/list 只透出标量字段），所以这里不编造聊天记录。 */
  const [picked, setPicked] = useState('');
  // 「清空」只清这个页面上的列表显示，机器人里的会话记录一条都不动
  const [cleared, setCleared] = useState([]);

  const load = useCallback(async () => {
    if (!loggedIn) {                            // 别发这个必 401 的请求
      setRows([]);
      setDsh([]);
      setErr('');
      setNeedLogin(true);
      return;
    }
    setBusy(true);
    try {
      const d = await get('/api/messages/list?limit=200');
      setRows(d.conversations || []);
      setDsh(d.dsh || []);
      setErr('');
      setNeedLogin(false);
    } catch (e) {
      setRows([]);
      setDsh([]);
      if (isAuthError(e)) { setNeedLogin(true); setErr(''); } else { setNeedLogin(false); setErr(e.message); }
    } finally {
      setBusy(false);
    }
  }, [loggedIn]);

  useEffect(() => { load(); }, [load]);

  const platOf = (c) => String(c.platform || '').toLowerCase();
  const roomOf = (c) => String(c.room || '').trim();
  const sceneOf = (c) => {
    const scene = String(c.scene || '').toLowerCase();
    if (scene) return scene;
    return String(c.key || '').startsWith('group_') ? 'group' : '';
  };

  /* dsh 那批会话（QQ 消息由 dsh 落盘）在后端是单独一组：只回 key/count/ts，没有 last 正文。
     ⚠ load_conversations() 已经把 dsh 会话并进 conversations 了，所以这里只补 conversations
     里没有的那些（按 platform:room 去重），否则同一个 QQ 会话会在列表里出现两行。 */
  const seenRooms = new Set((rows || []).map((c) => `${platOf(c)}:${roomOf(c) || String(c.key || '')}`));
  const dshRows = (dsh || [])
    .map((d) => {
      const key = String(d.key || '');
      const isGroup = key.startsWith('group_');
      const room = isGroup ? key.slice(6) : key;
      return { ...d, platform: 'qq', scene: isGroup ? 'group' : 'private', room, key, _dsh: true };
    })
    .filter((d) => !seenRooms.has(`qq:${d.room}`));
  const idOf = (c) => `${platOf(c)}:${roomOf(c) || String(c.key || '')}`;
  const all = [...(rows || []), ...dshRows].filter((c) => !cleared.includes(idOf(c)));
  const shown = platform ? all.filter((c) => platOf(c) === platform) : all;
  const loading = rows === null;
  const has = shown.length > 0;
  const current = all.find((c) => idOf(c) === picked) || null;

  /* 会话标题：后端给了 title/name 就直接用；否则用 platform + scene + room 拼出
     「QQ 群 123456 / QQ 好友 123456 / 微信 好友 xxx」—— 多个 QQ 会话因此能区分开。
     两个都拿不到（老后端）时才退回「<平台> 会话」。 */
  const givenName = (c) => String(c.title || c.name || '').trim();
  const label = (c) => {
    const given = givenName(c);
    if (given) return given;
    const plat = PLATFORM_LABELS[platOf(c)] || '未知平台';
    const room = roomOf(c) || String(c.key || '');
    if (!room) return `${plat} 会话`;
    return `${plat} ${SCENE_LABELS[sceneOf(c)] || '会话'} ${room}`;
  };
  const when = (c) => {
    const ts = Number(c.ts || 0);
    if (!ts) return '';
    try { return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false }); } catch { return ''; }
  };
  // 名字里已经带了平台（上面拼出来的那几种）时，meta 行就不再重复平台
  const meta = (c) => [
    givenName(c) ? (PLATFORM_LABELS[platOf(c)] || '') : '',
    c.count ? `${c.count} 条` : '',
    when(c),
  ].filter(Boolean).join(' · ');

  const refresh = async () => {
    await load();
    setMsg('已刷新会话列表');
  };

  return (
    <>
      <header className="page-head">
        <div>
          <h1>消息中心</h1>
          <p>QQ 与微信 iLink 的会话都在这里。</p>
        </div>
      </header>

      <div className="msg-bar">
        <span>平台</span>
        <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
          {MESSAGE_PLATFORMS.map(([key, labelText]) => (
            <option key={key} value={key}>
              {labelText}
            </option>
          ))}
        </select>
        <button type="button" className="btn" disabled={busy} onClick={refresh}>
          {busy ? '刷新中…' : '刷新'}
        </button>
        <button
          type="button"
          className="btn"
          disabled={!has}
          title="只清这个页面的显示，机器人里的记录不动"
          onClick={() => {
            setCleared(all.map(idOf));
            setPicked('');
            setMsg('列表已清空（只清显示，记录没动）');
          }}
        >
          清空列表
        </button>
        {cleared.length > 0 && (
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => { setCleared([]); setMsg('已读回会话列表'); }}
          >
            显示回来
          </button>
        )}
        <span className="msg-bar__stat">
          {loading ? '读取中…' : needLogin ? '' : `共 ${all.length} 个会话${platform ? `（当前筛选：${PLATFORM_LABELS[platform]}，${shown.length} 个）` : ''}`}
        </span>
      </div>

      <section className="card card--strong card--fill card--msg">
        {has && (
          <div className="msg-list">
            {shown.map((c, i) => (
              <div
                className={picked === idOf(c) ? 'msg-item msg-item--on' : 'msg-item'}
                key={`${platOf(c)}-${c.key || c.title || c.name || i}`}
                role="button"
                tabIndex={0}
                title="点一下看这条会话的全部信息"
                onClick={() => setPicked(idOf(c))}
                onKeyDown={(ev) => { if (ev.key === 'Enter') setPicked(idOf(c)); }}
              >
                <div className="msg-item__head">
                  <span className="msg-item__who">{label(c)}</span>
                  <span className="msg-item__meta">{meta(c)}</span>
                </div>
                {(c.last_text || c.last) && <div className="msg-item__last">{c.last_text || c.last}</div>}
              </div>
            ))}          </div>
        )}
      </section>

      {/* 内容查看：把这条会话有的字段按人话摊开。没有字段就不编 —— 正文接口还没上，
          这里照实说"只能看到最近一条"。 */}
      {current && (
        <section className="card card--wide msg-detail">
          <h2 className="card__title">会话内容</h2>
          <div className="stat-rows">
            <div className="stat-row">
              <span>会话</span>
              <b>{label(current)}</b>
            </div>
            <div className="stat-row">
              <span>平台</span>
              <b>{PLATFORM_LABELS[platOf(current)] || '未知平台'}</b>
            </div>
            <div className="stat-row">
              <span>场景</span>
              <b>{SCENE_LABELS[sceneOf(current)] || '会话'}</b>
            </div>
            <div className="stat-row">
              <span>会话标识</span>
              <b className="mono">{String(current.key || roomOf(current) || '—')}</b>
            </div>
            <div className="stat-row">
              <span>消息条数</span>
              <b>{Number(current.count || 0) ? `${current.count} 条` : '—'}</b>
            </div>
            <div className="stat-row">
              <span>最近一条</span>
              <b>{when(current) || '—'}</b>
            </div>
          </div>
          {(current.last_text || current.last) ? (
            <p className="card__note">最后一条：{current.last_text || current.last}</p>
          ) : (
            <p className="card__note">这条会话只有条数和时间，看不到正文。</p>
          )}
          <div className="btn-row btn-row--6">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              title="复制列表里已有的信息"
              onClick={async () => {
                const text = [
                  label(current),
                  `平台：${PLATFORM_LABELS[platOf(current)] || '未知平台'}`,
                  `会话标识：${String(current.key || roomOf(current) || '')}`,
                  when(current) ? `最近一条：${when(current)}` : '',
                  (current.last_text || current.last) ? `最后一条：${current.last_text || current.last}` : '',
                ].filter(Boolean).join('\n');
                try {
                  await copyToClipboard(text);
                  setMsg('已复制这条会话的信息');
                } catch (err) {
                  setMsg(`复制失败：${err.message}`);
                }
              }}
            >
              复制会话信息
            </button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPicked('')}>
              收起
            </button>
          </div>
        </section>
      )}

      {!has && !loading && (
        <div className="empty-state empty-state--messages">
          {needLogin ? (
            <>
              <div className="empty-state__title">读不到会话列表</div>
              <div className="empty-state__hint">{LOGIN_HINT}</div>
            </>
          ) : err ? (
            <>
              <div className="empty-state__title">读取失败</div>
              <div className="empty-state__hint">
                {err}
                （点上面「刷新」重试）
              </div>
            </>
          ) : platform ? (
            <>
              <div className="empty-state__title">这个平台还没有会话</div>
              <div className="empty-state__hint">
                机器人已经有 {all.length} 个会话，但没有「{PLATFORM_LABELS[platform]}」的：
                把上面的「平台」改回「全部」就能看到。
              </div>
            </>
          ) : (
            <>
              <div className="empty-state__title">还没有会话记录</div>
              <div className="empty-state__hint">
                机器人收到过消息后这里就会出现会话。
              </div>
            </>
          )}
        </div>
      )}

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}

/* 全局管理（服务）：只回答「服务在不在跑」+ 每行一个动作。
   端口 / 目录 / 日志路径这些运维细节全部收进「运维细节」折叠区，默认不占版面。
   数据来自 /api/services/status（bot + dsh），动作走 /api/services/{start,stop,restart}。 */
/* 全局管理（导出是为了让冒烟能直接渲染一遍：DSH 智能体块与那句说明要有断言锁着）。 */
export function ServicesPage({ config, health, services, onAccess, onLogs, onDeps,
                               loggedIn = true }) {
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState('');

  const act = async (action) => {
    const label = { start: '启动', stop: '停止', restart: '重启' }[action] || action;
    setBusy(action);
    try {
      await post(`/api/services/${action}`, { service: 'bot' });
      setMsg(`${label}指令已发送`);
    } catch (e) {
      setMsg(withAuthHint(e));
    } finally {
      setBusy('');
    }
  };

  const stopBot = () => {
    if (!window.confirm('确定停止机器人？\nQQ / 微信 会离线，机器人不再回复任何消息。')) return;
    act('stop');
  };

  /* 重启会打断正在进行的对话、让 QQ / 微信 离线约 10 秒 —— 全局管理页这一处原来没有确认，
     和首页/接入页的做法不一致，这里补齐。 */
  const restartBot = () => {
    if (!window.confirm('确定重启机器人？\n大约 10 秒内 QQ / 微信 会短暂离线，正在进行的对话会中断。')) return;
    act('restart');
  };

  const botState = services?.bot?.state;
  const botOn = botState === 'running';
  const botText =
    services == null
      ? '状态未知'
      : botOn
        ? '运行中'
        : botState === 'installed'
          ? '已停止（已部署）'
          : '未部署（点启动会用部署脚本装好）';
  const botTone = services == null ? 'unknown' : botOn ? 'running' : 'stopped';
  // 未登录时 config 是 null：不摆 12113 这个假端口（「配置归 0」的观感来源之一）
  const botPort = portText(config, health);
  const dshState = services?.dsh?.state || '';
  const dshText = dshState === 'running' ? '运行中' : '暂未开放（待接入）';

  return (
    <>
      <header className="page-head">
        <div>
          <h1>全局管理</h1>
          <p>服务状态与开关。</p>
        </div>
      </header>

      <section className="card svc">
        <div className="svc__row">
          <div className="svc__main">
            <div className="svc__name">机器人</div>
            <div className="svc__meta">QQ / 微信 共用一个进程，重启会短暂离线约 10 秒</div>
          </div>
          <div className="svc__side">
            <StatusBadge tone={botTone}>{botText}</StatusBadge>
            {botOn ? (
              <button type="button" className="btn" disabled={busy === 'restart'} onClick={restartBot}>
                重启
              </button>
            ) : (
              <button type="button" className="btn btn--primary" disabled={busy === 'start'} onClick={() => act('start')}>
                启动
              </button>
            )}
          </div>
        </div>

        <div className="svc__row">
          <div className="svc__main">
            <div className="svc__name">DSH 智能体</div>
            <div className="svc__meta">本机的群聊智能体通道，接入后会出现在这里</div>
          </div>
          <div className="svc__side">
            <StatusBadge tone={dshState === 'running' ? 'running' : 'unknown'}>{dshText}</StatusBadge>
          </div>
        </div>
      </section>

      <details className="fold">
        <summary className="fold__summary">运维细节：端口 / 目录 / 日志路径</summary>
        <div className="fold__body">
          <section className="card">
            <div className="stat-rows">
              <div className="stat-row">
                <span>端口</span>
                <b>{botPort}</b>
              </div>
              <div className="stat-row">
                <span>机器人目录</span>
                <b>{services?.bot?.bot_dir || '—'}</b>
              </div>
              <div className="stat-row">
                <span>日志路径</span>
                <b>{services?.bot?.log || '—'}</b>
              </div>
              <div className="stat-row">
                <span>版本 / 机器码</span>
                <b>
                  {health?.version || '—'}
                  {health?.machine_id ? ` · ${String(health.machine_id).slice(0, 8)}…` : ''}
                </b>
              </div>
            </div>
            <div className="p-row">
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                title="点这里复制路径。"
                onClick={() => copyServerPath(setMsg, '机器人目录', botEnvPathBase)}
              >
                复制机器人目录
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => copyServerPath(setMsg, '日志路径', async () => services?.bot?.log || '')}
              >
                复制日志路径
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={onLogs}>
                实时日志
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={onDeps}>
                依赖管理
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={onAccess}>
                接入配置
              </button>
              {/* 危险动作：第三级样式 + 二次确认 */}
              {botOn && (
                <button type="button" className="btn btn--ghost btn--sm" onClick={stopBot}>
                  停止机器人
                </button>
              )}
            </div>
          </section>
        </div>
      </details>

      {/* 日志保留：现在多少天、占多大、立刻清理 —— 与「日志」页共用同一个组件，
          两处入口看到的永远是同一份数据，不做两套。 */}
      <LogRetention onMsg={setMsg} />

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}

/** 机器人目录（不是 .env 文件本身）：运维细节里复制用 */
async function botEnvPathBase() {
  const s = await get('/api/services/status');
  const dir = s && s.bot && s.bot.bot_dir;
  if (!dir) throw new Error('没拿到 bot 目录（机器人还没部署过）');
  return dir;
}

/* 设置：拆成「外观 / 账号 / 高级」三个 tab（每屏只放一件事，每屏一个主操作）。
   外观 = 主题、质感、动态背景（即时生效，一个「保存设置」写入配置）；
   账号 = 邮箱 / 套餐权益 / 版本更新 / 退出登录；
   高级 = 开发者模式、启动行为、以及全部运维动作（复制路径、端口、依赖、日志）。
   外观项只存浏览器本地（appearance.js），端口等仍走 /api/config。 */
const UI_THEME_OPTIONS = ['极光玻璃', '瑞士极简', '深空终端'];
const ACCENT_OPTIONS = ['深邃蓝', '暗夜紫', '赛博青', '暖阳橙', '薄荷绿', '深空绿（新色板）'];
const GLASS_OPTIONS = ['默认（原样）', '液态玻璃', '颗粒磨砂'];
const SETTING_TABS = [
  ['appearance', '外观'],
  ['account', '账号'],
  ['advanced', '高级'],
];

export function Settings({
  config,
  services,
  onSaved,
  beginner,
  onBeginner,
  loggedIn,
  health,
  auth,
  onChanged,
  onToken,
  appearance,
  onAppearance,
  onGo = () => {},
  version = '',
  tab: tabProp = '',
  onTab = null,
  /* 「锁定这台设备」用：App 的 lockConsole（退出控制台会话，**不动星群账号**）。
     默认 null 时卡片自己会提示"没接上锁屏入口"，不会静默失效。 */
  onLockConsole = null,
}) {
  const [form, setForm] = useState(null);
  const [info, setInfo] = useState(null);
  const [msg, setMsg] = useState('');
  /* 页签默认由这里管；App 传 tab/onTab 时以 App 为准（登录提示条的「去登录」
     要能直接落到「账号」页签）。两种用法都留着，组件单用也不会坏。 */
  const [localTab, setLocalTab] = useState('appearance');
  const tab = tabProp || localTab;
  const setTab = (v) => { setLocalTab(v); if (onTab) onTab(v); };
  const [latest, setLatest] = useState('');
  const [checkedAt, setCheckedAt] = useState('');
  // 未登录时 /api/status 与 console/dev-mode 都 401，组件里以前是 .catch(()=>{}) 静默吞掉：
  // 表现是「安装根目录恒 —」「开发者模式恒关」，用户以为功能没了。这里记一句实话，见下面对应位置。
  const [infoErr, setInfoErr] = useState('');
  const [devErr, setDevErr] = useState('');
  // 开发者模式：无头端存在服务器的 config_home/console.json（跟桌面端 settings 里的
  // developer_mode 一个语义），开了才能在插件页从 PyPI 装第三方插件。
  const [devMode, setDevMode] = useState(false);
  const ap = { ...APPEARANCE_DEFAULTS, ...(appearance || {}) };
  const set = (patch) => onAppearance({ ...ap, ...patch });

  useEffect(() => {
    if (form === null && config) setForm(config);
  }, [config, form]);
  useEffect(() => {
    get('/api/status')
      .then((d) => { setInfo(d); setInfoErr(''); })
      .catch((e) => { setInfo(null); setInfoErr(isAuthError(e) ? 'login' : String(e.message)); });
    get('/api/console/dev-mode')
      .then((d) => { setDevMode(Boolean(d.developer_mode)); setDevErr(''); })
      .catch((e) => setDevErr(isAuthError(e) ? 'login' : String(e.message)));
  }, []);
  const f = form || {};
  const isGlass = ap.theme === UI_THEME_OPTIONS[0];

  const todo = (what) => setMsg(`${what}：无头端暂不支持，请在服务器或桌面端操作`);
  // 标题栏那个版本号（Linux 无头端自己的版本）在两处都要一致：没有就照实说读不到
  const curVersion = version || '版本读取中';
  // 检查更新：桌面端是查签发站的 /license/version（同源，控制台就挂在 astroswarm.cn 下）。
  // 「打开 settings.json / 打开安装目录 / 导入旧版本 / 卸载」在无头端没有任何可做的事
  // （服务器上没有本地文件管理器，卸载也不该由网页按钮代劳），已从界面上删掉。
  const checkUpdate = async () => {
    setMsg('正在检查更新…');
    try {
      const r = await fetch('/license/version', { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const v = String(d.version || '').trim();
      if (!v) throw new Error('返回里没有版本号');
      setLatest(v);
      setCheckedAt(new Date().toLocaleString('zh-CN', { hour12: false }));
      // 站点上的版本号是**桌面包**的编号（1.2.x），和 Linux 端不是同一串：
      // 以前拿它和写死的页面版本比大小，比出来的是假的。现在只报两个真值。
      setMsg(`站点最新桌面版 v${v}；本机 Linux 端 ${curVersion}。`
        + '要升级就按官网教程重装或更新这台机器。');
    } catch (e) {
      setMsg(`检查更新失败：${e.message}（页面不在 astroswarm.cn 下时跨域会被拦）`);
    }
  };
  /* 这里原来有一个「保存设置」按钮，它写的其实只有端口（外观项是改一下就即时存本浏览器的），
     按钮名字和它做的事对不上，而且和独立折叠区里的「保存端口」职责重叠。
     现在端口归上面「网络端口」卡片管，外观本来就是即时生效，所以这个按钮连同 save() 一起删掉 ——
     同一屏不再出现两个「保存」。onSaved 仍被 savePorts 用着，没丢。 */
  // 端口：重排版时输入框丢了（f.port 全文只出现在 save() 的 PUT 里，没有任何控件写它），
  // 于是「保存设置」一直在回写没变的值。这里补回两个输入，单独走 PUT /api/config。
  const savePorts = async () => {
    const port = Number(f.port);
    const botPort = Number(f.bot_port);
    const bad = (n) => !Number.isInteger(n) || n < 1 || n > 65535;
    if (bad(port) || bad(botPort)) {
      setMsg('端口必须是 1–65535 之间的整数');
      return;
    }
    try {
      const saved = await put('/api/config', { port, bot_port: botPort });
      setForm({ ...f, ...saved });
      onSaved();
      setMsg('端口已保存：改端口要重启对应服务才生效');
    } catch (e) {
      setMsg(`端口未写入服务器（${e.message}）`);
    }
  };
  /* 端口：NumberField 给的是数字，保存前 savePorts 自己会校验 1–65535 并给中文提示，
     所以这里只要不把 NaN 写进 form 就行（NaN 会让按钮一直显示"未保存"却点不动）。 */
  const setPortNum = (key) => (n) => setForm((cur) => ({ ...cur, [key]: Number.isFinite(n) ? n : cur[key] }));
  // 选择本地文件：浏览器拿不到真实路径，只能给个本次会话有效的 blob URL；
  // 要长期生效就把图片放到服务器上、在路径里填 URL（/bg.jpg 或 https://…）。
  const pickFile = (file) => {
    if (!file) return;
    set({ enabled: true, path: URL.createObjectURL(file) });
    setMsg('已选本地文件（仅本次会话有效）');
  };
  /* 这里的登录表单搬进了 LoginModal.jsx 的 LoginForm：弹窗与这个页签共用同一份实现。
     逻辑（邮箱/密码、登录中禁用、后端没回 token 不算成功、失败原因怎么显示）
     现在只有一处，改它两处一起变 —— 不再在这儿留第二份。 */
  /* 退出登录：/api/auth/logout 是**要鉴权**的接口（api.py 的 _require_auth），
     token 过期/被清时后端回 401 → post 抛错。以前没有 try/catch，于是 onToken('') 不执行、
     提示不显示、界面毫无反应 —— 用户只会觉得「退出登录按钮坏了」。
     现在无论如何都清掉本地 token 并跳回未登录态；服务器没确认退出时如实说明。
     本地 token 才是真正的「已登录」判据（App 里 token 状态就是这么用的），清掉它 =
     本机已经退出了，服务器那侧只是把会话记录也注销一下。 */
  const logout = async () => {
    let note = '已退出登录';
    try {
      await post('/api/auth/logout', {});
    } catch (e) {
      if (isAuthError(e)) {
        note = '已退出登录。';
      } else {
        note = `已退出本地登录，但服务器没确认（${e.message}）：本机已经退出，可稍后再试一次`;
      }
    } finally {
      onToken('');
      try {
        await onChanged();
      } catch {
        /* 刷新失败不影响「已退出」这件事，本地 token 已经清了 */
      }
      setMsg(note);
    }
  };
  const claim = async () => {
    try {
      await post('/api/auth/claim', { machine_id: health?.machine_id || '' });
      setMsg('已认领本机');
      await onChanged();
    } catch (e) {
      setMsg(String(e.message));
    }
  };
  /* 控制台密码屏与星群账号是两码事，别把这两行合成一句（下面那张卡才是控制台密码）。 */
  // 行高按桌面端实测钉死：值的换行数（Qt 的 wordWrap sizeHint）决定了 32/48 的行，
  // 无头端数据不同、换行数也不同，所以宽度和高度都按实测值固定，保证整块版面一致。
  const row = (label, value, mod = '', vw = 0) => (
    <div className={`set-row set-row--kv${mod}`} key={label}>
      <span>{label}</span>
      <b style={vw ? { width: `${vw}px` } : undefined}>{value}</b>
    </div>
  );

  return (
    <>
      <header className="page-head">
        <div>
          <h1>设置</h1>
          <p>环境信息与外观背景，端口在「高级」页签里改。</p>
        </div>
      </header>

      <div className="tabbar" role="tablist" aria-label="设置分类">
        {SETTING_TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? 'tabbar__item tabbar__item--on' : 'tabbar__item'}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {/* 账号：邮箱 / 套餐权益 / 版本更新。主操作 = 登录或认领本机 */}
      {tab === 'account' && (
        <div className="tabpane">
          <section className="card">
            <h2 className="card__title">星群账号</h2>
            {/* 这一行与下面「账号」那行、顶栏芯片、账号卡**同一个判据**（本地 token）：
                以前这里看 auth.email，芯片看 token —— 两者一不一致就会出现
                "上面写已登录、下面写未登录"的自相矛盾（老板截图里那种）。 */}
            <p className="card__note">
              {loggedIn
                ? `已登录${auth?.email ? `：${auth.email}` : ''}。这个邮箱用来在服务器上识别你这台机器。`
                : '插件、能力包、QQ 与微信通道全部免费。'}
            </p>
            {loggedIn ? (
              <div className="p-row">
                <button type="button" className="btn btn--primary" onClick={claim}>
                  认领本机
                </button>
                <button type="button" className="btn btn--ghost" onClick={logout}>
                  退出登录
                </button>
              </div>
            ) : (
              /* 与登录弹窗**同一个组件**（LoginModal.jsx 的 LoginForm）：
                 两处行为一致，不会这边改了那边忘 */
              <LoginForm onToken={onToken} onChanged={onChanged} onMsg={setMsg} />
            )}
            {/* 头像 + 昵称（官网个人中心同一份数据）：登录后能改，改完各处立刻一致。
                loggedIn 由 App 按**本地 token** 判断（这个组件里没有 token 这个名字）；
                email 只用来在资料还没到时兜底显示，不当作登录判据。 */}
            <AccountProfileBlock
              loggedIn={Boolean(loggedIn)}
              email={auth?.email || ''}
              onMsg={setMsg}
            />
            <div className="stat-rows">
              <div className="stat-row">
                <span>账号</span>
                <b>
                  {loggedIn
                    ? `已登录${auth?.plan ? `（${auth.plan}）` : ''}`
                    : '未登录 · 功能不受影响'}
                </b>
              </div>
            </div>
          </section>

          <section className="card">
            <h2 className="card__title">版本与更新</h2>
            <div className="stat-rows">
              <div className="stat-row">
                <span>当前版本</span>
                <b>{curVersion}</b>
              </div>
              <div className="stat-row">
                <span>最新版本</span>
                <b>{latest || '—'}</b>
              </div>
              <div className="stat-row">
                <span>上次检查</span>
                <b>{checkedAt || '—'}</b>
              </div>
            </div>
            <button type="button" className="btn btn--self" onClick={checkUpdate}>
              检查更新
            </button>
          </section>

          {/* 控制台密码（2026-10-10 老板："再在设置里做个登录web密码设置"）：
              改密码 / 锁屏都在这里，不用再去服务器上删文件。
              和上面「星群账号」是两件事，所以单独一张卡、字号一致。 */}
          <ConsolePasswordCard onLock={onLockConsole} onMsg={setMsg} />
        </div>
      )}

      {/* 高级：网络端口 / 开发者选项 + 运维动作（路径类才折起来）
          端口输入原来藏在「高级 → 再展开一层折叠」里，得点三次才找得到，
          而且和外观页签的「保存设置」抢同一件事（两个按钮都写 /api/config 的端口）。
          现在端口独立成一个**默认可见**的小节，只留一个「保存端口」，外观页签那个多余的
          「保存设置」已经删掉（外观本来就是即时生效的）。 */}
      {tab === 'advanced' && (
        <div className="tabpane">
          <section className="card set-net">
            <h2 className="card__title">网络端口</h2>
            <p className="card__note">
              两个端口都要在 1–65535 之间；改完必须重启对应的服务才生效。
            </p>
            <NumberField
              className="numf--net"
              label="控制台端口"
              value={f.port ?? ''}
              min={1}
              max={65535}
              hint="这个网页后台的端口（重启控制台进程生效）"
              onChange={setPortNum('port')}
            />
            <NumberField
              className="numf--net"
              label="机器人端口"
              value={f.bot_port ?? ''}
              min={1}
              max={65535}
              hint="QQ 协议端反向连接用的端口（重启机器人生效）"
              onChange={setPortNum('bot_port')}
            />
            <p className="card__note">
              控制台端口 = 这个网页后台的端口（重启控制台进程生效）；
              机器人端口 = QQ 协议端反向连接用的端口（重启机器人生效）。
            </p>
            {!loggedIn && <p className="card__note">{LOGIN_HINT}（端口存在服务器上，读写都要登录）</p>}
            <div className="p-row">
              <PillButton tone="primary" size="sm" onClick={savePorts}>
                保存端口
              </PillButton>
            </div>
          </section>

          <section className="card set-ai">
            <h2 className="card__title">AI 插件</h2>
            <p className="card__note">
              内置 AI 插件随程序自动安装，不可卸载。
              接口配置、平台开关、人格与 MCP 都请到「AI 大脑」页设置。
            </p>
            {/* 这里原来是一个「启用 AI 插件」的假勾选框（无 state、无 onChange），
                点了只在本渲染里跳一下、刷新就还原，卡片文案还承诺了「关闭后不加载 AI 功能」
                这个无头端并不存在的能力。改成只读状态徽标，不再假装能点。 */}
            <div className="stat-row">
              <span>AI 插件状态</span>
              <b>已安装 · 常开（无头端不提供关闭开关）</b>
            </div>
          </section>

          <section className="card set-boot">
            <h2 className="card__title">启动行为与开发者选项</h2>
            {/* 这三个都不折叠：开发者模式 = 安全相关的开关，小白模式 = 影响全站导航，
                读不到状态的提示也必须常显（APG：能改变后果的开关不能藏）。 */}
            <ToggleRow
              checked={devMode}
              label="开发者模式（恢复第三方插件安装，后果自负）"
              desc="开了才能在插件页从 PyPI 装第三方插件；装进来的东西跑在服务器上。"
              onChange={async (next) => {
                try {
                  const d = await post('/api/console/dev-mode', { on: next });
                  setDevMode(Boolean(d.developer_mode));
                  setMsg(next ? '开发者模式已开启：插件页可以从 PyPI 装第三方插件了' : '开发者模式已关闭');
                } catch (err) {
                  setDevMode(!next);
                  setMsg(withAuthHint(err));
                }
              }}
            />
            {devErr === 'login' && (
              <p className="card__note">
                {LOGIN_HINT}：开发者模式的当前状态读不到，上面的开关显示的不一定是服务器上的真实值。
              </p>
            )}
            {devErr && devErr !== 'login' && (
              <p className="card__note">开发者模式状态读取失败：{devErr}（可稍后刷新页面重试）</p>
            )}
            <ToggleRow
              checked={Boolean(beginner)}
              label="小白模式（只显示常用功能）"
              desc="关掉后侧栏会多出消息中心 / 插件 / 日志 / 依赖这些高级入口。"
              onChange={(next) => onBeginner(next)}
            />
            {/* 这里原来还有「启动程序时自动启动全部服务」「开机自动启动 AstroSwarm」两个勾选框：
                无 state、无 onChange，点一下变样、刷新就还原 —— 点了没用的假控件，已删除。
                无头端这两个行为由 systemd 管，网页改不了，如实写在下面。 */}
            <p className="card__note">
              开机自启由系统管理，网页上改不了。
              （在服务器上执行 systemctl enable / disable astroswarm-headless 生效）；
              桌面端才有上面那两个注册表开关。
            </p>
          </section>

          <details className="fold">
            <summary className="fold__summary">运维：安装目录 / 环境信息 / 快捷入口</summary>
            <div className="fold__body">
              <section className="card">
                <div className="set-row set-row--kv">
                  <span>安装根目录</span>
                  <b>{info?.data_home || info?.config_home || (infoErr ? '未读到' : '—')}</b>
                </div>
                <div className="set-row set-row--kv">
                  <span>Python</span>
                  <b>部署虚拟环境（随机器人一起装）</b>
                </div>
                {/* 这一行以前读的是 config?.bot_dir —— bot_dir 不在后端 SAFE_KEYS 里，
                    /api/config 永远不会返回它，所以恒显示「—」。同一个值 /api/services/status
                    的 services.bot.bot_dir 里有（全局管理页用的就是它），改用那一个。 */}
                <div className="set-row set-row--kv">
                  <span>机器人目录</span>
                  <b>{services?.bot?.bot_dir || '—'}</b>
                </div>
                {infoErr === 'login' && (
                  <p className="card__note">{LOGIN_HINT}：安装根目录与机器人目录读不到。</p>
                )}
                {infoErr && infoErr !== 'login' && (
                  <p className="card__note">环境信息读取失败：{infoErr}</p>
                )}
                <p className="card__note">
                  ⚠ BETA 测试版，可能不稳定。
                </p>
                <div className="p-row">
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    title="点这里复制路径。"
                    onClick={() => copyServerPath(setMsg, '安装根目录', async () => (await get('/api/status')).data_home || (await get('/api/status')).config_home || '')}
                  >
                    复制安装目录
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => copyServerPath(setMsg, '插件目录', pluginsDirPath)}
                  >
                    复制插件目录
                  </button>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => onGo('deps')}>
                    依赖管理
                  </button>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => onGo('logs')}>
                    实时日志
                  </button>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => onGo('services')}>
                    服务开关
                  </button>
                </div>
              </section>
            </div>
          </details>
        </div>
      )}

      {tab === 'appearance' && (
        <div className="tabpane">
        <section className="card card--strong set-appr">
          <h2 className="card__title">外观与动态背景</h2>
          <p className="card__note">
            下面每一项改完立刻生效，只记在这个浏览器里。
          </p>
          {/* 主题 / 强调色 / 质感：一眼就能看出效果的三项，常在表面 */}
          <div className="set-row">
            <span>UI 主题</span>
            <select
              className="p-select"
              value={ap.theme}
              onChange={(e) => set({ theme: e.target.value })}
              title="每个 UI 主题拥有独立的布局、导航、配色与质感"
            >
              {UI_THEME_OPTIONS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          {isGlass && (
            <div className="set-box">
              <div className="set-row">
                <span>强调色</span>
                <select className="p-select" value={ap.accent} onChange={(e) => set({ accent: e.target.value })}>
                  {ACCENT_OPTIONS.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}

          <div className="set-box">
            <div className="set-row">
              <span>UI 框质感</span>
              <select className="p-select" value={ap.glass} onChange={(e) => set({ glass: e.target.value })}>
                {GLASS_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <ToggleRow
            checked={ap.animations}
            label="减少界面动效"
            desc="关掉转场与浮动动画（低配机器 / 远程桌面更跟手）。"
            onChange={(next) => set({ animations: next })}
          />

          {/* 背景图/视频这一组是"配一次就不动"的低频项：收进折叠区。
              它是外观层，不涉及安全与隐私，收起来不会出事故。 */}
          <Collapse
            storageKey="appearance-background"
            title="背景图 / 视频"
            summary={ap.enabled ? (ap.path ? `已启用 · ${String(ap.path).slice(0, 40)}` : '已启用但没填路径') : '未启用'}
          >
            <ToggleRow
              checked={ap.enabled}
              label="启用背景（图片/视频）"
              desc="叠在界面最底层，不影响任何功能。"
              onChange={(next) => set({ enabled: next })}
            />

            <div className="p-row p-row--top">
              <input
                className="p-input"
                value={ap.path}
                placeholder="背景图片 / 视频的 URL（如 /bg.jpg 或 https://…）"
                spellCheck={false}
                aria-label="背景图片或视频的 URL"
                onChange={(e) => set({ path: e.target.value })}
              />
              <label className="pillbtn pillbtn--secondary pillbtn--md" title="选本地文件只在本次会话有效">
                选择文件 ...
                <input
                  type="file"
                  accept="image/*,video/*"
                  style={{ display: 'none' }}
                  onChange={(e) => pickFile(e.target.files && e.target.files[0])}
                />
              </label>
              <PillButton tone="secondary" onClick={() => set({ path: '' })}>
                清除
              </PillButton>
            </div>

            <div className="p-row p-row--chk">
              <ToggleRow
                checked={ap.autoplay}
                label="自动播放"
                onChange={(v) => set({ autoplay: v })}
              />
              <ToggleRow
                checked={ap.loop}
                label="循环播放"
                onChange={(v) => set({ loop: v })}
              />
            </div>

            {/* 三个滑块以前是三套裸 input[type=range]，数值还用不同写法显示
                （0.68 / 70% / 0）；现在统一成 NumberField 的滑杆形态，单位后缀写清楚。 */}
            <NumberField
              label="遮罩强度"
              value={ap.mask}
              min={30}
              max={95}
              slider
              unit="%"
              hint="背景越暗，界面文字越清楚"
              onChange={(v) => set({ mask: v })}
            />
            <NumberField
              label="UI 不透明度"
              value={ap.opacity}
              min={10}
              max={95}
              slider
              unit="%"
              hint="只影响极光玻璃主题的面板底色"
              onChange={(v) => set({ opacity: v })}
            />
            <NumberField
              label="视频亮度"
              value={ap.bright}
              min={-100}
              max={100}
              slider
              hint="0 = 原样；负值压暗，正值提亮"
              onChange={(v) => set({ bright: v })}
            />
          </Collapse>

          {/* 外观改动是「改一下即时生效」的：本地镜像走 App 里 useEffect → saveAppearance，
              同时 App 的 onAppearance 会写一遍服务器（/api/config 的 ui_* 键），
              所以换浏览器登录后这些偏好还在；未登录时只在本机生效并给一句中文提示。
              原来这里还有个「保存设置」按钮，实际只写端口 —— 名字和做的事不一致，也和外面上面的
              「保存端口」重复。现在这一屏没有任何「保存」：改动即时生效，端口归「高级 → 网络端口」。 */}
          <p className="card__note">
            以上改动即时生效，不需要点保存；会同步到服务器，换浏览器登录后仍然生效。
            端口、依赖、服务开关在「高级」页签里。
          </p>
        </section>
      </div>
      )}

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}
function Deps() {
  const [summary, setSummary] = useState('尚未扫描');
  const [missing, setMissing] = useState([]);
  const [checked, setChecked] = useState(0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  // 安装输出：后端把 pip 的每一行都写进 /api/deps/status 的 log，这里照原样显示
  // （桌面端任务面板里也是这份输出）。装不上时用户能拿着它找人看。
  const [output, setOutput] = useState([]);
  const [stage, setStage] = useState('');

  const absorb = (st) => {
    if (typeof st.checked === 'number') setChecked(st.checked);
    if (Array.isArray(st.missing)) setMissing(st.missing);
    if (Array.isArray(st.log)) setOutput(st.log.map((l) => String(l)));
    if (st.stage) setStage(String(st.stage));
    // st.error 是后端任务异常原文（可能含 pip 输出/路径）：先说人话，再附原文
    if (st.error) setMsg(`上一个依赖任务出错了，请把下面这段发给开发者：${st.error}`);
  };
  // 边跑边显示：轮询回调里既更新阶段文字，也把 log 追加出来
  const onPoll = (s) => {
    absorb(s);
    setStage(String(s.stage || ''));
    setMsg(String(s.stage || '执行中…'));
  };

  // 进页面先接一次状态：可能在插件页刚点过「重装缺失依赖」，结果要接上
  useEffect(() => {
    get('/api/deps/status')
      .then((st) => {
        absorb(st);
        if (st.checked) {
          setSummary(`上次扫描：共检查 ${st.checked} 项依赖，缺失 ${(st.missing || []).length} 项`);
        }
        if (st.running) setBusy(true);
      })
      .catch(() => {});
  }, []);

  const scan = async () => {
    setBusy(true);
    setMissing([]);
    setOutput([]);
    setSummary('正在后台扫描插件依赖 ...');
    try {
      const st = await runDeps('scan', onPoll);
      absorb(st);
      const n = (st.missing || []).length;
      setSummary(`上次扫描：共检查 ${st.checked || 0} 项依赖，缺失 ${n} 项`);
      // st.error 来自后端 _scan_deps 的异常，里面可能是 pip 输出或路径：
      // 直接甩给用户看不懂，先说人话再附原文。
      setMsg(st.error
        ? `扫描出错，请把下面这段发给开发者：${st.error}`
        : (n ? `缺失 ${n} 项，可点「安装缺失依赖」` : '依赖齐全，无需安装'));
    } catch (e) {
      setMsg(withAuthHint(e));
      setSummary('扫描失败');
    } finally {
      setBusy(false);
    }
  };

  const installMissing = async () => {
    if (!missing.length) {
      setMsg('当前没有缺失依赖');
      return;
    }
    setBusy(true);
    try {
      const st = await runDeps('install', onPoll);
      setMissing([]);
      setSummary(`上次扫描：共检查 ${checked} 项依赖，缺失 0 项；缺失依赖已安装，建议重新扫描验证`);
      setMsg(st.error
        ? `安装出错，请把下面这段发给开发者：${st.error}`
        : `安装完成：成功 ${(st.installed || []).length} 项，建议重新扫描验证`);
    } catch (e) {
      setMsg(withAuthHint(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <header className="page-head">
        <div>
          <h1>依赖管理</h1>
          <p>扫描插件依赖，缺什么一键装上。</p>
        </div>
      </header>

      <section className="card dep-scan">
        <p className="dep-value">{summary}</p>
        <p className="card__note">扫描在后台进行，缺失的会列在下面。</p>
        <div className="p-row">
          <button type="button" className="btn btn--primary" onClick={scan} disabled={busy}>
            开始全量扫描
          </button>
          <button
            type="button"
            className="btn"
            title="无头端没有本地文件管理器：点这里复制插件目录"
            onClick={() => copyServerPath(setMsg, '插件目录', pluginsDirPath)}
          >
            复制插件目录
          </button>
        </div>
      </section>

      <section className="card card--strong dep-missing">
        <h2 className="card__title">缺失依赖</h2>
        <div className="p-list dep-list">
          {missing.map((d) => (
            <div className="p-item" key={d}>
              {d}
            </div>
          ))}
        </div>
        <div className="p-row">
          <button
            type="button"
            className="btn btn--primary"
            disabled={!missing.length || busy}
            onClick={installMissing}
          >
            安装缺失依赖
          </button>
        </div>
      </section>

      {/* 依赖任务输出：桌面端任务面板里的那份 pip 输出，控制台这里照原样显示。
          这里原来是一张只有标题的空卡片，上一轮被删掉了；现在它有真内容（log），
          所以重新加回来 —— 装不上时用户能拿着这段去找人看。 */}
      <section className="card card--strong dep-log">
        <h2 className="card__title">依赖任务输出</h2>
        <p className="card__note">
          {busy
            ? `正在执行：${stage || '准备中…'}`
            : stage
              ? `上次执行：${stage}`
              : '还没跑过任务，跑一次就有输出。'}
        </p>
        <pre className="install-log">
          {output.length
            ? output.slice(-40).join('\n')
            : '（没有输出）'}
        </pre>
        <div className="p-row">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={!output.length}
            onClick={() => setOutput([])}
          >
            清空输出
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            title="去「日志」页看实时日志"
            onClick={() => setMsg('实时日志在左侧「日志」页（小白模式下要先在「设置」里关掉小白模式）')}
          >
            去日志页
          </button>
        </div>
      </section>

      {msg && <div className="toast">{msg}</div>}
    </>
  );
}
