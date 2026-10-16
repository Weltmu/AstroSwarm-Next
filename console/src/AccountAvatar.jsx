/* 圆形头像：有头像就用后端给的 data URL，没有就用名字首字兜底。
   兜底那一个必须和官网长得一样（老板 2026-10-06：两边昵称都是 Lihui，
   官网是蓝紫渐变圆 + 字母，控制台还是个灰圆），所以下面这套取字/取色
   算式是从官网 space.js 的 initialAvatar() 逐字搬过来的，改一边要改两边。
   所有显示账号的地方（侧栏 / 顶栏 / 图标栏 / 设置页账号卡）都用它，
   保证同一帧里到处一致 —— 数据来自 profile.js 那个共享 store。 */

/* 取字与取色的来源：和官网一个口径 —— 昵称 → 签名 → 邮箱。 */
export function avatarName(profile) {
  if (!profile) return '';
  return String(
    profile.nickname || profile.signature || profile.email || profile.display_name || '',
  ).trim();
}

/* 名字 → 色相：哈希算式与官网逐字一致（h * 31 + 字符码）。 */
export function avatarHue(name) {
  const text = String(name || '');
  let h = 0;
  for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) >>> 0;
  return h % 360;
}

/* 兜底头像的底色：官网是 canvas 的对角线渐变 hsl(h,60%,52%) → hsl(h+42,56%,32%)，
   这里换成同色的 CSS 渐变（135deg = 左上到右下，和 canvas 那条对角线同向）。 */
export function avatarGradient(name) {
  const hue = avatarHue(name);
  return `linear-gradient(135deg,hsl(${hue},60%,52%),hsl(${(hue + 42) % 360},56%,32%))`;
}

/* 头像地址：data URL 换了内容 src 就变了；万一是固定地址，拼时间戳防缓存，换完立刻是新图。 */
export function avatarSrc(avatar, stamp) {
  const src = String(avatar || '').trim();
  if (!src || /^data:/i.test(src) || !stamp) return src;
  return `${src}${src.includes('?') ? '&' : '?'}t=${stamp}`;
}

export function initialOf(name, email) {
  const text = String(name || '').trim() || String(email || '').trim();
  if (!text) return '星';
  const ch = text.slice(0, 1);
  return ch ? ch.toUpperCase() : '星';
}

export default function AccountAvatar({ profile, size = 40, className = '' }) {
  const name = avatarName(profile);
  const email = profile?.email || '';
  const avatar = profile?.avatar || '';
  const cls = ['acct-avatar', className].filter(Boolean).join(' ');
  if (avatar) {
    /* src 变了 React 就换图；固定地址再拼一个时间戳，免得浏览器拿旧缓存。 */
    return (
      <img
        className={cls}
        style={{ width: `${size}px`, height: `${size}px` }}
        src={avatarSrc(avatar, profile?.avatar_stamp)}
        alt={`${name || email || '账号'}的头像`}
      />
    );
  }
  /* 字大小按 0.46 走：官网那张图就是按这个比例画的，缩到多小字母都占同样比例。 */
  return (
    <span
      className={`${cls} acct-avatar--fallback`}
      style={{
        width: `${size}px`,
        height: `${size}px`,
        fontSize: `${Math.max(9, Math.round(size * 0.46))}px`,
        background: avatarGradient(name),
      }}
      title={name || email || '未登录'}
    >
      {initialOf(name, email)}
    </span>
  );
}
