/* 登录弹窗：账号入口（未登录提示条的「去登录」、侧栏/顶栏/图标栏的「星群账号」）
   点开的就是它 —— 像桌面端那个「星群账号」窗口，点开就填、登完就关，
   不再把人甩到「设置 → 账号」页签（老板 2026-10-06 的要求）。

   表单只有一份：LoginForm 同时被这个弹窗和「设置 → 账号」页签用（见 App.jsx）。

   2026-10-10 老板改的体验（本文件的重心）：**不再一上来就给「获取验证码」**。
   两步走，中间那一步由后端说了算：
     ① 第一步只有邮箱 +「下一步」→ POST /api/auth/check-email 问"这个邮箱注册过没有"；
     ② 注册过  → 「输入密码即可登录」：只有密码框 +「登录」（**没有**验证码框、
        **没有**获取验证码按钮），密码错就把后端那句中文原样显示；
        没注册过 → 「注册开户」：密码（≥8 位）+ 6 位验证码 +「获取验证码」（60 秒倒计时）
        +「注册并登录」，成功按老口径提示「账号已创建并登录」并关窗刷新。
     两种分支都能「返回改邮箱」（填错了有救）；查询失败**不**当成"没注册"，
     就在邮箱这一步说「暂时查不到，请稍后再试」—— 老用户看到注册界面比多等一会儿糟得多。

   拆成 View（纯渲染，吃 state + 回调）/ LoginForm（自带状态）两层：这样冒烟里能
   直接把三个分支各渲染一遍再断言（邮箱步不许有密码框与验证码框、注册步必须有
   获取验证码…），本项目一直这么做（见 _smoke/smoke.jsx）。

   两处都必须是纯视图 + 自带状态：没有服务器（接口全 401）时也照常渲染，
   SSR 直接渲它不会崩（_smoke/smoke.jsx 就是这么测的）。 */
import { useEffect, useRef, useState } from 'react';
import { post } from './api.js';

/* ── 下面几个是纯函数：不碰 React，冒烟里直接验（和 version.js / workshopIdea.js 一个做法）。
      容易被改坏、又最难在界面上稳定复现的就是这几句文案与开关判据，所以它们单独可测。 ── */

/* 邮箱的粗判：只挡「空的 / 明显不是邮箱」这两类，别让它们白挨一次按 IP 的限流。
   真判据在账号服务那边 —— 它会把中文原因回给我们，原样显示即可。 */
export function emailLooksOk(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(email || '').trim());
}

/* 注册开户用的 6 位验证码：必须正好 6 位数字（后端 auth.register 也是这么卡的）。 */
export function codeLooksOk(code) {
  return /^\d{6}$/.test(String(code || '').trim());
}

/* 开户密码：至少 8 位（后端注册那条路同一个门槛）。**登录不卡这条** ——
   老账号只认账号服务那侧，前端多拦一道只会让人以为密码格式变了。 */
export function passwordLooksOk(password) {
  return String(password || '').length >= 8;
}

/* 第一步（邮箱 + 下一步）的就地校验：返回 '' = 可以提交，否则是给用户看的那一句。 */
export function emailStepError(email) {
  const mail = String(email || '').trim();
  if (!mail) return '先填邮箱。';
  if (!emailLooksOk(mail)) return '邮箱格式不对，检查一下。';
  return '';
}

/* 已注册分支（只填密码）：空的不发请求，其余交给账号服务判（密码错/网络问题都由它说）。 */
export function passwordStepError(password) {
  return String(password || '').trim() ? '' : '先填密码。';
}

/* 未注册分支（注册开户）：密码 ≥8 位 + 6 位验证码，两条都先本地拦。 */
export function registerStepError(code, password) {
  if (!password) return '先填密码（至少 8 位）。';
  if (!passwordLooksOk(password)) return '密码至少 8 位。';
  if (!String(code || '').trim()) return '先点「获取验证码」，把 6 位码填上。';
  if (!codeLooksOk(code)) return '验证码是 6 位数字，检查一下。';
  return '';
}

