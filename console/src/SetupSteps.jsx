/* 概览页的「三步开箱」：1 连 AI → 2 接 QQ → 3 启动机器人。
   判断"这一步做完了没"的逻辑是纯函数（下面 setupSteps），方便单测与冒烟；
   颜色/文案由纯视图 SetupStepsView 渲染（完成绿、当前蓝、待办灰）。 */

export const STEP_TONE = { done: 'done', current: 'current', todo: 'todo' };

/** 三步的状态：done = 已配好，current = 现在做这个（第一个没完成的），其余 todo。 */
export function setupSteps({ aiModel, qqReady, botRunning }) {
  const done = [Boolean(String(aiModel || '').trim()), Boolean(qqReady), Boolean(botRunning)];
  const firstPending = done.findIndex((ok) => !ok);
  return [
    { id: 'ai', no: 1, title: '连 AI', hint: '把模型 Key 填上，机器人才会说话', button: '去填 Key' },
    { id: 'qq', no: 2, title: '接 QQ', hint: '填官方通道凭证，或让协议端连回来', button: '去接入' },
    { id: 'bot', no: 3, title: '启动机器人', hint: '启动后 QQ / 微信 通道才开始收消息', button: '启动' },
  ].map((step, idx) => {
    let tone = STEP_TONE.todo;
    if (done[idx]) tone = STEP_TONE.done;
    else if (idx === firstPending) tone = STEP_TONE.current;
    return {
      ...step,
      tone,
      status: done[idx] ? '✓ 已配好' : idx === firstPending ? '现在做这个' : '待办',
    };
  });
}

export default function SetupStepsView({ steps, onAction, busy }) {
  return (
    <div className="steps">
      {steps.map((step) => (
        <div className={`step step--${step.tone}`} key={step.id}>
          <span className="step__no">{step.no}</span>
          <div className="step__body">
            <b className="step__title">{step.title}</b>
            <span className="step__hint">{step.hint}</span>
            <span className={`step__status step__status--${step.tone}`}>{step.status}</span>
          </div>
          <button
            type="button"
            className="btn"
            disabled={busy && step.id === 'bot'}
            onClick={() => onAction(step.id)}
          >
            {step.id === 'bot' && busy ? '正在启动…' : step.button}
          </button>
        </div>
      ))}
    </div>
  );
}
