import { useEffect, useState } from 'react';

import { apiErrText, get } from './api';

/* QQ 官方通道的两件事，桌面端 QQ 页同源：
   1. 「群消息」——机器人到底在收「全量群消息」还是「只收 @」。
      这是"群里 @ 它没反应"的第一现场证据：模式不对，怎么 @ 都没用。
   2. 「官方通道体检」——把"不说话"的七八种原因逐条查出来（凭证/环境/进程/连接/
      群消息模式/AI 开关/群聊回话/模型），每条给结论和怎么办。 */

const LOGIN_HINT = '需要先登录星群账号才读得到（去「设置」页登录）。';

const LEVEL_MARK = { ok: '✓', bad: '✗', info: '•' };

/* 读不到就照实说：以前这里直接把 "HTTP 500" 甩在「群消息」那一行上，
   用户分不清是"功能没了"还是"服务暂时没回"。统一走 apiErrText。 */
function errText(e) {
  return apiErrText(e, '群消息状态读取中');
}

export default function QqChannelCard({ onMsg = () => {} }) {
  const [state, setState] = useState(null);
  const [stateErr, setStateErr] = useState('');
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadState = () => {
    get('/api/qq/group-state')
      .then((d) => {
        setState(d);
        setStateErr('');
      })
      .catch((e) => setStateErr(errText(e)));
  };

  useEffect(() => {
    loadState();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const diagnose = async () => {
    setBusy(true);
    try {
      const d = await get('/api/qq/diagnose');
      setReport(d);
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <QqChannelView
      state={state}
      stateErr={stateErr}
      report={report}
      busy={busy}
      onDiagnose={diagnose}
      onReload={loadState}
    />
  );
}

/* 纯视图：只吃数据 + 回调，不自己取数 —— 能被直接渲染做冒烟（见 _smoke/smoke.jsx）。 */
export function QqChannelView({ state, stateErr, report, busy, onDiagnose, onReload }) {
  const bad = (report?.rows || []).filter((r) => r.level === 'bad').length;
  return (
    <section className="card card--wide">
      <h2 className="card__title">QQ 官方通道 · 群消息与体检</h2>

      <div className="kv-row kv-row--wide">
        <span>群消息</span>
        <b>{state ? state.text : stateErr || '正在读取…'}</b>
      </div>
      {state && state.hint && <p className="card__note">{state.hint}</p>}
      {state && state.mode === 'none' && (
        <p className="card__note">
          把机器人拉进群并 @ 它一次，这里就会显示收消息模式。
        </p>
      )}

      <div className="btn-row btn-row--6">
        <button type="button" className="btn btn--primary" disabled={busy} onClick={onDiagnose}>
          {busy ? '正在体检…' : '官方通道体检'}
        </button>
        <button type="button" className="btn btn--ghost" onClick={onReload}>
          刷新群消息模式
        </button>
      </div>

      {report && (
        <>
          <p className="card__note">{report.summary}</p>
          <div className="diag-list">
            {(report.rows || []).map((row) => (
              <div className={`diag-row diag-row--${row.level}`} key={row.name}>
                <span className={`diag-row__mark diag-row__mark--${row.level}`}>
                  {LEVEL_MARK[row.level] || '•'}
                </span>
                <div className="diag-row__body">
                  <div className="diag-row__title">
                    {row.name}：{row.text}
                  </div>
                  {row.hint && <div className="diag-row__hint">{row.hint}</div>}
                </div>
              </div>
            ))}
          </div>
          <p className="card__note">
            {bad
              ? `查到 ${bad} 处问题，按上面带 ✗ 的条目处理。`
              : '体检没发现问题：该填的都填了、机器人也在跑。'}
          </p>
        </>
      )}
    </section>
  );
}
