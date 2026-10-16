import { useEffect, useState } from 'react';

import { apiErrText, get, put } from './api';
import { ToggleRow } from './components/Toggle.jsx';
import PillButton from './components/PillButton.jsx';

/* ============================================================================
   本机能力（可搜索 · 可开关）
   ----------------------------------------------------------------------------
   来源：桌面端 `ui/pages/plugins_page.py::_build_capability_panel`（2026-10-12 老板：
   「插件页面看不见好友申请那个功能」「加一个能搜索还带开关的」）。
   桌面端那一节列的是 `core/ai_config.list_capabilities()`；控制台这里读
   `GET /api/ai/capabilities`，后端用的是**同一个** ai_config / 同一份
   `disabled_tools`（机器人侧 aichat_manager.json），所以两边永远一致。

   为什么值得单开一节：插件页只管"能装什么"，而"能加好友、能发图、能跨平台发"
   这类是**内置插件里的工具**，以前在界面上根本没入口 —— 老板就是在找"加好友"。

   改完立即生效：机器人侧 chat.py 每条消息现读 disabled_tools
   （`chat_manager.get_disabled_tools()`），不用重启。
   ============================================================================ */

/* 搜到的字能是：名字 / 说明 / 来源 —— 与桌面端 `_refresh_capability_list` 同一口径。 */
export function filterCapabilities(items, keyword) {
  const kw = String(keyword || '').trim().toLowerCase();
  const list = Array.isArray(items) ? items : [];
  if (!kw) return list;
  return list.filter((it) => (
    String(it.name || '').toLowerCase().includes(kw)
    || String(it.desc || '').toLowerCase().includes(kw)
    || String(it.source || '').toLowerCase().includes(kw)
  ));
}

/* 角标：需要权限的能力标出权限名（桌面端写成「需要权限：xxx」跟在说明后面）。 */
export function capabilityBits(it) {
  const bits = [String((it && it.desc) || '')].filter(Boolean);
  if (it && it.source) bits.push(`来源：${it.source}`);
  if (it && it.permission) bits.push(`需要权限：${it.permission}`);
  return bits.join(' · ');
}

function errText(e) {
  return apiErrText(e, '本机能力');
}

export default function MachineCapabilities({ onMsg = () => {} }) {
  const [items, setItems] = useState([]);
  const [note, setNote] = useState('');
  const [kw, setKw] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [err, setErr] = useState('');
  const [busyName, setBusyName] = useState('');

  const load = () => {
    setErr('');
    get('/api/ai/capabilities')
      .then((d) => {
        setItems(Array.isArray(d.items) ? d.items : []);
        setNote(d.note || '');
        setLoaded(true);
      })
      .catch((e) => {
        /* 读不到就说读不到：以前无论什么错都挂着"正在加载…"，看着像卡死。 */
        const text = errText(e);
        setErr(text);
        setLoaded(true);
        onMsg(text);
      });
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = async (name, enabled) => {
    setBusyName(name);
    try {
      const res = await put('/api/ai/capabilities', { name, enabled });
      onMsg(`${enabled ? '已开启' : '已关闭'}：${name}`
        + (res && res.hot_reload ? '（立即生效，不用重启机器人）' : ''));
      load();
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusyName('');
    }
  };

  return (
    <MachineCapabilitiesView
      items={items}
      note={note}
      kw={kw}
      loaded={loaded}
      err={err}
      busyName={busyName}
      onKw={setKw}
      onToggle={toggle}
      onReload={load}
    />
  );
}

/* 纯视图：只吃 items + 回调，不自己取数 —— 冒烟可以直接渲染它（见 _smoke/smoke.jsx）。
   与 BotAiView 同一个套路（那边也是纯视图，所以能被喂假数据测）。 */
export function MachineCapabilitiesView({
  items = [], note = '', kw = '', loaded = false, err = '', busyName = '',
  onKw = () => {}, onToggle = () => {}, onReload = () => {},
}) {
  const shown = filterCapabilities(items, kw);
  const on = shown.filter((it) => it.enabled).length;

  return (
    <section className="card cap-panel">
      <h2 className="card__title">本机能力（可搜索 · 可开关）</h2>
      <p className="card__note">
        这是机器<b>现在真的能调</b>的能力（内置工具 + 内置插件里的动作 + 已装能力包）。
        关掉某个开关，机器人就不再用它 —— 模型那边直接看不见。
      </p>
      <p className="card__note">
        找「加好友」这类功能就在这里搜：申请加好友、通过好友申请都在下面。
      </p>

      <div className="p-row">
        <input
          className="p-input cap-search"
          value={kw}
          placeholder="搜索能力：输名字、说明或来源里的字都行"
          spellCheck={false}
          aria-label="搜索本机能力"
          onChange={(e) => onKw(e.target.value)}
        />
        <PillButton tone="ghost" onClick={onReload}>刷新</PillButton>
      </div>

      {!loaded ? (
        <p className="card__note">正在读取本机能力…</p>
      ) : err ? (
        <p className="card__note">{err}</p>
      ) : (
        <p className="card__note">
          {`共 ${shown.length} 项，开启 ${on} 项`}
          {kw ? '（已按搜索词过滤）' : ''}
          {note ? ` · ${note}` : ''}
        </p>
      )}

      {loaded && !err && shown.length === 0 ? (
        <p className="card__note">
          {kw ? '没有匹配的能力，换个词试试。' : '这台机器还没有可调的能力清单。'}
        </p>
      ) : null}

      <div className="cap-list">
        {shown.map((it) => (
          <ToggleRow
            key={it.name}
            checked={Boolean(it.enabled)}
            disabled={busyName === it.name}
            label={it.name}
            desc={capabilityBits(it)}
            onChange={(v) => onToggle(it.name, v)}
          />
        ))}
      </div>
    </section>
  );
}
