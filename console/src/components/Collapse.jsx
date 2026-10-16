import { useEffect, useRef, useState } from 'react';

/* ============================================================================
   折叠区（Collapse / Disclosure）
   ----------------------------------------------------------------------------
   规范来源：
     · W3C ARIA APG「Disclosure (Show/Hide)」：触发元素是 <button>，
       带 aria-expanded 与 aria-controls；标签文字**不随状态改变**
       （不能展开时写"展开"、收起时写"收起"——那是反模式），
       箭头靠旋转表示状态；键盘 Enter/Space 切换。
     · Apple HIG「Disclosure Controls」：标题行左侧/右侧带 chevron，默认收起。
     · GitHub Primer / Material 3「Expansion panel」：展开有高度与透明度过渡，
       不做"瞬移"；容器 role="group"。
   ----------------------------------------------------------------------------
   什么该折叠、什么绝对不能折叠（这条是给自己定的规矩，写在代码里免得后人忘）：

     ✅ 可以折叠：低频数值（间隔/冷却/免打扰时段/历史条数）、
        外观与动态背景、端口与网络细节、日志保留与清理、插件参数里
        概率/间隔/限速/互动窗口这类"配一次就不动"的项。
     ❌ 绝对不能折叠：
        1) 安全开关 —— 允许主动搭话、允许插件主动发消息、开放注册之类；
        2) 隐私开关 —— 识图（会把图片发到外部模型）、跨平台互通、自学写插件；
        3) 会导致事故的关键项 —— 端口号本身（改错就连不上）、
           控制台密码、"留空 = 一个都不许发"这种安全默认值的提示语；
        4) 报错/权限/登录状态 —— 用户必须一眼看到。
        这些一律常显在折叠区**外面**。折叠只用来收「数值」，不用来藏「开关」。
   ----------------------------------------------------------------------------
   默认收起；展开状态按 storageKey 记进 localStorage（同一台机器下次打开还是你上次的样子）。
   过渡：max-height（不写死像素，用 scrollHeight 量）+ opacity，180ms；收起后不占位。
   ---------------------------------------------------------------------------- */
export default function Collapse({
  title,
  summary = '',
  storageKey = '',
  defaultOpen = false,
  dirty = false,
  dirtyText = '已修改',
  children,
  className = '',
  id = '',
}) {
  const [open, setOpen] = useState(() => {
    if (!storageKey) return defaultOpen;
    try {
      const v = localStorage.getItem(`as_collapse_${storageKey}`);
      if (v === '1') return true;
      if (v === '0') return false;
    } catch { /* 隐私模式：忽略，用默认值 */ }
    return defaultOpen;
  });
  const [h, setH] = useState(defaultOpen ? 'auto' : 0);
  const inner = useRef(null);
  const first = useRef(true);

  /* 展开/收起时量一次真实高度再过渡（内容会变，不能写死 320px 这种数） */
  useEffect(() => {
    const el = inner.current;
    if (!el) return;
    if (first.current) {
      first.current = false;
      setH(open ? 'auto' : 0);
      return;
    }
    if (open) {
      setH(el.scrollHeight);
      const t = setTimeout(() => setH('auto'), 200);   // 过渡完放开，之后内容长高不被截
      return () => clearTimeout(t);
    }
    setH(el.scrollHeight);                              // 先钉住当前高度
    const raf = requestAnimationFrame(() => setH(0));    // 下一帧再收到 0，才有过渡
    return () => cancelAnimationFrame(raf);
  }, [open, children]);

  const toggle = () => {
    setOpen((v) => {
      const next = !v;
      if (storageKey) {
        try { localStorage.setItem(`as_collapse_${storageKey}`, next ? '1' : '0'); } catch { /* 忽略 */ }
      }
      return next;
    });
  };

  const bodyId = `${id || `collapse${storageKey || title}`}`.replace(/[^\w-]/g, '') + '-body';

  return (
    <div className={`collapse${open ? ' collapse--open' : ''}${className ? ` ${className}` : ''}`}>
      <h3 className="collapse__head">
        <button
          type="button"
          className="collapse__btn"
          aria-expanded={open ? 'true' : 'false'}
          aria-controls={bodyId}
          onClick={toggle}
        >
          <span className="collapse__chev" aria-hidden="true">
            <svg viewBox="0 0 16 16" width="14" height="14" focusable="false">
              <path d="M6 3.5 L10.5 8 L6 12.5" fill="none" stroke="currentColor"
                strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <span className="collapse__title">{title}</span>
          {dirty ? <span className="collapse__dirty">{dirtyText}</span> : null}
          {summary ? <span className="collapse__summary">{summary}</span> : null}
        </button>
      </h3>
      <div
        className="collapse__body"
        id={bodyId}
        role="group"
        aria-label={typeof title === 'string' ? title : undefined}
        style={{ maxHeight: h === 'auto' ? 'none' : `${h}px`, opacity: open ? 1 : 0 }}
        aria-hidden={open ? undefined : 'true'}
      >
        <div className="collapse__inner" ref={inner}>{children}</div>
      </div>
    </div>
  );
}
