"""DeepSeek 大脑：OpenAI 兼容 chat/completions + function calling 决策循环"""
import json
import logging

import httpx



MAX_HISTORY_TOTAL = 4000   # 喂给大脑的历史总字数上限
MAX_HISTORY_ITEM = 800     # 单条历史消息字数上限
MAX_USER_TEXT = 2000       # 当前用户消息字数上限
MAX_TOKENS_WITH_IMAGES = 1200  # 带图请求的 max_tokens 下限：思考模型的思考也算额度，
                               # 300 会被思考吃光 → 正文为空 → 上层误判"看不见图"（10-11 实测）


def _fit_history(history):
    """从最近往回裁剪历史：单条限长、总量限长，防止上下文爆炸。"""
    out = []
    total = 0
    for row in reversed(history or []):
        try:
            role = str(row["role"] or "user")
            text = str(row["text"] or "")
        except Exception:  # noqa: BLE001
            role = "user"
            text = ""
        if len(text) > MAX_HISTORY_ITEM:
            text = text[:MAX_HISTORY_ITEM] + "…（已截断）"
        out.append({"role": role, "text": text})
        total += len(text)
        if total >= MAX_HISTORY_TOTAL:
            break
    return list(reversed(out))


class ImageRejected(Exception):
    """主模型不收图片（接口 4xx）。

    2026-10-10 改：原来这里悄悄把图丢掉重试纯文本 —— 结果模型没看图还照样聊，
    用户以为它"看过了"。现在抛出来，让上层按三级降级处理：
    主模型 → 备用视觉模型 → 如实说看不见。
    """

    def __init__(self, status: int = 0, body: str = ""):
        super().__init__(f"主模型拒图 HTTP {status}: {body[:160]}")
        self.status = status
        self.body = body


class Brain:
    def __init__(self, cfg):
        self.cfg = cfg
        self.headers = {
            "Authorization": f"Bearer {cfg.ds_api_key}",
            "Content-Type": "application/json",
        }

    async def _chat_once(self, messages, tools):
        # 带图的请求要留足额度：思考模型的 reasoning 也计入 max_tokens，
        # 配置的 300 看图时常被思考吃光 → content 为空 → 上层以为"它没看见"（10-11 实测）
        # 额度语义：0/空 = 不限制 → 请求里不带 max_tokens，让接口按模型自身上限走。
        # 带图时若设了很小额度仍保底 MAX_TOKENS_WITH_IMAGES（思考模型会把正文挤空）。
        budget = int(self.cfg.ds_max_tokens or 0)
        if self._has_image(messages):
            budget = max(budget, MAX_TOKENS_WITH_IMAGES)
        payload = {
            "model": self.cfg.ds_model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.8,
        }
        if budget > 0:
            payload["max_tokens"] = budget
        async with httpx.AsyncClient(timeout=150) as client:
            r = await client.post(f"{self.cfg.ds_api_url}/chat/completions", headers=self.headers, json=payload)
            if r.status_code >= 400 and self._has_image(messages):
                # 主模型不收图：抛给上层走"备用视觉模型 → 如实说看不见"（三级降级）
                logging.warning("主模型拒图（HTTP %s）：%s", r.status_code, r.text[:200])
                raise ImageRejected(r.status_code, r.text)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            if not (msg.get("content") or "").strip() and (msg.get("reasoning_content") or "").strip():
                logging.warning("模型只输出了思考、正文为空（max_tokens=%s，0=不限制；看图时更常见）",
                                payload.get("max_tokens", 0))
            return msg

    @staticmethod
    def _has_image(messages) -> bool:
        for m in messages:
            c = m.get("content")
            if isinstance(c, list) and any(
                    isinstance(x, dict) and x.get("type") == "image_url" for x in c):
                return True
        return False

    @staticmethod
    def _user_content(user_text: str, images=None):
        """当前用户消息的 content：有图就拼成多模态分片（图在前，文字在后）。

        2026-10-10 加：老板的主模型（deepseek4.1）本来就能看图，没必要再依赖
        第二个"视觉模型"——那条路要额外密钥，没配的话模型就只看到文字，
        于是群里发图它回"看不见图"。
        """
        if not images:
            return user_text
        parts = []
        for url in images:
            parts.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})
        parts.append({"type": "text", "text": user_text})
        return parts

    async def think(self, system, history, user_text, tools, tool_exec, max_rounds=6,
                    images=None):
        """跑完整决策循环，返回最终回复文本（空串 = 不想说话）。

        images: 形如 ["data:image/jpeg;base64,..."] 的图片列表，直接随消息发给主模型。
        接口不认图（HTTP 4xx）时**自动退回纯文本**重试，保证不会因此不说话。
        """
        history = _fit_history(history)
        user_text = (user_text or "")[:MAX_USER_TEXT]
        messages = [{"role": "system", "content": system}]
        for row in history:
            messages.append({"role": row["role"], "content": row["text"]})
        messages.append({"role": "user", "content": self._user_content(user_text, images)})

        for _ in range(max_rounds):
            msg = await self._chat_once(messages, tools)
            messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
            if not msg.get("tool_calls"):
                return (msg.get("content") or "").strip()
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                logging.info("调用工具 %s args=%s", name, args)
                result = await tool_exec(name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })
        return ""
