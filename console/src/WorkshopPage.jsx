import { useEffect, useRef, useState } from 'react';

import { apiUrl, get, post, put } from './api';
import PluginWorkshopCard from './PluginWorkshopCard.jsx';

/* 插件工坊（控制台版）—— 和桌面端「插件工坊」同一套后端接口（/api/workshop/*）：
     GET  /api/workshop/status          能不能生成 / 用哪家模型 / 已装清单 / 当前通道
     PUT  /api/workshop/config          工坊自己的生成模型（与 AI 大脑分开配）
     POST /api/workshop/build-async     一句话 → 立刻回 job_id（生成走这条，进度与取消在卡片里）
     GET  /api/workshop/job             ?job_id=&since=N → 增量日志 / stage / state / result
     POST /api/workshop/cancel          {build_id: job_id} 下个检查点停下
     POST /api/workshop/install         {build_id} 装上
     POST /api/workshop/repair          {id} 按真实报错让 AI 改一版
     GET  /api/workshop/history?pack_id= 历史版本（stamp=时间戳目录名）
     POST /api/workshop/rollback|uninstall {id}
     POST /api/workshop/export          {id} → 服务器路径；?download=1 直接回 zip 字节
     POST /api/workshop/import          {zip_b64, filename}
     POST /api/workshop/upload-code     要一条上传用的邮箱验证码
     POST /api/workshop/upload          {id, note, code, username} → 等站长审核
     GET  /api/workshop/submissions     我投过的稿（后端已加工成人话 text）
     POST /api/workshop/submissions/withdraw {id} 撤回还没审的 */

function errText(e) {
  const text = String((e && e.message) || e || '');
  if (/401|403|登录|token/i.test(text)) return '需要先登录星群账号（去「设置」页登录）。';
  return text || '操作失败，请稍后重试。';
}

