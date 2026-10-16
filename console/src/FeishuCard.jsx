import { useEffect, useState } from 'react';

import { apiErrText, get, post, put } from './api';
import { ToggleRow } from './components/Toggle.jsx';

/* 飞书通道卡（新增）：与桌面端 feishu_page.py 同一件事 —— 凭证能填、能存，
   「装适配器」有按钮和安装日志，状态徽标说实话。
   ⚠ 徽标绝不说"已接入"：适配器没装 / 凭证没填，都照实写，见 feishuBadge()。

   后端契约（照抄，别自己发明字段）：
     GET  /api/feishu/status            → {enabled, app_id_set, app_secret_set,
                                            adapter_installed, ready, why}
     PUT  /api/feishu/config            ← {enabled?, app_id?, app_secret?,
                                            encrypt_key?, verification_token?}
                                          空串 = 不改；'__clear__' = 清空
                                        → {ok, changed, needs_restart}
     POST /api/feishu/install-adapter   → {ok, log:[...]}
   接口还没上时（404）显示「暂不可用」，不白屏 —— 统一走 apiErrText。 */

/* 状态徽标：四种诚实结论，顺序不能换。
   1) 凭证没填全 → 没填凭证（别的都谈不上）
   2) 适配器没装 → 没装适配器
   3) 开关没开   → 凭证已填，开关没开
   4) ready      → 已就绪（重启机器人后连上）
   导出给冒烟直接测，不用渲染整个卡片。 */
export function feishuBadge(status, statusErr) {
  if (statusErr) return { tone: 'unknown', label: '飞书 · 状态读不到' };
  if (!status) return { tone: 'unknown', label: '飞书 · 状态读取中' };
  const filled = Boolean(status.app_id_set) && Boolean(status.app_secret_set);
  if (!filled) return { tone: 'stopped', label: '飞书 · 没填凭证' };
  if (!status.adapter_installed) return { tone: 'warn', label: '飞书 · 没装适配器' };
  if (!status.enabled) return { tone: 'warn', label: '飞书 · 凭证已填，开关没开' };
  if (status.ready) return { tone: 'running', label: '飞书 · 已就绪（重启机器人后连上）' };
  return { tone: 'warn', label: '飞书 · 还不能用' };
}

const EMPTY_FORM = { app_id: '', app_secret: '', encrypt_key: '', verification_token: '' };

export default function FeishuCard() {
  const [status, setStatus] = useState(null);
  const [statusErr, setStatusErr] = useState('');
  const [form, setForm] = useState(EMPTY_FORM);
  const [edited, setEdited] = useState({});
  const [busy, setBusy] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [log, setLog] = useState([]);
  const [msg, setMsg] = useState('');

  const load = () => {
    get('/api/feishu/status')
      .then((d) => { setStatus(d); setStatusErr(''); })
      .catch((e) => { setStatus(null); setStatusErr(apiErrText(e)); });
  };
  useEffect(load, []);

  const field = (key, value) => {
    setForm((f) => ({ ...f, [key]: value }));
    setEdited((prev) => ({ ...prev, [key]: true }));
  };
  /* 清空用 '__clear__' 这个哨兵值（空串的语义是"不改"）。
     点了先在本地标记，保存时才发给后端 —— 免得手滑点一下就把服务器上的值清了。 */
  const clear = (key) => {
    setForm((f) => ({ ...f, [key]: '' }));
    setEdited((prev) => ({ ...prev, [key]: '__clear__' }));
    setMsg(`「${key}」已标记清空，点「保存」才真的生效。`);
  };

  const save = async () => {
    setBusy(true);
    try {
      const payload = {};
      if (edited.enabled !== undefined) payload.enabled = Boolean(edited.enabled);
      Object.keys(EMPTY_FORM).forEach((k) => {
        if (!edited[k]) return;                      // 没动过的键一个都不发
        payload[k] = edited[k] === '__clear__' ? '__clear__' : String(form[k] || '');
      });
      if (!Object.keys(payload).length) {
        setMsg('没有改动要保存。');
        return;
      }
      const r = await put('/api/feishu/config', payload);
      const changed = (r && r.changed) || [];
      setMsg(changed.length
        ? `已保存 ${changed.length} 项，重启机器人后生效`
        : '服务器说没有需要改的');
      setEdited({});
      setForm((f) => ({ ...f, app_secret: '', encrypt_key: '', verification_token: '' }));
      load();
    } catch (e) {
      setMsg(apiErrText(e));
    } finally {
      setBusy(false);
    }
  };

  const install = async () => {
    setInstalling(true);
    setLog(['正在安装飞书适配器…（要联网，约十几秒）']);
    try {
      const r = await post('/api/feishu/install-adapter', {});
      setLog((r && r.log) || []);
      setMsg(r && r.ok
        ? '适配器装好了，重启机器人后生效'
        : '适配器没装上，检查网络后重试');
      load();
    } catch (e) {
      setLog([]);
      setMsg(apiErrText(e));
    } finally {
      setInstalling(false);
    }
  };

  const toggle = (on) => {
    setForm((f) => ({ ...f, enabled: on }));
    setEdited((prev) => ({ ...prev, enabled: on }));
  };

  return (
    <FeishuView
      status={status}
      statusErr={statusErr}
      form={form}
      edited={edited}
      busy={busy}
      installing={installing}
      log={log}
      msg={msg}
      onField={field}
      onClear={clear}
      onToggle={toggle}
      onSave={save}
      onInstall={install}
      onReload={load}
    />
  );
}

