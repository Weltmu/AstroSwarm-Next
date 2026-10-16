/* 未登录提示条：挂在标题栏下面，第一眼就看得到。
   为什么必须显眼：未登录时 /api/services/status、/api/wechat/status、/api/ai/settings
   这些接口全部 401，各张卡片只能显示「状态读不到」—— 老板看到的就是一屏「读不到」，
   不知道该干什么。这里直接给一条带主操作的提示条，点一下就去登录。
   登录后（有 token）整条不渲染。

   纯视图：只吃 show + onLogin，不自己取数 —— 能被直接渲染做冒烟（见 _smoke/smoke.jsx）。
   点按钮时把**点击事件**交给 onLogin：调用方要从 currentTarget 记住是哪个按钮点开的
   （弹窗关掉以后焦点要还回它）。 */
export default function LoginBanner({ show, onLogin = () => {} }) {
  if (!show) return null;
  return (
    <div className="loginbar" role="status">
      <span className="loginbar__dot" aria-hidden="true" />
      <span className="loginbar__text">未登录：机器人与通道的真实状态读不到</span>
      <button type="button" className="btn btn--primary btn--sm" onClick={onLogin}>
        去登录
      </button>
    </div>
  );
}
