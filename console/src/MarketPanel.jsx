import { useEffect, useState } from 'react';

import { apiErrText, get } from './api';
import PillButton from './components/PillButton.jsx';

/* 官网插件市场（新增）：桌面端「插件」页那张市场列表的网页版。
   以前控制台在这一块只有一句「无头端还没接」，现在接上了。

   后端契约（照抄，别自己发明字段）：
     GET /api/plugins/market（要登录，带 Bearer token）
       成功 → {ok: true, count: N, entries: [{name, desc, version, category, access}]}
              access 是 core.plugin_market.plugin_access() 的原样返回，
              界面只负责把 label / reason 显示出来，**不自己推断能不能装**
       失败 → {ok: false, error: "market_unreachable" | "bad_signature"
                            | "verify_failed" | "bad_market", why: "中文原因", entries: []}
              → 显示「插件市场暂不可用：<why>」，**不白屏、也不当成空列表**
     返回是白名单字段：没有 sha256 / url / id，别去猜。

   纯视图 MarketView 只吃数据 + 回调，能被直接渲染做冒烟（见 _smoke/smoke.jsx）。 */

/** 搜索：按名字和说明过滤（和桌面端一致，大小写不敏感）。 */
export function marketFilter(entries, query) {
  const kw = String(query || '').trim().toLowerCase();
  const list = Array.isArray(entries) ? entries : [];
  if (!kw) return list;
  return list.filter((e) => `${(e && e.name) || ''} ${(e && e.desc) || ''}`
    .toLowerCase().includes(kw));
}

/** 权益标签：直接用后端给的 label / reason，前端不解释、不推断。 */
export function accessLabel(access) {
  const a = access || {};
  const text = String(a.label || a.reason || '').trim();
  return text || '免费';
}

export default function MarketPanel({ busyId = '', onInstall = () => {}, onMsg = () => {} }) {
  const [state, setState] = useState({ status: 'loading', entries: [], why: '' });
  const [query, setQuery] = useState('');

  const load = () => {
    setState({ status: 'loading', entries: [], why: '' });
    get('/api/plugins/market')
      .then((d) => {
        if (d && d.ok === false) {
          // 市场拉不到不算崩溃：照后端给的中文原因说，列表区不摆空列表
          setState({ status: 'error', entries: [], why: String(d.why || '拿不到清单') });
          return;
        }
        setState({
          status: 'ok',
          entries: Array.isArray(d && d.entries) ? d.entries : [],
          why: '',
        });
      })
      .catch((e) => {
        setState({ status: 'error', entries: [], why: apiErrText(e, '插件市场暂不可用') });
      });
  };
  useEffect(load, []);

  const install = async (entry) => {
    try {
      await onInstall(entry);
    } catch (e) {
      onMsg(apiErrText(e, '安装失败'));
    }
  };

  return (
    <MarketView
      state={state}
      query={query}
      setQuery={setQuery}
      busyId={busyId}
      onInstall={install}
      onReload={load}
    />
  );
}

/* 纯视图：只吃数据 + 回调，不自己取数 —— 能被直接渲染做冒烟。 */
export function MarketView({
  state = { status: 'loading', entries: [], why: '' },
  query = '', setQuery = () => {}, busyId = '',
  onInstall = () => {}, onReload = () => {},
}) {
  const loading = state.status === 'loading';
  const failed = state.status === 'error';
  const shown = marketFilter(state.entries, query);
  const total = (state.entries || []).length;
  // 装了但搜不到时，要说清是搜索的问题，不是「市场空了」
  const noMatch = !loading && !failed && total > 0 && shown.length === 0;

  return (
    <section className="card card--strong">
      <h2 className="card__title">官网市场</h2>
      <p className="card__note">和桌面端插件页同一份清单。</p>

      <div className="p-row">
        <input
          className="p-input mkt-search"
          value={query}
          placeholder="搜插件名或说明"
          spellCheck={false}
          disabled={loading || failed}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="button" className="btn btn--ghost" onClick={onReload}>
          刷新
        </button>
      </div>

      {loading && <p className="card__note">正在拉取官网市场…</p>}

      {failed && (
        <>
          <p className="card__note log-note">
            {state.why && state.why.startsWith('插件市场暂不可用')
              ? state.why
              : `插件市场暂不可用：${state.why || '拿不到清单'}`}
          </p>
          <div className="p-row">
            <button type="button" className="btn btn--ghost" onClick={onReload}>
              重新拉取
            </button>
          </div>
        </>
      )}

      {!loading && !failed && total === 0 && (
        <p className="card__note">官网市场里还没有插件。</p>
      )}

      {noMatch && (
        <p className="card__note">没有匹配的插件，换个词试试。</p>
      )}

      {!loading && !failed && shown.length > 0 && (
        <div className="mkt-cards">
          {shown.map((e) => {
            const name = String((e && e.name) || '');
            const cat = String((e && e.category) || '其它');
            const label = accessLabel(e && e.access);
            const allowed = !(e && e.access && e.access.allowed === false);
            const busy = busyId && busyId === name;
            return (
              <div className="mkt-card" key={name} title={name}>
                <span className="mkt-card__icon" data-cat={cat}>{cat.slice(0, 1)}</span>
                <div className="mkt-card__body">
                  <div className="mkt-card__head">
                    <b className="mkt-card__name">{name}</b>
                    {e && e.version ? <span className="mkt-card__ver">{`v${e.version}`}</span> : null}
                    <span className="mkt-badge">{cat}</span>
                    <span className="mkt-badge mkt-badge--free">{label}</span>
                  </div>
                  <div className="mkt-card__desc">
                    {(e && e.desc) || `${cat}：装上就能用`}
                  </div>
                </div>
                <div className="mkt-card__actions">
                  <button
                    type="button"
                    className="btn btn--primary"
                    disabled={!allowed || Boolean(busy)}
                    title={allowed ? '' : String((e && e.access && e.access.reason) || '当前不可安装')}
                    onClick={() => onInstall(e)}
                  >
                    {busy ? '正在安装…' : '安装'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!loading && !failed && total > 0 && (
        <p className="card__note">
          {query.trim() ? `匹配 ${shown.length} 个 / 共 ${total} 个` : `共 ${total} 个`}
        </p>
      )}
    </section>
  );
}
