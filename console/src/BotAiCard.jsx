import { useEffect, useState } from 'react';

import { apiErrText, get, put } from './api';
import { ToggleRow, default as Toggle } from './components/Toggle.jsx';
import PillButton from './components/PillButton.jsx';
import Collapse from './components/Collapse.jsx';
import NumberField from './components/NumberField.jsx';

/* 「机器人 AI」：群聊回话、引用解析、群里主动搭话、允许回话的群、免@连续对话。
   数据都在机器人侧 aichat_manager.json（不是控制台自己的配置），走后端
   /api/ai/settings（GET 读 / PUT 只写传过去的字段）。

   小白友好硬要求：全中文、大白话；不许出现"会员/付费/解锁"这类字样。
   免@连续对话的参数归「连续对话能力包」，这里只是让人看得见、改得动，
   装了那个包就以包里的参数为准（保存后会提示一句）。 */

const LOGIN_HINT = '需要先登录星群账号（去「设置」页登录）。';

/* 分条细节那几项的界面文案与单位后缀。**范围与默认值不在这里** ——
   那些一律用后端 multi_message_info.limits 给的（后端改范围，界面跟着变）。
   这里只放"叫什么、带什么单位"，因为后端元信息里没有中文 label。 */
const MULTI_LABELS = {
  max_parts: '最多分几条',
  max_part_chars: '每条最多多少字',
  part_delay_ms: '每字延迟',
  part_delay_max_ms: '单条间最多等',
};
const MULTI_UNITS = {
  max_parts: '条',
  max_part_chars: '字',
  part_delay_ms: '毫秒',
  part_delay_max_ms: '毫秒',
};

/* 读不到就照实说：以前无论什么错都把原文甩给用户（可能是 "HTTP 500"），
   也分不清是不是没登录。统一走 apiErrText 那套口径。 */
function errText(e) {
  return apiErrText(e, '机器人侧设置');
}

const SESSION_MODE_LABEL = {
  timeout: '超时断开（说一会儿自动停）',
  api: '读群聊天记录判断（每条都问一次 AI）',
  off: '已关闭（群里只有 @ 它才回）',
};

/* 待处理好友申请 → 界面文案。控制台**只读**（机器人进程才是处理那一方）。 */
export function friendRequestText(state) {
  const total = Number((state && state.total) || 0);
  if (!total) return '暂时没有人申请加好友';
  const first = ((state && state.pending) || [])[0] || {};
  const who = String(first.user_id || '').trim();
  const comment = String(first.comment || '').trim();
  return `${total} 个人在等着加好友：${who}${comment ? `（${comment}）` : ''}`
    + (total > 1 ? ` 等 ${total} 个` : '');
}

/* 插件主动发送白名单：只认 `group:xxx` 和 `user:xxx` 两种写法。
   返回 {targets, dropped} —— dropped 是看不清写法的那些（保存时会提示一句），
   这样后端不会收到没法用的值，用户也知道自己哪一行被跳过了。 */
export function normalizeSendTargets(list) {
  const targets = [];
  const dropped = [];
  for (const raw of list || []) {
    const text = String(raw == null ? '' : raw).trim();
    if (!text) continue;                                  // 空行直接忽略
    const m = /^(group|user)\s*[:：]\s*(\S+)$/.exec(text);
    if (!m) {
      dropped.push(text);
      continue;
    }
    const value = `${m[1]}:${m[2]}`;
    if (!targets.includes(value)) targets.push(value);
  }
  return { targets, dropped };
}

/* 「谁和谁算同一个人」——机器人侧写的绑定文件，这里只读来显示（和桌面端同一份数据）。
   openid/身份证在界面上不出现，只说平台名。导出给冒烟直接测，不用渲染整张卡。 */
const BIND_PLATFORM = {
  qq: 'QQ',
  qq_official: 'QQ 官方机器人',
  wechat: '微信 iLink',
  feishu: '飞书',
  telegram: '纸飞机',
};

export function bindingText(groups) {
  const list = Array.isArray(groups) ? groups : [];
  if (!list.length) return '还没绑定（在微信或 QQ 上发「绑定」拿码）';
  const parts = list.slice(0, 3).map((group) => {
    const labels = [];
    (Array.isArray(group) ? group : []).forEach((key) => {
      const head = String(key).split(':', 1)[0];
      const label = BIND_PLATFORM[head] || head;
      if (label && !labels.includes(label)) labels.push(label);
    });
    return labels.join(' ↔ ');
  }).filter(Boolean);
  if (!parts.length) return '还没绑定（在微信或 QQ 上发「绑定」拿码）';
  const more = list.length > 3 ? `（还有 ${list.length - 3} 组）` : '';
  return `${parts.join('；')}${more}`;
}