/* 后端查注册状态的结果 → 走哪条分支。真判据只有一个：registered === true。
   其余（ok=false / 少字段 / 网络错误）都算"没查到"，由调用方显示「暂时查不到」。 */
export function stepForCheck(res) {
  if (!res || res.ok !== true) return '';
  return res.registered === true ? 'password' : 'register';
}

/* 查注册状态失败时给用户看的那一句。
   限流（后端 429「查询太频繁了，过几分钟再试」）是他自己的操作节奏问题，
   直接把那句人话显示出来；其余（网络、账号服务报错）一律这句话，
   绝不显示 Failed to fetch 这类开发词，也绝不改成"未注册"。 */
export const CHECK_FAIL_TEXT = '暂时查不到，请稍后再试';

export function checkFailText(e) {
  const m = String((e && e.message) || e || '').trim();
  if (/频繁/.test(m)) return m;
  return CHECK_FAIL_TEXT;
}

/* 「获取验证码」按钮上的字：请求在飞 → 发送中…；其余时候一直是「获取验证码」。
   倒计时不占用按钮文案，它单独一行（见 cooldownText）—— 按钮只管「禁用」这件事，
   这样截图/肉眼都能同时看到「获取验证码」和还剩多少秒。 */
export function sendCodeText(sending) {
  return sending ? '发送中…' : '获取验证码';
}

/* 倒计时那一行：0 秒 = 没有这一行。 */
export function cooldownText(cooldown) {
  return cooldown > 0 ? `${cooldown}s 后可重发` : '';
}

/* 接口报错时显示什么：后端 4xx 已经把中文原因放在 detail 里（「登录失败：邮箱或密码错误」
   「注册失败：该邮箱已注册」「验证码没发出去：…」），api.js 会把它包成 Error.message ——
   原样显示，绝不把 400 当成崩溃。真没话说时给一句兜底。 */
export function errText(e, fallback = '没成功，稍后再试。') {
  const m = String((e && e.message) || e || '').trim();
  return m || fallback;
}

/* 「返回改邮箱」那一行：邮箱 + 一个低强调的返回入口（三步都在，填错了有救）。
   邮箱只读显示 —— 要改就回第一步，避免"改了一半又点登录"的糊涂状态。 */
function Who({ email, onBack, hint }) {
  return (
    <p className="loginform__who">
      <span className="loginform__mailtext">{email}</span>
      {onBack && (
        <button type="button" className="loginform__link" onClick={onBack}>
          改邮箱
        </button>
      )}
      {hint ? <span className="loginform__whohint">{hint}</span> : null}
    </p>
  );
}

/* 「忘记星群账号密码」那一行：**只把路径给出去**，控制台不做重置表单 ——
   官网 `login.html#reset` 自己会分流（未登录落登录页、已登录落个人中心，都是两步：验证码 → 新密码）。
   别把「星群账号」写成「星巢账号」（星巢是软件名）。 */
function ResetLine() {
  return (
    <p className="loginform__note">
      忘记星群账号密码？
      <a
        className="loginform__link"
        href="https://astroswarm.cn/login.html#reset"
        target="_blank"
        rel="noopener noreferrer"
      >
        去官网重置密码
      </a>
    </p>
  );
}

