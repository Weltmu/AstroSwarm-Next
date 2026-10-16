import { useEffect, useState } from 'react';

import { apiErrText, get, put } from './api';
import { ToggleRow } from './components/Toggle.jsx';
import QqChannelCard from './QqChannelCard';

/* QQ 官方机器人（QQ 开放平台）卡 —— 桌面端 QQ 页的「接入方式 = 官方机器人」那一半。
   控制台以前只有第三方 OneBot 那一半：官方机器人连 AppID / AppSecret 都没地方填。
   这里补齐凭证、开关、收消息模式、群名单，并把「官方通道体检」原样放在同一张卡里
   （体检组件继续用 QqChannelCard：群消息模式 + 一键体检，一行代码没改）。

   后端契约（照抄，别自己发明字段）：
     GET /api/qq/official → {enabled, appid_set, secret_set, mode, groups, allowed_groups}
     PUT /api/qq/official ← {enabled?, appid?, secret?, mode?}
                          → {ok, changed, needs_restart}
   接口还没上时（404）显示「暂不可用」，不白屏 —— 统一走 apiErrText。 */

/* 收消息模式：和「群消息」那块的用词保持同一套（全量 / 仅 @）。
   后端回别的值时按原样显示，不硬塞成这两项 —— 免得把真实状态说错。 */
export const QQ_OFFICIAL_MODES = [
  ['full', '接收所有消息（群里全部聊天）'],
  ['at_only', '仅 @ 消息（只在被 @ 时收）'],
];

export function qqOfficialBadge(status, statusErr) {
  if (statusErr) return { tone: 'unknown', label: 'QQ 官方 · 状态读不到' };
  if (!status) return { tone: 'unknown', label: 'QQ 官方 · 状态读取中' };
  if (!status.appid_set || !status.secret_set) return { tone: 'stopped', label: 'QQ 官方 · 没填凭证' };
  if (!status.enabled) return { tone: 'warn', label: 'QQ 官方 · 凭证已填，开关没开' };
  return { tone: 'running', label: 'QQ 官方 · 已开启' };
}

/* 群名单的形状（后端已确认，别猜）：groups / allowed_groups 都是**字符串数组**，
   元素是群的 openid。这里顺手也认对象数组（老数据 / 别处复用），字符串是正常路径。 */
export function groupRows(list) {
  return (Array.isArray(list) ? list : [])
    .map((g) => {
      if (g && typeof g === 'object') {
        return {
          id: String(g.openid || g.id || g.group_id || g.key || ''),
          count: Number(g.count || 0),
          ts: Number(g.last_ts || g.ts || 0),
        };
      }
      return { id: String(g == null ? '' : g), count: 0, ts: 0 };
    })
    .filter((r) => r.id);
}

