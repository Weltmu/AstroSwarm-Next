import { useEffect, useRef, useState } from 'react';
import { post, setConsoleToken } from './api.js';
import { ToggleRow } from './components/Toggle.jsx';

/* 控制台密码屏（2026-10-07 老板要求："再给程序加一个密码登录的功能
   （防止其他人篡改数据，有 15 天免密码登录验证）"）。

   为什么要有这一屏：控制台是 http://IP:7860 上的一个网页，同一个局域网里
   谁打开都能改配置、动机器人。星群账号 token 又在浏览器本地，等于没有门槛。
   所以在看到控制台**之前**先要一道只属于这台服务器的密码。

   两种形态（由后端 /api/console/session 的 need_setup 决定）：
     · 首次部署（need_setup=true）→「设置控制台密码」：两次输入 + 至少 8 位；
     · 以后每次新浏览器打开 →「输入控制台密码」+「记住这台设备 15 天」（默认勾上）。

   纯视图 + 三个回调，不自己取数以外的事：状态由 App 管（App 才知道会话验没验过），
   这里只负责"问用户要密码"这一件事。失败一律中文，绝不回显密码。 */
export default function ConsoleLogin({
  needSetup = false,
  onAuthed = () => {},
  onMsg = () => {},
}) {
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [remember, setRemember] = useState(true);   // 老板要的默认：勾上 15 天
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [locked, setLocked] = useState(false);      // 限流：试太多，暂时不让试
  const firstFieldRef = useRef(null);

  // 一进来焦点就在密码框上：这一屏的唯一任务就是输密码
  useEffect(() => {
    if (firstFieldRef.current) firstFieldRef.current.focus();
  }, [needSetup]);

  const submit = async (ev) => {
    if (ev) ev.preventDefault();
    if (busy) return;
    setErr('');
    const value = pw;
    if (!value) {
      setErr('请先填密码');
      return;
    }
    if (needSetup) {
      if (value.length < 8) {
        setErr('密码至少 8 位');
        return;
      }
      if (value !== pw2) {
        setErr('两次输入的密码不一样');
        return;
      }
    }
    setBusy(true);
    try {
      const path = needSetup ? '/api/console/setup' : '/api/console/login';
      const body = needSetup ? { password: value } : { password: value, remember };
      const data = await post(path, body);
      if (!data || !data.token) throw new Error('服务器没给出会话，请重试');
      // 记住与否决定存 localStorage（15 天）还是 sessionStorage（关浏览器即失效）
      setConsoleToken(data.token, needSetup ? true : remember);
      setPw('');
      setPw2('');
      onMsg(needSetup ? '控制台密码已设置，本设备 15 天内免密码' : '已进入控制台');
      onAuthed(data);
    } catch (e) {
      const m = String((e && e.message) || e);
      // 限流时说清楚"等一会儿"，别让人一直点
      if (/尝试过多|429/.test(m)) {
        setLocked(true);
        setErr('尝试过多，请等 1 分钟再试');
      } else {
        setErr(m || '进不去，请重试');
      }
    } finally {
      setBusy(false);
    }
  };

  const title = needSetup ? '设置控制台密码' : '控制台已锁定';
  const hint = needSetup
    ? '这台服务器还没有控制台密码。设一个（至少 8 位），以后换浏览器打开都要输它 —— 防止别人改你的配置。'
    : '这是服务器上的控制台，需要密码才能进去改配置。';

  return (
    <div className="cl-screen">
      <form className="cl-card" onSubmit={submit}>
        <div className="cl-head">
          <div>
            <h1 className="cl-title">{title}</h1>
            <p className="cl-sub">星巢控制台</p>
          </div>
        </div>

        <p className="cl-hint">{hint}</p>

        {/* autoComplete="current-password" 之外不加任何东西：
            密码框只收输入，绝不回显、不进 URL、不进日志。 */}
        <label className="field">
          <span>{needSetup ? '新密码' : '控制台密码'}</span>
          <input
            ref={firstFieldRef}
            className="p-input"
            type="password"
            value={pw}
            autoComplete={needSetup ? 'new-password' : 'current-password'}
            placeholder={needSetup ? '至少 8 位' : '请输入控制台密码'}
            disabled={locked}
            onChange={(e) => setPw(e.target.value)}
          />
        </label>

        {needSetup && (
          <label className="field">
            <span>再输一次</span>
            <input
              className="p-input"
              type="password"
              value={pw2}
              autoComplete="new-password"
              placeholder="两次要一样"
              disabled={locked}
              onChange={(e) => setPw2(e.target.value)}
            />
          </label>
        )}

        {!needSetup && (
          <div className="cl-remember">
            <ToggleRow
              checked={remember}
              label="记住这台设备 15 天（这台电脑不用每次输密码）"
              desc="只在这台浏览器有效；换设备或清缓存要重新输。"
              onChange={(v) => setRemember(v)}
            />
          </div>
        )}

        {err && <p className="cl-err" role="alert">{err}</p>}

        <button type="submit" className="btn btn--primary cl-go" disabled={busy || locked}>
          {busy ? '正在验证…' : needSetup ? '设置并进入' : '进入控制台'}
        </button>

        {locked && (
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => { setLocked(false); setErr(''); }}
          >
            我再试一次
          </button>
        )}

        <p className="cl-note">
          {needSetup
            ? '密码只存在这台服务器上（加密保存），不会明文写进配置文件，也不会出现在日志里。'
            : '忘了密码？在服务器上执行这条命令重置（只有能登服务器的人做得到）：'}
        </p>
        {/* 忘了怎么办：两个状态给同一条命令。首次设置这一屏以前只写了「密码存在哪」，
            真忘了的人第一反应就是回到这里找 —— 所以这一屏也补上，样式不抢主文案。 */}
        {needSetup && (
          <p className="cl-note">忘了怎么办 —— 在服务器上执行这条命令重置（只有能登服务器的人做得到）：</p>
        )}
        <p className="cl-cmd">astroswarm console-reset-password</p>
        <p className="cl-note">
          会作废所有已登录设备，下次打开控制台重新设置。想直接设成新密码：astroswarm console-reset-password --password 你的新密码
        </p>
      </form>
    </div>
  );
}