/* 纯视图：三个分支各长什么样，全由 step 决定（冒烟里直接喂 step 渲染断言）。 */
export function LoginFormView({
  step = 'email',
  email = '',
  password = '',
  code = '',
  err = '',
  busy = false,
  sending = false,
  cooldown = 0,
  autoFocus = false,
  onEmail = () => {},
  onPassword = () => {},
  onCode = () => {},
  onNext = () => {},
  onLogin = () => {},
  onRegister = () => {},
  onSendCode = () => {},
  onBack = null,
}) {
  const submit = step === 'password' ? onLogin : step === 'register' ? onRegister : onNext;
  const onSubmit = (ev) => {
    if (ev && ev.preventDefault) ev.preventDefault();
    submit(ev);
  };

  return (
    <form className="loginform" onSubmit={onSubmit}>
      {step === 'email' ? (
        <>
          {/* 第一步：只有邮箱 + 下一步。验证码那套**不出现**，问清楚注册状态再说。 */}
          <div className="p-row">
            <input
              className="p-input loginform__mail"
              value={email}
              placeholder="邮箱"
              spellCheck={false}
              autoComplete="username"
              autoFocus={autoFocus}
              onChange={(ev) => onEmail(ev.target.value)}
            />
            <button type="submit" className="btn btn--primary" disabled={busy}>
              {busy ? '查询中…' : '下一步'}
            </button>
          </div>
          {err && (
            <p className="loginform__msg" role="alert">
              {err}
            </p>
          )}
          <ResetLine />
        </>
      ) : (
        <>
          <Who
            email={email}
            onBack={onBack}
            hint={step === 'password' ? '' : '这个邮箱还没注册过'}
          />
          <div className="p-row">
            <input
              className="p-input loginform__pw"
              type="password"
              value={password}
              placeholder={step === 'password' ? '密码' : '密码（至少 8 位）'}
              autoComplete={step === 'password' ? 'current-password' : 'new-password'}
              autoFocus={autoFocus}
              onChange={(ev) => onPassword(ev.target.value)}
            />
          </div>
          {step === 'register' ? (
            <div className="p-row">
              {/* 没注册过才有这一行：6 位验证码 + 获取验证码（60 秒倒计时）+ 注册并登录 */}
              <input
                className="p-input loginform__code"
                value={code}
                placeholder="6 位验证码"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                title="邮箱里收到的 6 位数字"
                onChange={(ev) => onCode(ev.target.value.replace(/\D/g, '').slice(0, 6))}
              />
              <button
                type="button"
                className="btn loginform__send"
                disabled={sending || cooldown > 0}
                onClick={onSendCode}
              >
                {sendCodeText(sending)}
              </button>
              <button type="submit" className="btn btn--primary" disabled={busy}>
                {busy ? '注册中…' : '注册并登录'}
              </button>
            </div>
          ) : (
            <div className="p-row">
              {/* 注册过的老用户：只有密码框 + 登录，**没有**验证码框、没有获取验证码按钮 */}
              <button type="submit" className="btn btn--primary" disabled={busy}>
                {busy ? '登录中…' : '登录'}
              </button>
            </div>
          )}
          {err && (
            <p className="loginform__msg" role="alert">
              {err}
            </p>
          )}
          {!err && step === 'register' && cooldown > 0 && (
            <p className="loginform__hint">{`验证码已发出，${cooldownText(cooldown)}。`}</p>
          )}
          {step === 'password' && <ResetLine />}
        </>
      )}
    </form>
  );
}

/* 登录表单（弹窗与「设置 → 账号」页签共用）—— 两步分流，第一步只问邮箱。
   行为口径（改这里就两处一起变）：
     · 第一步 POST /api/auth/check-email：registered=true → 密码分支；
       registered=false → 注册开户分支；问不到 → 原地「暂时查不到，请稍后再试」，
       **留在邮箱这一步**（不显示注册界面）；
     · 密码分支 POST /api/auth/login（只发邮箱 + 密码，不发验证码）；
     · 注册分支 POST /api/auth/register（邮箱 + 6 位码 + 密码），成功提示
       「账号已创建并登录」；验证码由「获取验证码」POST /api/auth/send-code 拿，
       点成功**立即禁用 60 秒并倒计时**（后端按 IP 限流，倒计时替用户挡住连点）；
     · 三条本地校验（邮箱格式 / 密码非空 / 开户密码 ≥8 位 + 验证码 6 位）不过就不发请求；
     · 提交中按钮变「查询中… / 登录中… / 注册中…」并禁用，防连点；
     · 后端没回 token 就不算成功（以前这种情况也提示「登录成功」，界面却还是未登录）；
     · 失败原因就地显示在表单下面 —— 原样照抄后端那句人话。 */
