import { useEffect, useState } from 'react';
import { get, post } from './api.js';

/* 「设置 → 账号 → 控制台密码」卡片（2026-10-10 老板要求：
   "再在设置里做个登录web密码设置"）。

   和密码屏（ConsoleLogin.jsx）的分工：
     · 密码屏 = 进控制台**之前**那道门（没会话就只显示它，别的都看不到）；
     · 这张卡 = 进来**之后**改密码 / 锁屏的地方（不然密码设错了就只能去服务器上删文件）。

   两条形态由后端 /api/console/session 的 need_setup 决定：
     · need_setup=true  →「设置控制台密码」：两次输入 + 至少 8 位；
     · 已设过           →「修改控制台密码」：当前密码 + 新密码 + 确认新密码。

   安全底线（和后端 console_session.py 一套口径）：
     · 三个框都是 type="password"，**不回显、不预填、不改写成明文**；
     · 提交后立刻把 state 里的明文清掉（成功和失败都清）；
     · 错误只显示后端给的中文原因（"当前密码不对" / "密码至少 8 位" / 429 限流那句），
       前端不自己编一套说法，也不把原始 HTTP 码甩给用户；
     · 明文永不进 URL、永不进 localStorage（只走 POST body）。

   拆成 Card（状态 + 取数）/ View（纯渲染）两层，是为了能像别的卡一样在
   _smoke/smoke.jsx 里直接喂假数据渲染断言（本项目一直这么做）。 */

/** 后端返回的中文原因直接给用户看；网络类错误翻成人话。 */
export function consolePasswordErrText(e, fallback = '没改成，请重试') {
  const m = String((e && e.message) || e || '').trim();
  if (!m) return fallback;
  if (/尝试过多|429/.test(m)) return '尝试次数太多了，请等 1 分钟再试';
  if (/Failed to fetch|NetworkError|Load failed|not defined/i.test(m)) {
    return '连不上服务器：这台机器上的控制台服务可能没在跑';
  }
  return m;
}

/** 提交前的本地校验：返回中文原因，通过返回空串。
 *
 *  只拦"不用问服务器就知道错"的几条（空、太短、两次不一致、缺当前密码），
 *  **规则以后端为准**：常见密码 / 纯数字 / 键盘顺排 / 和旧密码相同这些只有后端知道，
 *  它返回的中文原因由 consolePasswordErrText 原样显示出来。
 *  纯函数（不碰 state），所以冒烟里能直接断言每一条。 */
export function consolePasswordCheck({ needSetup, oldPw, newPw, newPw2 }) {
  if (!newPw) return '请先填新密码';
  if (String(newPw).length < 8) return '密码至少 8 位';
  if (newPw !== newPw2) return '两次输入的新密码不一样';
  if (!needSetup && !oldPw) return '请先填当前密码';
  return '';
}

/** 纯视图：只吃数据 + 回调，不取数、不自己存明文。 */
export function ConsolePasswordView({
  needSetup = false,
  authed = true,
  oldPw = '',
  newPw = '',
  newPw2 = '',
  err = '',
  msg = '',
  busy = false,
  onOld = () => {},
  onNew = () => {},
  onNew2 = () => {},
  onSubmit = () => {},
  onLock = () => {},
}) {
  const title = needSetup ? '设置控制台密码' : '修改控制台密码';
  return (
    <section className="card" id="console-password-card">
      <h2 className="card__title">控制台密码</h2>
      <p className="card__note">
        控制台是这台服务器上的一个网页，同一个局域网谁打开都能改配置。
        这道密码只属于这台服务器，和星群账号是两件事，都要输。
      </p>

      <form className="cp-form" onSubmit={onSubmit}>
        <div className="cp-form__title">{title}</div>

        {!needSetup && (
          <label className="field">
            <span>当前密码</span>
            <input
              className="p-input"
              type="password"
              value={oldPw}
              autoComplete="current-password"
              placeholder="现在用的那个控制台密码"
              disabled={busy}
              onChange={(e) => onOld(e.target.value)}
            />
          </label>
        )}

        <label className="field">
          <span>新密码</span>
          <input
            className="p-input"
            type="password"
            value={newPw}
            autoComplete="new-password"
            placeholder="至少 8 位"
            disabled={busy}
            onChange={(e) => onNew(e.target.value)}
          />
        </label>

        <label className="field">
          <span>确认新密码</span>
          <input
            className="p-input"
            type="password"
            value={newPw2}
            autoComplete="new-password"
            placeholder="两次要一样"
            disabled={busy}
            onChange={(e) => onNew2(e.target.value)}
          />
        </label>

        <p className="card__note">
          至少 8 位；建议字母 + 数字混着来 —— 纯数字、12345678 这类顺子、
          以及 password 这种常见词会被服务器挡回来（会告诉你原因）。
        </p>

        <div className="btn-row">
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {busy ? '正在提交…' : needSetup ? '设置控制台密码' : '改密码'}
          </button>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={onLock}
            disabled={busy}
            title="退出这台设备上的控制台会话（回到密码屏）。星群账号不会退出。"
          >
            锁定这台设备
          </button>
        </div>

        {err && <p className="cl-err" role="alert">{err}</p>}
        {msg && <p className="cp-ok" role="status">{msg}</p>}
        {/* 会话状态：改密码不用重新登录，但"锁屏"之后要重新输；
            这里如实说一句，免得老板以为锁定按钮把密码也清了。 */}
        {!needSetup && !authed && (
          <p className="card__note">这台浏览器当前没有有效的控制台会话（改了密码也是别的设备要重输）。</p>
        )}
      </form>

      <p className="card__note">
        锁定 = 只退出这台设备的控制台会话（回到密码屏），星群账号不会退出；
        其它设备的会话不受影响。
      </p>
      <p className="card__note">
        忘记密码：在服务器上删掉控制台鉴权文件（默认
        {' '}<code>~/.config/astroswarm/console_auth.json</code>{' '}
        —— 和服务器配置文件同一个目录，root 装的通常在
        {' '}<code>/root/.config/astroswarm/console_auth.json</code>），
        再刷新本页就会回到「设置控制台密码」。删这一个文件就够了，
        对应的会话文件是 <code>console_sessions.json</code>，不用动它。
      </p>
    </section>
  );
}

