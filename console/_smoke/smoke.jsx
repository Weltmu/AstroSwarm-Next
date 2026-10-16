/* 冒烟：把卡片与账号头像块的「纯视图」真渲染一遍（喂假数据），检查
   1) 不会运行时报错；2) 该出现的中文文案都在；3) "会员/买断/未解锁" 这类旧字样不再出现。
   跑法（依赖 vite 自带的 esbuild）：
     node node_modules/esbuild/bin/esbuild _smoke/smoke.jsx --bundle --platform=node \
       --format=cjs --jsx=automatic --loader:.jsx=jsx --outfile=_smoke/smoke.cjs --log-level=error
     node _smoke/smoke.cjs                                                        */
import React, { act } from 'react';
import { renderToString } from 'react-dom/server';
import { createRoot } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import AccountAvatar, { avatarGradient, avatarHue, initialOf } from '../src/AccountAvatar.jsx';
import AccountProfileBlock from '../src/AccountProfileBlock.jsx';
import AvatarCrop, { CROP_CORNERS } from '../src/AvatarCrop.jsx';
import { AiBrain, Overview, ServicesPage, Settings, effectivePersonaText } from '../src/App.jsx';
import { TodayStats } from '../src/BrainStats.jsx';
import { NAV_GROUPS, ADVANCED_PAGES } from '../src/nav.js';
import { BotAiView, bindingText, friendRequestText, normalizeAiDraft, normalizeSendTargets } from '../src/BotAiCard.jsx';
import { MachineCapabilitiesView, filterCapabilities } from '../src/MachineCapabilities.jsx';
import { PluginWorkshopView } from '../src/PluginWorkshopCard.jsx';
import SetupStepsView, { setupSteps } from '../src/SetupSteps.jsx';
import { WorkshopView } from '../src/WorkshopPage.jsx';
import {
  ConsolePasswordView, consolePasswordCheck, consolePasswordErrText,
} from '../src/ConsolePasswordCard.jsx';
import ConsoleLogin from '../src/ConsoleLogin.jsx';
import { getIdea, setIdea } from '../src/workshopIdea.js';
import { fileToSquareJpeg, isAccountExpired, loadProfile, saveNickname, sendAvatar, setAccountExpiredHandler, syncProfileFromSite, watchProfile, PROFILE_POLL_MS } from '../src/profile.js';
import {
  CROP_QUALITY, CROP_SIZE, containSize, cropGeometry, fileToCroppedJpeg,
  initialCropBox, moveCropBox, resizeCropBox,
} from '../src/profile.js';
import { get } from '../src/api.js';
import { QqChannelView } from '../src/QqChannelCard.jsx';
import { QqOfficialView, groupRows, qqOfficialBadge } from '../src/QqOfficialCard.jsx';
import { FeishuView, feishuBadge } from '../src/FeishuCard.jsx';
import { TelegramView } from '../src/TelegramCard.jsx';
import { LogRetentionView, cleanupText, formatBytes } from '../src/LogRetention.jsx';
import LoginBanner from '../src/LoginBanner.jsx';
import LoginModal, {
  LoginForm, LoginFormView, emailLooksOk, codeLooksOk, passwordLooksOk,
  emailStepError, passwordStepError, registerStepError, stepForCheck,
  checkFailText, CHECK_FAIL_TEXT, sendCodeText, cooldownText, errText,
} from '../src/LoginModal.jsx';
import { MarketView, accessLabel, marketFilter } from '../src/MarketPanel.jsx';
import { versionLabel, VERSION_PENDING } from '../src/version.js';
/* 统一控件库（2026-10-12）：开关 / 胶囊按钮 / 折叠 / 数值输入 */
import Toggle, { ToggleRow } from '../src/components/Toggle.jsx';
import PillButton from '../src/components/PillButton.jsx';
import Collapse from '../src/components/Collapse.jsx';
import NumberField from '../src/components/NumberField.jsx';

const AVATAR = 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQ==';
const strip = (html) => String(html).replace(/<!-- -->/g, '');
const mustHave = [];
const mustNotHave = ['会员', '买断', '未解锁', '付费', '尊享', '免费版'];
let bad = 0;

function check(label, ok, extra = '') {
  if (!ok) {
    bad += 1;
    console.log(`✗ ${label}${extra ? ` —— ${extra}` : ''}`);
  } else {
    console.log(`✓ ${label}`);
  }
}

