import { useEffect, useRef, useState } from 'react';

/* ============================================================================
   数值输入（NumberField）—— 替代裸 <input type="number">
   ----------------------------------------------------------------------------
   为什么不用裸的：裸 number 框在三套主题下都是系统控件（带 上下小箭头 + 加宽的
   点击区），样式不跟主题走；而且它只给一个空格子，用户不知道单位、不知道合理范围、
   填超了也没人管（浏览器只在提交时管）。规范来源：
     · Material 3 Text field + Slider：带标签、支持前后缀（suffix text）、
       辅助文字与错误文字是**组件的一部分**，不是外面随便一句红字。
     · Apple HIG「Steppers / Sliders」：能用档位/滑杆选的就别让人敲数字。
     · GitHub Primer Forms：数值字段有 min/max 就会被 clamp，
       越界时输入框变红 + 一行说明，而不是静默接受。
   ----------------------------------------------------------------------------
   结构（一行）：
     [标签] [−] [ 数值 ] [+] [单位/后缀]     —— 步进按钮
   如果给了 min/max 且跨度小（≤ 20 档），自动改用**滑杆**（拖动 + 显示当前值与单位）；
   给了 options 时改用**档位胶囊**（能选档位的就不要让人填数字）。
   越界：输入框加红边 + 下方一行「只能填 1 ~ 1440」，并把值 clamp 回边界。
   ----------------------------------------------------------------------------
   用法：
     <NumberField label="搭话概率" value={p} onChange={setP} min={1} max={100} unit="%" />
     <NumberField label="记忆档位" value={v} onChange={setV}
                  options={[{value:'low',label:'省'},{value:'mid',label:'中'}]} />
     <NumberField label="遮罩强度" value={m} onChange={setM} min={0} max={100} unit="%" slider />
   ---------------------------------------------------------------------------- */