export default function WorkshopPage({ onMsg = () => {}, onGoPlugins = () => {}, onReloadPacks = () => {} }) {
  const [status, setStatus] = useState(null);
  const [cfg, setCfg] = useState({ provider: '', api_url: '', model: '', api_key: '' });
  const [cfgBusy, setCfgBusy] = useState(false);
  const [busyId, setBusyId] = useState('');
  const [historyId, setHistoryId] = useState('');
  const [history, setHistory] = useState([]);
  const [repairResult, setRepairResult] = useState(null);
  const [importing, setImporting] = useState(false);
  const [uploadId, setUploadId] = useState('');
  const [uploadForm, setUploadForm] = useState({ username: '', note: '', code: '' });
  const [uploadHint, setUploadHint] = useState('');
  const [submissions, setSubmissions] = useState(null);      // null = 还没读
  const [subBusyId, setSubBusyId] = useState('');
  /* 「获取模型」：和 AI 大脑走同一个接口（POST /api/ai/models），拿工坊这份地址与密钥去拉，
     结果填进「模型名」。拉不到不阻断 —— 手填一直是允许的，只把原因说清楚。 */
  const [models, setModels] = useState([]);
  const [modelBusy, setModelBusy] = useState(false);
  const fileRef = useRef(null);

  const loadSubmissions = () => {
    get('/api/workshop/submissions')
      .then((d) => setSubmissions(Array.isArray(d.submissions) ? d.submissions : []))
      .catch((e) => {
        setSubmissions([]);
        onMsg(errText(e));
      });
  };

  const load = () => {
    get('/api/workshop/status')
      .then((d) => {
        setStatus(d);
        setCfg((prev) => ({
          provider: d.provider || prev.provider || '',
          api_url: d.api_url || prev.api_url || '',
          model: d.model || prev.model || '',
          api_key: '',                       // 掩码不回填，留空 = 不改
        }));
        if (d.default_author && !uploadForm.username) {
          setUploadForm((f) => ({ ...f, username: d.default_author }));
        }
      })
      .catch((e) => onMsg(errText(e)));
  };

  useEffect(() => {
    load();
    loadSubmissions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const saveConfig = async () => {
    setCfgBusy(true);
    try {
      const d = await put('/api/workshop/config', {
        provider: cfg.provider,
        api_url: cfg.api_url,
        model: cfg.model,
        api_key: cfg.api_key,          // 空串 = 后端保留原来的 Key
      });
      setStatus((s) => ({ ...(s || {}), ...d }));
      onMsg('工坊的生成模型已保存');
      load();
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setCfgBusy(false);
    }
  };

  /* 「获取模型」：服务商上新/改名很快，写死的列表不够用。
     后端 /api/ai/models 用「接口地址 + 密钥」现拉真实模型列表（与 AI 大脑同一套逻辑），
     密钥留空时后端会用服务器上存的那把 —— 所以没重填密钥也能拉。 */
  const fetchModels = async () => {
    setModelBusy(true);
    try {
      const d = await post('/api/ai/models', {
        api_url: String(cfg.api_url || ''),
        api_key: String(cfg.api_key || ''),
        protocol: 'openai',
      });
      const list = Array.isArray(d.models) ? d.models.map(String) : [];
      setModels(list);
      onMsg(list.length
        ? `拉到 ${list.length} 个模型，在「模型名」下面挑一个。`
        : '服务商没返回模型列表，手填模型名即可。');
    } catch (e) {
      setModels([]);
      onMsg(`获取模型失败：${e.message}（不影响手填模型名）`);
    } finally {
      setModelBusy(false);
    }
  };

  /* 装/改/回滚完都要重启一次机器人才生效（桌面端工坊页同一个按钮）。
     重启会打断正在进行的对话，所以照全站的做法加二次确认。 */
  const restartBot = async () => {
    if (!window.confirm('确定重启机器人？\n大约 10 秒内 QQ / 微信 会短暂离线，正在进行的对话会中断。')) return;
    try {
      await post('/api/services/restart', { service: 'bot' });
      onMsg('已发送重启指令，机器人起来后新插件才生效');
    } catch (e) {
      onMsg(errText(e));
    }
  };

  const installBuilt = async (built) => {    if (!built?.build_id) return;
    setBusyId('install');
    try {
      await post('/api/workshop/install', { build_id: built.build_id });
      onMsg('插件已装好，重启一次机器人生效');
      setRepairResult(null);
      load();
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusyId('');
    }
  };

  const repair = async (pid) => {
    setBusyId(pid);
    onMsg('正在让 AI 按报错改一版…（要几分钟，别关页面）');
    try {
      const built = await post('/api/workshop/repair', { id: pid });
      setRepairResult(built);
      onMsg(`改好了：${built.name || pid}，检查通过就能装`);
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusyId('');
    }
  };

  /** 拉一次历史并展开（回滚后也用它刷新，所以不在这里做"再点一次收起"的判断）。 */
  const loadHistory = async (pid) => {
    const d = await get(`/api/workshop/history?pack_id=${encodeURIComponent(pid)}`);
    setHistory(d.history || []);
    setHistoryId(pid);
  };

  const showHistory = async (pid) => {
    if (historyId === pid) {
      setHistoryId('');
      return;
    }
    setBusyId(pid);
    try {
      await loadHistory(pid);
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusyId('');
    }
  };

  const rollback = async (pid) => {
    if (!window.confirm(`确定回滚「${pid}」到上一个版本？\n当前版本会先存进历史，随时能换回来。`)) return;
    setBusyId(pid);
    try {
      await post('/api/workshop/rollback', { id: pid });
      onMsg('已回滚到上一个版本，重启一次机器人生效');
      await loadHistory(pid);       // 回滚后历史变了，列表要按新结果刷新
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusyId('');
    }
  };

  const uninstall = async (pid) => {
    if (!window.confirm(`确定卸载「${pid}」？\n插件目录和它的历史版本一起删掉，不能撤销。`)) return;
    setBusyId(pid);
    try {
      await post('/api/workshop/uninstall', { id: pid });
      onMsg('已卸载，重启一次机器人生效');
      load();
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusyId('');
    }
  };

  const exportPack = async (pid) => {
    setBusyId(pid);
    try {
      const d = await post('/api/workshop/export', { id: pid });
      onMsg(`已在服务器上打好包：${d.path}（共 ${d.files} 个文件）`);
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusyId('');
    }
  };

  /* 下载到本地：走 export?download=1，后端直接回 zip 字节。
     不能拿 <a href> 直链 —— 那个请求带不上登录用的 Authorization 头，
     所以自己 fetch + blob + createObjectURL 触发下载。 */
  const downloadPack = async (pid) => {
    setBusyId(pid);
    try {
      const token = localStorage.getItem('astroswarm_token') || '';
      const res = await fetch(apiUrl('/api/workshop/export?download=1'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ id: pid }),
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const data = await res.json();
          detail = data.detail || data.message || detail;
        } catch {
          /* 不是 JSON 就用状态码 */
        }
        throw new Error(detail);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${pid}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      onMsg(`已开始下载 ${pid}.zip`);
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusyId('');
    }
  };

  const withdraw = async (id) => {    if (!window.confirm('确定撤回这次投稿？\n撤回后可以改好再投一次；已经审过、已上架的撤不了。')) return;
    setSubBusyId(String(id));
    try {
      await post('/api/workshop/submissions/withdraw', { id: Number(id) || 0 });
      onMsg('已撤回这次投稿');
      loadSubmissions();
    } catch (e) {
      onMsg(errText(e));       // 后端说不能撤就照原样显示原因
    } finally {
      setSubBusyId('');
    }
  };

  const pickImport = async (file) => {
    if (!file) return;
    setImporting(true);
    onMsg('正在检查这个插件包（静态检查 + 沙箱试跑）…');
    try {
      const b64 = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error('这个文件读不出来'));
        reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '');
        reader.readAsDataURL(file);
      });
      await post('/api/workshop/import', { zip_b64: b64, filename: file.name });
      onMsg('插件包装好了，重启一次机器人生效');
      load();
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const sendCode = async () => {
    setBusyId('code');
    try {
      const d = await post('/api/workshop/upload-code', {});
      setUploadHint(`验证码已发到 ${d.account_email_masked || d.email || '你的邮箱'}，5 分钟内有效`);
      if (d.default_author && !uploadForm.username) {
        setUploadForm((f) => ({ ...f, username: d.default_author }));
      }
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusyId('');
    }
  };

  /* 这个页面只管**工坊生成的**插件（/api/workshop/status 每条带 ai_generated）；
     官网下载 / 手动装的能力包归「插件管理」页，既不在这里列，也不给投稿。 */  const generatedItems = ((status && status.installed) || []).filter((it) => it.ai_generated === true);

  const notGenerated = '这个不是工坊生成的插件，不能投稿；官网下载的能力包去「插件管理」页管理。';

  const openUpload = (pid) => {
    const id = String(pid);
    if (!generatedItems.some((it) => String(it.id) === id)) {
      onMsg(notGenerated);
      return;
    }
    setUploadId((cur) => (cur === id ? '' : id));
  };

  const submitUpload = async () => {
    if (!generatedItems.some((it) => String(it.id) === String(uploadId))) {
      onMsg(notGenerated);
      return;
    }
    setBusyId('upload');
    try {
      const d = await post('/api/workshop/upload', {
        id: uploadId,
        note: uploadForm.note,
        code: uploadForm.code,
        username: uploadForm.username,
      });
      onMsg(d.message || '已提交，等站长审核');
      setUploadId('');
      setUploadForm((f) => ({ ...f, note: '', code: '' }));
      setUploadHint('');
    } catch (e) {
      onMsg(errText(e));
    } finally {
      setBusyId('');
    }
  };

  return (
    <WorkshopView
      status={status}
      cfg={cfg}
      setCfg={setCfg}
      cfgBusy={cfgBusy}
      busyId={busyId}
      historyId={historyId}
      history={history}
      repairResult={repairResult}
      importing={importing}
      uploadId={uploadId}
      setUploadId={setUploadId}
      uploadForm={uploadForm}
      setUploadForm={setUploadForm}
      uploadHint={uploadHint}
      models={models}
      modelBusy={modelBusy}
      onFetchModels={fetchModels}
      onReloadInstalled={load}
      onRestartBot={restartBot}
      fileRef={fileRef}
      onMsg={onMsg}
      onGoPlugins={onGoPlugins}
      onSaveConfig={saveConfig}
      onRepair={repair}
      onHistory={showHistory}
      onRollback={rollback}
      onUninstall={uninstall}
      onExport={exportPack}
      onImport={pickImport}
      onSendCode={sendCode}
      onSubmitUpload={submitUpload}
      onInstallBuilt={installBuilt}
      onUpload={openUpload}
      onDownload={downloadPack}
      submissions={submissions}
      subBusyId={subBusyId}
      onWithdraw={withdraw}
      onReloadSubmissions={loadSubmissions}
    />
  );
}

