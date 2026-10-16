import { useEffect, useRef, useState } from 'react';

import { get, post } from './api';
import { useIdea } from './workshopIdea.js';

/* 插件工坊的「一句话生成」—— 走异步接口，能看进度、能取消：
     POST /api/workshop/build-async {need}  → {ok, job_id, state:'running', note}   立刻返回
     GET  /api/workshop/job?job_id=&since=N → {state, logs[新增行], log_count, stage,
                                               elapsed, result, error}
     POST /api/workshop/cancel {build_id: job_id} → {state:'cancelling', cancelled, note}
     POST /api/workshop/install {build_id}  → 装上（之后重启机器人）

   取消是"下一个检查点停下"，正在跑的模型调用不会立刻断 —— 后端的 note 就是这么写的，
   所以界面把这句话**原样显示**，不写"已立即停止"。
   job_id 存在 localStorage：刷新页面后还能接着看进度（生成在后台继续跑）。 */

const JOB_KEY = 'as_workshop_job';
const POLL_MS = 1200;

function errText(e) {
  const text = String((e && e.message) || e || '');
  if (/401|403|登录|token/i.test(text)) return '需要先登录星群账号（去「设置」页登录）。';
  return text || '操作失败，请稍后重试。';
}

export default function PluginWorkshopCard({ onMsg = () => {}, onInstalled = () => {} }) {
  const carried = useIdea();
  const [need, setNeed] = useState(carried);
  const [jobId, setJobId] = useState(() => localStorage.getItem(JOB_KEY) || '');
  const [state, setState] = useState('');            // running / done / error / cancelled
  const [logs, setLogs] = useState([]);
  const [stage, setStage] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [installing, setInstalling] = useState(false);
  // 过程日志要不要摊开：状态放容器里，视图保持无 hook（冒烟里可以直接当函数调用）
  const [showLogs, setShowLogs] = useState(false);
  const sinceRef = useRef(0);

  // 概览页那句话带过来了：自动填进输入框（用户还能接着改）
  useEffect(() => {
    if (carried) setNeed((prev) => (prev ? prev : carried));
  }, [carried]);

  /* 轮询进度：只要还有 job_id 就每 1.2 秒拉一次增量日志，跑完（done/error/cancelled）就停。
     刷新后再进来也是这样 —— jobId 从 localStorage 恢复，接着看。 */
  useEffect(() => {
    if (!jobId) return undefined;
    let alive = true;
    let timer = null;
    const tick = async () => {
      try {
        const d = await get(`/api/workshop/job?job_id=${encodeURIComponent(jobId)}&since=${sinceRef.current}`);
        if (!alive) return;
        if (Array.isArray(d.logs) && d.logs.length) {
          sinceRef.current = Number(d.log_count || sinceRef.current + d.logs.length);
          setLogs((prev) => [...prev, ...d.logs]);
        } else if (typeof d.log_count === 'number') {
          sinceRef.current = d.log_count;
        }
        setState(String(d.state || ''));
        setStage(String(d.stage || ''));
        setElapsed(Number(d.elapsed || 0));
        if (d.state === 'done') {
          setResult(d.result || null);
          onMsg(`生成好了：${(d.result && d.result.name) || '新插件'}，检查通过就能装`);
          localStorage.removeItem(JOB_KEY);
          return;
        }
        if (d.state === 'error') {
          setError(String(d.error || '生成失败'));
          onMsg(String(d.error || '生成失败'));
          localStorage.removeItem(JOB_KEY);
          return;
        }
        if (d.state === 'cancelled') {
          onMsg('这次生成已经停了，可以改改需求再生成一次');
          localStorage.removeItem(JOB_KEY);
          return;
        }
      } catch (e) {
        if (!alive) return;
        setError(errText(e));
        localStorage.removeItem(JOB_KEY);
        return;
      }
      timer = setTimeout(tick, POLL_MS);
    };
    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  const generate = async () => {
    const text = String(need || '').trim();
    if (!text) {
      onMsg('先说说你想要什么功能，一句话就行');
      return;
    }
    setStarting(true);
    setLogs([]);
    setStage('');
    setElapsed(0);
    setResult(null);
    setError('');
    setCancelling(false);
    sinceRef.current = 0;
    try {
      const d = await post('/api/workshop/build-async', { need: text });
      const id = String(d.job_id || '');
      if (!id) throw new Error('服务器没给出生成任务号');
      localStorage.setItem(JOB_KEY, id);
      setState(String(d.state || 'running'));
      setJobId(id);
      onMsg(d.note || '已经开始生成了：可能要几分钟，别关页面');
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!jobId) return;
    setCancelling(true);
    try {
      const d = await post('/api/workshop/cancel', { build_id: jobId });
      // 后端原话：取消是下一个检查点停下，正在跑的模型调用会自己结束 —— 照实说
      onMsg(d.cancelled ? (d.note || '已发出取消') : (d.note || '它已经结束了'));
      if (!d.cancelled) setState(String(d.state || ''));
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setCancelling(false);
    }
  };

  const install = async () => {
    if (!result?.build_id) return;
    setInstalling(true);
    try {
      await post('/api/workshop/install', { build_id: result.build_id });
      onMsg('插件已装好，重启一次机器人生效');
      setResult(null);
      setLogs([]);
      setStage('');
      setState('');
      setJobId('');
      localStorage.removeItem(JOB_KEY);
      onInstalled();
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setInstalling(false);
    }
  };

  return (
    <PluginWorkshopView
      need={need}
      setNeed={setNeed}
      jobId={jobId}
      state={state}
      logs={logs}
      stage={stage}
      elapsed={elapsed}
      result={result}
      error={error}
      starting={starting}
      cancelling={cancelling}
      installing={installing}
      showLogs={showLogs}
      onToggleLogs={() => setShowLogs((v) => !v)}
      onGenerate={generate}
      onCancel={cancel}
      onInstall={install}
    />
  );
}

/* 纯视图：只吃数据 + 回调（能被直接渲染做冒烟，见 _smoke/smoke.jsx）。 */
export function PluginWorkshopView({
  need, setNeed, jobId, state, logs, stage, elapsed, result, error,
  starting, cancelling, installing, showLogs, onToggleLogs, onGenerate, onCancel, onInstall,
}) {
  const running = state === 'running' || starting;
  const STATE_TEXT = {
    running: '正在生成…',
    done: '生成完了，检查通过',
    error: '生成失败',
    cancelled: '已停止',
  };

  return (
    <section className="card card--wide">
      <h2 className="card__title">插件工坊：一句话，让 AI 给你写个插件</h2>
      <p className="card__note">
        说清你想要什么功能（比如"每天早八报一次天气"），AI 会写出插件、自检一遍再给你装。
        生成用的是你在上面配的那个模型。
      </p>
      <div className="p-row">
        <input
          className="p-input p-input--wide"
          value={need}
          placeholder="你想要什么功能？一句话，我让 AI 给你做一个插件"
          spellCheck={false}
          onChange={(e) => setNeed(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !running) onGenerate();
          }}
        />
        <button type="button" className="btn btn--primary" disabled={running} onClick={onGenerate}>
          {running ? '正在生成…' : '去做插件'}
        </button>
        {running && (
          <button type="button" className="btn btn--danger" disabled={cancelling} onClick={onCancel}>
            {cancelling ? '正在取消…' : '取消'}
          </button>
        )}
      </div>

      {(jobId || (logs && logs.length > 0)) && (
        <div className="ws-progress">
          <div className="ws-progress__head">
            <b>{STATE_TEXT[state] || '正在生成…'}</b>
            {stage && <span className="card__note">{`正在做：${stage}`}</span>}
            {elapsed > 0 && <span className="card__note">{`已用 ${Math.round(elapsed)} 秒`}</span>}
            <button type="button" className="btn btn--ghost btn--sm" onClick={onToggleLogs}>
              {showLogs ? '收起过程' : `看过程（${(logs || []).length} 行）`}
            </button>
          </div>
          <p className="card__note">
            生成要几分钟，别关页面；刷新了也能接着看。
            点「取消」会在下个检查点停下。
          </p>
          {showLogs && (logs || []).length > 0 && (
            <pre className="ws-logs mono-area">{logs.join('\n')}</pre>
          )}
        </div>
      )}

      {error && <p className="send-warn">{`生成失败：${error}`}</p>}

      {result && (
        <>
          <div className="kv-row kv-row--wide">
            <span>这次生成</span>
            <b>{result.name || '新插件'}</b>
          </div>
          {result.description && <p className="card__note">{result.description}</p>}
          <p className="card__note">
            {`文件 ${(result.files || []).length} 个：${(result.files || []).slice(0, 8).join('、')}`}
          </p>
          <div className="btn-row btn-row--6">
            <button type="button" className="btn btn--primary" disabled={installing} onClick={onInstall}>
              {installing ? '正在装…' : '装上这个插件'}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
