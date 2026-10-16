import { useCallback, useEffect, useRef, useState } from 'react';
import { apiErrText, apiUrl, authUrl, get, post, put } from './api.js';
import MemoryTimeline from './MemoryTimeline.jsx';
import KnowledgeBase from './KnowledgeBase.jsx';
import { RecentMessages, StatsPanel, TodayStats } from './BrainStats.jsx';
import AccountAvatar from './AccountAvatar.jsx';
import AccountProfileBlock from './AccountProfileBlock.jsx';
import BotAiCard from './BotAiCard.jsx';
import WorkshopPage from './WorkshopPage.jsx';
import ProductDownloads from './ProductDownloads.jsx';
import SetupStepsView, { setupSteps } from './SetupSteps.jsx';
import { ADVANCED_PAGES, NAV_GROUPS } from './nav.js';
import { versionLabel } from './version.js';
import { setIdea } from './workshopIdea.js';
import { ensureProfile, useProfile } from './profile.js';
import QqChannelCard from './QqChannelCard.jsx';
import QqOfficialCard from './QqOfficialCard.jsx';
import FeishuCard, { feishuBadge } from './FeishuCard.jsx';
import TelegramCard from './TelegramCard.jsx';
import LogRetention from './LogRetention.jsx';
import LoginBanner from './LoginBanner.jsx';
import MarketPanel from './MarketPanel.jsx';
import ZipInstall from './ZipInstall.jsx';
import {
  APPEARANCE_DEFAULTS,
  applyAppearance,
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

/** 写剪贴板：clipboard API 在非安全上下文（http 且非 localhost）不可用，兜底用 execCommand。 */
async function copyToClipboard(text) {
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

/**
 * 「打开插件目录 / 打开日志目录」在网页里没有对应动作（浏览器打不开服务器上的目录）。
 * 按钮名照桌面端，点一下就把路径给你并复制好 —— 用 SSH / SFTP 打开的就是这个目录。
 */
async function openServerPath(setMsg, what, makePath) {
  try {
    const p = await makePath();
    await copyToClipboard(p);
    setMsg(`${what}：${p}（服务器上的目录，已复制，用 SSH / SFTP 打开）`);
  } catch (e) {
    setMsg(`${what}打不开、路径也没取到：${e.message}`);
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
   运行 = 连通道看健康；能力 = 人