/* 纯视图：只吃数据 + 回调（能被直接渲染做冒烟，见 _smoke/smoke.jsx）。 */
export function WorkshopView(props) {
  const {
    status, cfg, setCfg, cfgBusy, busyId, historyId, history, repairResult, importing,
    uploadId, setUploadId, uploadForm, setUploadForm, uploadHint, fileRef,
    models = [], modelBusy = false, onFetchModels = () => {},
    onReloadInstalled = () => {}, onRestartBot = () => {},
    onSaveConfig, onRepair, onHistory, onRollback, onUninstall, onExport, onImport,
    onSendCode, onSubmitUpload, onInstallBuilt, onGoPlugins, onMsg, onReloadPacks, onUpload,
    onDownload, submissions, subBusyId, onWithdraw, onReloadSubmissions,
  } = props;
  const providers = (status && status.providers) || [];
  /* 只列**工坊生成的**（ai_generated === true）；官网下载 / 手动装的能力包归「插件管理」页，
     这里只报一个数量并给个入口。 */
  const allInstalled = (status && status.installed) || [];
  const installed = allInstalled.filter((it) => it.ai_generated === true);
  const otherCount = allInstalled.length - installed.length;
  const ready = status ? Boolean(status.ready) : null;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>插件工坊</h1>
          <p>说一句需求，AI 写好插件并装到机器人。</p>
        </div>
        <div className="page-head__right">
          <button type="button" className="btn btn--ghost" onClick={onGoPlugins}>
            插件管理
          </button>
        </div>
      </header>

      {/* 1) 生成用的模型：和「AI 大脑」分开配。 */}
      <section className="card card--wide">
        <h2 className="card__title">工坊用哪个模型来写插件</h2>
        <p className="card__note">
          这里和「AI 大脑」是分开的两份配置，
          这里管"让 AI 写插件"用哪个模型。工坊只认会写代码的强模型
          {status?.hint ? `（${status.hint}）` : ''}，小模型写出来的插件容易不能用。
        </p>
        {ready === false && status?.why && <p className="send-warn">{status.why}</p>}
        <div className="p-row">
          <span>服务商</span>
          <select
            className="p-select"
            id="ws-model-cfg"
            value={cfg.provider || ''}
            onChange={(e) => {
              const hit = providers.find((p) => p.key === e.target.value);
              setCfg((c) => ({
                ...c,
                provider: e.target.value,
                api_url: hit ? hit.url : c.api_url,
                model: hit ? hit.model : c.model,
              }));
            }}
          >
            <option value="">（还没选）</option>
            {providers.map((p) => (
              <option key={p.key} value={p.key}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div className="p-row">
          <span>接口地址</span>
          <input
            className="p-input p-input--wide"
            value={cfg.api_url || ''}
            placeholder="https://api.deepseek.com"
            spellCheck={false}
            onChange={(e) => setCfg((c) => ({ ...c, api_url: e.target.value }))}
          />
        </div>
        <div className="p-row">
          <span>模型名</span>
          <input
            className="p-input"
            value={cfg.model || ''}
            placeholder="deepseek-chat"
            spellCheck={false}
            onChange={(e) => setCfg((c) => ({ ...c, model: e.target.value }))}
          />
          {/* 拉一次服务商的真实模型列表（写死的预设会过期） */}
          <button
            type="button"
            className="btn"
            disabled={modelBusy}
            title="按上面的接口地址与密钥拉服务商真实模型列表（失败也能手填）。"
            onClick={onFetchModels}
          >
            {modelBusy ? <span>获取中…</span> : <span>获取模型</span>}
          </button>
          <span>密钥</span>
          <input
            className="p-input p-input--wide"
            value={cfg.api_key || ''}
            placeholder={status?.api_key_masked ? `已保存 ${status.api_key_masked}（留空 = 不改）` : '填服务商的 Key'}
            spellCheck={false}
            onChange={(e) => setCfg((c) => ({ ...c, api_key: e.target.value }))}
          />
          <button type="button" className="btn btn--primary" disabled={cfgBusy} onClick={onSaveConfig}>
            {cfgBusy ? '保存中…' : '保存'}
          </button>
        </div>
        {models.length > 0 && (
          <div className="p-row">
            <span>可选模型</span>
            <select
              className="p-select"
              value={models.includes(String(cfg.model || '')) ? String(cfg.model) : ''}
              onChange={(e) => { if (e.target.value) setCfg((c) => ({ ...c, model: e.target.value })); }}
            >
              <option value="">（从拉到的列表里挑一个）</option>
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
        )}
        <p className="card__note">
          {ready === null
            ? '正在读取工坊配置…'
            : ready
              ? `现在可用：${status?.provider_name || ''} ${status?.model || ''}`
              : '还没配好：配好上面四项，下面才能生成。'}
        </p>
      </section>

      {/* 还没配生成模型时先把这一步顶出来（桌面端同一条提示）：没配就生成不了，
          与其让人写完需求才发现，不如现在就把入口指出来。 */}
      {ready === false && (
        <section className="card card--wide">
          <h2 className="card__title">先配生成模型</h2>
          <p className="card__note">先选服务商、填密钥，配好才能开始生成插件。</p>
          <div className="btn-row btn-row--6">
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => {
                const el = document.getElementById('ws-model-cfg');
                if (el) el.focus();
              }}
            >
              配置生成模型
            </button>
          </div>
        </section>
      )}

      {/* 2) 一句话生成（复用首页带过来的那句话） */}
      <PluginWorkshopCard onMsg={onMsg} onInstalled={onReloadPacks} />

      {repairResult && (
        <section className="card card--wide">
          <h2 className="card__title">AI 改的这一版</h2>
          <p className="card__note">
            {`${repairResult.name || ''} ${repairResult.description || ''}`}
            {`（${(repairResult.files || []).length} 个文件）`}
          </p>
          <div className="btn-row btn-row--6">
            <button type="button" className="btn btn--primary" onClick={() => onInstallBuilt(repairResult)}>
              安装到机器人
            </button>
          </div>
        </section>
      )}

      {/* 3) 工坊装的插件：导出 / 修一版 / 回滚 / 卸载 / 投稿
             —— 只列工坊生成的。 */}
      <section className="card card--wide">
        <h2 className="card__title">我做的插件（工坊生成的）</h2>
        <div className="btn-row btn-row--6">
          <button type="button" className="btn btn--ghost" onClick={onReloadInstalled}>
            刷新
          </button>
          <span className="p-gap" />
          <button type="button" className="btn" onClick={onRestartBot}>
            重启机器人生效
          </button>
        </div>
        {installed.length === 0 ? (
          <p className="card__note">还没有工坊生成的插件，上面写一句需求试试。</p>
        ) : (
          <div className="ws-list">
            {installed.map((it) => {
              const pid = String(it.id);
              return (
                <div className="ws-item" key={pid}>
                  <div className="ws-item__head">
                    <b>{it.name || pid}</b>
                    <span className="card__note">{`${pid} · v${it.version || '—'}`}</span>
                  </div>
                  <div className="btn-row btn-row--6">
                    <button type="button" className="btn" disabled={busyId === pid} onClick={() => onExport(pid)}>
                      在服务器上打包
                    </button>
                    <button type="button" className="btn btn--primary" disabled={busyId === pid} onClick={() => onDownload(pid)}>
                      下载到本地
                    </button>
                    <button type="button" className="btn" disabled={busyId === pid} onClick={() => onRepair(pid)}>
                      让 AI 修一版
                    </button>
                    <button type="button" className="btn btn--ghost" disabled={busyId === pid} onClick={() => onHistory(pid)}>
                      {historyId === pid ? '收起历史' : '历史版本'}
                    </button>
                    <button type="button" className="btn btn--ghost" disabled={busyId === pid} onClick={() => onRollback(pid)}>
                      换回上一版
                    </button>
                    {/* 投稿只对工坊生成的插件开放（这列表里都是），点错也拦得住 */}
                    <button type="button" className="btn btn--ghost" onClick={() => onUpload(pid)}>
                      投稿到官网
                    </button>
                    <button type="button" className="btn btn--danger" disabled={busyId === pid} onClick={() => onUninstall(pid)}>
                      卸载
                    </button>
                  </div>
                  {historyId === pid && (
                    <div className="ws-history">
                      {history.length === 0 ? (
                        <span className="card__note">还没有历史版本（回滚过一次才会有）。</span>
                      ) : (
                        history.map((h) => (
                          <div className="ws-history__row" key={h.stamp}>
                            <span className="mono">{h.stamp}</span>
                            <span className="card__note">上一个版本（回滚会把它换回来）</span>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                  {uploadId === pid && (
                    <div className="ws-upload">
                      <p className="card__note">
                        投稿要先登录星群账号，每次上传都要邮箱验证码。
                        点「发送验证码」，把邮箱收到的 6 位码填上。
                      </p>
                      <div className="p-row">
                        <span>作者名</span>
                        <input
                          className="p-input"
                          value={uploadForm.username}
                          placeholder="默认用账号邮箱前缀"
                          spellCheck={false}
                          onChange={(e) => setUploadForm((f) => ({ ...f, username: e.target.value }))}
                        />
                        <span>一句话说明</span>
                        <input
                          className="p-input p-input--wide"
                          value={uploadForm.note}
                          placeholder="这个插件是干什么的"
                          spellCheck={false}
                          onChange={(e) => setUploadForm((f) => ({ ...f, note: e.target.value }))}
                        />
                      </div>
                      <div className="p-row">
                        <span>邮箱验证码</span>
                        <input
                          className="p-input"
                          value={uploadForm.code}
                          placeholder="6 位数字"
                          spellCheck={false}
                          onChange={(e) => setUploadForm((f) => ({ ...f, code: e.target.value }))}
                        />
                        <button type="button" className="btn" disabled={busyId === 'code'} onClick={onSendCode}>
                          {busyId === 'code' ? <span>发送中…</span> : <span>发送验证码</span>}
                        </button>
                        <button type="button" className="btn btn--primary" disabled={busyId === 'upload'} onClick={onSubmitUpload}>
                          {busyId === 'upload' ? '提交中…' : '提交审核'}
                        </button>
                      </div>
                      {uploadHint && <p className="card__note">{uploadHint}</p>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {/* 官网下载 / 手动装的能力包不归这里管：只报数量 + 给个入口 */}
        {otherCount > 0 && (
          <p className="card__note">
            {`另有 ${otherCount} 个官网下载 / 手动装的能力包，去「插件管理」页管理。`}
            <button type="button" className="btn btn--ghost btn--sm" onClick={onGoPlugins}>
              插件管理
            </button>
          </p>
        )}
      </section>

      {/* 4) 导入别人给的插件包 */}
      <section className="card card--wide">
        <h2 className="card__title">导入插件包</h2>
        <p className="card__note">
          别人发你的 zip 可以在这里导入，会先检查再装。
          不合格不会装上去。
        </p>
        <div className="btn-row btn-row--6">
          <button
            type="button"
            className="btn"
            disabled={importing}
            onClick={() => fileRef.current && fileRef.current.click()}
          >
            {importing ? '正在检查…' : '选一个 zip 导入'}
          </button>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".zip"
          style={{ display: 'none' }}
          onChange={(e) => onImport(e.target.files && e.target.files[0])}
        />
      </section>

      {/* 5) 我的投稿：看审核状态；还没审的可以撤回 */}
      <section className="card card--wide">
        <h2 className="card__title">我的投稿</h2>
        <p className="card__note">
          上传的插件要人工审核，通过后才会上架。
        </p>
        <p className="card__note">
          投给官网的插件都在这儿；还没审的可以撤回。
        </p>
        {submissions === null ? (
          <p className="card__note">正在读取投稿记录…</p>
        ) : submissions.length === 0 ? (
          <p className="card__note">还没有投过稿。做好插件后点「投稿到官网」。</p>
        ) : (
          <div className="ws-list">
            {submissions.map((s) => (
              <div className="ws-item" key={String(s.id)}>
                <div className="ws-item__head">
                  <b>{s.text || '（没写名字）'}</b>
                </div>
                {String(s.status) === 'pending' && (
                  <div className="btn-row btn-row--6">
                    <button
                      type="button"
                      className="btn btn--ghost"
                      disabled={String(subBusyId) === String(s.id)}
                      onClick={() => onWithdraw(s.id)}
                    >
                      {String(subBusyId) === String(s.id) ? '正在撤回…' : '撤回'}
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        <div className="btn-row btn-row--6">
          <button type="button" className="btn btn--ghost" onClick={onReloadSubmissions}>
            刷新投稿记录
          </button>
        </div>
      </section>
    </>
  );
}

/* 回滚会自己再弹一次二次确认（在容器里），视图只管调用 */
