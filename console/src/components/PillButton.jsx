/* ============================================================================
   胶囊按钮（Pill Button）
   ----------------------------------------------------------------------------
   规范来源：
     · Material 3 Button：圆角 = 高度一半（全圆角药丸）；尺寸档 32 / 40 / 56dp；
       水平内边距 24 / 24 / 24dp（带图标时两端各 16dp，图标与文字间距 8dp）。
     · Apple HIG Buttons：最小点击高度 44pt（本控制台在桌面浏览器里，取 32 作下限并
       在 CSS 里用 ::before 把命中区补到 32 —— 小号 28 的视觉高度不变，但点得到）。
     · Tailwind UI / shadcn 的按钮五态：default / hover / active / disabled / loading；
       loading 时禁用点击并**保持宽度不变**（文字换成 spinner 不抖）。
     · WCAG 2.2 SC 2.5.8 Target Size (Minimum)：指针目标 ≥ 24×24 CSS px；
       本控制台的小号按钮 28 高 × ≥44 宽，满足。
   尺寸（本控制台尺度，桌面端字体 13px）：
     小 sm  高 28 · 水平内边距 12 · 字号 12.5
     中 md  高 32 · 水平内边距 16 · 字号 13     ← 默认
     高 lg  高 40 · 水平内边距 20 · 字号 13.5
   圆角一律 999px（= 高度一半，压扁时也不会露出直角）。
   五个外观：primary（主）/ secondary（次级，默认）/ ghost（无边框）/ danger（危险）/ link。
   ----------------------------------------------------------------------------
   用法：
     <PillButton onClick={fn}>保存</PillButton>
     <PillButton tone="primary" size="sm" loading={busy}>保存中</PillButton>
     <PillButton tone="danger" icon="trash">卸载</PillButton>

   为什么还要留着 `className="btn ..."` 那套：204 处现有调用点不能一次性重写，
   所以 styles.css 里把 `.btn` 本身也改成了胶囊（同一个视觉体系），
   新代码和重构过的代码用这个组件；两者长得一样，不会出现"两种按钮"。 */
export default function PillButton({
  children,
  onClick = () => {},
  tone = 'secondary',
  size = 'md',
  type = 'button',
  disabled = false,
  loading = false,
  icon = null,
  title = '',
  ariaLabel = '',
  className = '',
  block = false,
  ...rest
}) {
  const cls = [
    'pillbtn',
    `pillbtn--${tone}`,
    size === 'sm' ? 'pillbtn--sm' : size === 'lg' ? 'pillbtn--lg' : 'pillbtn--md',
    block ? 'pillbtn--block' : '',
    loading ? 'pillbtn--loading' : '',
    className,
  ].filter(Boolean).join(' ');

  return (
    <button
      type={type}
      className={cls}
      disabled={disabled || loading}
      aria-busy={loading ? 'true' : undefined}
      aria-label={ariaLabel || undefined}
      title={title || ''}
      onClick={onClick}
      {...rest}
    >
      {/* loading 时不换掉文字：spinner 绝对定位在左侧，宽度不跳 */}
      {loading ? <span className="pillbtn__spin" aria-hidden="true" /> : null}
      {icon ? <span className="pillbtn__icon" aria-hidden="true">{icon}</span> : null}
      <span className="pillbtn__text">{children}</span>
    </button>
  );
}