/* 开关行：改成控制台统一控件（2026-10-12）。
   以前这里是本文件内一个私有 <label class="check"> + 16px 裸复选框的小组件 —— 老板原话
   "开关这种的太 low 了"。现在换成 src/components/Toggle.jsx 的整行开关：
   轨道 44×24 / 滑块 20 / role="switch" / 空格与回车都能切 / 有键盘焦点环。 */
function Switch({ checked, onChange, children, title }) {
  return (
    <ToggleRow
      checked={Boolean(checked)}
      onChange={onChange}
      label={children}
      desc={title || ''}
    />
  );
}

function shortTime(ts) {
  const value = Number(ts || 0);
  if (!value) return '—';
  const d = new Date(value * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* 接口返回 → 界面草稿。抽成纯函数是为了能被冒烟直接喂假数据测默认值：
   识图 / 自学 / 跨平台互通三个开关在机器人侧的默认值都是 true，
   后端还没回这三个字段时要按「开」显示，不能因为前端读不到就显示成关。

   2026-10-12 追加「多条式回复」+「超长消息上限」：
   范围/默认值**一律用后端给的元信息**（multi_message_info / max_incoming_chars_info），
   前端不写死 —— 后端改范围这里跟着变。元信息没回来时（老后端）整块不显示，不猜。 */
export function normalizeAiDraft(d) {
  const raw = d || {};
  const on = (v) => (v === undefined ? true : Boolean(v));
  return {
    group_chat_enabled: Boolean(raw.group_chat_enabled),
    rich_parse_enabled: Boolean(raw.rich_parse_enabled),
    group_welcome_enabled: on(raw.group_welcome_enabled),
    image_recognition_enabled: on(raw.image_recognition_enabled),
    self_forge_enabled: on(raw.self_forge_enabled),
    cross_platform_enabled: on(raw.cross_platform_enabled),
    identity_bindings: Array.isArray(raw.identity_bindings) ? raw.identity_bindings : [],
    allowed_send_targets: Array.isArray(raw.allowed_send_targets)
      ? raw.allowed_send_targets.map(String) : [],
    allowed_groups: Array.isArray(raw.allowed_groups) ? raw.allowed_groups.map(String) : [],
    known_groups: Array.isArray(raw.known_groups) ? raw.known_groups : [],
    interject: { ...(raw.interject || {}) },
    session: { ...(raw.session || {}) },
    // 多条式回复：只在这里做一次兜底（元信息缺失时用后端默认档），具体范围交给控件
    multi_message: { ...(raw.multi_message || {}) },
    multi_message_info: raw.multi_message_info || null,
    max_incoming_chars: typeof raw.max_incoming_chars === 'number' ? raw.max_incoming_chars : null,
    max_incoming_chars_info: raw.max_incoming_chars_info || null,
    /* 2026-10-12（与桌面端 brain_page.py:673-695 同源）：
       自动通过好友申请 + Agent 模式。**能力不在就不显示** ——
       has_approve_friend / has_agent_tools 由后端 ai_config.has_capability 算好回给我们，
       前端不自己猜（桌面端也是这个判据）。 */
    auto_approve_friend: Boolean(raw.auto_approve_friend),
    agent_mode_enabled: raw.agent_mode_enabled === undefined
      ? true : Boolean(raw.agent_mode_enabled),
    has_approve_friend: Boolean(raw.has_approve_friend),
    has_agent_tools: Boolean(raw.has_agent_tools),
    friend_requests: raw.friend_requests || null,
  };
}

export default function BotAiCard({ onMsg = () => {}, onOpenPack = () => {}, onSaved = () => {} }) {
  const [loaded, setLoaded] = useState(false);
  const [needLogin, setNeedLogin] = useState(false);
  const [loadErr, setLoadErr] = useState('');
  const [draft, setDraft] = useState(null);
  const [saved, setSaved] = useState('');
  const [busy, setBusy] = useState(false);

  const load = () => {
    setLoadErr('');
    /* 好友申请清单是**另一份数据**（机器人进程落盘、控制台只读）：
       读不到不影响这一卡，所以单独 catch，绝不连累主设置的渲染。 */
    const pendingReq = get('/api/ai/friend-requests')
      .then((d) => d)
      .catch(() => null);
    Promise.all([get('/api/ai/settings'), pendingReq])
      .then(([d, pending]) => {
        setDraft(normalizeAiDraft({ ...d, friend_requests: pending }));
        setLoaded(true);
        setNeedLogin(false);
      })
      .catch((e) => {
        /* 读不到就说读不到：以前无论什么错都显示「正在读取机器人侧设置…」，
           接口 500 的时候这句话会一直挂着，看着像卡死。 */
        const text = errText(e);
        setNeedLogin(text === LOGIN_HINT);
        setLoadErr(text);
        onMsg(text);
      });
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async (current) => {
    setBusy(true);
    setSaved('');
    try {
      const res = await put('/api/ai/settings', {
        group_chat_enabled: current.group_chat_enabled,
        rich_parse_enabled: current.rich_parse_enabled,
        group_welcome_enabled: Boolean(current.group_welcome_enabled),
        image_recognition_enabled: Boolean(current.image_recognition_enabled),
        self_forge_enabled: Boolean(current.self_forge_enabled),
        cross_platform_enabled: Boolean(current.cross_platform_enabled),
        allowed_send_targets: normalizeSendTargets(current.allowed_send_targets).targets,
        allowed_groups: current.allowed_groups,
        interject: {
          enabled: Boolean(current.interject.enabled),
          probability: Number(current.interject.probability ?? 0.3),
          interval_min: Number(current.interject.interval_min ?? 30),
          interval_max: Number(current.interject.interval_max ?? 90),
          history_count: Number(current.interject.history_count ?? 10),
        },
        session: {
          mode: String(current.session.mode || 'timeout'),
          expire_seconds: Number(current.session.expire_seconds ?? 30),
        },
        /* 多条式回复（0.2.42 新增）：对象里**只写界面真的有的键**，
           后端对未知键会 400 —— 元信息没回来时（老后端）这一项整个不提交。 */
        ...(current.multi_message_info ? {
          multi_message: {
            mode: String(current.multi_message.mode
              || (current.multi_message_info && current.multi_message_info.default_mode) || 'auto'),
            max_parts: Number(current.multi_message.max_parts
              ?? current.multi_message_info.limits.max_parts.default),
            max_part_chars: Number(current.multi_message.max_part_chars
              ?? current.multi_message_info.limits.max_part_chars.default),
            part_delay_ms: Number(current.multi_message.part_delay_ms
              ?? current.multi_message_info.limits.part_delay_ms.default),
            part_delay_max_ms: Number(current.multi_message.part_delay_max_ms
              ?? current.multi_message_info.limits.part_delay_max_ms.default),
          },
        } : {}),
        ...(typeof current.max_incoming_chars === 'number'
          ? { max_incoming_chars: Number(current.max_incoming_chars) } : {}),
        /* 2026-10-12：这两项在桌面端是"拨一下立刻存"（brain_page.py:683/693 的
           toggled.connect → ai_config.save_*），这里保持同一手感：改动一起提交时也带上，
           单独拨动时走下面的 saveOne。 */
        ...(current.has_approve_friend
          ? { auto_approve_friend: Boolean(current.auto_approve_friend) } : {}),
        ...(current.has_agent_tools
          ? { agent_mode_enabled: Boolean(current.agent_mode_enabled) } : {}),
      });
      const changed = (res && res.changed) || [];
      // 2026-10-11 热重载：这张卡里的每一项机器人侧都是**每条消息现读**，
      // 所以保存完就是生效了 —— 不再提示"重启机器人生效"。
      setSaved(changed.length ? '已保存，立即生效（无需重启）' : '没有需要保存的改动');
      onMsg('机器人 AI 设置已保存，立即生效（无需重启机器人）');
      onSaved();
      load();
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusy(false);
    }
  };

  /* 单个开关立刻存（与桌面端一致）：只提交那一个键，后端只写传进来的字段。 */
  const saveOne = async (patch, okText) => {
    setBusy(true);
    setSaved('');
    try {
      await put('/api/ai/settings', patch);
      setSaved(okText);
      onMsg(okText);
      onSaved();
      load();
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusy(false);
    }
  };

  if (!loaded || !draft) {
    return (
      <section className="card card--brain">
        <h2 className="card__title">机器人 AI（群聊）</h2>
        <p className="card__note">
          {needLogin ? LOGIN_HINT : (loadErr || '正在读取机器人侧设置…')}
        </p>
        {Boolean(loadErr) && (
          <div className="btn-row btn--row-6">
            <button type="button" className="btn" onClick={load}>
              重试
            </button>
          </div>
        )}
      </section>
    );
  }
  return (
    <BotAiView
      draft={draft}
      setDraft={setDraft}
      busy={busy}
      saved={saved}
      onSave={() => save(draft)}
      onSaveOne={saveOne}
      onReload={load}
      onOpenPack={onOpenPack}
    />
  );
}

/* 纯视图：只吃 draft + 回调，不自己取数 —— 这样能被直接渲染做冒烟（见 _smoke/smoke.jsx）。 */
export function BotAiView({
  draft, setDraft, busy, saved, onSave, onSaveOne = () => {}, onReload,
  onOpenPack = () => {},
}) {
  const known = draft.known_groups || [];
  const allowedSet = new Set(draft.allowed_groups || []);
  const isChecked = (gid) => allowedSet.size === 0 || allowedSet.has(String(gid));
  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));
  const setInterject = (patch) =>
    setDraft((d) => ({ ...d, interject: { ...d.interject, ...patch } }));
  const setSession = (patch) => setDraft((d) => ({ ...d, session: { ...d.session, ...patch } }));
  const setMulti = (patch) =>
    setDraft((d) => ({ ...d, multi_message: { ...d.multi_message, ...patch } }));

  const toggleGroup = (gid) => {
    const id = String(gid);
    const next = new Set(known.map((g) => String(g.openid)).filter((g) => isChecked(g)));
    if (next.has(id)) next.delete(id);
    else next.add(id);
    // 全勾 = 不限群（写空名单），和桌面端一个口径
    set({ allowed_groups: next.size === known.length ? [] : [...next] });
  };

  return (
    <section className="card card--brain">
      <h2 className="card__title">机器人 AI（群聊）</h2>
      <p className="card__note">
        这里管的是"机器人在群里怎么说话"。<b>改完保存立即生效，不用重启机器人</b>
        （群开关 / 群白名单 / 免@有效期 / 搭话参数都是每条消息现读）。
      </p>

      <Switch
        checked={draft.group_chat_enabled}
        onChange={(v) => set({ group_chat_enabled: v })}
        title="关掉后机器人不在任何群里说话"
      >
        允许在群里回话（默认开）
      </Switch>
      <Switch
        checked={draft.rich_parse_enabled}
        onChange={(v) => set({ rich_parse_enabled: v })}
        title="关掉后 AI 只看得到纯文本。"
      >
        引用、记录、图片一起给 AI 看（默认开）
      </Switch>
      <Switch
        checked={draft.image_recognition_enabled}
        onChange={(v) => set({ image_recognition_enabled: v })}
        title="用当前模型识图；模型不支持图片时打开也没用。"
      >
        能看图片和表情包（默认开）
      </Switch>
      <Switch
        checked={draft.self_forge_enabled}
        onChange={(v) => set({ self_forge_enabled: v })}
        title="不会的事它自己写个插件装上（只有主人能触发）。"
      >
        不会的事自己写插件（默认开）
      </Switch>
      <Switch
        checked={draft.cross_platform_enabled}
        onChange={(v) => set({ cross_platform_enabled: v })}
        title="按身份绑定把消息/文件发到本人的另一个平台；发不了别人。"
      >
        跨平台互通（默认开）
      </Switch>
      <div className="stat-row stat-row--bind">
        <span>绑定状态</span>
        <b>{bindingText(draft.identity_bindings)}</b>
      </div>
      <Switch
        checked={draft.group_welcome_enabled}
        onChange={(v) => set({ group_welcome_enabled: v })}
        title="被拉进新群时，让 AI 按人设说句欢迎。"
      >
        新群进来打个招呼（默认开）
      </Switch>
      <p className="card__note">
        欢迎语由 AI 按当前人设现说，不占主动消息额度。
        
      </p>

      {/* ── 多条式回复（2026-10-12，随 0.2.42 后端一起上）──────────────────
          信息层级：三档（关/自动/总是）**常显** —— 它决定"机器人像不像人"，
          藏起来老板找不到；五个数值项收进「高级」折叠。
          范围与默认值全部来自后端元信息（multi_message_info / max_incoming_chars_info），
          前端一个数字都不写死。 */}
      {draft.multi_message_info && (
        <>
          <h3 className="card__subtitle">多条式回复</h3>
          <NumberField
            className="numf--wide"
            label="多条式回复"
            value={String(draft.multi_message.mode
              || draft.multi_message_info.default_mode || 'auto')}
            options={(draft.multi_message_info.modes || []).map((m) => ({ value: m.value, label: m.label }))}
            onChange={(v) => setMulti({ mode: v })}
          />
          <p className="card__note">
            {(draft.multi_message_info.modes || [])
              .find((m) => m.value === String(draft.multi_message.mode
                || draft.multi_message_info.default_mode))?.hint
              || draft.multi_message_info.note || ''}
          </p>
          <Collapse
            storageKey="botai-multi-message"
            title="分条细节（5 项）"
            summary={`最多 ${draft.multi_message.max_parts ?? draft.multi_message_info.limits.max_parts.default} 条 · 每条 ≤ ${draft.multi_message.max_part_chars ?? draft.multi_message_info.limits.max_part_chars.default} 字 · 每字 ${draft.multi_message.part_delay_ms ?? draft.multi_message_info.limits.part_delay_ms.default} 毫秒`}
          >
            <p className="card__note">
              {draft.multi_message_info.note || ''}
            </p>
            {Object.entries(draft.multi_message_info.limits || {}).map(([key, lim]) => (
              <NumberField
                key={key}
                label={MULTI_LABELS[key] || key}
                value={draft.multi_message[key] ?? lim.default}
                min={lim.min}
                max={lim.max}
                unit={MULTI_UNITS[key] || ''}
                hint={lim.note || ''}
                onChange={(v) => setMulti({ [key]: v })}
              />
            ))}
            <NumberField
              label="别人发来的消息最多读多少字"
              value={draft.max_incoming_chars
                ?? (draft.max_incoming_chars_info && draft.max_incoming_chars_info.default) ?? 4000}
              min={draft.max_incoming_chars_info ? draft.max_incoming_chars_info.min : 0}
              max={draft.max_incoming_chars_info ? draft.max_incoming_chars_info.max : 100000}
              step={100}
              unit="字"
              hint={(draft.max_incoming_chars_info && draft.max_incoming_chars_info.note)
                || '粘超长文进来时先截断；填 0 = 不限制。'}
              onChange={(v) => set({ max_incoming_chars: v })}
            />
          </Collapse>
        </>
      )}

      {/* ── 好友申请 + Agent 模式（2026-10-12 随桌面端一起上）────────────────
          桌面端这两项在「AI 大脑 → 记忆与 MCP」里，**能力不在就不显示开关**
          （老板原话："插件没装，AI 大脑的开关是不是该隐藏"）。
          判据由后端 ai_config.has_capability 算好回给我们，前端不自己猜。
          这两项在桌面端是"拨一下立刻存"（brain_page.py:683/693），这里同一手感。 */}
      {draft.has_approve_friend && (
        <>
          <h3 className="card__subtitle">好友申请</h3>
          <ToggleRow
            checked={Boolean(draft.auto_approve_friend)}
            disabled={busy}
            label="自动通过好友申请（卖家/客服用）"
            desc="打开后：有人申请加机器人好友，它直接通过（不再问你）。默认关闭——开着等于谁加都通过。"
            onChange={(v) => {
              set({ auto_approve_friend: v });
              onSaveOne(
                { auto_approve_friend: v },
                v ? '已开启：自动通过好友申请（立即生效）' : '已关闭：自动通过好友申请（立即生效）',
              );
            }}
          />
          <p className="card__note">
            待处理：{friendRequestText(draft.friend_requests)}
            —— 控制台只读这份清单，机器人那边才是处理申请的一方。
          </p>
        </>
      )}

      {draft.has_agent_tools && (
        <ToggleRow
          checked={draft.agent_mode_enabled !== false}
          disabled={busy}
          label="Agent 模式：先动手办事，再说话"
          desc="开启后它会真的去调工具（发到别的平台、记事、提醒、抓网页、发图…），办完再用人设的口吻回你；关掉就只聊天不动手。"
          onChange={(v) => {
            set({ agent_mode_enabled: v });
            onSaveOne(
              { agent_mode_enabled: v },
              v ? '已开启：Agent 模式（立即生效）' : '已关闭：Agent 模式（立即生效）',
            );
          }}
        />
      )}

      <h3 className="card__subtitle">群里主动搭话</h3>
      <Switch
        checked={draft.interject.enabled}
        onChange={(v) => setInterject({ enabled: v })}
        title="按下面的概率与间隔，自己挑群里的记录插一句。"
      >
        允许在群里主动搭话（不定时插一句，默认关）
      </Switch>
      <p className="card__note">
        得先能收到群里的全部消息，去「QQ」页看「群消息」。
        显示「全量模式」才谈得上搭话。
      </p>
      {/* 概率 / 间隔 / 参考条数 = 低频数值，收进折叠区；
          「允许在群里主动搭话」这个**关键开关**留在外面常显（开了它会自己找群里的人说话）。 */}
      <Collapse
        storageKey="botai-interject"
        title="搭话参数（4 项）"
        summary={`概率 ${Math.round(Number(draft.interject.probability ?? 0.3) * 100)}% · 间隔 ${Number(draft.interject.interval_min ?? 30)}–${Number(draft.interject.interval_max ?? 90)} 分 · 参考 ${Number(draft.interject.history_count ?? 10)} 条`}
      >
        <NumberField
          label="搭话概率"
          value={Math.round(Number(draft.interject.probability ?? 0.3) * 100)}
          min={1}
          max={100}
          slider
          unit="%"
          hint="每条够格的消息有多大概率插一句"
          onChange={(v) => setInterject({ probability: v / 100 })}
        />
        <NumberField
          label="最短间隔"
          value={Number(draft.interject.interval_min ?? 30)}
          min={1}
          max={1440}
          unit="分"
          onChange={(v) => setInterject({ interval_min: v })}
        />
        <NumberField
          label="最长间隔"
          value={Number(draft.interject.interval_max ?? 90)}
          min={1}
          max={1440}
          unit="分"
          onChange={(v) => setInterject({ interval_max: v })}
        />
        <NumberField
          label="参考最近条数"
          value={Number(draft.interject.history_count ?? 10)}
          min={1}
          max={50}
          unit="条"
          hint="插话前先看多少条群记录"
          onChange={(v) => setInterject({ history_count: v })}
        />
      </Collapse>

      <h3 className="card__subtitle">允许回话的群</h3>
      {known.length === 0 ? (
        <p className="card__note">
          还没记录到群。不勾选 = 不限群（默认）。
        </p>
      ) : (
        <>
          <p className="card__note">
            {allowedSet.size === 0
              ? `不限群（默认）· 已记录 ${known.length} 个群，想限制就取消勾选`
              : `只允许勾选的 ${allowedSet.size} 个群（已记录 ${known.length} 个）`}
          </p>
          <div className="group-list">
            {known.map((g) => {
              const gid = String(g.openid || '');
              const isOn = isChecked(gid);
              return (
                <div
                  className="group-list__row"
                  key={gid}
                  title={gid}
                  style={{ cursor: 'pointer' }}
                  onClick={(e) => { if (!e.target.closest('.tgl')) toggleGroup(gid); }}
                >
                  {/* 这里语义上是"这个群允许回话吗"—— 用开关比勾选框更贴切（APG switch）。
                      文案里"取消勾选"的说法保留不动（老板读惯了这个词）。 */}
                  <Toggle
                    checked={isOn}
                    ariaLabel={`允许在群 ${gid} 里回话`}
                    onChange={() => toggleGroup(gid)}
                  />
                  <span className="mono">{gid.slice(0, 8)}…</span>
                  <span className="group-list__meta">
                    最近 {shortTime(g.last_ts)} · 收过 {Number(g.count || 0)} 条
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}

      <h3 className="card__subtitle">插件可以主动发到哪里</h3>
      {/* 安全默认值必须写在最前面：留空 = 一个都不许发，防插件当群发器 */}
      <p className="send-warn">
        <b>留空 = 插件一个都不许主动发消息</b>（这是默认、也是最安全的设置）。
        只有这里列出的群/人，插件才能主动找它。
      </p>
      <p className="card__note">
        一行一个，写法是 <span className="mono">group:群号</span> 或
        <span className="mono"> user:用户标识</span>；写法不对的行保存时会跳过并告诉你。
      </p>
      {(draft.allowed_send_targets || []).length === 0 ? (
        <p className="card__note">现在是空的：插件不会主动给任何人发消息。</p>
      ) : null}
      <div className="send-list">
        {(draft.allowed_send_targets || []).map((item, idx) => (
          <div className="send-row" key={`target-${idx}`}>
            <input
              className="p-input"
              value={item}
              placeholder="group:123456 或 user:标识"
              spellCheck={false}
              onChange={(e) => {
                const next = [...(draft.allowed_send_targets || [])];
                next[idx] = e.target.value;
                set({ allowed_send_targets: next });
              }}
            />
            <button
              type="button"
              className="btn btn--ghost"
              title="删掉这一行"
              onClick={() => {
                const next = (draft.allowed_send_targets || []).filter((_, i) => i !== idx);
                set({ allowed_send_targets: next });
              }}
            >
              删除
            </button>
          </div>
        ))}
      </div>
      <div className="p-row">
        <button
          type="button"
          className="btn btn--ghost"
          onClick={() => set({ allowed_send_targets: [...(draft.allowed_send_targets || []), ''] })}
        >
          添加一行
        </button>
        {(() => {
          const { dropped } = normalizeSendTargets(draft.allowed_send_targets);
          return dropped.length ? (
            <span className="card__note">
              {`这 ${dropped.length} 行写法不对，保存时会跳过：${dropped.join('、')}`}
            </span>
          ) : null;
        })()}
      </div>

      <h3 className="card__subtitle">免 @ 连续对话</h3>
      <p className="card__note">
        当前：{SESSION_MODE_LABEL[String(draft.session.mode || 'timeout')] || '超时断开'}
        {' '}· 有效期 {Number(draft.session.expire_seconds ?? 30)} 秒。
      </p>
      {/* 三种模式各一句人话，别让人对着英文单词猜 */}
      <ul className="mode-help">
        <li>
          <b>超时断开</b>：被叫醒后一段时间内接着说不用再 @。
          （上/下面的有效期就是这段时间）。
        </li>
        <li>
          <b>读群聊天记录判断</b>：不看时间，群里每条消息都让 AI 看一眼要不要接话 ——
          更聪明，但每条都花 token。
        </li>
        <li>
          <b>关掉</b>：群里只有 @ 它才回，其余一律不理。
        </li>
      </ul>
      <p className="card__note">
        这组参数的"正式家"参数以「连续对话能力包」为准，
        这里改的值会被它盖住。
      </p>
      <div className="p-row">
        <button
          type="button"
          className="btn btn--ghost"
          title="去这个能力包的配置页调参数（没装就先装上）"
          onClick={() => onOpenPack('session-chat')}
        >
          去「连续对话能力包」配置
        </button>
        <span className="card__note">没装过就先在插件页把它装上，再点「配置」。</span>
      </div>
      <div className="p-row">
        <span>判断方式</span>
        <select
          className="p-select"
          value={String(draft.session.mode || 'timeout')}
          onChange={(e) => setSession({ mode: e.target.value })}
          aria-label="免 @ 连续对话的判断方式"
        >
          <option value="timeout">超时断开（推荐：说一会儿自动停）</option>
          <option value="api">读群聊天记录判断（每条都问一次 AI，费 token）</option>
          <option value="off">关掉（群里只有 @ 它才回）</option>
        </select>
      </div>
      {/* 有效期（秒）是低频数值 → 收进折叠区；判断方式这个 select 常显（它决定行为差异） */}
      <Collapse
        storageKey="botai-session"
        title="免 @ 有效期"
        summary={`${Number(draft.session.expire_seconds ?? 30)} 秒`}
      >
        <NumberField
          label="有效期"
          value={Number(draft.session.expire_seconds ?? 30)}
          min={30}
          max={300}
          step={10}
          unit="秒"
          hint="被叫醒后这段时间内接着说话不用再 @（30~300 秒）"
          onChange={(v) => setSession({ expire_seconds: v })}
        />
      </Collapse>

      <div className="btn-row btn-row--6">
        <PillButton tone="primary" disabled={busy} loading={busy} onClick={onSave}>
          {busy ? '正在保存…' : '保存'}
        </PillButton>
        <PillButton tone="ghost" disabled={busy} onClick={onReload}>
          重新读取
        </PillButton>
        {saved && <span className="card__note">{saved}</span>}
      </div>
    </section>
  );
}

