import { useEffect, useState } from 'react';

import { apiErrText, get } from './api';

/* 纸飞机（Telegram）通道卡：**故意不做成能用的样子**。
   后端 GET /api/telegram/status 回 {supported:false, why, planned:true} ——
   现在还没有接入方式，页面就照实说「还在规划中」，不给任何看起来能填的控件，
   免得用户对着一个填不进去的表单找半天。

   纯视图 TelegramView 只吃 status + err，能被直接渲染做冒烟。 */

export default function TelegramCard() {
  const [status, setStatus] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    get('/api/telegram/status')
      .then((d) => { setStatus(d); setErr(''); })
      .catch((e) => { setStatus(null); setErr(apiErrText(e, '状态读取中')); });
  }, []);

  return <TelegramView status={status} err={err} />;
}

export function TelegramView({ status, err }) {
  return (
    <section className="card card--wide">
      <h2 className="card__title">纸飞机</h2>
      <div className="stat-row">
        <span>接入状态</span>
        <b>暂未开放 · 还在规划中</b>
      </div>
      <div className="stat-row">
        <span>现在能做什么</span>
        <b>不用管它，等做好了会出现在这里</b>
      </div>
      {status && status.why ? (
        <p className="card__note">{status.why}</p>
      ) : status ? (
        <p className="card__note">通道还没做，填不了任何东西。</p>
      ) : err ? (
        <p className="card__note">{err}</p>
      ) : (
        <p className="card__note">状态读取中…</p>
      )}
      {status && status.planned === false && (
        <p className="card__note">
          服务器说这条通道已经能用，页面还没跟上，请刷新。
        </p>
      )}
    </section>
  );
}
