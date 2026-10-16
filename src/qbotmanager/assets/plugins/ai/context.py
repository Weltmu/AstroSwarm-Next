import json
import os
import time
from typing import List, Dict
import nonebot_plugin_localstore as store

CONTEXT_FILE = store.get_plugin_data_file("aichat_context.json")
MAX_CONTEXT_LENGTH = 20  # 每个对话最大消息数

# { "user_id_or_group_id": [{"role": "user|assistant|system", "content": "...", "ts": 1699999999.0}] }
# `ts` 是 2026-10-11 加的（老板报的"空@把一小时前的内容当问题答了"：以前**根本没存时间**，
# 也就不可能做"上文回溯时间上限"）。老数据没有 ts，由 prompt_window 按"很久以前"处理。
_contexts: Dict[str, List[Dict[str, str]]] = {}

def load_contexts():
    global _contexts
    if os.path.exists(CONTEXT_FILE):
        try:
            with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
                _contexts = json.load(f)
        except Exception:
            _contexts = {}

def save_contexts():
    with open(CONTEXT_FILE, "w", encoding="utf-8") as f:
        json.dump(_contexts, f, ensure_ascii=False, indent=2)

def get_context(key: str) -> List[Dict[str, str]]:
    return _contexts.get(key, [])

def add_message(key: str, role: str, content: str, ts: float = None, user_id: str = ""):
    """记一条上下文（带时间戳；`user_id` 是**这条是谁说的**）。

    `user_id` 是 2026-10-15 加的：空 @ 的"微窗口"要把范围收到**同一个人**身上
    （"先打字、再 @ 一下"是同一个人的动作），没有它就分不出哪条是谁说的。
    老数据没有这个字段 —— 读的那侧一律按"不知道是谁"处理，行为与以前一致。
    """
    context = _contexts.get(key, [])
    row = {"role": role, "content": content,
           "ts": float(ts if ts is not None else time.time())}
    if user_id:
        row["user_id"] = str(user_id)
    context.append(row)
    if len(context) > MAX_CONTEXT_LENGTH:
        context = context[-MAX_CONTEXT_LENGTH:]
    _contexts[key] = context
    save_contexts()

def clear_context(key: str):
    if key in _contexts:
        del _contexts[key]
        save_contexts()

def add_user_message_to_group(group_id: str, user_id: str, nickname: str, content: str):
    """
    专门用于群聊环境添加用户消息
    """
    key = f"group_{group_id}"
    user_info = f"用户{user_id}({nickname})"
    user_message = f"{user_info}: {content}"
    add_message(key, "user", user_message)