/* 纯视图：只吃数据 + 回调，不自己取数 —— 能被直接渲染做冒烟（见 _smoke/smoke.jsx）。 */
export function FeishuView({
  status, statusErr, form = EMPTY_FORM, edited = {}, busy, installing, log = [], msg = '',
  onField = () => {}, onClear = () => {}, onToggle = () => {},
  onSave = () => {}, onInstall = () => {}, onReload = () => {},
}) {
  const badge = feishuBadge(status, statusErr);
  const enabled = edited.enabled !== undefined
    ? Boolean(edited.enabled)
    : Boolean(status && status.enabled);
  const filled = Boolean(status && status.app_id_set && status.app_secret_set);

  return (
    <section className="card card--wide">
      <span className={`badge badge--${badge.tone}`}>
        <i className="badge__dot" />
        <span className="badge__label">{badge.label}</span>
      </span>

      <div className="stat-row">
        <span>适配器</span>
        <b>
          {status == null
            ? (statusErr || '读取中…')
            : status.adapter_installed ? '已装好' : '没装（点下面「装适配器」）'}
        </b>
      </div>
      <div className="stat-row">
        <span>凭证</span>
        <b>
          {status == null
            ? (statusErr || '读取中…')
            : filled ? 'AppID / AppSecret 都填了' : '还没填 AppID / AppSecret'}
        </b>
      </div>
      {statusErr && <p className="card__note">{statusErr}</p>}
      {status && status.why && <p className="card__note">{status.why}</p>}

      <ToggleRow
        checked={enabled}
        label="启用飞书通道（凭证齐全并装好适配器后生效）"
        desc="关掉后飞书消息不进 AI，凭证仍保留在服务器上。"
        onChange={onToggle}
      />

      <div className="kv-row">
        <span>App ID</span>
        <input
          className="mono"
          value={form.app_id || ''}
          spellCheck={false}
          placeholder="cli_ 开头的那串"
          title="开发者后台的 App ID"
          onChange={(e) => onField('app_id', e.target.value)}
        />
      </div>
      <div className="kv-row">
        <span>App Secret</span>
        <input
          className="mono"
          type="password"
          value={form.app_secret || ''}
          spellCheck={false}
          placeholder="留空 = 不改"
          onChange={(e) => onField('app_secret', e.target.value)}
        />
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          title="把服务器上的 App Secret 清掉（点「保存」才生效）"
          onClick={() => onClear('app_secret')}
        >
          清除
        </button>
      </div>
      <div className="kv-row">
        <span>Encrypt Key</span>
        <input
          className="mono"
          type="password"
          value={form.encrypt_key || ''}
          spellCheck={false}
          placeholder="留空 = 不改（可留空不填）"
          onChange={(e) => onField('encrypt_key', e.target.value)}
        />
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          title="把服务器上的 Encrypt Key 清掉（点「保存」才生效）"
          onClick={() => onClear('encrypt_key')}
        >
          清除
        </button>
      </div>
      <div className="kv-row">
        <span>Verification Token</span>
        <input
          className="mono"
          type="password"
          value={form.verification_token || ''}
          spellCheck={false}
          placeholder="留空 = 不改（可留空不填）"
          onChange={(e) => onField('verification_token', e.target.value)}
        />
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          title="把服务器上的 Verification Token 清掉（点「保存」才生效）"
          onClick={() => onClear('verification_token')}
        >
          清除
        </button>
      </div>
      <p className="card__note">
        留空 = 不改；点「清除」再保存才清掉。
      </p>

      <div className="btn-row btn-row--6">
        <button type="button" className="btn btn--primary" disabled={busy} onClick={onSave}>
          {busy ? '正在保存…' : '保存'}
        </button>
        <button type="button" className="btn" disabled={installing} onClick={onInstall}>
          {installing ? '正在装适配器…' : '装适配器'}
        </button>
        <button type="button" className="btn btn--ghost" onClick={onReload}>
          重新读取
        </button>
      </div>

      {log.length > 0 && (
        <pre className="install-log">{log.join('\n')}</pre>
      )}

      <p className="card__note">
        {`① 飞书开放平台建应用；② 加「机器人」能力；
③ 开消息收发权限；④ 事件订阅选「长连接」；
⑤ 填 App ID / App Secret；⑥ 发布等管理员通过。`}
      </p>
      {msg && <p className="card__note">{msg}</p>}
    </section>
  );
}