export default function NumberField({
  label,
  value,
  onChange = () => {},
  min,
  max,
  step = 1,
  unit = '',
  hint = '',
  options = null,
  slider = false,
  disabled = false,
  width = '',
  className = '',
  /* 语义标签：默认 div；放进 <p> 里时传 as="span"（span 不能是块级容器，
     所以只用 display:grid 的 span 也合法 —— HTML 允许 span 设 display）。 */
  as: Tag = 'div',
  id = '',
}) {
  const [raw, setRaw] = useState(value === undefined || value === null ? '' : String(value));
  const [bad, setBad] = useState('');
  const focused = useRef(false);

  /* 外部值变了（重新读取/联动）而用户没在输入时，跟着走 */
  useEffect(() => {
    if (focused.current) return;
    setRaw(value === undefined || value === null ? '' : String(value));
  }, [value]);

  const lo = min === undefined || min === null ? -Infinity : Number(min);
  const hi = max === undefined || max === null ? Infinity : Number(max);
  const clamp = (n) => Math.min(hi, Math.max(lo, n));

  const commit = (text) => {
    const t = String(text).trim();
    if (t === '') { setBad('不能留空'); return; }
    const n = Number(t);
    if (!Number.isFinite(n)) { setBad('只能填数字'); return; }
    if (n < lo) { setBad(`最小 ${lo}`); onChange(clamp(n)); return; }
    if (n > hi) { setBad(`最大 ${hi}`); onChange(clamp(n)); return; }
    setBad('');
    onChange(n);
  };

  const bump = (dir) => {
    const cur = Number(String(raw).trim());
    const base = Number.isFinite(cur) ? cur : (Number.isFinite(lo) ? lo : 0);
    const next = clamp(base + dir * step);
    setRaw(String(next));
    setBad('');
    onChange(next);
  };

  /* 档位胶囊：能选档位的就不要让人填数字 */
  if (Array.isArray(options) && options.length) {
    return (
      <Tag className={`numf numf--choice${className ? ` ${className}` : ''}`}>
        <span className="numf__label">{label}</span>
        <span className="numf__opts" role="radiogroup" aria-label={typeof label === 'string' ? label : undefined}>
          {options.map((o) => {
            const on = String(o.value) === String(value);
            return (
              <button
                key={String(o.value)}
                type="button"
                role="radio"
                aria-checked={on ? 'true' : 'false'}
                className={`numf__opt${on ? ' numf__opt--on' : ''}`}
                disabled={disabled}
                onClick={() => onChange(o.value)}
              >
                {o.label}
              </button>
            );
          })}
        </span>
        {hint ? <span className="numf__hint">{hint}</span> : null}
      </Tag>
    );
  }

  /* 滑杆：给了 slider，或 min/max 跨度 ≤ 20 档**且没有额外说明**时（拖比敲快）。
     为什么要求没有 hint：像「最多分几条（1~6）」这种，档位虽少但后端还给了一句
     "为什么只有 6 条"的说明，改成滑杆那句话就没地方摆了 —— 有说明就用 −/+ 数字框。 */
  const span = Number.isFinite(lo) && Number.isFinite(hi) ? hi - lo : Infinity;
  const useSlider = !disabled && (slider || (span > 0 && span <= 20 && step === 1 && !hint));

  if (useSlider) {
    const cur = Number.isFinite(Number(value)) ? Number(value) : lo;
    return (
      <Tag className={`numf numf--slider${className ? ` ${className}` : ''}`} style={width ? { width } : undefined}>
        <span className="numf__label">{label}</span>
        <input
          type="range"
          className="numf__range"
          min={lo} max={hi} step={step} value={cur}
          disabled={disabled}
          aria-label={typeof label === 'string' ? label : undefined}
          onChange={(e) => onChange(Number(e.target.value))}
        />
        <output className="numf__out">{`${cur}${unit}`}</output>
        {hint ? <span className="numf__hint">{hint}</span> : null}
      </Tag>
    );
  }

  return (
    <Tag className={`numf${bad ? ' numf--bad' : ''}${disabled ? ' numf--disabled' : ''}${className ? ` ${className}` : ''}`}>
      <label className="numf__label" htmlFor={id || undefined}>{label}</label>
      <span className="numf__box" style={width ? { width } : undefined}>
        <button type="button" className="numf__step" tabIndex={-1} aria-hidden="true"
          disabled={disabled} onClick={() => bump(-1)}>−</button>
        <input
          id={id || undefined}
          className="numf__input"
          type="text"
          inputMode="decimal"
          value={raw}
          disabled={disabled}
          aria-label={typeof label === 'string' ? label : undefined}
          aria-invalid={bad ? 'true' : undefined}
          onFocus={() => { focused.current = true; }}
          onChange={(e) => { setRaw(e.target.value); if (bad) setBad(''); }}
          onBlur={(e) => { focused.current = false; commit(e.target.value); }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); commit(raw); e.currentTarget.blur(); }
            if (e.key === 'ArrowUp') { e.preventDefault(); bump(1); }
            if (e.key === 'ArrowDown') { e.preventDefault(); bump(-1); }
          }}
        />
        <button type="button" className="numf__step" tabIndex={-1} aria-hidden="true"
          disabled={disabled} onClick={() => bump(1)}>+</button>
        {unit ? <span className="numf__unit">{unit}</span> : null}
      </span>
      {bad
        ? <span className="numf__err" role="alert">{bad}</span>
        : (
          <>
            {/* 范围提示与说明**两行都给**（不是二选一）：
                范围是"能填多少"，说明是"这项是干什么的" —— 少哪个用户都得猜。 */}
            {Number.isFinite(lo) && Number.isFinite(hi)
              ? <span className="numf__hint">{`范围 ${lo} ~ ${hi}${unit ? ` ${unit}` : ''}`}</span>
              : null}
            {hint ? <span className="numf__hint">{hint}</span> : null}
          </>
        )}
    </Tag>
  );
}