export function LoginForm({
  onToken = () => {},
  onChanged = async () => {},
  onMsg = null,
  onSuccess = null,
  autoFocus = false,
}) {
  const [step, setStep] = useState('email');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  // 剩余秒数：> 0 时「获取验证码」禁用 + 表单下面显示「验证码已发出，Ns 后可重发。」
  const [cooldown, setCooldown] = useState(0);

  // 倒计时：每秒减 1，减到 0 自己停（卸载时清掉，关掉弹窗不会留下定时器）
  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const t = setTimeout(() => setCooldown((n) => (n > 0 ? n - 1 : 0)), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  /* 返回改邮箱：把上一步的输入（密码/验证码/倒计时）都清掉 —— 换了邮箱，
     之前那些就都不作数了，留着只会让人以为"填过了"。 */
  const backToEmail = () => {
    setStep('email');
    setErr('');
    setPassword('');
    setCode('');
    setCooldown(0);
  };

  /* 第一步：查这个邮箱注册过没有。查询失败就停在原地给一句人话（不显示注册界面）。 */
  const gotoNext = async () => {
    if (busy) return;
    const bad = emailStepError(email);
    if (bad) {
      setErr(bad);
      return;
    }
    setBusy(true);
    setErr('');
    try {
      const res = await post('/api/auth/check-email', { email: email.trim() });
      const next = stepForCheck(res);
      if (!next) {
        setErr(CHECK_FAIL_TEXT);
        return;
      }
      setStep(next);
    } catch (ex) {
      setErr(checkFailText(ex));
    } finally {
      setBusy(false);
    }
  };

  /* 注册过的老用户：邮箱 + 密码就能登（后端原文的中文原因原样显示）。 */
  const doLogin = async () => {
    if (busy) return;
    const bad = passwordStepError(password);
    if (bad) {
      setErr(bad);
      return;
    }
    setBusy(true);
    setErr('');
    try {
      const res = await post('/api/auth/login', { email: email.trim(), password });
      if (!res || !res.token) {
        setErr('没有登录成功，请检查邮箱与密码。');
        return;
      }
      onToken(res.token);
      setPassword('');
      if (onMsg) onMsg('已登录');
      // 登录/注册/认领/退出之后配置与状态才有权限拉：让首页四卡与今日数据立刻拿到真实值
      await onChanged();
      if (onSuccess) onSuccess();
    } catch (ex) {
      setErr(errText(ex));
    } finally {
      setBusy(false);
    }
  };

  /* 没注册过：发验证码 → 填 6 位码 + 开户密码 → 注册并登录。 */
  const doRegister = async () => {
    if (busy) return;
    const bad = registerStepError(code, password);
    if (bad) {
      setErr(bad);
      return;
    }
    setBusy(true);
    setErr('');
    try {
      const res = await post('/api/auth/register', {
        email: email.trim(),
        code: code.trim(),
        password,
      });
      if (!res || !res.token) {
        setErr('没有注册成功，请检查验证码与密码。');
        return;
      }
      onToken(res.token);
      setPassword('');
      setCode('');
      if (onMsg) onMsg('账号已创建并登录');
      await onChanged();
      if (onSuccess) onSuccess();
    } catch (ex) {
      setErr(errText(ex));
    } finally {
      setBusy(false);
    }
  };

  /* 获取验证码：邮箱空 / 格式不对就地提示，一个请求都不发；
     发失败（后端 400）把中文原因显示出来；成功才开始 60 秒倒计时。 */
  const sendCode = async () => {
    if (sending || cooldown > 0) return;
    const mail = email.trim();
    const bad = emailStepError(mail);
    if (bad) {
      setErr(bad);
      return;
    }
    setSending(true);
    setErr('');
    try {
      await post('/api/auth/send-code', { email: mail });
      setCooldown(60);
    } catch (ex) {
      setErr(errText(ex, '验证码没发出去，稍后再试。'));
    } finally {
      setSending(false);
    }
  };

  return (
    <LoginFormView
      step={step}
      email={email}
      password={password}
      code={code}
      err={err}
      busy={busy}
      sending={sending}
      cooldown={cooldown}
      autoFocus={autoFocus}
      onEmail={setEmail}
      onPassword={setPassword}
      onCode={setCode}
      onNext={gotoNext}
      onLogin={doLogin}
      onRegister={doRegister}
      onSendCode={sendCode}
      onBack={step === 'email' ? null : backToEmail}
    />
  );
}

/* 弹窗本体。
   关闭方式三种都留：遮罩、ESC、右上角 ×（老板要的「点哪都能关」）；
   关掉之后焦点回到点开它的那个按钮（键盘用户不会掉到页面最顶上）。
   open=false 时直接返回 null：它的状态全在 LoginForm 里，没有常驻节点，
   未登录 + 服务器没起来时页面上不会多出任何东西。 */
export default function LoginModal({
  open,
  onClose = () => {},
  returnFocusTo = null,
  loggedIn = false,
  email = '',
  onToken,
  onChanged,
  onMsg,
  onGoAccount = null,
}) {
  const boxRef = useRef(null);

  // ESC 关闭：挂在 document 上（弹窗里没有被聚焦的元素时也能接住按键）
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (ev) => {
      if (ev.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // 打开后把焦点放进弹窗（键盘直接能打字；先找输入框，找不到才退到按钮 ——
  // querySelector('input, button') 会先撞上 DOM 里更靠前的「×」，焦点落在关闭键上）
  useEffect(() => {
    if (open) {
      const box = boxRef.current;
      const first = box && (box.querySelector('input') || box.querySelector('button'));
      if (first && first.focus) first.focus();
      return undefined;
    }
    if (returnFocusTo && returnFocusTo.focus) returnFocusTo.focus();
    return undefined;
  }, [open, returnFocusTo]);

  if (!open) return null;

  const close = () => onClose();

  return (
    <div className="loginmodal">
      {/* 遮罩自己就能关：点空白处不要求用户去找那个 × */}
      <div className="loginmodal__mask" onClick={close} aria-hidden="true" />
      <div
        className="loginmodal__box card card--strong"
        role="dialog"
        aria-modal="true"
        aria-label="登录星群账号"
        ref={boxRef}
      >
        <div className="loginmodal__head">
          <h2 className="card__title">登录星群账号</h2>
          <button type="button" className="loginmodal__close" onClick={close} title="关闭" aria-label="关闭">
            ×
          </button>
        </div>
        {loggedIn ? (
          <>
            <p className="card__note">{email ? `已登录：${email}` : '已经登录。'}</p>
            {onGoAccount && (
              <div className="p-row">
                <button type="button" className="btn" onClick={onGoAccount}>
                  去设置里管理
                </button>
              </div>
            )}
          </>
        ) : (
          <>
            <p className="card__note">
              用星群账号邮箱登录 —— 这一步是<b>可选</b>的：不登录也能用控制台，
              登录后能用账号相关的功能。插件、能力包、QQ 与微信通道全部免费。
            </p>
            <LoginForm
              onToken={onToken}
              onChanged={onChanged}
              onMsg={onMsg}
              onSuccess={close}
              autoFocus
            />
            {/* 忘记星群账号密码那一行在 LoginForm 里（设置页签也用它，两处一起有）——
                不在这儿再放一遍，免得同一个弹窗里出现两条一样的链接。 */}
          </>
        )}
      </div>
    </div>
  );
}
