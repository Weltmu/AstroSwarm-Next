import { useEffect, useState } from 'react';

import { apiErrText, get, post, put } from './api';
import PillButton from './components/PillButton.jsx';
import NumberField from './components/NumberField.jsx';

/* 日志保留区（新增）：桌面端 Settings → 日志保留那一块的网页版。
   全局管理页与日志页共用这一个组件（同一份数据、同一个入口，不做两套）。

   后端契约（照抄，别自己发明字段）：
     GET  /api/logs/retention   → {keep_days, usage:{bytes,files}, archives:[...]}
     PUT  /api/logs/retention   ← {keep_days}       → {ok, keep_days}
     POST /api/logs/cleanup-now → {ok, rotated, deleted, bytes_freed}
   接口还没上时（404）显示「暂不可用」，不白屏、也不假装能改。 */

/** 人类看得懂的体积：12.3 MB / 812 KB / 90 B（冒烟里直接测这个纯函数）。 */
export function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/** 清理结果的实话：打包了几个、删了几个、释放多少。 */
export function cleanupText(res) {
  if (!res) return '';
  const rotated = Number(res.rotated || 0);
  const deleted = Number(res.deleted || 0);
  const freed = formatBytes(res.bytes_freed);
  if (!rotated && !deleted) return '没有需要清理的旧日志。';
  return `已打包 ${rotated} 个、删除 ${deleted} 个，释放 ${freed}`;
}

const EMPTY = { usage: { bytes: 0, files: 0 }, archives: [] };

export default function LogRetention({ onMsg = () => {} }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  const [days, setDays] = useState('');
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState(null);

  const load = () => {
    get('/api/logs/retention')
      .then((d) => {
        setData(d);
        setErr('');
        setDays(String(d.keep_days ?? ''));
      })
      .catch((e) => { setData(null); setErr(apiErrText(e)); });
  };
  useEffect(load, []);

  const save = async () => {
    const n = Number(days);
    if (!Number.isInteger(n) || n < 1 || n > 365) {
      onMsg('保留天数要填 1–365 之间的整数');
      return;
    }
    setBusy('save');
    try {
      const r = await put('/api/logs/retention', { keep_days: n });
      onMsg(`已保存：日志保留 ${r && r.keep_days ? r.keep_days : n} 天`);
      load();
    } catch (e) {
      onMsg(apiErrText(e));
    } finally {
      setBusy('');
    }
  };

  const cleanup = async () => {
    if (!window.confirm('确定立刻清理旧日志？\n超过保留天数的日志会先打包压缩，更旧的直接删除，不能恢复。')) return;
    setBusy('clean');
    setResult(null);
    try {
      const r = await post('/api/logs/cleanup-now', {});
      setResult(r);
      onMsg(cleanupText(r));
      load();
    } catch (e) {
      onMsg(apiErrText(e));
    } finally {
      setBusy('');
    }
  };

  return (
    <LogRetentionView
      data={data || EMPTY}
      err={err}
      days={days}
      setDays={setDays}
      busy={busy}
      result={result}
      onSave={save}
      onCleanup={cleanup}
      onReload={load}
    />
  );
}

/* 纯视图：只吃数据 + 回调，不自己取数 —— 能被直接渲染做冒烟（见 _smoke/smoke.jsx）。 */
export function LogRetentionView({
  data = EMPTY, err = '', days = '', setDays = () => {}, busy = '',
  result = null, onSave = () => {}, onCleanup = () => {}, onReload = () => {},
}) {
  const usage = (data && data.usage) || {};
  const archives = Array.isArray(data && data.archives) ? data.archives : [];
  const names = archives
    .map((a) => (a && typeof a === 'object'
      ? String(a.name || a.file || a.path || '')
      : String(a == null ? '' : a)))
    .filter(Boolean);

  return (
    <section className="card set-net">
      <h2 className="card__title">日志保留</h2>
      <p className="card__note">
        超过天数的日志先压缩，再旧的直接删掉。
      </p>

      {err ? (
        <p className="card__note log-note">{err}</p>
      ) : (
        <>
          <div className="stat-row">
            <span>当前保留</span>
            <b>{data.keep_days ? `${data.keep_days} 天` : '读取中…'}</b>
          </div>
          <div className="stat-row">
            <span>日志占用</span>
            <b>{`${formatBytes(usage.bytes)} · ${Number(usage.files || 0)} 个文件`}</b>
          </div>
          <div className="stat-row">
            <span>旧日志包</span>
            <b>{archives.length ? `${archives.length} 个` : '没有'}</b>
          </div>
          {names.length > 0 && (
            <p className="card__note">最近几个：{names.slice(-3).join('、')}</p>
          )}

          <NumberField
            label="保留天数"
            value={days}
            min={1}
            max={365}
            unit="天"
            hint="改小后可立刻清理；1~365 天"
            onChange={(v) => setDays(v)}
          />

          <div className="btn-row btn-row--6">
            <PillButton tone="primary" size="sm" loading={busy === 'save'} onClick={onSave}>
              {busy === 'save' ? '正在保存…' : '保存天数'}
            </PillButton>
            <PillButton size="sm" loading={busy === 'clean'} onClick={onCleanup}>
              {busy === 'clean' ? '正在清理…' : '立刻清理旧日志'}
            </PillButton>
            <PillButton tone="ghost" size="sm" onClick={onReload}>
              刷新占用
            </PillButton>
          </div>

          {result && <p className="card__note log-note">本次清理：{cleanupText(result)}</p>}
        </>
      )}
    </section>
  );
}