function shortTime(ts) {
  const v = Number(ts || 0);
  if (!v) return '';
  const d = new Date(v * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function QqOfficialCard({ onMsg = () => {} }) {
  const [status, setStatus] = useState(null);
  const [statusErr, setStatusErr] = useState('');
  const [form, setForm] = useState({ appid: '', secret: '' });
  const [enabled, setEnabled] = useState(false);
  const [mode, setMode] = useState('');
  const [edited, setEdited] = useState({});
  const [busy, setBusy] = useState(false);
  /* 沙箱模式（测试环境）：桌面端存在 settings.qq_official_sandbox，机器人侧读它写 QQ_IS_SANDBOX。
     无头端的无头配置表里暂时还没有这个键 —— 所以：读不到就写一句实话，写不进去也写一句实话，
     界面上绝不显示成"已经存上了"。后端把这个键加进 SAFE_KEYS 后这里会自动开始正常保存。 */
  const [sandbox, setSandbox] = useState(false);
  const [sandboxNote, setSandboxNote] = useState('');

  const load = () => {
    get('/api/qq/official')
      .then((d) => {
        setStatus(d);
        setStatusErr('');
        setEnabled(Boolean(d.enabled));
        setMode(String(d.mode || ''));
        setForm({ appid: '', secret: '' });
        setEdited({});
      })
      .catch((e) => { setStatus(null); setStatusErr(apiErrText(e)); });
    get('/api/config')
      .then((c) => {
        if (typeof c?.qq_official_sandbox === 'boolean') {
          setSandbox(c.qq_official_sandbox);
          setSandboxNote('');
        } else {
          setSandbox(false);
          setSandboxNote('服务器还没接这个开关：勾了只在本页生效，要用沙箱请先在桌面端打开。');
        }
      })
      .catch(() => setSandboxNote('沙箱模式的当前值读不到（要登录）。'));
  };
  useEffect(load, []);

  const saveSandbox = async (v) => {
    setSandbox(Boolean(v));
    try {
      const saved = await put('/api/config', { qq_official_sandbox: Boolean(v) });
      if (saved && saved.qq_official_sandbox !== undefined) {
        setSandboxNote(v ? '已开沙箱模式，重启机器人后生效。' : '已关沙箱模式，重启机器人后生效。');
      } else {
        setSandboxNote('服务器还没接这个开关：本次只在本页生效。');
      }
    } catch (e) {
      setSandboxNote(`沙箱模式没写进服务器（${e.message}）。`);
    }
  };

  const mark = (key, value) => {
    setEdited((prev) => ({ ...prev, [key]: value }));
  };

  const save = async () => {
    setBusy(true);
    try {
      const payload = {};
      if (edited.enabled !== undefined) payload.enabled = Boolean(edited.enabled);
      if (edited.appid !== undefined) payload.appid = String(form.appid || '');
      if (edited.secret !== undefined) payload.secret = String(form.secret || '');
      if (edited.mode !== undefined) payload.mode = String(mode || '');
      if (!Object.keys(payload).length) {
        onMsg('没有改动要保存。');
        return;
      }
      const r = await put('/api/qq/official', payload);
      /* mode 在服务端是只读的（收消息模式由 QQ 开放平台那边决定），传了会回
         mode_ignored: true + note —— 这是**正常返回**，不是错误，照后端的话说一句就行。 */
      if (r && r.mode_ignored) {
        onMsg(String(r.note || '收消息模式以 QQ 开放平台为准，这里改了不生效'));
      } else {
        const changed = (r && r.changed) || [];
        onMsg(changed.length
          ? `QQ 官方配置已保存 ${changed.length} 项，重启机器人后生效`
          : '服务器说没有需要改的');
      }
      load();
    } catch (e) {
      onMsg(apiErrText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <QqOfficialView
      status={status}
      statusErr={statusErr}
      form={form}
      enabled={enabled}
      mode={mode}
      edited={edited}
      busy={busy}
      sandbox={sandbox}
      sandboxNote={sandboxNote}
      onSandbox={saveSandbox}
      onToggle={(v) => { setEnabled(v); mark('enabled', v); }}
      onMode={(v) => { setMode(v); mark('mode', v); }}
      onField={(k, v) => { setForm((f) => ({ ...f, [k]: v })); mark(k, v); }}
      onSave={save}
      onReload={load}
      diagnose={<QqChannelCard onMsg={onMsg} />}
    />
  );
}

/* 纯视图：只吃数据 + 回调，不自己取数 —— 能被直接渲染做冒烟（见 _smoke/smoke.jsx）。 */
export function QqOfficialView({
  status, statusErr, form = {}, enabled = false, mode = '', edited = {}, busy,
  sandbox = false, sandboxNote = '', onSandbox = () => {},
  onToggle = () => {}, onMode = () => {}, onField = () => {},
  onSave = () => {}, onReload = () => {}, diagnose = null,
}) {
  const badge = qqOfficialBadge(status, statusErr);
  const groups = groupRows(status && status.groups);
  const allowed = groupRows(status && status.allowed_groups);
  const allowedSet = new Set(allowed.map((g) => g.id));
  const knownModes = QQ_OFFICIAL_MODES.map(([v]) => v);
  // 后端回了没见过的值：照样列出来，别把真实状态说成「仅 @ 消息」
  const modes = mode && !knownModes.includes(mode)
    ? [...QQ_OFFICIAL_MODES, [mode, `当前：${mode}`]]
    : QQ_OFFICIAL_MODES;

  return (
    <>
      <section className="card card--wide">
        <span className={`badge badge--${badge.tone}`}>
          <i className="badge__dot" />
          <span className="badge__label">{badge.label}</span>
        </span>

        <div className="stat-row">
          <span>凭证</span>
          <b>
            {status == null
              ? (statusErr || '读取中…')
              : (status.appid_set && status.secret_set)
                ? 'AppID / AppSecret 都填了'
                : '还没填 AppID / AppSecret'}
          </b>
        </div>
        {statusErr && <p className="card__note">{statusErr}</p>}

        {/* 「启用 QQ 官方机器人」= 关键开关（关掉整个官方通道就断了）→ 常显，不折。
            下面那些凭证/模式/沙箱也是"改一次就不动"的低频项，但它们是**会出事故**的
            （填错就连不上、沙箱忘关会发错环境），所以一律常显，不折进折叠区。 */}
        <ToggleRow
          checked={Boolean(enabled)}
          label="启用 QQ 官方机器人"
          desc="关掉后官方通道立刻停收消息（第三方 OneBot 不受影响）。"
          onChange={onToggle}
        />

        {/* 沙箱模式（测试环境）：与桌面端 QQ 页同一个勾选项（qq_official_sandbox → QQ_IS_SANDBOX）。
            服务器的无头配置表里暂时还没有这个键，所以保存后要**看返回值里有没有它**：
            没有就照实说"只在本机生效"，绝不显示成已存上。 */}
        <ToggleRow
          checked={Boolean(sandbox)}
          disabled={busy}
          label="沙箱模式（测试环境）"
          desc="官方机器人测试环境用的通道，正式上线要关掉。"
          onChange={onSandbox}
        />
        {sandboxNote && <p className="card__note">{sandboxNote}</p>}

        <div className="kv-row">
          <span>AppID</span>
          <input
            className="mono"
            value={form.appid || ''}
            spellCheck={false}
            placeholder={status && status.appid_set ? '已填好，留空 = 不改' : 'q.qq.com 开发设置里的 AppID'}
            title="q.qq.com → 开发设置 → AppID"
            onChange={(e) => onField('appid', e.target.value)}
          />
        </div>
        <div className="kv-row">
          <span>AppSecret</span>
          <input
            className="mono"
            type="password"
            value={form.secret || ''}
            spellCheck={false}
            placeholder={status && status.secret_set ? '已填好，留空 = 不改' : 'QQ 开放平台里的 AppSecret'}
            title="留空 = 不改，不会覆盖服务器上的值"
            onChange={(e) => onField('secret', e.target.value)}
          />
        </div>

        <div className="kv-row">
          <span>收消息模式</span>
          <select
            className="kv-select--ws"
            value={mode}
            disabled={!status}
            title="@ 它没反应时先看这里"
            onChange={(e) => onMode(e.target.value)}
          >
            {modes.map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        </div>
        <p className="card__note">
          {`模式以 QQ 开放平台为准，这里只看。
改完重启生效；那边还要开「接收所有消息」权限。`}
        </p>

        <h3 className="card__subtitle">群名单</h3>
        {groups.length === 0 ? (
          <p className="card__note">还没记录到群。拉进群并 @ 它一次就有了。</p>
        ) : (
          <>
            <p className="card__note">
              {allowed.length === 0
                ? `不限群（已记录 ${groups.length} 个）`
                : `只允许其中 ${allowed.length} 个（已记录 ${groups.length} 个）`}
            </p>
            <div className="group-list">
              {groups.map((g) => (
                <div className="group-list__row" key={g.id} title={g.id}>
                  <span className="mono">{g.id.slice(0, 8)}…</span>
                  <span className="group-list__meta">
                    {allowedSet.has(g.id) ? '在名单里' : '不在名单里'}
                    {g.ts ? ` · 最近 ${shortTime(g.ts)}` : ''}
                    {g.count ? ` · 收过 ${g.count} 条` : ''}
                  </span>
                </div>
              ))}
            </div>
            <p className="card__note">
              改哪些群能回话：AI 大脑 → 允许回话的群。
            </p>
          </>
        )}

        <div className="btn-row btn-row--6">
          <button type="button" className="btn btn--primary" disabled={busy} onClick={onSave}>
            {busy ? '正在保存…' : '保存官方配置'}
          </button>
          <button type="button" className="btn btn--ghost" onClick={onReload}>
            重新读取
          </button>
          {Object.keys(edited).length > 0 && (
            <span className="card__note">有改动没保存</span>
          )}
        </div>
      </section>

      {/* 群消息模式 + 官方通道体检：和桌面端 QQ 页那张状态卡同一套结论 */}
      {diagnose}
    </>
  );
}
