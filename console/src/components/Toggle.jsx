import { useId } from 'react';

/* ============================================================================
   开关（Toggle）—— 全控制台共用，替代「独占一行的裸复选框」
   ----------------------------------------------------------------------------
   规范来源（抄的是数值，不是印象）：
     · Apple HIG「Toggles」：开关只表示**立即生效**的 on/off，不要配"保存"按钮；
       轨道 51×31pt、滑块直径 27pt、按下时滑块横向拉伸（本体不变形）。
     · Material 3 Switch：轨道 52×32dp、滑块 20dp、关闭态滑块 16dp 带 2dp 描边、
       两侧留 6dp；关闭轨道 = surfaceContainerHighest，开启轨道 = primary。
     · W3C ARIA APG「Switch Pattern」：role="switch" + aria-checked，
       空格切换（回车可选），标签在状态变化时**不能改字**，
       附加说明走 aria-describedby。
     · Radix Primitives Switch：空格与回车都切换；data-state 驱动样式。
   下面是按上面四条折算到本控制台 12/13px 正文尺度的取值（桌面端字体比移动端小，
   所以轨道取 44×24 而不是 52×32，滑块 20 —— 与 16px 复选框视觉重量持平）：
     轨道 44×24 · 圆角 999 · 滑块 20×20（关闭态 16×16 + 2px 描边，M3 同款）
     开：轨道 var(--as-accent) + 白滑块；关：轨道 #3A4354 + 白滑块
     过渡 transform/background 160ms cubic-bezier(.22,.65,.25,1)（在 150~200ms 区间内）
     按下：滑块横向拉伸 22×20（Apple 的做法），松手回弹
     hover：轨道叠一层 6% 白；active：叠 10%；disabled：整行降到 45% 不透明度且不响应
     焦点环：2px var(--as-focus) + 2px offset，只在键盘 :focus-visible 时出现
   ----------------------------------------------------------------------------
   用法 A（推荐，带标签 + 一句说明的行）：
     <ToggleRow checked={v} onChange={setV} label="允许主动搭话"
                desc="按下面的概率与间隔，自己挑群里的记录插一句。" />
   用法 B（放进已有布局，只要那个小开关）：
     <Toggle checked={v} onChange={setV} ariaLabel="启用" /> */
export default function Toggle({
  checked = false,
  onChange = () => {},
  disabled = false,
  ariaLabel = '',
  ariaDescribedBy = '',
  size = 'md',
  title = '',
  className = '',
}) {
  const on = Boolean(checked);
  const cls = ['tgl', size === 'sm' ? 'tgl--sm' : '', disabled ? 'tgl--disabled' : '', className]
    .filter(Boolean).join(' ');
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on ? 'true' : 'false'}
      aria-label={ariaLabel || undefined}
      aria-describedby={ariaDescribedBy || undefined}
      disabled={disabled}
      title={title || ''}
      className={cls}
      onClick={() => { if (!disabled) onChange(!on); }}
    >
      <span className="tgl__track" aria-hidden="true">
        <span className="tgl__thumb" />
      </span>
    </button>
  );
}

/* 整行开关：左侧「标签 + 一句说明」，右侧开关。整行可点（不用去瞄那个小滑块）。
   说明用 aria-describedby 关联 —— 读屏会念完标签再念说明（APG 要求）。 */
export function ToggleRow({
  checked = false,
  onChange = () => {},
  disabled = false,
  label,
  desc = '',
  title = '',
  badge = null,
  id = '',
}) {
  const auto = useId();
  const rowId = id || `tglrow${auto.replace(/[^A-Za-z0-9_-]/g, '')}`;
  const descId = desc ? `${rowId}-desc` : '';
  return (
    <div
      className={`tgl-row${disabled ? ' tgl-row--disabled' : ''}${checked ? ' tgl-row--on' : ''}`}
      title={title || ''}
      onClick={(e) => {
        /* 点整行就切换；点在开关本身上时它自己已经处理了，别切两次 */
        if (disabled || e.target.closest('.tgl')) return;
        onChange(!checked);
      }}
    >
      <div className="tgl-row__text">
        <div className="tgl-row__label">
          <span>{label}</span>
          {badge}
        </div>
        {desc ? <p className="tgl-row__desc" id={descId}>{desc}</p> : null}
      </div>
      <Toggle
        checked={checked}
        onChange={onChange}
        disabled={disabled}
        ariaLabel={typeof label === 'string' ? label : undefined}
        ariaDescribedBy={descId}
      />
    </div>
  );
}