export default function ConsolePasswordCard({ onLock = null, onMsg = () => {} }) {
  // null = 还没问到（不闪界面）；true/false = 后端说的 need_setup
  const [needSetup, setNeedSetup] = useState(null);
  const [authed, setAuthed] = useState(false);
  const [oldPw, setOldPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [newPw2, setNewPw2] = useState('');
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const clearPw = () => { setOldPw(''); setNewPw(''); setNewPw2(''); };

  useEffect(() => {
    let alive = true;
    // 只问会话状态，不问任何密码相关的东西（后端也不会回）。
    // 问不到（老后端 404 / 服务没起）时按"已设过密码"渲染：改密码这条路更保守，
    // 不会让人在没有密码的机器上误以为"设一下就能锁住"，也不会白屏。
    get('/api/console/session')
      .then((s) => {
        if (!alive) return;
        setNeedSetup(Boolean(s && s.need_setup));
        setAuthed(Boolean(s && s.authed));
      })
      .catch(() => {
        if (!alive) return;
        setNeedSetup(false);
        setAuthed(false);
      });
    return () => { alive = false; };
  }, []);

  const submit = async (ev) => {
    if (ev) ev.preventDefault();
    if (busy) return;
    setErr('');
    setMsg('');
    const bad = consolePasswordCheck({ needSetup, oldPw, newPw, newPw2 });
    if (bad) { setErr(bad); return; }
    setBusy(true);
    try {
      if (needSetup) {
        await post('/api/console/setup', { password: newPw });
        setNeedSetup(false);
        setAuthed(true);
        setMsg('控制台密码已设置；以后换浏览器打开都要输它');
        onMsg('控制台密码已设置');
      } else {
        const r = await post('/api/console/password', { old: oldPw, new: newPw });
        const killed = Number((r && r.revoked_others) || 0);
        setMsg(killed > 0
          ? `密码已改，其它设备需要重新输入（已作废 ${killed} 个其它设备的会话）`
          : '密码已改，其它设备需要重新输入');
        onMsg('控制台密码已改');
      }
      clearPw();
    } catch (e) {
      setErr(consolePasswordErrText(e));
      clearPw();          // 失败也不把明文留在界面上
    } finally {
      setBusy(false);
    }
  };

  const lock = async () => {
    if (busy) return;
    setErr('');
    setMsg('');
    if (typeof onLock !== 'function') {
      setErr('这一版界面没有接上锁屏入口，请刷新页面后重试');
      return;
    }
    setBusy(true);
    try {
      await onLock();
    } finally {
      setBusy(false);
    }
  };

  if (needSetup === null) {
    return (
      <section className="card" id="console-password-card">
        <h2 className="card__title">控制台密码</h2>
        <p className="card__note">正在读取控制台密码状态…</p>
      </section>
    );
  }

  return (
    <ConsolePasswordView
      needSetup={needSetup}
      authed={authed}
      oldPw={oldPw}
      newPw={newPw}
      newPw2={newPw2}
      err={err}
      msg={msg}
      busy={busy}
      onOld={setOldPw}
      onNew={setNewPw}
      onNew2={setNewPw2}
      onSubmit={submit}
      onLock={lock}
    />
  );
}