async function main() {
  const now = Date.now() / 1000;

  /* 人设卡片那一段要真挂载、真点一次（React 18 的 act 需要这个标志，否则刷屏警告） */
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;

  /* Node 里没有 localStorage，而 api.js 会读它拿登录 token（浏览器里有）。
     这里补一个最小替身，否则所有请求都会在 api.js 里抛错。 */
  if (!globalThis.localStorage) {
    const store = new Map();
    globalThis.localStorage = {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    };
  }

  /* App.jsx 里的页面在渲染时就会读 window.location.hostname（拼反向 WS 地址）、
     个别按钮回调里用 window.confirm。Node 里补一个最小替身，页面才渲染得出来。 */
  if (!globalThis.window) {
    globalThis.window = {
      location: { hostname: '127.0.0.1', protocol: 'http:', origin: 'http://127.0.0.1' },
      confirm: () => true,
      open: () => {},
    };
  }
  /* React 提交前会查一次焦点元素，里面用 `element instanceof window.HTMLIFrameElement`。
     Node 里没有这个构造器（上面那个 window 替身也没有）→ commitRoot 会崩，这里补齐。
     顺带补 Element/HTMLElement：react-dom 的另几处 instanceof 也要它们。 */
  class FakeEl {}
  if (!globalThis.window.HTMLIFrameElement) globalThis.window.HTMLIFrameElement = FakeEl;
  if (!globalThis.window.Element) globalThis.window.Element = FakeEl;
  if (!globalThis.window.HTMLElement) globalThis.window.HTMLElement = FakeEl;
  if (!globalThis.Element) globalThis.Element = FakeEl;
  if (!globalThis.HTMLElement) globalThis.HTMLElement = FakeEl;
  if (!globalThis.HTMLIFrameElement) globalThis.HTMLIFrameElement = FakeEl;
  /* 统一控件库的折叠区用 requestAnimationFrame 做"先量高度再收到 0"那一步
     （这样展开/收起才有过渡，不会跳）。Node 里没有它 → 补一个立刻执行的最小替身，
     否则一渲染 Collapse 就 ReferenceError。 */
  if (!globalThis.requestAnimationFrame) {
    globalThis.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 0);
    globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
  }

  /* ── 机器人 AI（群聊）卡片 ───────────────────────────────── */
  const draft = {
    group_chat_enabled: true,
    rich_parse_enabled: true,
    image_recognition_enabled: true,
    self_forge_enabled: true,
    cross_platform_enabled: true,
    // 身份绑定：形如 [['wechat:xxx', 'qq_official:123']]，界面上只显示平台名
    identity_bindings: [['wechat:abc', 'qq_official:9']],
    allowed_groups: [],
    known_groups: [
      { openid: 'D596CCDD43D380A5DE34691E3F6E1384', last_ts: now - 60, count: 12 },
      { openid: 'AAAABBBBCCCCDDDDEEEEFFFF00001111', last_ts: now - 90000, count: 3 },
    ],
    interject: { enabled: false, probability: 0.3, interval_min: 30, interval_max: 90, history_count: 10 },
    session: { mode: 'timeout', expire_seconds: 30 },
  };
  const botAi = strip(renderToString(React.createElement(BotAiView, {
    draft, setDraft: () => {}, busy: false, saved: '', onSave: () => {}, onReload: () => {},
  })));

  /* ── QQ 群消息 + 体检卡片 ────────────────────────────────── */
  const state = {
    mode: 'at_only',
    text: '仅 @ 消息（只在群里 @ 它时才收得到） · 最近 21:30',
    hint: '当前只收得到 @ 它的消息。想让它看到群里全部聊天：去 q.qq.com → 你的机器人 → 打开「接收所有消息」。',
    last_ts: now,
    events: { GROUP_AT_MESSAGE_CREATE: 3 },
  };
  const report = {
    summary: '查到 2 处会导致「不说话」的问题，按下面带 ✗ 的条目一条条处理就行。',
    rows: [
      { name: '凭证', level: 'ok', text: 'AppID / Token / AppSecret 都填好了', hint: '' },
      { name: '机器人进程', level: 'bad', text: 'NoneBot 没在跑', hint: '点页面上方「启动全部」。' },
      { name: '群消息', level: 'info', text: '仅 @ 消息', hint: '去开「接收所有消息」。' },
    ],
  };
  const qqCard = strip(renderToString(React.createElement(QqChannelView, {
    state, stateErr: '', report, busy: false, onDiagnose: () => {}, onReload: () => {},
  })));

  /* ── 账号头像与昵称 ─────────────────────────────────────── */
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes('/api/account/profile')) {
      return {
        ok: true,
        json: async () => ({
          email: 'li@astroswarm.cn',
          nickname: '清秋',
          signature: '做个能用的东西',
          display_name: '清秋',
          plan: 'permanent',
          avatar: AVATAR,
        }),
      };
    }
    return { ok: true, json: async () => ({}) };
  };
  const loaded = await loadProfile();
  check('账号资料能读进共享 store', Boolean(loaded && loaded.display_name === '清秋'));

  const withAvatar = renderToString(React.createElement(AccountAvatar, {
    profile: { display_name: '清秋', avatar: AVATAR },
  }));
  const fallback = renderToString(React.createElement(AccountAvatar, {
    profile: { display_name: '清秋' },
  }));
  const block = strip(renderToString(React.createElement(AccountProfileBlock, {
    loggedIn: true, onMsg: () => {},
  })));
  const blockOut = strip(renderToString(React.createElement(AccountProfileBlock, {
    loggedIn: false, onMsg: () => {},
  })));

  check('有头像时渲染 <img>', withAvatar.includes('<img') && withAvatar.includes(AVATAR));
  check('没头像时显示名字首字', fallback.includes('清') && !fallback.includes('<img'));
  check('没名字时用邮箱首字母', initialOf('', 'li@astroswarm.cn') === 'L');
  check('啥都没有时兜底一个字', initialOf('', '') === '星');

  /* 兜底头像必须和官网长得一模一样（老板 2026-10-06：两边昵称都叫 Lihui，
     官网是蓝紫渐变圆 + L，控制台还是个灰圆）。下面 siteStops() 是从官网
     space.js 的 initialAvatar() 照抄的一份参考实现：官网那张图是 canvas 画的
     hsl(h,60%,52%) → hsl(h+42,56%,32%) 对角渐变，控制台用同色的 CSS 渐变
     （135deg 就是左上→右下），两边算出来的两个色标必须逐字相等。 */
  const siteStops = (name) => {
    let h = 0;
    for (let i = 0; i < String(name).length; i += 1) h = (h * 31 + String(name).charCodeAt(i)) >>> 0;
    const hue = h % 360;
    return `hsl(${hue},60%,52%)+hsl(${(hue + 42) % 360},56%,32%)`;
  };
  const stopsOf = (css) => (String(css).match(/hsl\([^)]*\)/g) || []).join('+');
  check('avatar 为空时画的是字母头像（不是空圆、也不是图片）',
    fallback.includes('acct-avatar--fallback') && !fallback.includes('<img'));
  check('字母头像带官网同款的渐变底（135deg 对角，和官网 canvas 同向）',
    fallback.includes('linear-gradient(135deg,hsl('), fallback.slice(0, 160));
  check('字母头像的取色与官网 initialAvatar() 逐字同源（Lihui / 清秋 / 星野 / 邮箱）',
    ['Lihui', '清秋', '星野', 'li@astroswarm.cn'].every((n) => stopsOf(avatarGradient(n)) === siteStops(n)),
    avatarGradient('Lihui'));
  check('老板那个账号（Lihui）算出来就是蓝 → 蓝紫，和官网对得上',
    avatarHue('Lihui') === 207
    && avatarGradient('Lihui').includes('hsl(207,60%,52%)')
    && avatarGradient('Lihui').includes('hsl(249,56%,32%)'),
    avatarGradient('Lihui'));
  const avatarCss = readFileSync(join(__dirname, '..', 'src', 'styles.css'), 'utf8');
  check('字母头像的圆角与白字照官网写死（官网 .navav / .me__av 就是 50% + 白字）',
    /\.acct-avatar\s*\{[^}]*border-radius:\s*50%/.test(avatarCss)
    && /\.acct-avatar--fallback\s*\{[^}]*color:\s*#fff/.test(avatarCss));
  check('昵称显示出来', block.includes('清秋'));
  check('邮箱小字显示出来', block.includes('li@astroswarm.cn'));
  check('签名显示出来', block.includes('做个能用的东西'));
  check('三个动作都在', ['改昵称', '换头像', '从官网同步'].every((t) => block.includes(t)));
  check('未登录时给的是人话（2026-10-06 老板定的引导句）', blockOut.includes('未登录')
    && blockOut.includes('登录后这里会显示官网的头像和昵称'));
  // 有 token 的那一份：头像真渲染成 <img>、昵称用 display_name、签名单独一行
  const loggedBlock = strip(renderToString(React.createElement(AccountProfileBlock, {
    loggedIn: true, email: 'liguangxu17@163.com', onMsg: () => {},
  })));
  check('有 token：头像 <img> + 昵称（display_name）+ 签名都渲染出来',
    loggedBlock.includes('<img') && loggedBlock.includes(AVATAR)
    && loggedBlock.includes('清秋') && loggedBlock.includes('做个能用的东西'));
  check('有 token：昵称位不再写死「—」，也不冒「先登录星群账号」那句',
    loggedBlock.includes('li@astroswarm.cn') && !loggedBlock.includes('—')
    && !loggedBlock.includes('先登录星群账号'));
  check('没 token：整块是引导（不摆头像、不写「—」、不报错）',
    blockOut.includes('登录后这里会显示官网的头像和昵称')
    && !blockOut.includes('<img') && !blockOut.includes('—') && !blockOut.includes('acct-card__err'));

  try {
    await fileToSquareJpeg({ type: 'text/plain', name: 'a.txt' });
    check('非图片文件必须被拦住', false);
  } catch (e) {
    check('非图片文件给人话提示', /不是图片/.test(String(e.message)), String(e.message));
  }

  /* ── 新群打招呼开关 + 插件主动发送白名单 ──────────────────────────────── */
  check('新群打招呼开关在（默认开）', botAi.includes('新群进来打个招呼（默认开）'));
  check('写了欢迎语是 AI 现说的', botAi.includes('欢迎语由 AI 按当前人设现说'));
  check('白名单的安全默认值写清楚了', botAi.includes('留空 = 插件一个都不许主动发消息'));
  check('白名单写法说明在', botAi.includes('group:群号') && botAi.includes('user:用户标识'));
  check('空名单时另有说明', botAi.includes('现在是空的：插件不会主动给任何人发消息'));
  check('「添加一行」按钮在', botAi.includes('添加一行'));

  // 直接调纯视图拿元素树，手动跑交互：添加 / 改值 / 删除
  const patches = [];
  const treeWithTargets = BotAiView({
    draft: { ...draft, allowed_send_targets: ['group:123', ''] },
    setDraft: (fn) => patches.push(fn),
    busy: false,
    saved: '',
    onSave: () => {},
    onReload: () => {},
    onOpenPack: () => {},
  });
  const walk = (node, visit) => {
    if (!node || typeof node !== 'object') return;
    visit(node);
    const kids = node.props && node.props.children;
    const list = Array.isArray(kids) ? kids : [kids];
    for (const child of list) walk(child, visit);
  };
  const findButtonByText = (node, needle) => {
    let hit = null;
    walk(node, (n) => {
      if (!hit && n.type === 'button'
        && JSON.stringify((n.props || {}).children || '').includes(needle)) hit = n;
    });
    return hit;
  };
  const findInputs = (node) => {
    const out = [];
    walk(node, (n) => {
      if (n.type === 'input' && n.props && String(n.props.className || '').includes('p-input')) out.push(n);
    });
    return out;
  };

  /* ── 账号卡：三个按钮打的是对的接口 + 读不到资料时的样子（2026-10-06 老板反馈：
        「已经登陆了但还是显示未登录，头像昵称也没同步」）─────────────────────
     真点击在浏览器里也验过（见验收记录）；这里用替身 fetch 把「点了会打哪个接口、
     请求体是什么」钉死：同步 → POST /api/account/refresh；改昵称 / 换头像 →
     POST /api/account/profile（同一条路，字段分别是 nickname / avatar）。 */
  const acctCalls = [];
  const acctMsgs = [];
  const fetchBackup = globalThis.fetch;        // 上面那个"能读到资料"的替身，最后要还回去
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = String(opts.method || 'GET').toUpperCase();
    const body = opts.body ? JSON.parse(String(opts.body)) : null;
    acctCalls.push({ url: u, method, body });
    if (u.includes('/api/account/profile')) {
      if (method === 'GET') {
        return { ok: false, status: 401, json: async () => ({ detail: '本机还没登录星群账号' }) };
      }
      const sent = body || {};
      return {
        ok: true,
        json: async () => ({
          email: 'liguangxu17@163.com',
          nickname: String(sent.nickname || 'Lihui'),
          signature: '',
          display_name: String(sent.nickname || 'Lihui'),
          plan: 'permanent',
          avatar: String(sent.avatar || ''),
        }),
      };
    }
    if (u.includes('/api/account/refresh')) {
      return {
        ok: true,
        json: async () => ({
          email: 'liguangxu17@163.com', nickname: 'Lihui', signature: '',
          display_name: 'Lihui', plan: 'permanent', avatar: AVATAR,
        }),
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  let acctTree = null;
  const GrabAcct = () => {
    acctTree = AccountProfileBlock({
      loggedIn: true, email: 'liguangxu17@163.com', onMsg: (m) => acctMsgs.push(m),
    });
    return null;
  };
  renderToString(React.createElement(GrabAcct));
  const nickBtn = findButtonByText(acctTree, '改昵称');
  const avatarBtn = findButtonByText(acctTree, '换头像');
  const syncBtn = findButtonByText(acctTree, '从官网同步');
  check('账号卡：改昵称 / 换头像 / 从官网同步 三个按钮都在',
    Boolean(nickBtn && avatarBtn && syncBtn));
  if (syncBtn) {
    syncBtn.props.onClick();
    await new Promise((r) => setTimeout(r, 20));
  }
  check('点「从官网同步」→ POST /api/account/refresh',
    acctCalls.some((c) => c.method === 'POST' && c.url.includes('/api/account/refresh')),
    JSON.stringify(acctCalls));
  check('同步成功说的是「已从官网同步」',
    acctMsgs.some((m) => String(m).includes('已从官网同步')), JSON.stringify(acctMsgs));

  await saveNickname('Lihui');
  const nickCall = acctCalls
    .filter((c) => c.method === 'POST' && c.url.includes('/api/account/profile')).pop();
  check('改昵称 → POST /api/account/profile，体是 {nickname}',
    Boolean(nickCall && nickCall.body && nickCall.body.nickname === 'Lihui'),
    JSON.stringify(nickCall));

  await sendAvatar(AVATAR);
  const avCall = acctCalls
    .filter((c) => c.method === 'POST' && c.url.includes('/api/account/profile')).pop();
  check('换头像 → POST /api/account/profile，体是 {avatar}（data URL）',
    Boolean(avCall && avCall.body && avCall.body.avatar === AVATAR),
    JSON.stringify(avCall && avCall.body));
  check('后端认下头像后就不再打老接口（/api/account/avatar 一次都不打）',
    !acctCalls.some((c) => c.url.includes('/api/account/avatar')),
    JSON.stringify(acctCalls.map((c) => c.url)));

  /* 后端还停在"这个接口只认 nickname"那一版：返回里没有头像 → 自动退回老接口，
     绝不让「换头像」变成点了没反应、界面还报"已换好"。 */
  const okMock = globalThis.fetch;
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = String(opts.method || 'GET').toUpperCase();
    acctCalls.push({ url: u, method, body: opts.body ? JSON.parse(String(opts.body)) : null });
    const withAvatar = u.includes('/api/account/avatar');
    return {
      ok: true,
      json: async () => ({
        email: 'liguangxu17@163.com', nickname: withAvatar ? 'Lihui' : '', signature: '',
        display_name: withAvatar ? 'Lihui' : 'liguangxu17', plan: 'permanent',
        avatar: withAvatar ? AVATAR : '',
      }),
    };
  };
  const legacy = await sendAvatar(AVATAR);
  check('老后端（只认 nickname）时：自动退回 /api/account/avatar，头像照样换成功',
    Boolean(legacy.ok) && acctCalls.some((c) => c.method === 'POST'
      && c.url.includes('/api/account/avatar') && c.body && c.body.avatar === AVATAR));
  globalThis.fetch = okMock;

  await loadProfile();                         // 上面的替身 fetch 对 GET 一律回 401
  const errBlock = strip(renderToString(React.createElement(AccountProfileBlock, {
    loggedIn: true, email: 'liguangxu17@163.com', onMsg: () => {},
  })));
  check('读不到资料时给中文原因，不再冒「先登录星群账号，再改昵称或头像。」',
    errBlock.includes('账号资料没读到')
    && !errBlock.includes('先登录星群账号，再改昵称或头像。')
    && !/HTTP \d+|Failed to fetch/.test(errBlock));
  check('读不到资料时用登录邮箱兜底（昵称位不是「—」），卡片结构还在',
    errBlock.includes('liguangxu17') && errBlock.includes('acct-card__name')
    && !errBlock.includes('>—<'));

  /* ── 401（token 已失效）必须清本地 token，否则同一页自相矛盾（2026-10-10 老板截图）──
     场景就是老板截图那种：localStorage 里还留着 token（顶栏芯片据此说「已登录 · permanent」），
     但服务端那份已经失效 —— 读资料接口回 401。
     修法：读资料这条路上任何 401 都通知 App 清 token，让芯片 / 侧栏 / 账号卡 /
     设置页那几行字**一起**回到「未登录」，并给一句「登录状态已过期，请重新登录」。 */
  check('401 认得出是「星群账号登录过期」，控制台密码那一层不算（两条路不能混）',
    isAccountExpired({ status: 401 }) === true
    && isAccountExpired(Object.assign(new Error('本机还没登录星群账号'), { status: 401 })) === true
    && isAccountExpired({ status: 401, consoleLocked: true }) === false
    && isAccountExpired({ status: 500 }) === false
    && isAccountExpired({ status: 400, message: '同步失败：可能未登录或网络不通' }) === false
    && isAccountExpired(null) === false);
  check('api.js 把状态码带在错上（调用方不用去猜错误文本）',
    await (async () => {
      globalThis.fetch = async () => ({ ok: false, status: 401, json: async () => ({ detail: '本机还没登录星群账号' }) });
      try {
        await get('/api/account/profile');
        return false;
      } catch (e) {
        return e.status === 401 && isAccountExpired(e) === true;
      }
    })());
  const expiredHits = [];
  setAccountExpiredHandler(() => expiredHits.push('expired'));
  localStorage.setItem('astroswarm_token', 'stale-token');
  globalThis.fetch = async () => ({ ok: false, status: 401, json: async () => ({ detail: '本机还没登录星群账号' }) });
  await loadProfile();                         // 进「设置 → 账号」那一次
  await loadProfile({ silent: true });         // 焦点回来 / 每 60 秒那一次（静默也必须清）
  setAccountExpiredHandler(null);
  check('读资料回 401 → 通知 App 清 token（前台与静默两条路都通知）',
    expiredHits.length === 2, JSON.stringify(expiredHits));
  check('控制台密码过期（console_locked）不误伤星群账号 token',
    await (async () => {
      const hits = [];
      setAccountExpiredHandler(() => hits.push('x'));
      globalThis.fetch = async () => ({
        ok: false, status: 401, json: async () => ({ detail: '需要控制台密码', console_locked: true }),
      });
      await loadProfile();
      setAccountExpiredHandler(null);
      return hits.length === 0;
    })());
  localStorage.removeItem('astroswarm_token');   // 别把上面那枚假 token 留给后面的用例

  globalThis.fetch = fetchBackup;              // 还回原来那个替身，后面的用例照旧
  await loadProfile();

  /* 「设置 → 账号」整页真渲染一遍（不只渲染那张卡）：App 把 loggedIn/email 传下来的
     那条线就在这儿，少传一个变量（例如在 Settings 里写了 App 才有的 token）
     整页会直接崩成「页面出错了」—— 老板看到的就是这种。 */
  const acctPage = strip(renderToString(React.createElement(Settings, {
    config: { port: 7860, bot_port: 12113, beginner_mode: false },
    services: null,
    onSaved: () => {},
    beginner: false,
    onBeginner: () => {},
    loggedIn: true,
    health: { ok: true, version: '0.2.10', machine_id: 'abc123456789' },
    auth: { email: 'li@astroswarm.cn', plan: 'permanent' },
    onChanged: async () => {},
    onToken: () => {},
    appearance: {},
    onAppearance: () => {},
    version: '0.2.10',
    tab: 'account',
    onTab: () => {},
  })));
  check('「设置 → 账号」整页能渲染（不再出现"页面出错了"）',
    acctPage.includes('acct-card') && acctPage.includes('acct-card__name')
    && !acctPage.includes('token is not defined'));
  check('「设置 → 账号」整页里昵称/头像是真资料（不是「—」、不是"先登录"）',
    acctPage.includes('class="acct-card__name">清秋</b>') && acctPage.includes(AVATAR)
    && acctPage.includes('li@astroswarm.cn') && !acctPage.includes('先登录星群账号'));

  /* 「同页两处说法必须一致」（老板 2026-10-10 的判别标准）：
     登录态文案**只认本地 token 这一个判据** —— 下面第一段就是老板截图那种情形：
     auth 里还留着上次的邮箱与套餐（服务端配置还在），但本地 token 已经没了，
     于是以前会出现"账号卡说未登录、另一行说已登录（permanent）"。 */
  const acctPaneOff = strip(renderToString(React.createElement(Settings, {
    config: { port: 7860, bot_port: 12113, beginner_mode: false },
    services: null,
    onSaved: () => {},
    beginner: false,
    onBeginner: () => {},
    loggedIn: false,
    health: { ok: true, version: '0.2.10', machine_id: 'abc123456789' },
    auth: { email: 'li@astroswarm.cn', plan: 'permanent' },
    onChanged: async () => {},
    onToken: () => {},
    appearance: {},
    onAppearance: () => {},
    version: '0.2.10',
    tab: 'account',
    onTab: () => {},
  })));
  check('登录态一致（未登录）：设置页里一处「已登录」都不出现（auth 里还留着旧邮箱也不行）',
    !acctPaneOff.includes('已登录'), acctPaneOff.slice(0, 300));
  check('登录态一致（未登录）：账号卡说「未登录」，「账号」那行也说未登录',
    acctPaneOff.includes('acct-card__name">未登录') && acctPaneOff.includes('未登录 · 功能不受影响'));
  check('登录态一致（已登录）：账号卡是真资料，「账号」那行不说未登录',
    acctPage.includes('acct-card__name">清秋') && !acctPage.includes('未登录 · 功能不受影响'));

  /* ── 控制台密码卡（2026-10-10 老板："再在设置里做个登录web密码设置"）────────
     SSR 不跑 useEffect，所以 here 验"卡片骨架确实进了设置页"；
     两个形态的具体控件用纯视图 ConsolePasswordView 直接喂数据渲染（下一段）。 */
  check('设置页里有「控制台密码」卡片',
    acctPage.includes('console-password-card') && acctPage.includes('控制台密码'));
  check('设置页里的控制台密码卡（还没问到状态时）不闪密码框、也不白屏',
    acctPage.includes('正在读取控制台密码状态') && !acctPage.includes('确认新密码'));

  // 已设过密码：当前密码 + 新密码 + 确认新密码 + 「改密码」+「锁定这台设备」
  const cpSet = strip(renderToString(React.createElement(ConsolePasswordView, {
    needSetup: false, authed: true,
    oldPw: '', newPw: '', newPw2: '', err: '', msg: '', busy: false,
  })));
  check('控制台密码卡（已设过）：三个框 + 改密码 + 锁定这台设备都在',
    cpSet.includes('修改控制台密码') && cpSet.includes('当前密码')
    && cpSet.includes('新密码') && cpSet.includes('确认新密码')
    && cpSet.includes('改密码') && cpSet.includes('锁定这台设备'));
  check('控制台密码卡（已设过）：三个密码框都是 type="password"，一个明文框都没有',
    (cpSet.match(/type="password"/g) || []).length === 3
    && !cpSet.includes('type="text"'));
  check('控制台密码卡：小字写明「忘记密码删哪个文件」（console_auth.json，不是会话文件）',
    cpSet.includes('console_auth.json') && cpSet.includes('console_sessions.json')
    && cpSet.includes('设置控制台密码'));
  check('控制台密码卡：写明锁定只退这台设备、星群账号不退',
    cpSet.includes('星群账号不会退出'));

  // 没设过密码（need_setup）：只有两次输入，没有「当前密码」
  const cpNew = strip(renderToString(React.createElement(ConsolePasswordView, {
    needSetup: true, authed: false,
    oldPw: '', newPw: '', newPw2: '', err: '', msg: '', busy: false,
  })));
  check('控制台密码卡（首次部署）：显示「设置控制台密码」两次输入、且不问当前密码',
    cpNew.includes('设置控制台密码') && cpNew.includes('确认新密码')
    && !cpNew.includes('当前密码')
    && (cpNew.match(/type="password"/g) || []).length === 2);
  check('控制台密码卡：强度提示与「至少 8 位」占位符在',
    cpNew.includes('至少 8 位'));
  check('控制台密码卡：后端给的中文原因照实显示（不翻译成英文、不吞掉）',
    strip(renderToString(React.createElement(ConsolePasswordView, {
      needSetup: false, authed: true, err: '当前密码不对',
    }))).includes('当前密码不对'));
  check('控制台密码卡：成功提示是「密码已改，其它设备需要重新输入」',
    strip(renderToString(React.createElement(ConsolePasswordView, {
      needSetup: false, authed: true, msg: '密码已改，其它设备需要重新输入',
    }))).includes('密码已改，其它设备需要重新输入'));

  // 本地校验（纯函数）：每条规则返回中文原因，通过返回空串
  const cpv = (o) => consolePasswordCheck({ needSetup: false, oldPw: '', newPw: '', newPw2: '', ...o });
  check('改密码校验：没填新密码 → 「请先填新密码」', cpv({}) === '请先填新密码', cpv({}));
  check('改密码校验：新密码不足 8 位 → 「密码至少 8 位」',
    cpv({ oldPw: 'oldpass1', newPw: '1234567', newPw2: '1234567' }) === '密码至少 8 位');
  check('改密码校验：两次不一致 → 「两次输入的新密码不一样」',
    cpv({ oldPw: 'oldpass1', newPw: 'newpass12', newPw2: 'newpass13' }) === '两次输入的新密码不一样');
  check('改密码校验：没填当前密码 → 「请先填当前密码」',
    cpv({ newPw: 'newpass12', newPw2: 'newpass12' }) === '请先填当前密码');
  check('改密码校验：都合规 → 放行（空串），把"弱密码"判给后端',
    cpv({ oldPw: 'oldpass1', newPw: '12345678', newPw2: '12345678' }) === '',
    '12345678 长度够就放行，弱不弱由后端说了算');
  check('首次部署校验：不问当前密码，两次一致 + 8 位以上就放行',
    consolePasswordCheck({ needSetup: true, oldPw: '', newPw: 'newpass12', newPw2: 'newpass12' }) === '');
  check('网络类错误翻成人话（不把 Failed to fetch 甩给用户）',
    consolePasswordErrText(new Error('Failed to fetch')).includes('连不上服务器')
    && consolePasswordErrText(new Error('当前密码不对')) === '当前密码不对'
    && consolePasswordErrText(new Error('尝试过多，请稍后再试')).includes('等 1 分钟'));

  /* ── 长图裁剪成 1:1（老板 2026-10-06：「没有 1:1 剪切功能」；
        2026-10-07 他否掉了"图片铺满取景框、拖着图片跑"那一版，改成
        **照片整张完整显示 + 在照片上拖方框**）────────────────────────────────
     用一张 600×1300 的长图把这条链钉死：
       照片 contain 完整显示 → 初始方框（短边 80%、居中、正方形）→ 拖框 / 拖角后的方框
       → 源矩形恒为正方形且不越界 → canvas 512×512 → 导出的 data URL 宽高相等；
     另外验「拖角变大后导出那块跟着变大」「选完不直接上传」「点取消一个请求都不发」。 */
  const LONG = { w: 600, h: 1300 };          // 6:13 的长图（600×1300）
  const box0 = initialCropBox(LONG.w, LONG.h);
  check('长图初始方框是正方形：600×1300 → 短边 80% = 480，居中放在 (60,410)',
    box0.side === 480 && box0.x === 60 && box0.y === 410, JSON.stringify(box0));
  const g1 = cropGeometry(LONG.w, LONG.h, box0);
  check('初始方框对应的源矩形也是正方形，且整块落在照片里',
    g1.sw === g1.sh && g1.sw === 480 && g1.sx === 60 && g1.sy === 410
    && g1.sx >= 0 && g1.sy >= 0 && g1.sx + g1.sw <= LONG.w && g1.sy + g1.sh <= LONG.h,
    JSON.stringify(g1));
  /* 照片是**整张**显示出来的（contain）：宽高同比例、两边都在 80vw × 70vh 里 —— 长图不裁边 */
  const fit = containSize(LONG.w, LONG.h, 1024, 560);
  check('照片按 contain 完整显示：600×1300 缩成一条 258×560 的完整竖图，两边都在上限里',
    Math.abs(fit.w / fit.h - LONG.w / LONG.h) < 0.01 && fit.w <= 1024 && fit.h <= 560
    && fit.h === 560 && fit.w < 1024, JSON.stringify(fit));
  const SHRINK = { se: [-9999, -9999], nw: [9999, 9999], ne: [-9999, 9999], sw: [9999, -9999] };
  const GROW = { se: [9999, 9999], nw: [-9999, -9999], ne: [9999, -9999], sw: [-9999, 9999] };
  check('拖动 / 四角缩放到哪一档，源矩形都严格正方形、且整块落在照片里',
    [
      moveCropBox(box0, -99999, -99999, LONG.w, LONG.h),
      moveCropBox(box0, 99999, 99999, LONG.w, LONG.h),
      resizeCropBox(box0, 'se', 120, 120, LONG.w, LONG.h, 40),
      resizeCropBox(box0, 'nw', -120, -120, LONG.w, LONG.h, 40),
      resizeCropBox(box0, 'ne', 60, -60, LONG.w, LONG.h, 40),
      resizeCropBox(box0, 'sw', -60, 60, LONG.w, LONG.h, 40),
    ].every((b) => {
      const g = cropGeometry(LONG.w, LONG.h, b);
      return g.sw === g.sh && g.sw > 0
        && g.sx >= -1e-6 && g.sy >= -1e-6
        && g.sx + g.sw <= LONG.w + 1e-6 && g.sy + g.sh <= LONG.h + 1e-6;
    }));
  const grew = resizeCropBox(box0, 'se', 60, 60, LONG.w, LONG.h, 40);
  const grewG = cropGeometry(LONG.w, LONG.h, grew);
  const shrunk = resizeCropBox(box0, 'se', -60, -60, LONG.w, LONG.h, 40);
  check('拖右下角往外拉 → 方框等比变大、导出的那块跟着变大；往里收 → 变小（全程 1:1）',
    grew.side > box0.side && grewG.sw === grew.side && grewG.sw > g1.sw
    && shrunk.side < box0.side && shrunk.x === box0.x && shrunk.y === box0.y,
    `${box0.side} → ${grew.side} / ${shrunk.side}`);
  check('四个角都能拖：往小拖有 40px 下限、往大拖不会顶出照片，全程还是正方形',
    CROP_CORNERS.join(',') === 'nw,ne,sw,se'
    && CROP_CORNERS.every((c) => {
      const small = resizeCropBox(box0, c, SHRINK[c][0], SHRINK[c][1], LONG.w, LONG.h, 40);
      const big = resizeCropBox(box0, c, GROW[c][0], GROW[c][1], LONG.w, LONG.h, 40);
      const gs = cropGeometry(LONG.w, LONG.h, small);
      const gb = cropGeometry(LONG.w, LONG.h, big);
      return Math.abs(small.side - 40) < 1e-6 && big.side <= Math.min(LONG.w, LONG.h)
        && gs.sw === gs.sh && gb.sw === gb.sh && gs.sw <= small.side + 1 && gb.sw <= big.side + 1
        && gs.sx >= 0 && gs.sy >= 0 && gs.sx + gs.sw <= LONG.w && gs.sy + gs.sh <= LONG.h
        && gb.sx >= 0 && gb.sy >= 0 && gb.sx + gb.sw <= LONG.w && gb.sy + gb.sh <= LONG.h;
    }));
  const gTop = cropGeometry(LONG.w, LONG.h, moveCropBox(box0, -99999, -99999, LONG.w, LONG.h));
  const gBottom = cropGeometry(LONG.w, LONG.h, moveCropBox(box0, 99999, 99999, LONG.w, LONG.h));
  check('方框拖过头会夹在照片边界上（左上角 / 右下角各贴一次，不会跑出照片）',
    gTop.sw === gTop.sh && gTop.sx === 0 && gTop.sy === 0
    && gBottom.sw === gBottom.sh && gBottom.sx + gBottom.sw === LONG.w
    && gBottom.sy + gBottom.sh === LONG.h,
    `${JSON.stringify(gTop)} / ${JSON.stringify(gBottom)}`);
  check('窄图（1300×600）同样只取正方形，不会被拉伸',
    (() => {
      const b = initialCropBox(1300, 600);
      const g = cropGeometry(1300, 600, b);
      return b.side === 480 && g.sw === g.sh && g.sw === 480;
    })());

  /* 替身三件套（canvas / Image / FileReader）：Node 里没有真画布，
     但「代码到底让画布画了多大、源矩形取的是哪一块」必须能验。
     toDataURL 交回一张带真实宽高的最小 JPEG，于是"导出的 data URL 解析出来是不是正方形"
     也能真解析一遍，而不是只看 canvas 的属性。 */
  const prevDoc = globalThis.document;
  const prevImage = globalThis.Image;
  const prevReader = globalThis.FileReader;
  const canvases = [];
  const drawn = [];
  const fakeJpeg = (w, h) => {
    const app0 = [0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x00,
      0x00, 0x01, 0x00, 0x01, 0x00, 0x00];
    const sof0 = [0xFF, 0xC0, 0x00, 0x11, 0x08, (h >> 8) & 255, h & 255,
      (w >> 8) & 255, w & 255, 0x03, 0x01, 0x11, 0x00, 0x02, 0x11, 0x01, 0x03, 0x11, 0x01];
    const bytes = [0xFF, 0xD8, ...app0, ...sof0, 0xFF, 0xD9];
    return 'data:image/jpeg;base64,' + Buffer.from(bytes).toString('base64');
  };
  const jpegSize = (url) => {
    const b = Buffer.from(String(url).split(',')[1] || '', 'base64');
    for (let i = 0; i + 8 < b.length; i += 1) {
      if (b[i] === 0xFF && b[i + 1] === 0xC0) {
        return { h: (b[i + 5] << 8) | b[i + 6], w: (b[i + 7] << 8) | b[i + 8] };
      }
    }
    return null;
  };
  globalThis.document = {
    createElement(tag) {
      if (tag !== 'canvas') return {};
      const ctx = {
        fillStyle: '', imageSmoothingEnabled: false, imageSmoothingQuality: '',
        fillRect: () => {},
        drawImage: (_img, sx, sy, sw, sh, dx, dy, dw, dh) => drawn.push({ sx, sy, sw, sh, dx, dy, dw, dh }),
      };
      const cv = {
        width: 0,
        height: 0,
        getContext: () => ctx,
        toDataURL(type, quality) {
          cv.type = type;
          cv.quality = quality;
          return fakeJpeg(cv.width, cv.height);
        },
      };
      canvases.push(cv);
      return cv;
    },
  };
  globalThis.Image = class {
    set src(_v) { setTimeout(() => { this.onload && this.onload(); }, 0); }
    get src() { return ''; }
  };
  Object.defineProperty(globalThis.Image.prototype, 'naturalWidth', { get: () => LONG.w });
  Object.defineProperty(globalThis.Image.prototype, 'naturalHeight', { get: () => LONG.h });
  globalThis.FileReader = class {
    readAsDataURL() { setTimeout(() => { this.result = 'data:image/jpeg;base64,AAAA'; this.onload && this.onload(); }, 0); }
  };

  const longFile = { type: 'image/jpeg', name: 'long-6x13.jpg', size: 260000 };
  const cropped = await fileToCroppedJpeg(longFile, cropGeometry(LONG.w, LONG.h, box0));
  const cv = canvases[canvases.length - 1];
  check('导出用的 canvas 宽高同值（512×512，1:1 在本地就裁好了，不靠服务端）',
    Boolean(cv) && cv.width === cv.height && cv.width === CROP_SIZE,
    cv ? `${cv.width}×${cv.height}` : '没有画布');
  const lastDraw = drawn[drawn.length - 1];
  check('drawImage 的源矩形是正方形，取的就是方框那一块（不是把整张压扁）',
    Boolean(lastDraw) && lastDraw.sw === lastDraw.sh && lastDraw.sw === 480
    && lastDraw.sx === 60 && lastDraw.sy === 410 && lastDraw.dw === CROP_SIZE && lastDraw.dh === CROP_SIZE,
    JSON.stringify(lastDraw));
  check('导出的 data URL 解析出来宽高相等（512×512）',
    (() => { const s = jpegSize(cropped); return Boolean(s) && s.w === 512 && s.h === 512; })(),
    JSON.stringify(jpegSize(cropped)));
  check('导出走 JPEG + 0.9 质量（体积稳在 512KB 以内）',
    Boolean(cv) && cv.type === 'image/jpeg' && cv.quality === CROP_QUALITY,
    cv ? `${cv.type} / ${cv.quality}` : '没有画布');
  /* 把方框拖大再导一次：画的源矩形要跟着变大，但输出仍然钉死在 512×512。 */
  const croppedBig = await fileToCroppedJpeg(longFile, grewG);
  const cvBig = canvases[canvases.length - 1];
  const drawBig = drawn[drawn.length - 1];
  check('方框拖大 → 导出那一块跟着变大（480 → 540），但输出仍是 512×512 的正方形',
    Boolean(drawBig) && drawBig.sw > lastDraw.sw && drawBig.sw === drawBig.sh && drawBig.sw === 540
    && cvBig.width === CROP_SIZE && cvBig.height === CROP_SIZE
    && (() => { const s = jpegSize(croppedBig); return Boolean(s) && s.w === s.h && s.w === 512; })(),
    JSON.stringify(drawBig));

  /* 裁剪框本身：照片整张 contain 显示、方框画在照片上、框外压暗；提示是一句人话。 */
  let cropTree = null;
  let cropCancelled = 0;
  const cropMsgs = [];
  const GrabCrop = () => {
    cropTree = AvatarCrop({
      file: longFile,
      busy: false,
      onCancel: () => { cropCancelled += 1; },
      onConfirm: (u) => cropMsgs.push(u),
    });
    return null;
  };
  const cropHtml = strip(renderToString(React.createElement(AvatarCrop, {
    file: longFile, busy: false, onCancel: () => {}, onConfirm: () => {},
  })));
  renderToString(React.createElement(GrabCrop));      // 同一份 props 拿元素树，好点按钮
  check('裁剪框：照片是 contain 完整显示（最多 80vw × 70vh），方框画在照片上',
    cropHtml.includes('cropmodal__stage') && cropHtml.includes('cropmodal__photo')
    && cropHtml.includes('cropmodal__img') && cropHtml.includes('max-width:80vw')
    && cropHtml.includes('max-height:70vh'));
  check('裁剪框：一句人话提示 + 重置 / 取消 / 确定 三个按钮',
    cropHtml.includes('拖动方框选择要保留的部分，拖四角可以放大缩小')
    && Boolean(findButtonByText(cropTree, '重置')) && Boolean(findButtonByText(cropTree, '取消'))
    && Boolean(findButtonByText(cropTree, '确定')));
  check('裁剪框：四个角各有一个拖拽手柄（手柄 = 等比缩放的入口）',
    CROP_CORNERS.join(',') === 'nw,ne,sw,se' && CROP_CORNERS.length === 4
    && cropHtml.includes('cropmodal__hint'));
  /* 方框 / 压暗 / 手柄是图片读出来之后才画的（服务端渲染看不到），
     这两条直接查源码：样式在，拖拽走原生 pointer 事件（鼠标 + 触屏同一套）。 */
  const cropCss = readFileSync(join(__dirname, '..', 'src', 'styles.css'), 'utf8');
  const cropSrc = readFileSync(join(__dirname, '..', 'src', 'AvatarCrop.jsx'), 'utf8');
  check('裁剪框：框外压暗 + 细白线方框 + 四角圆点手柄（样式都在），触屏不被页面滚动抢走',
    cropCss.includes('.cropmodal__dim') && cropCss.includes('.cropmodal__rect')
    && cropCss.includes('.cropmodal__dot') && cropCss.includes('touch-action: none'));
  check('裁剪框：拖框 / 拖角走原生 pointer 事件（鼠标 + 触屏同一套）',
    cropSrc.includes('onPointerDown') && cropSrc.includes('onPointerMove')
    && cropSrc.includes('onPointerUp') && cropSrc.includes('setPointerCapture'));

  /* 「取消」= 一个请求都不发。 */
  const cropFetch = globalThis.fetch;
  const sentWhileCropping = [];
  globalThis.fetch = async (u, o = {}) => {
    sentWhileCropping.push({ url: String(u), method: String(o.method || 'GET') });
    return { ok: true, json: async () => ({}) };
  };
  const cropCancelBtn = findButtonByText(cropTree, '取消');
  if (cropCancelBtn) cropCancelBtn.props.onClick();
  check('点「取消」→ 裁剪框关掉、一个请求都不发',
    cropCancelled === 1 && sentWhileCropping.length === 0 && cropMsgs.length === 0,
    `cancel=${cropCancelled} 请求=${JSON.stringify(sentWhileCropping)}`);

  /* 账号卡：给「换头像」的 file input 喂一张 6:13 的长图 —— 只是打开裁剪框，
     绝不再"选完就直传"。 */
  let pickTree = null;
  const GrabPick = () => {
    pickTree = AccountProfileBlock({
      loggedIn: true, email: 'liguangxu17@163.com', onMsg: () => {},
    });
    return null;
  };
  renderToString(React.createElement(GrabPick));
  let fileInput = null;
  walk(pickTree, (n) => {
    if (!fileInput && n.type === 'input' && n.props && n.props.type === 'file') fileInput = n;
  });
  check('账号卡里能找到「换头像」的文件选择框', Boolean(fileInput));
  if (fileInput) fileInput.props.onChange({ target: { files: [longFile] } });
  check('长图选完先开裁剪框，不会直接上传（请求数还是 0）',
    sentWhileCropping.length === 0, JSON.stringify(sentWhileCropping));
  globalThis.fetch = cropFetch;

  globalThis.document = prevDoc;
  globalThis.Image = prevImage;
  globalThis.FileReader = prevReader;


  const addBtn = findButtonByText(treeWithTargets, '添加一行');
  check('找到「添加一行」按钮', Boolean(addBtn));
  if (addBtn) {
    addBtn.props.onClick();
    const next = patches[patches.length - 1]({ allowed_send_targets: ['group:123', ''] });
    check('添加一行=在末尾加空行', next.allowed_send_targets.length === 3
      && next.allowed_send_targets[2] === '', JSON.stringify(next.allowed_send_targets));
  }
  const delBtn = findButtonByText(treeWithTargets, '删除');
  check('找到「删除」按钮', Boolean(delBtn));
  if (delBtn) {
    delBtn.props.onClick();
    const next = patches[patches.length - 1]({ allowed_send_targets: ['group:123'] });
    check('删除=把这行去掉', next.allowed_send_targets.length === 1,
      JSON.stringify(next.allowed_send_targets));
  }
  const targetInput = findInputs(treeWithTargets)
    .find((n) => String(n.props.placeholder || '').includes('group:'));
  check('白名单输入框在（带写法提示）', Boolean(targetInput));
  if (targetInput) {
    targetInput.props.onChange({ target: { value: 'user:abc' } });
    const next = patches[patches.length - 1]({ allowed_send_targets: ['group:123', ''] });
    check('改一行的值只改这一行', next.allowed_send_targets[0] === 'user:abc'
      && next.allowed_send_targets[1] === '', JSON.stringify(next.allowed_send_targets));
  }

  const norm = normalizeSendTargets(['group:123', ' user:abc ', 'group：456', '', '  ',
    'telegram:9', 'group:123']);
  check('白名单规范化：合法值留下并去重',
    JSON.stringify(norm.targets) === JSON.stringify(['group:123', 'user:abc', 'group:456']),
    JSON.stringify(norm.targets));
  check('白名单规范化：看不清的挑出来告诉用户',
    JSON.stringify(norm.dropped) === JSON.stringify(['telegram:9']), JSON.stringify(norm.dropped));

  /* ── 免@连续对话：三种模式说明 + 「去能力包配置」按钮真的能触发跳转 ──────────
     BotAiView 是纯视图（内部不用 hook），所以可以直接当函数调用拿到元素树，
     再找到那个按钮把 onClick 手动跑一遍 —— "点了会不会跳"是真验证过的。 */
  check('三种模式的人话说明都在', ['说一会儿自动停', '每条消息都让 AI 看一眼', '群里只有 @ 它才回']
    .every((t) => botAi.includes(t)));
  check('「去能力包配置」按钮在', botAi.includes('去「连续对话能力包」配置'));

  const opened = [];
  const tree = BotAiView({
    draft,
    setDraft: () => {},
    busy: false,
    saved: '',
    onSave: () => {},
    onReload: () => {},
    onOpenPack: (pid) => opened.push(pid),
  });
  const findButton = (node, needle) => {
    if (!node || typeof node !== 'object') return null;
    const kids = node.props && node.props.children;
    if (node.type === 'button' && JSON.stringify(kids || '').includes(needle)) return node;
    const list = Array.isArray(kids) ? kids : [kids];
    for (const child of list) {
      const hit = findButton(child, needle);
      if (hit) return hit;
    }
    return null;
  };
  const packBtn = findButton(tree, '连续对话能力包');
  check('找到「去能力包配置」按钮', Boolean(packBtn));
  if (packBtn) {
    packBtn.props.onClick();
    check('点它会跳到 session-chat 的配置', opened.length === 1 && opened[0] === 'session-chat',
      JSON.stringify(opened));
  }

  /* ── 三步开箱：状态判断（纯函数）+ 视图渲染 + 按钮回调 ────────────────── */
  const s0 = setupSteps({ aiModel: '', qqReady: false, botRunning: false });
  check('三步标题与顺序', s0.map((s) => s.title).join('>') === '连 AI>接 QQ>启动机器人');
  check('什么都没配时：第 1 步是「现在做这个」，其余待办',
    s0[0].status === '现在做这个' && s0[1].status === '待办' && s0[2].status === '待办'
    && s0[0].tone === 'current' && s0[1].tone === 'todo',
    JSON.stringify(s0.map((s) => [s.status, s.tone])));

  const s1 = setupSteps({ aiModel: 'deepseek-chat', qqReady: false, botRunning: false });
  check('填了模型名：第 1 步变「✓ 已配好」，第 2 步变当前',
    s1[0].status === '✓ 已配好' && s1[0].tone === 'done'
    && s1[1].status === '现在做这个' && s1[1].tone === 'current'
    && s1[2].status === '待办', JSON.stringify(s1.map((s) => [s.status, s.tone])));

  const s2 = setupSteps({ aiModel: 'deepseek-chat', qqReady: true, botRunning: false });
  check('QQ 有凭证（或已连上）：前两步都算完成，第 3 步是当前',
    s2[0].tone === 'done' && s2[1].tone === 'done'
    && s2[2].status === '现在做这个' && s2[2].tone === 'current',
    JSON.stringify(s2.map((s) => [s.status, s.tone])));

  const s3 = setupSteps({ aiModel: 'deepseek-chat', qqReady: true, botRunning: true });
  check('机器人也在跑：三步全部「✓ 已配好」',
    s3.every((s) => s.status === '✓ 已配好' && s.tone === 'done'),
    JSON.stringify(s3.map((s) => s.status)));

  const stepHtml = strip(renderToString(React.createElement(SetupStepsView, {
    steps: s1, busy: false, onAction: () => {},
  })));
  check('三步视图渲染出来了', ['1', '2', '3', '连 AI', '接 QQ', '启动机器人'].every((t) => stepHtml.includes(t)));
  check('每步的按钮文案在', ['去填 Key', '去接入', '启动'].every((t) => stepHtml.includes(t)));
  check('状态文字在', stepHtml.includes('✓ 已配好') && stepHtml.includes('现在做这个') && stepHtml.includes('待办'));

  const acted = [];
  const stepsTree = SetupStepsView({ steps: s1, busy: false, onAction: (id) => acted.push(id) });
  const qqBtn = findButtonByText(stepsTree, '去接入');
  check('找到第 2 步的「去接入」按钮', Boolean(qqBtn));
  if (qqBtn) {
    qqBtn.props.onClick();
    check('点「去接入」→ 回调收到 qq', JSON.stringify(acted) === JSON.stringify(['qq']), JSON.stringify(acted));
  }
  const startBtn = findButtonByText(stepsTree, '启动');
  check('找到第 3 步的「启动」按钮', Boolean(startBtn));

  /* ── 插件工坊卡片 + 「那句话带过去」的共享状态 ─────────────────────────── */
  setIdea('每天早八报一次天气');
  check('那句话能存能读（概览 → 插件页）', getIdea() === '每天早八报一次天气', getIdea());

  const wsBase = {
    need: '', setNeed: () => {}, jobId: '', state: '', logs: [], stage: '', elapsed: 0,
    result: null, error: '', starting: false, cancelling: false, installing: false,
    showLogs: false, onToggleLogs: () => {},
    onGenerate: () => {}, onCancel: () => {}, onInstall: () => {},
  };
  const wsEmpty = strip(renderToString(React.createElement(PluginWorkshopView, wsBase)));
  check('工坊输入框占位文字对', wsEmpty.includes('你想要什么功能？一句话，我让 AI 给你做一个插件'));
  check('「去做插件」按钮在', wsEmpty.includes('去做插件'));

  // 生成中：stage + 已用时间 + 可折叠过程 + 取消按钮（取消的说明必须照实说"不会立刻断"）
  const wsRunning = strip(renderToString(React.createElement(PluginWorkshopView, {
    ...wsBase,
    need: '每天早八报一次天气',
    jobId: 'job-1',
    state: 'running',
    logs: ['正在理解需求…', '正在写 manifest.json', '正在做静态检查'],
    stage: '正在做静态检查',
    elapsed: 42.5,
  })));
  check('生成中：显示当前在做什么与已用时间',
    wsRunning.includes('正在做：正在做静态检查') && wsRunning.includes('已用 43 秒'));
  check('生成中：过程可折叠并且报行数', wsRunning.includes('看过程（3 行）'));
  check('生成中：有取消按钮', wsRunning.includes('取消'));
  check('生成中：取消的说明照实说（不写"已立即停止"）',
    wsRunning.includes('会在下个检查点停下'));
  check('生成中：说了刷新还能接着看',
    wsRunning.includes('刷新了也能接着看'), );

  const wsDone = strip(renderToString(React.createElement(PluginWorkshopView, {
    ...wsBase,
    need: '每天早八报一次天气',
    jobId: 'job-1',
    state: 'done',
    logs: ['好了'],
    result: { build_id: 'b1', name: '天气早报', description: '每天早上八点播报天气', files: ['manifest.json', 'tools/weather.py'] },
  })));
  check('生成结果渲染出来了', wsDone.includes('天气早报') && wsDone.includes('每天早上八点播报天气'));
  check('文件清单与「装上这个插件」在', wsDone.includes('tools/weather.py') && wsDone.includes('装上这个插件'));
  check('带过来的那句话填进了工坊输入框', wsDone.includes('每天早八报一次天气'));

  const wsErr = strip(renderToString(React.createElement(PluginWorkshopView, {
    ...wsBase, jobId: 'job-2', state: 'error', error: '模型没配好：请先在工坊里填 API Key',
  })));
  check('失败时把人话原因显示出来',
    wsErr.includes('生成失败：模型没配好：请先在工坊里填 API Key'));

  const genCalls = [];
  const cancelCalls = [];
  const wsTree = PluginWorkshopView({
    ...wsBase,
    need: 'x',
    jobId: '',
    state: '',
    logs: ['一', '二'],
    onGenerate: () => genCalls.push(1),
    onCancel: () => cancelCalls.push(1),
  });
  const genBtn = findButtonByText(wsTree, '去做插件');
  check('找到「去做插件」按钮', Boolean(genBtn));
  if (genBtn) {
    genBtn.props.onClick();
    check('点它会触发生成', genCalls.length === 1);
  }

  // 生成中那张树：按钮变成「正在生成…」并且有「取消」
  const wsRunningTree = PluginWorkshopView({
    ...wsBase,
    need: 'x',
    jobId: 'job-3',
    state: 'running',
    logs: ['一', '二'],
    onGenerate: () => genCalls.push(1),
    onCancel: () => cancelCalls.push(1),
  });
  const cancelBtn = findButtonByText(wsRunningTree, '取消');
  check('找到「取消」按钮', Boolean(cancelBtn));
  if (cancelBtn) {
    cancelBtn.props.onClick();
    check('点它会真的发出取消', cancelCalls.length === 1, JSON.stringify(cancelCalls));
  }
  const logBtn = findButtonByText(wsRunningTree, '看过程');
  check('找到「看过程」按钮', Boolean(logBtn));

  /* ── 一、导航合并成 4 组（桌面端与控制台必须一一对应）────────────────────
     分组与顺序以桌面端 main_window.NAV_GROUPS 为准：
       ("机器人", ("首页", "接入")),
       ("工坊", ("插件", "插件工坊")),
       ("人格", ("AI 大脑",)),
       ("设置", ("全局管理", "消息中心", "日志", "依赖", "设置")),
     共 4 组 10 项；通道详情页（微信/QQ/飞书/纸飞机）不单独占行，收在「接入」里。
     ⚠ 这组断言曾经写的是更早的一版分组，和两边代码都对不上；这里按桌面端对齐。 */
  const navShape = NAV_GROUPS.map((g) => [g.title, g.items.map((i) => i[0])]);
  check('导航是 4 组、顺序与名字都对',
    JSON.stringify(navShape.map((n) => n[0])) === JSON.stringify(['机器人', '工坊', '人格', '设置']),
    JSON.stringify(navShape.map((n) => n[0])));
  check('机器人组：首页 / 接入（通道详情页收在接入里）',
    JSON.stringify(navShape[0][1]) === JSON.stringify(['home', 'access']),
    JSON.stringify(navShape[0][1]));
  check('工坊组：插件 + 插件工坊（顺序与桌面端一致）',
    JSON.stringify(navShape[1][1]) === JSON.stringify(['plugins', 'workshop']),
    JSON.stringify(navShape[1][1]));
  check('人格组：只有 AI 大脑',
    JSON.stringify(navShape[2][1]) === JSON.stringify(['brain']), JSON.stringify(navShape[2][1]));
  check('设置组：全局管理 / 消息中心 / 日志 / 依赖 / 设置',
    JSON.stringify(navShape[3][1]) === JSON.stringify(['services', 'messages', 'logs', 'deps', 'settings']),
    JSON.stringify(navShape[3][1]));
  const navIds = NAV_GROUPS.flatMap((g) => g.items.map((i) => i[0]));
  check('原来 9 个页面一个都没少（合并只是收进组里）',
    ['home', 'access', 'messages', 'logs', 'deps', 'plugins', 'brain', 'services', 'settings']
      .every((id) => navIds.includes(id)),
    JSON.stringify(navIds));
  check('新增的「插件工坊」也在导航里',
    navIds.includes('workshop') && navIds.length === 10, JSON.stringify(navIds));
  check('小白模式名单 = 桌面端 ADVANCED_PAGES 的子集（消息中心/插件/日志/依赖）',
    ['messages', 'plugins', 'logs', 'deps'].every((id) => ADVANCED_PAGES.includes(id)),
    JSON.stringify(ADVANCED_PAGES));
  check('「插件工坊」不在小白模式的隐藏名单里（老板当场找不到过一次，永远留着）',
    !ADVANCED_PAGES.includes('workshop'), JSON.stringify(ADVANCED_PAGES));

  /* ── 二、工坊页（控制台）：模型配置 / 已装插件的动作 / 投稿 / 导入 ───────── */
  const wsProps = {
    status: {
      ready: true,
      why: '',
      provider: 'deepseek',
      provider_name: 'DeepSeek',
      model: 'deepseek-chat',
      api_key_masked: '****1234',
      hint: 'DeepSeek / ChatGPT / Claude / 智谱 / 通义 / Kimi',
      providers: [{ key: 'deepseek', name: 'DeepSeek', url: 'https://api.deepseek.com', model: 'deepseek-chat' }],
      installed: [
        { id: 'weather_pack', name: '天气早报', version: '1.0.0', ai_generated: true },
        { id: 'session-chat', name: '连续对话能力包', version: '1.0.0', ai_generated: false },
      ],
    },
    cfg: { provider: 'deepseek', api_url: 'https://api.deepseek.com', model: 'deepseek-chat', api_key: '' },
    setCfg: () => {},
    cfgBusy: false,
    busyId: '',
    historyId: 'weather_pack',
    history: [{ stamp: '20261002-101500', dir: '/x' }],
    repairResult: null,
    importing: false,
    uploadId: 'weather_pack',
    setUploadId: () => {},
    uploadForm: { username: '清秋', note: '每天早八报天气', code: '' },
    setUploadForm: () => {},
    uploadHint: '验证码已发到 li***@qq.com，5 分钟内有效',
    fileRef: { current: null },
    onMsg: () => {},
    onGoPlugins: () => {},
    onSaveConfig: () => {},
    onRepair: () => {},
    onHistory: () => {},
    onRollback: () => {},
    onUninstall: () => {},
    onExport: () => {},
    onImport: () => {},
    onSendCode: () => {},
    onSubmitUpload: () => {},
    onInstallBuilt: () => {},
    onReloadPacks: () => {},
    onUpload: () => {},
  };
  wsProps.onDownload = () => {};
  wsProps.submissions = [
    { id: 7, text: '天气早报 · v1.0.0 · 审核中 · 10-03 09:10', status: 'pending' },
    { id: 6, text: '喝水提醒 · v1.0.0 · 被驳回 · 10-02 20:00 · 站长说：名字太泛', status: 'rejected' },
  ];
  wsProps.subBusyId = '';
  wsProps.onWithdraw = () => {};
  wsProps.onReloadSubmissions = () => {};
  const wsPage = strip(renderToString(React.createElement(WorkshopView, wsProps)));
  check('工坊页：模型配置块说清了「和 AI 大脑分开配」',
    wsPage.includes('工坊用哪个模型来写插件') && wsPage.includes('和「AI 大脑」是分开的两份配置'));
  check('工坊页：Key 用掩码回填（不显示明文）',
    wsPage.includes('****1234') && wsPage.includes('留空 = 不改'));
  check('工坊页：已装插件 + 六个动作都在',
    ['让 AI 修一版', '换回上一版', '投稿到官网', '卸载']
      .every((t) => wsPage.includes(t))
    // 历史开着时按钮写「收起历史」，收起时才写「历史版本」
    && (wsPage.includes('历史版本') || wsPage.includes('收起历史')));
  check('工坊页：历史行显示时间戳', wsPage.includes('20261002-101500'));
  check('工坊页：投稿表单（作者名/说明/验证码/发送/提交）',
    ['作者名', '一句话说明', '邮箱验证码', '发送验证码', '提交审核'].every((t) => wsPage.includes(t)));
  check('工坊页：验证码提示在', wsPage.includes('验证码已发到 li***@qq.com'));
  check('工坊页：导入块在', wsPage.includes('导入插件包') && wsPage.includes('选一个 zip 导入'));

  check('工坊页：只列工坊生成的插件（官网下载的不进来）',
    wsPage.includes('天气早报') && !wsPage.includes('连续对话能力包'),
    wsPage.includes('连续对话能力包') ? '官网下载的包被列进来了' : '');
  check('工坊页：其余的报数量并指向插件管理',
    wsPage.includes('另有 1 个官网下载 / 手动装的能力包，去「插件管理」页管理。'));

  check('工坊页：有「下载到本地」按钮（走 export?download=1）',
    wsPage.includes('下载到本地') && wsPage.includes('在服务器上打包'));
  check('工坊页：我的投稿显示状态与驳回原因',
    wsPage.includes('我的投稿')
    && wsPage.includes('天气早报 · v1.0.0 · 审核中 · 10-03 09:10')
    && wsPage.includes('站长说：名字太泛'));
  check('工坊页：只有"审核中"的投稿才给撤回按钮',
    wsPage.includes('撤回') && wsPage.includes('刷新投稿记录'));
  const subEmpty = strip(renderToString(React.createElement(WorkshopView, {
    ...wsProps, submissions: [],
  })));
  check('工坊页：没投过稿时给人话', subEmpty.includes('还没有投过稿'));
  const subLoading = strip(renderToString(React.createElement(WorkshopView, {
    ...wsProps, submissions: null,
  })));
  check('工坊页：投稿记录没读到时说"正在读取"', subLoading.includes('正在读取投稿记录'));

  const wsActions = [];
  const wsPageTree = WorkshopView({
    ...wsProps,
    onExport: () => wsActions.push('export'),
    onRollback: () => wsActions.push('rollback'),
    onUninstall: () => wsActions.push('uninstall'),
    onSendCode: () => wsActions.push('code'),
    onSubmitUpload: () => wsActions.push('upload'),
    onUpload: () => wsActions.push('openUpload'),
    onDownload: () => wsActions.push('download'),
    onWithdraw: () => wsActions.push('withdraw'),
  });
  const clickText = (needle, tag) => {
    const node = findButtonByText(wsPageTree, needle);
    check(`找到「${needle}」按钮`, Boolean(node));
    if (node) node.props.onClick();
    return wsActions[wsActions.length - 1];
  };
  check('点「在服务器上打包」→ 走到打包', clickText('在服务器上打包') === 'export');
  check('点「下载到本地」→ 走到下载', clickText('下载到本地') === 'download');
  check('点「撤回」→ 走到撤回', clickText('撤回') === 'withdraw');
  check('点「投稿到官网」→ 只有工坊生成的才有这个按钮，且走到投稿',
    clickText('投稿到官网') === 'openUpload');
  check('点「换回上一版」→ 走到回滚', clickText('换回上一版') === 'rollback');
  check('点「卸载」→ 走到卸载', clickText('卸载') === 'uninstall');
  check('点「发送验证码」→ 去要验证码', clickText('发送验证码') === 'code');
  check('点「提交审核」→ 去投稿', clickText('提交审核') === 'upload');

  /* ── 三、「官网现在能下载的东西」整块已删（2026-10-10 老板："这些东西意义何在"）──
     服务器端控制台里摆 Windows/安卓安装包没意义（那是官网给访客看的），而且那几条
     常年显示「—」。原来那 7 条断言（layoutCards / findConsoleProduct / 产品卡渲染）
     随组件一起删；换成"这一块确实不在了"的断言，防止以后又被谁悄悄加回来。
     注意：*文件里*保留 .jsx 源码本身不必存在，这里只查"设置页不再引用它"。 */
  check('设置页里不再有「官网现在能下载的东西」这一块',
    !acctPage.includes('官网现在能下载的东西') && !acctPage.includes('prod-cards'));
  check('设置页保留了「版本与更新」的当前版本 + 检查更新（只删下载块，不删更新检查）',
    acctPage.includes('版本与更新') && acctPage.includes('检查更新'));
  check('控制台源码里已没有 ProductDownloads 组件（文件已删，不留死代码）',
    !readFileSync(join(__dirname, '..', 'src', 'App.jsx'), 'utf8').includes('ProductDownloads'));

  /* ── 四、本轮新增：飞书 / 纸飞机 / QQ 官方 / 日志保留 / 登录提示条 / AI 新开关 ── */

  // 飞书徽标：三种结论各测一次，必须诚实
  const fbNoCred = feishuBadge({ enabled: true, app_id_set: false, app_secret_set: false, adapter_installed: false, ready: false }, '');
  check('飞书徽标：凭证没填 → 「没填凭证」', fbNoCred.label.includes('没填凭证'), fbNoCred.label);
  const fbNoAdapter = feishuBadge({ enabled: true, app_id_set: true, app_secret_set: true, adapter_installed: false, ready: false }, '');
  check('飞书徽标：凭证齐了但没装适配器 → 「没装适配器」',
    fbNoAdapter.label.includes('没装适配器'), fbNoAdapter.label);
  const fbReady = feishuBadge({ enabled: true, app_id_set: true, app_secret_set: true, adapter_installed: true, ready: true }, '');
  check('飞书徽标：都齐了才说「已就绪」', fbReady.tone === 'running' && fbReady.label.includes('已就绪'), fbReady.label);
  check('飞书徽标：读不到就说读不到，不装成「没填凭证」',
    feishuBadge(null, '暂不可用：服务器还没这个接口').label.includes('读不到'));

  const feishuHtml = strip(renderToString(React.createElement(FeishuView, {
    status: {
      enabled: false, app_id_set: true, app_secret_set: true,
      adapter_installed: false, ready: false, why: '凭证已填，还差适配器',
    },
    statusErr: '', form: {}, edited: {}, busy: false, installing: false,
    log: ['正在安装飞书适配器…'], msg: '',
  })));
  check('飞书卡：四个凭证框与两个动作都在',
    ['App ID', 'App Secret', 'Encrypt Key', 'Verification Token', '装适配器', '保存']
      .every((t) => feishuHtml.includes(t)));
  check('飞书卡：安装日志显示出来', feishuHtml.includes('正在安装飞书适配器'));
  check('飞书卡：服务器给的 why 照实显示', feishuHtml.includes('凭证已填，还差适配器'));
  check('飞书卡：密钥框写了「留空 = 不改」', feishuHtml.includes('留空 = 不改'));

  // 纸飞机：只许说「还在规划中」，不许出现任何能填的控件
  const tgHtml = strip(renderToString(React.createElement(TelegramView, { status: null, err: '' })));
  check('纸飞机卡：说清还在规划中', tgHtml.includes('还在规划中'));
  check('纸飞机卡：一个输入框都没有（别做成能用的样子）', !tgHtml.includes('<input'));

  // QQ 官方机器人
  check('QQ 官方徽标：没填凭证就说没填凭证',
    qqOfficialBadge({ appid_set: false, secret_set: false }, '').label.includes('没填凭证'));
  check('QQ 官方徽标：读不到就说读不到',
    qqOfficialBadge(null, '暂不可用').label.includes('读不到'));
  check('群名单：对象数组与纯字符串数组都认',
    JSON.stringify(groupRows([{ openid: 'G1', count: 2 }, 'G2', '', null]).map((r) => [r.id, r.count]))
      === JSON.stringify([['G1', 2], ['G2', 0]]),
    JSON.stringify(groupRows([{ openid: 'G1', count: 2 }, 'G2', '', null])));
  const qqOffHtml = strip(renderToString(React.createElement(QqOfficialView, {
    status: {
      enabled: true, appid_set: true, secret_set: true, mode: 'at_only',
      groups: [{ openid: 'AAAABBBB', count: 3 }], allowed_groups: [],
    },
    statusErr: '', form: {}, enabled: true, mode: 'at_only', edited: {}, busy: false,
  })));
  check('QQ 官方卡：AppID / AppSecret / 收消息模式 / 群名单都在',
    ['AppID', 'AppSecret', '收消息模式', '群名单', '仅 @ 消息'].every((t) => qqOffHtml.includes(t)));
  check('QQ 官方卡：群名单说清在不在名单里', qqOffHtml.includes('不在名单里'));

  // 日志保留
  check('体积格式化：B / KB / MB 都对',
    formatBytes(0) === '0 B' && formatBytes(900) === '900 B'
    && formatBytes(1536) === '1.5 KB' && formatBytes(23 * 1024 * 1024) === '23.0 MB',
    `${formatBytes(0)} / ${formatBytes(1536)} / ${formatBytes(23 * 1024 * 1024)}`);
  check('清理结果说清打包了几个、删了几个、释放多少',
    cleanupText({ rotated: 2, deleted: 3, bytes_freed: 1048576 }) === '已打包 2 个、删除 3 个，释放 1.0 MB',
    cleanupText({ rotated: 2, deleted: 3, bytes_freed: 1048576 }));
  check('没东西可清时也给人话',
    cleanupText({ rotated: 0, deleted: 0, bytes_freed: 0 }) === '没有需要清理的旧日志。');
  const lrHtml = strip(renderToString(React.createElement(LogRetentionView, {
    data: { keep_days: 10, usage: { bytes: 1048576, files: 4 }, archives: ['a.log.gz', 'b.log.gz'] },
    err: '', days: '10', setDays: () => {}, busy: '', result: null,
  })));
  check('日志保留区：天数 / 占用 / 旧日志包 / 两个按钮都在',
    ['保留天数', '1.0 MB · 4 个文件', '立刻清理旧日志', '保存天数'].every((t) => lrHtml.includes(t))
    && lrHtml.includes('2 个'), lrHtml.slice(0, 0));
  const lrErr = strip(renderToString(React.createElement(LogRetentionView, {
    data: {}, err: '暂不可用：服务器还没这个接口，等后端补上再来。',
  })));
  check('日志保留区：接口没上时说暂不可用，不摆假数字',
    lrErr.includes('暂不可用') && !lrErr.includes('个文件'));

  // 未登录提示条
  const lbOff = strip(renderToString(React.createElement(LoginBanner, { show: true, onLogin: () => {} })));
  check('未登录提示条：一句话 + 「去登录」按钮都在',
    lbOff.includes('未登录：机器人与通道的真实状态读不到') && lbOff.includes('去登录'));
  check('登录之后提示条自己消失',
    strip(renderToString(React.createElement(LoginBanner, { show: false }))) === '');

  /* ── 登录弹窗（2026-10-06）：账号入口从「跳设置页签」改成开弹窗，
        表单与「设置 → 账号」页签共用 LoginModal.jsx 里的 LoginForm 这一份实现。
        遮罩与右上角 × 在这里真点一遍；ESC 与「焦点还回触发按钮」要在真浏览器里验。 */
  check('登录弹窗：没打开时一个节点都不渲染（不占位、不白屏）',
    strip(renderToString(React.createElement(LoginModal, { open: false }))) === '');

  const modalHtml = strip(renderToString(React.createElement(LoginModal, {
    open: true, loggedIn: false, onToken: () => {}, onChanged: async () => {},
  })));
  check('登录弹窗：标题 + 邮箱 + 密码 + 登录按钮都在',
    ['登录星群账号', '邮箱', '密码', '登录'].every((t) => modalHtml.includes(t)), modalHtml.slice(0, 140));
  check('登录弹窗：说明只有一行，弹层是模态的（role=dialog + aria-modal）',
    modalHtml.includes('用星群账号邮箱登录') && modalHtml.includes('role="dialog"')
    && modalHtml.includes('aria-modal="true"'), modalHtml.slice(0, 140));
  check('登录弹窗：已登录时不摆表单，只报「已登录」',
    strip(renderToString(React.createElement(LoginModal, {
      open: true, loggedIn: true, email: 'li@astroswarm.cn',
    }))).includes('已登录：li@astroswarm.cn'));

  /* 三种关法里的两种在这里按下：遮罩、右上角 ×。
     LoginModal 里有 hook，不能当纯函数直接调 —— 在一个空组件里渲染一次把元素树接出来。 */
  const modalClicks = [];
  let modalTree = null;
  const GrabModal = () => {
    modalTree = LoginModal({
      open: true,
      loggedIn: false,
      onClose: () => modalClicks.push('close'),
      onToken: () => {},
      onChanged: async () => {},
    });
    return null;
  };
  renderToString(React.createElement(GrabModal));
  let maskEl = null;
  walk(modalTree, (n) => {
    if (!maskEl && n.props && String(n.props.className || '') === 'loginmodal__mask') maskEl = n;
  });
  check('登录弹窗：遮罩在（点它就能关）', Boolean(maskEl && typeof maskEl.props.onClick === 'function'));
  if (maskEl) maskEl.props.onClick();
  const closeBtn = findButtonByText(modalTree, '×');
  check('登录弹窗：右上角 × 在', Boolean(closeBtn && typeof closeBtn.props.onClick === 'function'));
  if (closeBtn) closeBtn.props.onClick();
  check('登录弹窗：点遮罩、点 × 都真的调了关闭',
    modalClicks.length === 2, JSON.stringify(modalClicks));

  /* 表单只有一份的守卫：页面里不许再出现第二份登录实现，
     登录接口只准在 LoginForm 里调一次（以前设置页和弹窗会各写一遍）。 */
  const appSrc = readFileSync(join(__dirname, '..', 'src', 'App.jsx'), 'utf8');
  const modalSrc = readFileSync(join(__dirname, '..', 'src', 'LoginModal.jsx'), 'utf8');
  check('登录表单只有一份：设置页与弹窗都引用 LoginModal 的 LoginForm',
    (appSrc.match(/<LoginForm/g) || []).length === 1
    && /export function LoginForm/.test(modalSrc));
  check('登录接口只在 LoginForm 里调（App.jsx 里已经没有了）',
    (modalSrc.match(/post\('\/api\/auth\/login'/g) || []).length === 1
    && !appSrc.includes('api/auth/login'));
  check('LoginForm 是独立导出的（两处能复用，不是塞在弹窗里的私有件）',
    typeof LoginForm === 'function');

  /* ── 两步分流：登录 / 没注册就注册开户（2026-10-10 老板最终版）──────────────
         第一步 = 邮箱 +「下一步」→ POST /api/auth/check-email 问"注册过没有"；
         注册过 → 密码框 +「登录」（**没有**验证码框、**没有**获取验证码）；
         没注册过 → 密码 + 6 位验证码 +「获取验证码」+「注册并登录」。
         三条本地校验、倒计时文案、后端 400 的中文原因都抽成纯函数，这里直接验；
         三个分支的样子用纯视图 LoginFormView 各渲染一遍再断言。 */
  check('就地校验：邮箱粗判挡住空的 / 明显不对的',
    emailLooksOk('li@astroswarm.cn') && emailLooksOk(' a@b.co ')
    && !emailLooksOk('') && !emailLooksOk('li') && !emailLooksOk('li@astroswarm'));
  check('第一步校验：空邮箱 / 格式不对都在本地拦（不发请求）',
    emailStepError('') === '先填邮箱。'
    && emailStepError('li@astroswarm') === '邮箱格式不对，检查一下。'
    && emailStepError('li@astroswarm.cn') === '');
  check('已注册分支：只要求密码非空（登录不卡 ≥8 位，规则以账号服务为准）',
    passwordStepError('') === '先填密码。' && passwordStepError('1234') === ''
    && passwordStepError('12345678') === '');
  check('未注册分支：开户密码 ≥8 位 + 6 位验证码，两条都先本地拦',
    registerStepError('', '') === '先填密码（至少 8 位）。'
    && registerStepError('', '1234567') === '密码至少 8 位。'
    && registerStepError('', '12345678') === '先点「获取验证码」，把 6 位码填上。'
    && registerStepError('123', '12345678') === '验证码是 6 位数字，检查一下。'
    && registerStepError('123456', '12345678') === ''
    && codeLooksOk('000000') && !codeLooksOk('12345') && !codeLooksOk('1234567')
    && passwordLooksOk('12345678') && !passwordLooksOk('1234567'));
  check('后端查注册状态的结果决定分支：true → 密码，false → 注册；问不到 → 原地不动',
    stepForCheck({ ok: true, registered: true }) === 'password'
    && stepForCheck({ ok: true, registered: false }) === 'register'
    && stepForCheck({ ok: false, reason: '连不上' }) === ''
    && stepForCheck(null) === '' && stepForCheck({}) === '');
  check('查询失败只说「暂时查不到，请稍后再试」，**绝不**当成"未注册"',
    checkFailText(new Error('Failed to fetch')) === CHECK_FAIL_TEXT
    && checkFailText(null) === CHECK_FAIL_TEXT
    && checkFailText(new Error('连不上星群账号服务：timed out')) === CHECK_FAIL_TEXT
    && CHECK_FAIL_TEXT === '暂时查不到，请稍后再试'
    && !/未注册|注册开户/.test(CHECK_FAIL_TEXT));
  check('被限流时显示后端那句人话（查询太频繁…），不当成"未注册"',
    checkFailText(new Error('查询太频繁了，过几分钟再试')) === '查询太频繁了，过几分钟再试');
  check('获取验证码按钮的字：请求在飞是「发送中…」，其余是「获取验证码」',
    sendCodeText(true) === '发送中…' && sendCodeText(false) === '获取验证码');
  check('倒计时那一行：59s 后可重发；0 秒就没有这一行',
    cooldownText(59) === '59s 后可重发' && cooldownText(60) === '60s 后可重发'
    && cooldownText(0) === '');
  check('后端 400 的中文原因原样显示（登录 / 注册 / 发码那几条都算），没话说才兜底',
    errText(new Error('登录失败：邮箱或密码错误')) === '登录失败：邮箱或密码错误'
    && errText(new Error('注册失败：该邮箱已注册')) === '注册失败：该邮箱已注册'
    && errText(new Error('注册失败：验证码错误或已过期')) === '注册失败：验证码错误或已过期'
    && errText(new Error('验证码没发出去：邮箱格式不对')) === '验证码没发出去：邮箱格式不对'
    && errText(null, '验证码没发出去，稍后再试。') === '验证码没发出去，稍后再试。');
  check('弹窗表单：只剩一个表单，没有"登录 / 注册"页签',
    !modalHtml.includes('role="tablist"') && !modalHtml.includes('注册新账号')
    && !modalHtml.includes('密码登录'));
  check('第一步（弹窗默认样子）：只有邮箱 + 下一步，**没有**密码框 / 验证码框 / 获取验证码',
    modalHtml.includes('邮箱') && modalHtml.includes('下一步')
    && !modalHtml.includes('type="password"')
    && !modalHtml.includes('6 位验证码')
    && !modalHtml.includes('获取验证码')
    && !modalHtml.includes('注册并登录'),
    modalHtml.slice(0, 260));
  check('第一步不再劝人发码（旧那句「没注册过？点发送验证码…会自动帮你注册」必须消失）',
    !modalHtml.includes('会自动帮你注册') && !modalHtml.includes('6 位验证码（可选）'));
  check('第一步就有「忘记星群账号密码？去官网重置密码」（老用户随时找得到出路）',
    modalHtml.includes('忘记星群账号密码') && modalHtml.includes('login.html#reset'));

  /* 三种分支各渲一遍（纯视图，喂 step 就行）——老板要的判据就在这里：
     已注册分支**不许**出现验证码那套；未注册分支**必须**出现「获取验证码」。 */
  const stepEmail = strip(renderToString(React.createElement(LoginFormView, {
    step: 'email', email: '', busy: false,
  })));
  const stepPw = strip(renderToString(React.createElement(LoginFormView, {
    step: 'password', email: 'li@astroswarm.cn', password: '', busy: false,
    onBack: () => {},
  })));
  const stepReg = strip(renderToString(React.createElement(LoginFormView, {
    step: 'register', email: 'new@astroswarm.cn', password: '', code: '', busy: false,
    cooldown: 0, onBack: () => {},
  })));
  check('已注册分支：只有密码框 +「登录」，**不出现验证码框、不出现获取验证码按钮**',
    stepPw.includes('type="password"') && stepPw.includes('登录')
    && !stepPw.includes('6 位验证码') && !stepPw.includes('获取验证码')
    && !stepPw.includes('注册并登录')
    && (stepPw.match(/type="password"/g) || []).length === 1,
    stepPw.slice(0, 260));
  check('已注册分支：认得出是哪个邮箱 + 有「改邮箱」返回入口',
    stepPw.includes('li@astroswarm.cn') && stepPw.includes('改邮箱'));
  check('已注册分支：忘了密码那一行还在（去官网重置密码）',
    stepPw.includes('忘记星群账号密码') && stepPw.includes('login.html#reset'));
  check('未注册分支：出现「获取验证码」+ 6 位验证码框 + 开户密码 +「注册并登录」',
    stepReg.includes('获取验证码') && stepReg.includes('6 位验证码')
    && stepReg.includes('密码（至少 8 位）') && stepReg.includes('注册并登录')
    && (stepReg.match(/type="password"/g) || []).length === 1,
    stepReg.slice(0, 300));
  check('未注册分支：说清这是新账号，并且能「改邮箱」退回去',
    stepReg.includes('还没注册过') && stepReg.includes('改邮箱'));
  check('第一步视图里没有密码框（进第二步才可能出现）',
    !stepEmail.includes('type="password"') && stepEmail.includes('下一步'));
  check('倒计时那一行只在未注册分支出现（点过获取验证码之后）',
    strip(renderToString(React.createElement(LoginFormView, {
      step: 'register', email: 'new@astroswarm.cn', password: '12345678',
      code: '123456', cooldown: 42,
    }))).includes('42s 后可重发')
    && !strip(renderToString(React.createElement(LoginFormView, {
      step: 'password', email: 'li@astroswarm.cn', password: '12345678', cooldown: 42,
    }))).includes('42s 后可重发'));

  // 同一份实现在「设置 → 账号」页签里渲染出来的样子（弹窗与页签共用一个组件）
  const formHtml = strip(renderToString(React.createElement(LoginForm, {
    onToken: () => {}, onChanged: async () => {},
  })));
  check('共用表单：页签里渲染的是同一套字段（第一步也是邮箱 + 下一步）',
    formHtml.includes('loginform') && formHtml.includes('下一步')
    && !formHtml.includes('type="password"'));
  check('四个接口各只调一次：check-email / login / register / send-code（都在 LoginForm 里）',
    (modalSrc.match(/post\('\/api\/auth\/check-email'/g) || []).length === 1
    && (modalSrc.match(/post\('\/api\/auth\/login'/g) || []).length === 1
    && (modalSrc.match(/post\('\/api\/auth\/register'/g) || []).length === 1
    && (modalSrc.match(/post\('\/api\/auth\/send-code'/g) || []).length === 1
    && !modalSrc.includes('login-or-register')
    && !appSrc.includes('api/auth/login'),
    JSON.stringify((modalSrc.match(/post\('\/api\/auth\/[a-z-]+'/g) || [])));
  check('注册成功说的是「账号已创建并登录」，登录成功说「已登录」',
    modalSrc.includes('账号已创建并登录') && modalSrc.includes("onMsg('已登录')"));
  check('后端没回 token 就不算成功（登录 / 注册两条路都拦）',
    (modalSrc.match(/!res \|\| !res\.token/g) || []).length === 2);
  check('App 挂上了「登录状态过期」的处理：清 token + 一句「登录状态已过期，请重新登录」',
    /setAccountExpiredHandler\(/.test(appSrc)
    && appSrc.includes("onToken('')")
    && appSrc.includes('登录状态已过期，请重新登录'),
    'appSrc 里没有清 token 的处理');

  /* ── 回归锁：useEffect 里不许直接传"会返回 Promise 的函数" ──────────────
     2026-10-12 截图验收时抓到的真 bug：插件详情页写的是 `useEffect(load, [pid])`，
     load 里最后一句是 `return get(...).then(...).catch(...)` → effect 的返回值
     被 React 当成清理函数，**离开这一页时**去调 promise.destroy() →
     "destroy is not a function" → 整页被 ErrorBoundary 接住显示「页面出错了」。
     这种写法静态看得见（`useEffect(标识符, [deps])`），所以在这里钉死。 */
  /* ⚠ 注释里为了说明"以前怎么写错的"也带着 `useEffect(load, [pid])` 这串字，
     所以判据要求**整句真的以分号结尾**（注释那句后面跟的是空格和破折号）。 */
  check('插件详情页：两个 effect 不再直接传会返回 Promise 的 load/loadEnt',
    !/useEffect\((load|loadEnt),\s*\[pid\]\)\s*;/.test(appSrc)
    && /useEffect\(\(\)\s*=>\s*\{\s*load\(\);\s*\},\s*\[pid\]\)\s*;/.test(appSrc)
    && /useEffect\(\(\)\s*=>\s*\{\s*loadEnt\(\);\s*\},\s*\[pid\]\)\s*;/.test(appSrc),
    'appSrc 里还有 useEffect(load/loadEnt, [deps]); 的写法');

  // AI 大脑：三个新开关的默认值必须是「开」，绑定状态要显示出来
  const dflt = normalizeAiDraft({});
  check('识图 / 自学 / 跨平台互通：后端没回这三个字段时按「开」显示',
    dflt.image_recognition_enabled === true && dflt.self_forge_enabled === true
    && dflt.cross_platform_enabled === true, JSON.stringify(dflt));
  check('后端明确回 false 时不能当成开',
    normalizeAiDraft({ image_recognition_enabled: false }).image_recognition_enabled === false);
  check('绑定状态：没绑定时给人话',
    bindingText([]) === '还没绑定（在微信或 QQ 上发「绑定」拿码）', bindingText([]));
  check('绑定状态：拼成「微信 iLink ↔ QQ 官方机器人」',
    bindingText([['wechat:abc', 'qq_official:9']]) === '微信 iLink ↔ QQ 官方机器人',
    bindingText([['wechat:abc', 'qq_official:9']]));

  /* ── 多条式回复 + 超长消息上限（0.2.42 后端，2026-10-12 控制台补界面）────────
     规矩：范围与默认值**只能用后端给的元信息**，前端一个数字都不许写死。
     所以这里喂一份"故意跟真实默认值都不一样"的元信息，界面必须照着它渲染 ——
     如果渲染出来还是 1~6 / 3 这些真值，说明哪里写死了。 */
  const META_FIX = {
    multi_message_info: {
      note: '分条说明（测试用）', default_mode: 'auto',
      modes: [
        { value: 'off', label: '关（永远只发一条）', hint: '拼成一条发出去。' },
        { value: 'auto', label: '自动（默认：由内容决定）', hint: '该分的分。' },
        { value: 'always', label: '总是（尽量分条）', hint: '有多少段发多少段。' },
      ],
      limits: {
        max_parts: { default: 2, min: 1, max: 9, note: '最多几条（测试口径）' },
        max_part_chars: { default: 111, min: 50, max: 2000, note: '每条多少字（测试口径）' },
        part_delay_ms: { default: 7, min: 0, max: 500, note: '每字延迟（测试口径）' },
        part_delay_max_ms: { default: 1234, min: 300, max: 5000, note: '最多等多久（测试口径）' },
      },
    },
    max_incoming_chars: 222,
    max_incoming_chars_info: { default: 222, min: 1, max: 333, note: '超长上限（测试口径）' },
  };
  const metaDraft = normalizeAiDraft({
    group_chat_enabled: true,
    interject: { enabled: false },
    session: { mode: 'timeout' },
    allowed_groups: [], known_groups: [],
    multi_message: { mode: 'always', max_parts: 5 },
    ...META_FIX,
  });
  const metaHtml = strip(renderToString(React.createElement(BotAiView, {
    draft: metaDraft, setDraft: () => {}, busy: false, saved: '',
    onSave: () => {}, onReload: () => {},
  })));
  check('多条式回复：三档用胶囊（不是下拉）+ 说明小字跟着当前档位走',
    metaHtml.includes('numf__opt') && metaHtml.includes('关（永远只发一条）')
    && metaHtml.includes('有多少段发多少段。')
    && !/<select[^>]*>[^<]*多条式回复/.test(metaHtml));
  check('多条式回复：范围与默认值全部来自后端元信息（喂了假范围就必须显示假范围）',
    metaHtml.includes('范围 1 ~ 9 条') && metaHtml.includes('范围 50 ~ 2000 字')
    && metaHtml.includes('范围 0 ~ 500 毫秒') && metaHtml.includes('范围 300 ~ 5000 毫秒')
    && !metaHtml.includes('范围 1 ~ 6 条'),
    metaHtml.match(/范围 [0-9]+ ~ [0-9]+ ?[^\s<]*/g)?.join(' / ') || '(一个范围提示都没有)');
  check('超长消息上限：输入框补上了（这项以前只有后端、没有界面）',
    metaHtml.includes('别人发来的消息最多读多少字') && metaHtml.includes('超长上限（测试口径）')
    && metaHtml.includes('范围 1 ~ 333 字'),
    `有标签=${metaHtml.includes('别人发来的消息最多读多少字')} 有说明=${metaHtml.includes('超长上限（测试口径）')}`);
  check('分条细节收进折叠区（标题行给摘要），三档常显不折',
    metaHtml.includes('分条细节（5 项）') && metaHtml.includes('collapse__btn')
    && /最多 <!-- -->5<!-- --> 条|最多 5 条/.test(metaHtml));

  // 老后端（没有元信息）时整块不出现，且保存时不提交这些键 —— 不能猜着写
  const oldDraft = normalizeAiDraft({ group_chat_enabled: true, interject: {}, session: {} });
  const oldHtml = strip(renderToString(React.createElement(BotAiView, {
    draft: oldDraft, setDraft: () => {}, busy: false, saved: '',
    onSave: () => {}, onReload: () => {},
  })));
  check('后端没有 multi_message_info 时：不显示这一块（也不拿默认值去写服务器）',
    oldDraft.multi_message_info === null && oldDraft.max_incoming_chars === null
    && !oldHtml.includes('多条式回复') && !oldHtml.includes('分条细节'));

  /* ── 本机能力（可搜索 · 可开关）：桌面端 2026-10-12 新加的一节 ──────────────
     老板的话：「插件页面看不见好友申请那个功能」「加一个能搜索还带开关的」。
     这里钉四件事：① 有搜索框（且真的按 名字/说明/来源 过滤）；
     ② 每一项是开关行、带说明（不是裸复选框）；③ "加好友"能被搜到；
     ④ 老后端（没这个接口）不显示这一节。 */
  const capItems = [
    { name: 'approve_friend', desc: '通过某个人的好友申请。', source: '内置 ai 插件',
      permission: 'send_message', enabled: true },
    { name: 'list_friend_requests', desc: '看现在有哪些人申请加你。', source: '内置 ai 插件',
      permission: '', enabled: false },
    { name: 'knowledge_search', desc: '从本地知识库检索文档片段', source: '主程序',
      permission: '', enabled: true },
  ];
  const capHtml = strip(renderToString(React.createElement(MachineCapabilitiesView, {
    items: capItems, note: '改完立即生效，不用重启机器人。', kw: '', loaded: true, err: '',
    onKw: () => {}, onToggle: () => {}, onReload: () => {},
  })));
  check('本机能力：搜索框在（placeholder 与桌面端同一句）',
    capHtml.includes('搜索能力：输名字、说明或来源里的字都行'));
  check('本机能力：每一项是开关行（role=switch）+ 说明 + 来源/权限都写出来',
    capHtml.includes('role="switch"') && capHtml.includes('通过某个人的好友申请。')
    && capHtml.includes('来源：内置 ai 插件') && capHtml.includes('需要权限：send_message')
    && !capHtml.includes('type="checkbox"'));
  check('本机能力：计数说人话（共几项 / 开启几项）',
    capHtml.includes('共 3 项，开启 2 项'));
  check('本机能力：搜索真按 名字/说明/来源 过滤（"加好友"能搜到 approve_friend）',
    filterCapabilities(capItems, 'approve_friend').length === 1
    && filterCapabilities(capItems, '好友申请').length === 1
    && filterCapabilities(capItems, '申请').length === 2
    && filterCapabilities(capItems, '主程序').length === 1
    && filterCapabilities(capItems, '').length === 3,
    `approve_friend=${filterCapabilities(capItems, 'approve_friend').length}`
    + ` 好友申请=${filterCapabilities(capItems, '好友申请').length}`
    + ` 申请=${filterCapabilities(capItems, '申请').length}`
    + ` 主程序=${filterCapabilities(capItems, '主程序').length}`
    + ` 空=${filterCapabilities(capItems, '').length}`);
  const capEmptyHtml = strip(renderToString(React.createElement(MachineCapabilitiesView, {
    items: [], note: '', kw: '没有这种东西', loaded: true, err: '',
    onKw: () => {}, onToggle: () => {}, onReload: () => {},
  })));
  check('本机能力：搜不到时照实说（不当成"没有能力"）',
    capEmptyHtml.includes('没有匹配的能力，换个词试试。'));
  const capErrHtml = strip(renderToString(React.createElement(MachineCapabilitiesView, {
    items: [], note: '', kw: '', loaded: true, err: '连不上服务器', 
    onKw: () => {}, onToggle: () => {}, onReload: () => {},
  })));
  check('本机能力：读不到就说读不到（不显示成"没有能力"）',
    capErrHtml.includes('连不上服务器') && !capErrHtml.includes('共 0 项'));

  /* ── 好友申请 + Agent 模式（桌面端 brain_page.py:673-695 同源）────────────
     **能力不在就不显示开关**（老板原话："插件没装，AI 大脑的开关是不是该隐藏"）。 */
  const capOnDraft = normalizeAiDraft({
    group_chat_enabled: true, interject: {}, session: {},
    has_approve_friend: true, has_agent_tools: true,
    auto_approve_friend: true, agent_mode_enabled: false,
    friend_requests: { total: 2, pending: [{ user_id: '10001', comment: '买家' },
      { user_id: '10002', comment: '' }] },
  });
  const capOnHtml = strip(renderToString(React.createElement(BotAiView, {
    draft: capOnDraft, setDraft: () => {}, busy: false, saved: '',
    onSave: () => {}, onSaveOne: () => {}, onReload: () => {},
  })));
  check('AI 大脑：能力在时显示「自动通过好友申请」与「Agent 模式」（文案照桌面端）',
    capOnHtml.includes('自动通过好友申请（卖家/客服用）')
    && capOnHtml.includes('Agent 模式：先动手办事，再说话'));
  check('AI 大脑：好友申请开关带说明（默认关闭 / 谁加都通过）',
    capOnHtml.includes('打开后：有人申请加机器人好友，它直接通过（不再问你）')
    && capOnHtml.includes('默认关闭——开着等于谁加都通过'));
  check('AI 大脑：待处理好友申请照实显示（2 个、第一个是谁）',
    capOnHtml.includes('2 个人在等着加好友') && capOnHtml.includes('10001')
    && capOnHtml.includes('（买家）'));
  check('AI 大脑：没有待处理时说人话（不是空白）',
    friendRequestText(null) === '暂时没有人申请加好友'
    && friendRequestText({ total: 0, pending: [] }) === '暂时没有人申请加好友'
    && friendRequestText({ total: 1, pending: [{ user_id: '9', comment: '' }] })
      === '1 个人在等着加好友：9');
  check('AI 大脑：能力不在时这两个开关**不显示**（老后端 / 插件没装）',
    oldDraft.has_approve_friend === false && oldDraft.has_agent_tools === false
    && oldDraft.auto_approve_friend === false && oldDraft.agent_mode_enabled === true
    && !oldHtml.includes('自动通过好友申请') && !oldHtml.includes('Agent 模式'));

  // 标题栏版本号：只在服务器给的时候才有数字，拿不到就说「版本读取中」
  check('版本号：优先用 /api/status 里的（Linux 无头端自己的版本）',
    versionLabel({ version: '0.2.10' }, { version: '9.9.9' }) === 'v0.2.10',
    versionLabel({ version: '0.2.10' }, { version: '9.9.9' }));
  check('版本号：/api/status 没有就退到 /api/config',
    versionLabel({}, { version: '0.3.0' }) === 'v0.3.0');
  check('版本号：已经带 v 的不重复加',
    versionLabel({ version: 'v0.4.0' }, null) === 'v0.4.0');
  check('版本号：两处都读不到时显示「版本读取中」，不写死数字',
    versionLabel(null, null) === VERSION_PENDING && versionLabel({}, {}) === VERSION_PENDING,
    versionLabel(null, null));
  check('版本号：页面里不再有写死的 1.1.0（那是网页旧编号，不是产品版本）',
    !JSON.stringify([VERSION_PENDING, versionLabel({ version: '0.2.10' }, null)]).includes('1.1.0'));

  /* ── 品牌边界（2026-10-10 老板定名）：**网站叫星群，软件叫星巢 Nest** ──────────
     · 改：控制台页面标题、顶栏品牌、密码屏副标题（用户看得见的软件名）；
     · 不改：「星群账号」是网站（astroswarm.cn）的账号，一个字都不许动，
       还有包名 astroswarm-console。
     这几条是防回归的：谁把软件名改回「星群」或顺手把账号品牌改成星巢，这里就红。 */
  const indexHtml = readFileSync(join(__dirname, '..', 'index.html'), 'utf8');
  check('页面标题：软件名是「星巢 Nest 控制台」',
    /<title>\s*星巢 Nest 控制台\s*<\/title>/.test(indexHtml), indexHtml.slice(0, 200));
  check('顶栏品牌：App.jsx 的 titlebar__title 写的是「星巢 Nest」',
    /titlebar__title">星巢 Nest</.test(appSrc));
  check('软件名不许再出现在标题/顶栏/密码屏（「星巢」在，旧写法不在）',
    !/<title>\s*AstroSwarm/.test(indexHtml)
    && !appSrc.includes('titlebar__title">AstroSwarm 星群')
    && !appSrc.includes('星群控制台')
    && !readFileSync(join(__dirname, '..', 'src', 'ConsoleLogin.jsx'), 'utf8').includes('星群控制台'));
  check('密码屏副标题：ConsoleLogin.jsx 写的是「星巢控制台」',
    readFileSync(join(__dirname, '..', 'src', 'ConsoleLogin.jsx'), 'utf8').includes('星巢控制台'));
  check('账号品牌不许动：控制台里仍然是「星群账号」（不是「星巢账号」）',
    appSrc.includes('星群账号') && !appSrc.includes('星巢账号'));
  check('包名不许动：package.json 仍是 astroswarm-console（换了路径就变）',
    JSON.parse(readFileSync(join(__dirname, '..', 'package.json'), 'utf8')).name === 'astroswarm-console');

  /* ── 「忘了密码怎么办」的指引（2026-10-10 老板要求）─────────────────────
     三个界面都要能看到出路：
       ① 控制台首次设置密码屏（needSetup=true）
       ② 控制台输入密码屏（needSetup=false）
       ③ 星群账号登录弹窗（LoginModal）
     这里**真渲染一遍再断言渲染结果**，不只看源码字符串：文案被条件写没了、
     挪到不显示的分支里，这些都能抓到。少了任何一处，用户就又回到「忘了没辙」。 */
  const RESET_CMD = 'astroswarm console-reset-password';
  const RESET_TAIL = '会作废所有已登录设备';

  const setupHtml = strip(renderToString(React.createElement(ConsoleLogin, { needSetup: true })));
  const unlockHtml = strip(renderToString(React.createElement(ConsoleLogin, { needSetup: false })));

  check('① 设置密码屏：给了重置命令 + 「会作废所有已登录设备」+ --password 写法',
    setupHtml.includes(RESET_CMD)
    && setupHtml.includes(RESET_TAIL)
    && setupHtml.includes(RESET_CMD + ' --password'),
    setupHtml.slice(-300));
  check('① 设置密码屏：原来那句「密码只存在这台服务器上」没被挤掉',
    setupHtml.includes('密码只存在这台服务器上'));
  check('① 设置密码屏：写明这是本地密码、没有网页找回通道',
    setupHtml.includes('忘了怎么办') && setupHtml.includes('只有能登服务器的人做得到'));

  check('② 输入密码屏：命令 / 「会作废所有已登录设备」/ --password 三样都在（与①口径一致）',
    unlockHtml.includes(RESET_CMD)
    && unlockHtml.includes(RESET_TAIL)
    && unlockHtml.includes(RESET_CMD + ' --password'),
    unlockHtml.slice(-300));
  check('② 输入密码屏：仍然写着「忘了密码？」',
    unlockHtml.includes('忘了密码？'));
  check('两个密码屏的命令都走 .cl-cmd（等宽、可整段选中，不抢主文案）',
    /class="cl-cmd"/.test(setupHtml) && /class="cl-cmd"/.test(unlockHtml));
  check('两个密码屏都不许出现「星巢账号」（软件叫星巢，账号仍叫星群账号）',
    !setupHtml.includes('星巢账号') && !unlockHtml.includes('星巢账号'));

  const acctModalHtml = strip(renderToString(React.createElement(LoginModal, {
    open: true, loggedIn: false, onClose: () => {},
  })));
  check('③ 账号登录弹窗：忘记密码指向官网（未登录落登录页、已登录落个人中心，由官网分流）',
    acctModalHtml.includes('忘记星群账号密码')
    && acctModalHtml.includes('https://astroswarm.cn/login.html#reset'),
    acctModalHtml.slice(-300));
  check('③ 账号登录弹窗：只把路径给出去，不在这儿做重置表单（第一步没有密码框、也没有「新密码」）',
    !acctModalHtml.includes('新密码')
    && (acctModalHtml.match(/type="password"/g) || []).length === 0,
    'password inputs = ' + (acctModalHtml.match(/type="password"/g) || []).length);
  check('③ 账号登录弹窗：说清星群账号登录是「可选」的（不登录也能用控制台）',
    acctModalHtml.includes('可选') && acctModalHtml.includes('不登录也能用控制台'));
  check('③ 控制台不自己判断官网登录态：链接固定打到 login.html#reset，没有账号密码输入之外的逻辑',
    (acctModalHtml.match(/login\.html#reset/g) || []).length === 1);

  /* ── 五、官网市场（/api/plugins/market）───────────────────────────────── */
  const MARKET_ENTRIES = [
    { name: '天气早报', desc: '每天早上八点播报天气', version: '1.0.0', category: '生活', access: { allowed: true, label: '免费', reason: '免费' } },
    { name: '群管助手', desc: '自动踢广告', version: '2.1.0', category: '管理', access: { allowed: true, label: '免费', reason: '免费' } },
  ];
  check('市场搜索：按名字过滤',
    marketFilter(MARKET_ENTRIES, '天气').length === 1, JSON.stringify(marketFilter(MARKET_ENTRIES, '天气')));
  check('市场搜索：说明里的字也能搜到（和桌面端一致）',
    marketFilter(MARKET_ENTRIES, '广告').length === 1);
  check('市场搜索：空关键字给全部', marketFilter(MARKET_ENTRIES, '  ').length === 2);
  check('权益标签：直接用后端给的 label，不自己推断',
    accessLabel({ allowed: true, label: '免费', reason: '免费' }) === '免费');
  check('权益标签：没有 label 时退到 reason', accessLabel({ reason: '需权益' }) === '需权益');
  check('权益标签：什么都没有时兜底「免费」', accessLabel({}) === '免费');

  const marketOk = strip(renderToString(React.createElement(MarketView, {
    state: { status: 'ok', entries: MARKET_ENTRIES, why: '' },
    query: '', setQuery: () => {}, busyId: '',
  })));
  check('市场列表：名字 / 说明 / 版本 / 分类 / 权益标签 / 安装按钮都在',
    ['天气早报', '每天早上八点播报天气', 'v1.0.0', '生活', '免费', '安装']
      .every((t) => marketOk.includes(t)));
  check('市场列表：报总条数', marketOk.includes('共 2 个'));

  const marketLoading = strip(renderToString(React.createElement(MarketView, {
    state: { status: 'loading', entries: [], why: '' },
  })));
  check('市场加载中：说「正在拉取官网市场」', marketLoading.includes('正在拉取官网市场'));
  check('市场加载中：不摆空列表那句话', !marketLoading.includes('官网市场里还没有插件'));

  const marketErr = strip(renderToString(React.createElement(MarketView, {
    state: { status: 'error', entries: [], why: '市场签名校验没过，为安全起见不显示列表' },
  })));
  check('市场失败：显示「插件市场暂不可用：<why>」',
    marketErr.includes('插件市场暂不可用：市场签名校验没过，为安全起见不显示列表'), marketErr.slice(0, 0));
  check('市场失败：不当成空列表、也不白屏',
    !marketErr.includes('官网市场里还没有插件') && marketErr.includes('重新拉取'));

  const marketEmpty = strip(renderToString(React.createElement(MarketView, {
    state: { status: 'ok', entries: [], why: '' },
  })));
  check('市场为空（拉到了但没条目）：照实说还没插件',
    marketEmpty.includes('官网市场里还没有插件'));

  const marketNoMatch = strip(renderToString(React.createElement(MarketView, {
    state: { status: 'ok', entries: MARKET_ENTRIES, why: '' }, query: '不存在的东西',
  })));
  check('搜不到时说清是搜索的问题', marketNoMatch.includes('没有匹配的插件'));

  const marketCalls = [];
  const marketTree = MarketView({
    state: { status: 'ok', entries: MARKET_ENTRIES, why: '' },
    query: '', setQuery: () => {}, busyId: '',
    onInstall: (e) => marketCalls.push(e.name),
  });
  const installBtn = findButtonByText(marketTree, '安装');
  check('市场列表：找到「安装」按钮', Boolean(installBtn));
  if (installBtn) {
    installBtn.props.onClick();
    check('点「安装」→ 把这一条交出去', JSON.stringify(marketCalls) === JSON.stringify(['天气早报']),
      JSON.stringify(marketCalls));
  }
  const marketBusy = strip(renderToString(React.createElement(MarketView, {
    state: { status: 'ok', entries: MARKET_ENTRIES, why: '' }, busyId: '天气早报',
  })));
  check('安装中：按钮变成「正在安装…」', marketBusy.includes('正在安装…'));



  /* ── 版式对齐（2026-10-04）：区块顺序 / 标题用词 / 与桌面端同一句话 ───────── */

  // 首页「今日数据」：标题与五个行项目照桌面端 home_page.py（未登录也要有五行，不留白）
  const todayOff = strip(renderToString(React.createElement(TodayStats, { loggedIn: false })));
  check('今日数据：五个行项目都在（消息总数 / AI 调用 / MCP 服务器 / 记忆条数 / 身份绑定）',
    ['消息总数', 'AI 调用', 'MCP 服务器', '记忆条数', '身份绑定'].every((t) => todayOff.includes(t)),
    todayOff.slice(0, 120));
  check('今日数据：没登录时每行都写「登录后显示」，不摆 0 也不留空',
    (todayOff.match(/登录后显示/g) || []).length === 5,
    String((todayOff.match(/登录后显示/g) || []).length));

  // 首页本体（纯 SSR：useEffect 不跑，所以不会真发请求）：
  // 顺序 = 三步条 → 平台四卡 → 今日数据 / 最近消息 → 机器人 / 当前任务
  const homeHtml = strip(renderToString(React.createElement(Overview, {
    health: { ok: true, version: '0.2.10', bot_port: 12113 },
    auth: {},
    services: { bot: { state: 'running', qq: 'connected', bot_dir: '/opt/x', log: '/opt/x/log' } },
    config: { ai_model: 'deepseek-chat', qq_app_id: '1905694838' },
    loggedIn: false,
    onAccess: () => {}, onMessages: () => {}, onServices: () => {}, onGo: () => {},
  })));
  // 「机器人」这几个字在别处也出现过（下一步引导、平台卡说明），所以拿标题标签定位。
  // ⚠ 2026-10-06 结构对齐：三步条改成桌面端那样的**一条横排**（不再有「三步就能用起来」这个标题），
  //   所以第一个定位标记由 `三步就能用起来` 换成条上的 `1 连 AI`；其余标记与顺序没动。
  const order = ['1 连 AI', 'QQ · 运行中', '微信 ·', '飞书 ·', '纸飞机 ·',
    '今日数据</h2>', '最近消息</h2>', '机器人</h2>', '当前任务</h2>'];
  const at = order.map((t) => homeHtml.indexOf(t));
  check('首页：区块顺序 = 三步条 → 平台四卡 → 今日数据/最近消息 → 机器人/当前任务',
    at.every((i) => i >= 0) && at.every((v, i) => i === 0 || v > at[i - 1]),
    JSON.stringify(at.map((i, k) => [order[k], i])));
  /* ⚠ 这条以前只断言 `AppID 1905694838` / `官方通道 已配置`，但上面这次渲染传的是
     loggedIn:false —— 而 configText()/configuredText() 对未登录的既定行为就是
     **不摆值**、统一说「登录后显示」（同一条规矩见上面「今日数据」那条断言）。
     也就是说这条断言从"未登录不摆值"那次改动起就一直红着（改前基线实测也是红的，
     与本次改动无关）。这里按真实意图补全：未登录说「登录后显示」，
     登录后必须真的把 AppID 与「已配置」摆出来 —— 比原来只测一种形态更严。 */
  const homeInHtml = strip(renderToString(React.createElement(Overview, {
    health: { ok: true, version: '0.2.10', bot_port: 12113 },
    auth: {},
    services: { bot: { state: 'running', qq: 'connected', bot_dir: '/opt/x', log: '/opt/x/log' } },
    config: { ai_model: 'deepseek-chat', qq_app_id: '1905694838' },
    loggedIn: true,
    onAccess: () => {}, onMessages: () => {}, onServices: () => {}, onGo: () => {},
  })));
  check('首页：QQ 卡两行明细（AppID + 官方通道）—— 未登录说「登录后显示」、不摆值',
    homeHtml.includes('AppID 登录后显示') && homeHtml.includes('官方通道 登录后显示'));
  check('首页：QQ 卡两行明细（AppID + 官方通道）—— 登录后摆真值与「已配置」',
    homeInHtml.includes('AppID 1905694838') && homeInHtml.includes('官方通道 已配置'));
  check('首页：微信卡写「iLink 私聊个人助手」', homeHtml.includes('iLink 私聊个人助手'));
  check('首页：飞书 / 纸飞机都写「未接入 / 规划中」',
    homeHtml.includes('飞书 · 未接入') && homeHtml.includes('纸飞机 · 未接入')
    && homeHtml.includes('规划中'));
  check('首页：标题与人话照桌面端（各平台服务状态总览 / 一键启停全部通道 / 点上面任意一条）',
    ['各平台服务状态总览，一键进入管理', '一键启停全部通道。', '点上面任意一条 → 进入消息中心',
      '去做插件', '启动全部', '停止全部', '重启'].every((t) => homeHtml.includes(t)));
  check('首页：输入框那句话仍在（一句话带进工坊）',
    homeHtml.includes('你想要什么功能？一句话，我让 AI 给你做一个插件'));

  // 工坊页：获取模型 / 先配生成模型 / 我做的插件（工坊生成的）等桌面端用词
  check('工坊页：有「获取模型」按钮（拉服务商真实模型列表）',
    wsPage.includes('获取模型'));
  check('工坊页：已装配件块叫「我做的插件（工坊生成的）」，含「刷新」「重启机器人生效」',
    ['我做的插件（工坊生成的）', '刷新', '重启机器人生效'].every((t) => wsPage.includes(t)));
  const wsRepair = strip(renderToString(React.createElement(WorkshopView, {
    ...wsProps,
    repairResult: { build_id: 'b1', name: '天气早报', description: '每天早八报天气', files: ['a.py'] },
  })));
  check('工坊页：AI 改的那一版给「安装到机器人」按钮', wsRepair.includes('安装到机器人'));
  const wsNotReady = strip(renderToString(React.createElement(WorkshopView, {
    ...wsProps, status: { ...wsProps.status, ready: false, why: '还没填密钥' },
  })));
  check('工坊页：没配模型时顶出「先配生成模型 / 配置生成模型」',
    wsNotReady.includes('先配生成模型') && wsNotReady.includes('配置生成模型')
    && wsNotReady.includes('先选服务商、填密钥，配好才能开始生成插件。'));
  check('工坊页：投稿区写清「上传的插件要人工审核，通过后才会上架。」',
    wsPage.includes('上传的插件要人工审核，通过后才会上架。')
    && wsPage.includes('点「发送验证码」，把邮箱收到的 6 位码填上。'));

  // QQ 官方卡：沙箱模式（测试环境）
  const sandboxCalls = [];
  const qqSandboxTree = QqOfficialView({
    status: { enabled: true, appid_set: true, secret_set: true, mode: 'at_only', groups: [], allowed_groups: [] },
    statusErr: '', form: {}, enabled: true, mode: 'at_only', edited: {}, busy: false,
    sandbox: false, sandboxNote: '服务器还没接这个开关：勾了只在本页生效。',
    onSandbox: (v) => sandboxCalls.push(v),
  });
  const sandboxHtml = strip(renderToString(React.createElement(QqOfficialView, {
    status: { enabled: true, appid_set: true, secret_set: true, mode: 'at_only', groups: [], allowed_groups: [] },
    statusErr: '', form: {}, enabled: true, mode: 'at_only', edited: {}, busy: false,
    sandbox: false, sandboxNote: '服务器还没接这个开关：勾了只在本页生效。',
    onSandbox: () => {},
  })));
  check('QQ 官方卡：有「沙箱模式（测试环境）」勾选项',
    sandboxHtml.includes('沙箱模式（测试环境）'));
  check('QQ 官方卡：服务器没这个开关时如实说，不装成已保存',
    sandboxHtml.includes('服务器还没接这个开关'));
  const sandboxBox = (function walk(n) {
    if (!n || typeof n !== 'object') return null;
    /* 2026-10-12：沙箱模式从裸复选框换成了统一开关（role="switch" + aria-checked），
       所以这里两种都认 —— 断言的意思没变（"点了要真的把新值交出去"），只是控件换了。 */
    if (n.props && typeof n.props.onChange === 'function'
      && ((n.type === 'input' && n.props.type === 'checkbox')
        || n.props.role === 'switch')) return n;
    if (n.props && n.props.checked === false && typeof n.props.onChange === 'function') return n;
    const kids = Array.isArray(n.props?.children) ? n.props.children : [n.props?.children];
    for (const k of kids) { const hit = walk(k); if (hit) return hit; }
    return null;
  }(qqSandboxTree));
  if (sandboxBox) sandboxBox.props.onChange(true);
  check('点「沙箱模式」→ 把新值交出去', sandboxCalls[0] === true, JSON.stringify(sandboxCalls));

  // 全局管理：DSH 智能体块 + 机器人那句说明（桌面端 services_page.py 同一个词）
  const svcHtml = strip(renderToString(React.createElement(ServicesPage, {
    config: { bot_port: 12113 },
    health: { ok: true, version: '0.2.10', machine_id: 'abcdef123456' },
    services: { bot: { state: 'running', bot_dir: '/opt/x', log: '/opt/x/log' }, dsh: { state: 'running' } },
    onAccess: () => {}, onLogs: () => {}, onDeps: () => {},
  })));
  check('全局管理：DSH 智能体块 + 「本机的群聊智能体通道」都在',
    svcHtml.includes('DSH 智能体') && svcHtml.includes('本机的群聊智能体通道'));
  check('全局管理：机器人那句说明与桌面端一字不差',
    svcHtml.includes('QQ / 微信 共用一个进程，重启会短暂离线约 10 秒'));

  /* ── 人设卡片：三块并成一张 + 胶囊切换（预设 / 自定义）───────────────────────
     老板原话：智能体档案 / 人设工坊（本地）/ 人设与主动聊天 功能重合、设置框离得远，
     要「整合到一个框里」+「胶囊按钮切换形态」。这一块把整合结果钉死：
       1) 只有一张「人设与主动聊天」卡（不再是三张）；
       2) 顶部是胶囊分段控件，两段：预设 / 自定义（底部胶囊 + 圆形滑块 data-pos 跟着走）；
       3) 切换即互斥：切「预设」要停用本地人设并写回配置；切「自定义」要关掉预设档案；
       4) 「当前生效：…」状态行两种形态都在，文案对得上；
       5) 主动聊天那几项跟人设来源无关，两种形态都显示。
     React 的点击在这里直接调 props.onClick（不依赖 DOM 事件系统），
     所以只给 React 补一份最小 DOM 替身就能既渲染、又真的点一次。 */
  function makeEl(tag) {
    const node = {
      tagName: String(tag || 'div').toUpperCase(),
      nodeType: 1,
      style: {},
      children: [],
      childNodes: [],
      parentNode: null,
      attributes: {},
      listeners: {},
      get firstChild() { return this.childNodes[0] || null; },
      appendChild(c) {
        if (c && c.parentNode) c.parentNode.removeChild(c);
        c.parentNode = this;
        this.childNodes.push(c);
        this.children.push(c);
        return c;
      },
      removeChild(c) {
        this.childNodes = this.childNodes.filter((x) => x !== c);
        this.children = this.children.filter((x) => x !== c);
        c.parentNode = null;
        return c;
      },
      insertBefore(c, before) {
        const i = before ? this.childNodes.indexOf(before) : -1;
        if (c.parentNode) c.parentNode.removeChild(c);
        c.parentNode = this;
        if (i < 0) { this.childNodes.push(c); this.children.push(c); } else {
          this.childNodes.splice(i, 0, c); this.children.splice(i, 0, c);
        }
        return c;
      },
      setAttribute(k, v) { this.attributes[k] = String(v); },
      removeAttribute(k) { delete this.attributes[k]; },
      addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
      removeEventListener(t, fn) { this.listeners[t] = (this.listeners[t] || []).filter((x) => x !== fn); },
      dispatchEvent() { return true; },
      // createRoot 会往容器上挂 _reactListening* 并读 document 做事件委托，这两个都要有
      _reactListening: {},
      ownerDocument: null,
      // <select> 挂载时 React 要读 node.options（HTMLOptionsCollection）；<textarea> 要读 value
      get options() {
        return this.childNodes.filter((c) => c && c.tagName === 'OPTION');
      },
      value: '',
      selected: false,
      disabled: false,
      set textContent(v) { this.childNodes = []; this.children = []; this._text = String(v); },
      get textContent() {
        return this._text !== undefined ? this._text
          : this.childNodes.map((c) => c.textContent || '').join('');
      },
    };
    node.ownerDocument = pwDoc;
    return node;
  }
  const pwDoc = {
    nodeType: 9,
    _reactListening: {},
    createElement: (t) => makeEl(t),
    createElementNS: (_ns, t) => makeEl(t),
    createTextNode: (v) => textNode(v),
    createComment: (v) => ({ nodeType: 8, nodeValue: String(v), parentNode: null }),
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return true; },
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementsByTagName: () => [],
  };
  pwDoc.documentElement = makeEl('html');
  pwDoc.body = makeEl('body');
  pwDoc.body.ownerDocument = pwDoc;
  pwDoc.activeElement = pwDoc.body;
  pwDoc.defaultView = globalThis;
  if (!globalThis.document) globalThis.document = pwDoc;
  const textNode = (v) => ({ nodeType: 3, nodeValue: String(v), textContent: String(v), parentNode: null });
  const pwWalk = (n) => {
    if (!n) return [];
    if (typeof n === 'string' || typeof n === 'number') return [];
    if (Array.isArray(n)) return n.flatMap(pwWalk);
    if (typeof n !== 'object') return [];
    if (n.props) return [n, ...pwWalk(n.props.children)];        // React 元素（renderToString）
    if (n.nodeType === 1) return [n, ...pwWalk(n.childNodes)];   // DOM 节点（真挂载后的树）
    return [];
  };
  const pwTextOf = (n) => {
    if (n == null) return '';
    if (n.props) {
      return (Array.isArray(n.props.children) ? n.props.children : [n.props.children])
        .map((c) => (typeof c === 'object' && c ? pwTextOf(c) : String(c))).join('');
    }
    return String(n.textContent || '');
  };
  const pwCls = (n) => String(
    (n.props && n.props.className)
    || (n.nodeType === 1 ? ((n.attributes || {}).class || '') : ''));
  const pwByClass = (tree, cls) => pwWalk(tree).filter(
    (n) => pwCls(n).split(/\s+/).includes(cls));
  const pwBtnByText = (tree, text) => pwWalk(tree).find(
    (n) => (n.type === 'button' || n.tagName === 'BUTTON')
      && pwTextOf(n).replace(/\s+/g, '') === text);

  const calls = [];
  /* 假后端是**有状态的**：停用本地人设之后，列表里那一条就不再是「启用中」——
     不然「切换后状态行改口」这条断言测不出真东西（服务端不会替你演）。 */
  let fakeActivePersona = 'com.demo.rose';
  const aiJson = (url, opts) => {
    const u = String(url);
    calls.push({ path: u.split('/api/')[1] || u, method: (opts && opts.method) || 'GET', body: (opts && opts.body) || '' });
    if (u.includes('/api/persona/list')) {
      return { ok: true, items: [{ id: 'com.demo.rose', name: '玫瑰', version: '1.0', active: fakeActivePersona === 'com.demo.rose', tools: [] }], active: fakeActivePersona, dir: '/opt/x/personas' };
    }
    if (u.includes('/api/ai/personality')) return { ok: true, personality: '你是玫瑰 冷静 简洁' };
    if (u.includes('/api/mcp/config')) return { ok: true, mcp: { enabled: false, servers: {} } };
    if (u.includes('/api/tools/status')) return { ok: true, installed: [{ id: 'ai' }] };
    if (u.includes('/api/persona/deactivate')) { fakeActivePersona = ''; return { ok: true, personality: '' }; }
    if (u.includes('/api/config')) {
      const body = JSON.parse((opts && opts.body) || '{}');
      if (body.local_persona_id === '') fakeActivePersona = '';
      return { ok: true, agent_profile_enabled: false, local_persona_id: '', agent_profile_id: 'star_helper', ...body };
    }
    return { ok: true };
  };
  globalThis.fetch = async (url, opts) => ({ ok: true, json: async () => aiJson(url, opts) });

  const host = makeEl('div');
  let aiRoot;
  await act(async () => {
    aiRoot = createRoot(host);
    aiRoot.render(React.createElement(AiBrain, {
      config: { agent_profile_enabled: false, agent_profile_id: 'star_helper', ai_platforms: {} },
      onSaved: () => {}, beginner: false, loggedIn: false,
    }));
    await Promise.resolve();
  });
  // AiBrain 的返回是个 Fragment（页头 + 顶部摘要 + 若干卡片 + toast），
  // 所以根级有好几个兄弟节点：要扫的是整个容器，不是第一个孩子。
  const brainTree = () => host.childNodes;
  check('人设卡片：组件挂上了（调 DOM 用）',
    brainTree().length >= 3, `top=${host.childNodes.length}`);
  // data-pos 是给 CSS 定位滑块用的属性：DOM 节点上读 attributes，React 元素上读 props
  const attrOf = (n, name) => (n.props ? n.props[name] : (n.attributes || {})[name]);
  /* 挂载之后的树是真 DOM 节点：React 把 props 挂在 __reactProps$<随机> 上（没有 .props）。
     这里取「点了会发生什么」用，比造一套合成事件更贴近真实行为。 */
  const propsOf = (n) => {
    if (!n) return {};
    if (n.props) return n.props;
    const k = Object.keys(n).find((x) => x.startsWith('__reactProps$'));
    return (k && n[k]) || {};
  };
  const brainHtml = () => strip(renderToString(React.createElement(AiBrain, {
    config: { agent_profile_enabled: false, agent_profile_id: 'star_helper', ai_platforms: {} },
    onSaved: () => {}, beginner: false, loggedIn: false,
  })));

  check('人设：三块并成一张卡（标题只剩一个「人设与主动聊天」，旧的两个标题都不在了）',
    (() => {
      const titles = pwByClass(brainTree(), 'card__title').map(pwTextOf);
      const withPill = pwWalk(brainTree()).filter(
        (n) => pwCls(n).split(/\s+/).includes('card') && pwByClass(n, 'seg__btn').length === 2);
      return titles.filter((t) => t === '人设与主动聊天').length === 1
        && !titles.includes('智能体档案') && !titles.includes('人设工坊（本地）')
        && withPill.length === 1;
    })(), pwByClass(brainTree(), 'card__title').map(pwTextOf).join('|'));

  const segButtons = pwByClass(brainTree(), 'seg__btn');
  /* 2026-10-12：这张卡里现在有**两个**胶囊分段控件 —— 人设来源（预设/自定义，2 段）
     和下面的记忆档位（无/基础/智能，seg--3）。这里要断的是"人设那个正好两段"，
     所以只取前两个（DOM 顺序：人设的 .seg 在记忆的 .seg--3 之前）。 */
  const personaSegButtons = segButtons.slice(0, 2);
  check('人设：顶部是胶囊分段控件，正好两段（预设 / 自定义）',
    personaSegButtons.length === 2
    && pwTextOf(personaSegButtons[0]) === '预设' && pwTextOf(personaSegButtons[1]) === '自定义',
    segButtons.map(pwTextOf).join('|'));
  const thumbs = pwByClass(brainTree(), 'seg__thumb');
  /* 同理：滑块只认带 data-pos 的那个（人设的）；记忆档位那个是内联 transform，没有 data-pos */
  const personaThumb = thumbs.filter((t) => attrOf(t, 'data-pos') != null);
  check('人设：胶囊里有圆形滑块，按已保存配置停在「自定义」那一侧（档案是关的 → data-pos=custom）',
    personaThumb.length === 1 && attrOf(personaThumb[0], 'data-pos') === 'custom'
    && attrOf(personaSegButtons[1], 'aria-selected') === 'true',
    JSON.stringify(personaThumb.map((t) => attrOf(t, 'data-pos'))) + ' / '
    + JSON.stringify(personaSegButtons.map((b) => attrOf(b, 'aria-selected'))));
  check('人设：状态行初始说的是本地人设《玫瑰》（列表里 active 的那条）',
    pwByClass(brainTree(), 'brain-status').map(pwTextOf).join('').includes('当前生效：本地人设《玫瑰》'),
    pwByClass(brainTree(), 'brain-status').map(pwTextOf).join(''));
  check('人设：预设态显示「启用档案」开关 + 档案选择，不显示本地人设列表',
    brainHtml().includes('启用档案') && brainHtml().includes('档案选择')
    && !brainHtml().includes('导入人设'));
  check('人设：主动聊天那几项两种形态都要有（预设态也有）',
    ['启用主动聊天（机器人会不定时主动找用户聊天）', '目标用户', '搭话群聊'].every((t) => brainHtml().includes(t)));

  calls.length = 0;
  await act(async () => { propsOf(segButtons[0]).onClick(); await Promise.resolve(); });
  const afterPreset = calls.slice();
  check('切换互斥：点「预设」会停用本地人设（POST /api/persona/deactivate）',
    afterPreset.some((c) => c.path === 'persona/deactivate' && c.method === 'POST'),
    JSON.stringify(afterPreset.map((c) => `${c.method} ${c.path}`)));
  const cfgPreset = afterPreset.filter((c) => c.path === 'config' && c.method === 'PUT');
  check('切换互斥：点「预设」把档案开关写回配置，并清掉本地人设 id',
    cfgPreset.length === 1
    && JSON.parse(cfgPreset[0].body || '{}').agent_profile_enabled === true
    && JSON.parse(cfgPreset[0].body || '{}').local_persona_id === '',
    cfgPreset.map((c) => c.body).join('|'));
  check('切换互斥：切过去有就地提示 + 状态行跟着变成预设档案',
    /已切到预设/.test(pwByClass(brainTree(), 'brain-pill-hint').map(pwTextOf).join(''))
    && pwByClass(brainTree(), 'brain-status').map(pwTextOf).join('').includes('当前生效：预设档案 星群助手'),
    pwByClass(brainTree(), 'brain-status').map(pwTextOf).join('') + ' / ' + pwByClass(brainTree(), 'brain-pill-hint').map(pwTextOf).join(''));
  check('切换互斥：滑块滑到「预设」那一侧（data-pos=preset）',
    attrOf(pwByClass(brainTree(), 'seg__thumb')[0], 'data-pos') === 'preset');
  check('切换互斥：切到预设后，卡片里显示「预设」那一段的内容（档案说明在、人格设定不在）',
    (() => {
      const panes = pwByClass(brainTree(), 'persona-pane');
      return panes.length === 1 && attrOf(panes[0], 'aria-label') === '预设';
    })());
  // SSR 不跑 effect（也就读不到人设列表）：档案开着时状态行就该说「预设档案 星群助手」。
  // 本地人设优先那一档由下面的纯函数断言盖住。
  const aiBrainPreset = strip(renderToString(React.createElement(AiBrain, {
    config: { agent_profile_enabled: true, agent_profile_id: 'star_helper', ai_platforms: {} },
    onSaved: () => {}, beginner: false, loggedIn: false,
  })));
  check('人设：档案开着时状态行 = 当前生效：预设档案 星群助手（SSR 直出，不靠 effect）',
    aiBrainPreset.includes('当前生效：预设档案 星群助手'),
    aiBrainPreset.slice(aiBrainPreset.indexOf('当前生效'), aiBrainPreset.indexOf('当前生效') + 40));
  const noProfileHtml = strip(renderToString(React.createElement(AiBrain, {
    config: { agent_profile_enabled: true, agent_profile_id: 'star_helper', ai_platforms: {} },
    onSaved: () => {}, beginner: false, loggedIn: false, profiles: [],
  })));
  check('人设：一个档案都没有时不摆空白，写「还没有下载档案，去「插件工坊」看看」',
    noProfileHtml.includes('还没有下载档案，去「插件工坊」看看')
    && !noProfileHtml.includes('档案选择'));
  check('人设：手写人设的状态行文案对（两态都没选就是手写）',
    effectivePersonaText({ agent_profile_enabled: false }, null, '星群助手') === '当前生效：手写人设'
    && effectivePersonaText({ agent_profile_enabled: true }, null, '李清菡') === '当前生效：预设档案 李清菡'
    && effectivePersonaText({ agent_profile_enabled: true }, { id: 'x', name: '阿星' }, '李清菡') === '当前生效：本地人设《阿星》');

  calls.length = 0;
  await act(async () => { propsOf(segButtons[1]).onClick(); await Promise.resolve(); });
  const afterCustom = calls.slice();
  const cfgCustom = afterCustom.filter((c) => c.path === 'config' && c.method === 'PUT');
  check('切换互斥：点「自定义」把档案开关关掉（agent_profile_enabled=false）',
    cfgCustom.length === 1 && JSON.parse(cfgCustom[0].body || '{}').agent_profile_enabled === false,
    cfgCustom.map((c) => c.body).join('|'));
  check('切换互斥：切到自定义不动本地人设列表接口（不发 activate/deactivate）',
    !afterCustom.some((c) => c.path === 'persona/activate' || c.path === 'persona/deactivate'),
    JSON.stringify(afterCustom.map((c) => `${c.method} ${c.path}`)));
  check('切换互斥：切到自定义后卡片里显示「自定义」那一段（导入人设按钮 + 人格设定都在）',
    (() => {
      const panes = pwByClass(brainTree(), 'persona-pane');
      if (panes.length !== 1 || attrOf(panes[0], 'aria-label') !== '自定义') return false;
      const texts = pwWalk(panes[0]);
      return texts.some((n) => (n.type === 'button' || n.tagName === 'BUTTON') && pwTextOf(n) === '导入人设')
        && texts.some((n) => n.type === 'textarea' || n.tagName === 'TEXTAREA');
    })(), JSON.stringify(pwByClass(brainTree(), 'brain-pill-hint').map(pwTextOf)));
  check('切换互斥：滑块滑回「自定义」那一侧（data-pos=custom），且提示同样是一句实话',
    attrOf(pwByClass(brainTree(), 'seg__thumb')[0], 'data-pos') === 'custom'
    && /已切到自定义/.test(pwByClass(brainTree(), 'brain-pill-hint').map(pwTextOf).join('')),
    attrOf(pwByClass(brainTree(), 'seg__thumb')[0], 'data-pos') + ' / '
    + pwByClass(brainTree(), 'brain-pill-hint').map(pwTextOf).join(''));

  calls.length = 0;
  await act(async () => { propsOf(segButtons[1]).onClick(); await Promise.resolve(); });
  check('切换互斥：已经关掉的档案开关不再重复写（没变化就不动服务器配置）',
    !calls.some((c) => c.path === 'config'),
    JSON.stringify(calls.map((c) => `${c.method} ${c.path}`)));
  await act(async () => { aiRoot.unmount(); });

  /* ── 文案与关键内容 ─────────────────────────────────────── */
  // 注意：这里原来还拼了 `prodHtml`（官网下载卡）——那块 2026-10-10 已整体删除，
  // 所以它也不再进这份"所有卡片文案"的合集；其余拼接项一个没动。
  const all = `${botAi}${qqCard}${block}${blockOut}${strip(withAvatar)}${stepHtml}${wsEmpty}${wsDone}${wsPage}`
    + `${feishuHtml}${tgHtml}${qqOffHtml}${lrHtml}${lbOff}${marketOk}${marketErr}${marketLoading}`
    + `${modalHtml}${formHtml}${stepEmail}${stepPw}${stepReg}`;
  mustHave.push(
    ['机器人 AI（群聊）', all],
    ['允许在群里回话（默认开）', all],
    ['引用、记录、图片一起给 AI 看（默认开）', all],
    ['允许在群里主动搭话（不定时插一句，默认关）', all],
    /* 2026-10-12：这四个数值从一行的裸 input 换成了统一控件（NumberField）——
       标签与单位分开了：「搭话概率(%)」现在是标签「搭话概率」+ 单位后缀「%」，
       而且整组被收进折叠区（标题行给当前值摘要）。断言按新结构写，含义不变：
       概率/间隔/参考条数这三样仍然在页面上看得见（展开折叠区就能改）。 */
    ['搭话概率', all],
    ['最短间隔', all],
    ['最长间隔', all],
    ['参考最近条数', all],
    ['搭话参数（4 项）', all],
    ['概率 30% · 间隔 30–90 分 · 参考 10 条', all],
    ['不限群（默认）', all],
    ['D596CCDD', all],
    ['收过 12 条', all],
    ['免 @ 连续对话', all],
    ['超时断开（推荐：说一会儿自动停）', all],
    ['QQ 官方通道 · 群消息与体检', all],
    ['仅 @ 消息（只在群里 @ 它时才收得到）', all],
    ['接收所有消息', all],
    ['官方通道体检', all],
    ['NoneBot 没在跑', all],
    ['diag-row--bad', all],
    // —— 本轮补齐的开关与卡片 ——
    ['能看图片和表情包（默认开）', all],
    ['不会的事自己写插件（默认开）', all],
    ['跨平台互通（默认开）', all],
    ['绑定状态', all],
    ['微信 iLink ↔ QQ 官方机器人', all],
    ['飞书 · 没装适配器', all],
    ['App Secret', all],
    ['装适配器', all],
    ['Verification Token', all],
    ['纸飞机', all],
    ['还在规划中', all],
    ['立刻清理旧日志', all],
    ['保留天数', all],
    ['未登录：机器人与通道的真实状态读不到', all],
    ['去登录', all],
    ['登录星群账号', all],
    ['用星群账号邮箱登录', all],
    // 两步分流（2026-10-10）：第一步邮箱 + 下一步；注册过才有密码框（「改邮箱」能退回）；
    // 没注册过才出现「获取验证码」+ 6 位验证码 +「注册并登录」。
    ['下一步', all],
    ['改邮箱', all],
    ['还没注册过', all],
    ['获取验证码', all],
    ['6 位验证码', all],
    ['注册并登录', all],
    ['官网市场', all],
    ['和桌面端插件页同一份清单', all],
    ['正在拉取官网市场', all],
    ['插件市场暂不可用', all],
  );
  for (const [needle, hay] of mustHave) {
    check(`应出现: ${needle}`, String(hay).includes(needle));
  }
  for (const needle of mustNotHave) {
    check(`不应出现: ${needle}`, !all.includes(needle));
  }

  /* ── 自动同步（2026-10-06 老板：任意一端改完，另一端别让我手动刷新）──────────────
     这里给 window/document 装一副替身，用 25ms 顶替 60 秒那一档：
     既能验"真的在跳"，又能验"该停的时候真停"。替身用完还回去。 */
  const realWindow = globalThis.window;
  const winOn = {};
  const docOn = {};
  const bag = (store) => ({
    addEventListener: (t, fn) => { (store[t] = store[t] || []).push(fn); },
    removeEventListener: (t, fn) => { store[t] = (store[t] || []).filter((x) => x !== fn); },
  });
  globalThis.window = { ...realWindow, ...bag(winOn) };
  globalThis.document = { visibilityState: 'visible', ...bag(docOn) };
  const fire = (store, type) => (store[type] || []).slice().forEach((fn) => fn());
  const settle = (ms = 80) => new Promise((r) => setTimeout(r, ms));

  let pollGets = 0;
  const answerWith = (profile) => {
    globalThis.fetch = async (url) => {
      if (String(url).includes('/api/account/profile')) {
        pollGets += 1;
        return { ok: true, json: async () => profile };
      }
      return { ok: true, json: async () => ({}) };
    };
  };
  const LIVE = {
    email: 'liguangxu17@163.com', nickname: 'Lihui', signature: '',
    display_name: 'Lihui', plan: 'permanent', avatar: '',
  };
  answerWith(LIVE);

  check('轮询间隔就是 60 秒', PROFILE_POLL_MS === 60000, String(PROFILE_POLL_MS));
  const stopWatch = watchProfile(true, 25);
  await settle();
  check('每 60 秒那一档真的在跳（这里 25ms 替身验）', pollGets >= 2, `GET ${pollGets} 次`);

  pollGets = 0;
  fire(winOn, 'focus');
  await settle(20);
  check('窗口重新获得焦点 → 立刻又读一次资料（在官网改完切回来就能看到）',
    pollGets >= 1, `GET ${pollGets} 次`);

  pollGets = 0;
  globalThis.document.visibilityState = 'hidden';
  fire(docOn, 'visibilitychange');
  await settle();
  check('页面藏起来 → 定时器停，后台一次请求都不发', pollGets === 0, `GET ${pollGets} 次`);

  pollGets = 0;
  globalThis.document.visibilityState = 'visible';
  stopWatch();                                   // 相当于组件卸载
  await settle();
  check('卸载（调一次停止函数）→ 定时器清掉、两个监听摘干净，之后不再发任何请求',
    pollGets === 0 && (winOn.focus || []).length === 0
    && (docOn.visibilitychange || []).length === 0,
    `GET ${pollGets} 次 / 剩 ${(winOn.focus || []).length}+${(docOn.visibilitychange || []).length} 个监听`);

  pollGets = 0;
  const stopAnon = watchProfile(false, 25);
  await settle();
  stopAnon();
  check('没登录时一次都不打（不白报 401）', pollGets === 0, `GET ${pollGets} 次`);

  /* 后台那次读失败必须静默：界面还是上一次的昵称与头像，不冒错、不白屏 */
  answerWith(LIVE);
  await loadProfile();
  globalThis.fetch = async () => { throw new Error('Failed to fetch'); };
  await loadProfile({ silent: true });           // 就是轮询/焦点那一路
  const kept = strip(renderToString(React.createElement(AccountProfileBlock, {
    loggedIn: true, onMsg: () => {},
  })));
  check('后台那次读失败：保留上一次的资料，不冒错、不白屏',
    kept.includes('Lihui') && kept.includes('acct-avatar--fallback')
    && !kept.includes('acct-card__err') && !/读取中|HTTP \d|Failed to fetch/.test(kept));

  globalThis.window = realWindow;
  delete globalThis.document;

  /* ── 统一控件库（2026-10-12）─────────────────────────────────────────
     这批断言锁的是"老板提的三件事"：开关不再是裸复选框、按钮是胶囊、数值有单位与范围。
     它们渲染成 HTML 之后必须带这些属性/类名 —— 以后谁把控件换回裸控件，这里先红。 */
  const tglOn = renderToString(React.createElement(Toggle, {
    checked: true, onChange: () => {}, ariaLabel: '测试开关',
  }));
  const tglOff = renderToString(React.createElement(Toggle, {
    checked: false, onChange: () => {}, ariaLabel: '测试开关',
  }));
  check('开关：role="switch" + aria-checked 两态都标出来',
    tglOn.includes('role="switch"') && tglOn.includes('aria-checked="true"')
    && tglOff.includes('aria-checked="false"'));
  check('开关：不再是 input[type=checkbox]（老板嫌"太 low"的就是它）',
    !tglOn.includes('type="checkbox"') && tglOn.includes('tgl__track') && tglOn.includes('tgl__thumb'));

  const tglRow = strip(renderToString(React.createElement(ToggleRow, {
    checked: true, onChange: () => {}, label: '允许主动搭话', desc: '它自己会找群里的人说话',
  })));
  check('开关行：标签 + 一句说明都在，且说明用 aria-describedby 关联给读屏',
    tglRow.includes('允许主动搭话') && tglRow.includes('它自己会找群里的人说话')
    && /aria-describedby="[^"]+"/.test(tglRow) && /id="[^"]+-desc"/.test(tglRow));

  const pill = renderToString(React.createElement(PillButton, {
    tone: 'primary', size: 'sm', children: '保存',
  }));
  check('胶囊按钮：类名带 pillbtn + 尺寸档（圆角=高度一半由 CSS 保证）',
    pill.includes('pillbtn') && pill.includes('pillbtn--primary') && pill.includes('pillbtn--sm'));
  const pillLoading = renderToString(React.createElement(PillButton, {
    loading: true, children: '保存中',
  }));
  check('胶囊按钮：loading 时 aria-busy=true 且文字还在（宽度不跳）',
    pillLoading.includes('aria-busy="true"') && pillLoading.includes('保存中'));

  const col = renderToString(React.createElement(Collapse, {
    title: '主动聊天频率（5 项）', summary: '间隔 30–90 分', children: React.createElement('span', null, '内容'),
  }));
  check('折叠区：默认收起（aria-expanded=false）+ 标题行带摘要',
    col.includes('aria-expanded="false"') && col.includes('aria-controls=')
    && col.includes('主动聊天频率（5 项）') && col.includes('间隔 30–90 分'));

  const nf = strip(renderToString(React.createElement(NumberField, {
    label: '搭话概率', value: 30, min: 1, max: 100, unit: '%', hint: '每条够格的消息有多大概率插一句',
  })));
  check('数值输入：不是裸 input[type=number]，带标签与单位后缀、范围提示 + 说明两行都给',
    !nf.includes('type="number"') && nf.includes('搭话概率') && nf.includes('numf__unit')
    && nf.includes('范围 1 ~ 100 %') && nf.includes('每条够格的消息有多大概率插一句'));
  const nfChoice = strip(renderToString(React.createElement(NumberField, {
    label: '只 @ 不写字时', value: 'greet',
    options: [{ value: 'greet', label: '轻唤一句' }, { value: 'silent', label: '不回复' }],
  })));
  check('数值输入：给档位时出档位胶囊（能选档位的就不让人填数字）',
    nfChoice.includes('numf__opt') && nfChoice.includes('轻唤一句') && nfChoice.includes('不回复')
    && nfChoice.includes('role="radio"'));

  console.log(bad ? `冒烟失败：${bad} 项` : '冒烟通过：卡片视图 + 账号头像块 + 统一控件库都能正常渲染 ✓');
  process.exit(bad ? 1 : 0);
}

main().catch((e) => {
  console.error('冒烟崩了：', e);
  process.exit(1);
});
