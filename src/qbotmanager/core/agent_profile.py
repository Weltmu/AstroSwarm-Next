# -*- coding: utf-8 -*-
"""智能体档案：可选 AI 预设（李清菡等）+ 通道能力矩阵 + 唤醒词生成。

范围：档案数据、通道能力裁剪、唤醒词生成（LLM 一次调用 + 正则兜底）。
李清菡的 agent 进程本体与官方 QQ/iLink 适配器单独接入。
"""
import json
import re
import urllib.error
import urllib.request

DEFAULT_PROFILE_ID = "star_helper"
LIQINGHAN_PROFILE_ID = "liqinghan"

DEFAULT_WAKE_WORDS = ["李清菡", "学姐", "姐姐", "菡姐", "菡菡"]

# 李清菡实际注册的 27 个工具（tools.py _register；2026-10-16 补漏掉的 forget，
# 审计 liqinghan/F1、platform/I —— 档案页工具清单以前少一项，跟真实能力对不上）
LIQINGHAN_TOOLS = [
    "get_time", "recall_message", "mute_user", "mute_all",
    "read_recent_history", "set_reminder", "add_rule", "list_rules",
    "remove_rule", "checkin", "work", "balance", "leaderboard",
    "get_user_profile", "set_relationship", "remember", "recall", "forget",
    "adjust_anger", "product_faq", "describe_image",
    "start_game", "game_guess", "game_end", "game_state",
    "send_face", "generate_image",
]

PROFILES = {
    DEFAULT_PROFILE_ID: {
        "id": DEFAULT_PROFILE_ID,
        "name": "星巢助手",
        "description": "默认助手，无额外功能层，行为由内置 AI 插件与 dsh 预设决定。",
        "tools": [],
        "default_wake_words": [],
    },
    LIQINGHAN_PROFILE_ID: {
        "id": LIQINGHAN_PROFILE_ID,
        "name": "李清菡",
        "description": "真人感智能体：群聊插话、语音、图片描述/生成、游戏、群管、记忆等。"
                       "人格由插件自带的李清菡人设决定（可在 .env 里用 "
                       "QBM_AGENT_PERSONA_EXTRA 追加补充，那段优先级最高）。",
        # ⚠ 2026-10-15 改文案：以前这里写"人格仍跟随程序「人格设定」" —— 与实现不符
        # （persona.py 用的是插件内的固定人设 + PERSONA_EXTRA，程序侧「人格设定」
        # 那一段从没被读进来）。没去改实现，是因为那会把整份李清菡人设换掉，
        # 属于产品决策；先把承诺改成实话，别让用户改了「人格设定」以为生效了。
        "tools": LIQINGHAN_TOOLS,
        "default_wake_words": list(DEFAULT_WAKE_WORDS),
    },
}


def list_profiles() -> list:
    """按展示顺序返回内置档案列表（dict 副本）。"""
    return [dict(PROFILES[pid]) for pid in (DEFAULT_PROFILE_ID, LIQINGHAN_PROFILE_ID)
            if pid in PROFILES]


def load_profile(profile_id: str) -> dict | None:
    """按 id 加载档案；未知 id 返回 None。"""
    prof = PROFILES.get(profile_id)
    return dict(prof) if prof else None


# ---- 通道能力矩阵 ----
# source = "<channel>:<scope>"，如 qq:c2c / qq:group / wechat:private。
# 能力值：chat / memory / voice / image / image_gen / proactive /
#         group_interject / group_admin / recall / tools。
# 官方 QQ 无 CQ 表情代码，因此 qq_face 能力不启用（send_face 工具会被裁剪）。
CHANNEL_CAPABILITIES = {
    "qq:c2c": {"chat", "memory", "voice", "image", "image_gen",
               "proactive", "recall", "tools"},
    "qq:group": {"chat", "memory", "voice", "image", "image_gen",
                 "proactive", "group_interject", "group_admin",
                 "recall", "tools"},
    # ---- QQ 官方机器人（nonebot-adapter-qq）----
    # 2026-10-15 补：以前这里**根本没有 qq_official 的键**，而 `channel_capabilities()`
    # 对未知通道返回**空集** —— 于是官方通道的本地动作权限只剩
    # send_message / memory / network / timer：
    #   · send_image / send_file / send_voice / recall_message 被从模型工具表里整批裁掉；
    #   · `adapters/qq_official.py` 里**早就实现好**的 send_image / send_file /
    #     send_voice / send_card / send_buttons 全成了死代码 —— 连全项目**唯一**
    #     实现了键盘的那条也调不到；
    #   · `tool_packs/group-manager` 只声明 adapters=["qq_official"] 却要求
    #     group_admin，在它自己声明的通道上必然 ToolPermissionError（等于装不上）。
    #
    # 下面这份是**对着适配器实现逐个核对**出来的，不是照抄上面 qq:* 两行：
    #   · image / voice / image_gen：适配器走官方富媒体上传接口，已实现 → 给；
    #   · recall：适配器有 delete_group_message / delete_c2c_message → 给；
    #   · proactive / group_interject：**不给** —— 官方主动消息按日限额度，
    #     发一条少一条，不该默认让定时播报/群聊搭话去烧额度；
    #   · group_admin：**给**（2026-10-15 安全闸就位之后）。之前一直没给，是因为
    #     误以为"官方 API 查不到喊话人在群里的角色"—— 其实官方**更直接**：
    #     群消息事件的 `author.member_role`（member/admin/owner）是必填字段，
    #     适配器已带进 `ctx.extra["member_role"]`，一次接口都不用调。
    #     现在 `tools_bridge._act_mute` 两种通道都用它做"谁喊都动手"那道闸：
    #     非群主/管理员一律拒绝；取不到角色也保守拒绝。
    #     （OneBot 侧反而麻烦些，要另查 get_group_member_info，还可能查失败。）
    "qq_official:c2c": {"chat", "memory", "voice", "image", "image_gen",
                        "recall", "tools"},
    "qq_official:group": {"chat", "memory", "voice", "image", "image_gen",
                          "group_admin", "recall", "tools"},
    "wechat:private": {"chat", "memory", "tools"},
    # 2026-10-15：这两条通道也可能来**群消息**（iLink 事件自带 group_id、飞书事件带
    # chat_id）。以前没有 wechat:group / feishu:group 这两个键，而未知通道返回**空集**
    # → 群里那条消息会被裁掉**全部**工具（连 chat 都被判掉），表现是"在群里回了话但
    # 什么都不会做"。群聊按"只聊天 + 记记忆"给，不给群管/主动/搭话。
    "wechat:group": {"chat", "memory", "tools"},
    "feishu:private": {"chat", "memory", "tools"},
    "feishu:group": {"chat", "memory", "tools"},
    "telegram:private": {"chat", "memory", "tools"},
}

# 工具 → 所需能力（缺省按 chat，即任何聊天通道都可用）
TOOL_CAPABILITIES = {
    "mute_user": "group_admin",
    "mute_all": "group_admin",
    "set_reminder": "proactive",
    "describe_image": "image",
    "send_face": "qq_face",
    "generate_image": "image_gen",
    "remember": "memory",
    "recall": "memory",
    "forget": "memory",
    "recall_message": "recall",
}


def channel_capabilities(source: str) -> set:
    """返回指定来源的能力集合；未知通道一律空（全部禁用）。"""
    return set(CHANNEL_CAPABILITIES.get(source, ()))


def enabled_tools(source: str, tools: list) -> list:
    """按通道能力裁剪工具列表（保持原顺序）。"""
    caps = channel_capabilities(source)
    return [t for t in tools if TOOL_CAPABILITIES.get(t, "chat") in caps]


def can_proactive(source: str) -> bool:
    return "proactive" in channel_capabilities(source)


def can_group_admin(source: str) -> bool:
    return "group_admin" in channel_capabilities(source)


# ---- 唤醒词 ----
WAKE_PROMPT = (
    "根据以下人设文本，生成 8 到 12 个自然的中文唤醒词/称呼"
    "（角色名、昵称、关系称谓，例如：李清菡、学姐、菡姐）。\n"
    "只输出 JSON 字符串数组，不要解释、不要代码块、不要其他文字。\n\n"
    "人设：\n{persona}"
)

_NAME_PATTERNS = [
    # “你是X / 你叫X / 我叫X / 名字是X / 大家都叫我X / 叫我X”
    r"(?:你是|我是|你叫|我叫|名字是|你的名字(?:是|为)?|大家都叫我|都叫我|叫我)"
    r"\s*[「“\"'『]?\s*([\u4e00-\u9fa5A-Za-z0-9_.-]{1,16})",
    # 引号/书名号里的称呼
    r"[「“\"'『]([\u4e00-\u9fa5A-Za-z0-9_.-]{1,12})[」”\"'』]",
]


def extract_wake_words(persona: str) -> list:
    """从人格文本确定性提取称呼（名字/昵称句式 + 引号内称呼）。"""
    if not persona:
        return []
    found = []
    for pat in _NAME_PATTERNS:
        for m in re.finditer(pat, persona):
            word = m.group(1).strip().strip("，。、；：！？,.!? \t")
            if (1 <= len(word) <= 16 and word not in found
                    and not re.search(r"[\s，。、；：！？,.!?\"'“”‘’【】]", word)):
                found.append(word)
            if len(found) >= 16:
                return found
    return found


def parse_wake_words(raw: str) -> list | None:
    """解析模型返回的 JSON 数组；格式不对返回 None。"""
    if not raw:
        return None
    text = re.sub(r"^```(?:json)?[ \t]*\n?", "", raw.strip())
    text = re.sub(r"\n?```[ \t]*$", "", text)
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    words = []
    for item in data:
        if not isinstance(item, str):
            continue
        word = item.strip()
        if (1 <= len(word) <= 16 and word not in words
                and not re.search(r"[\s，。、；：！？,.!?\"'“”‘’【】]", word)):
            words.append(word)
        if len(words) >= 16:
            break
    return words or None


def generate_wake_words(persona: str, call_llm, default: list | None = None) -> list:
    """按人格生成唤醒词：LLM 一次调用 → 解析失败走正则 → 兜底默认词。"""
    default = list(default) if default else list(DEFAULT_WAKE_WORDS)
    if not persona or not persona.strip():
        return default
    prompt = WAKE_PROMPT.format(persona=persona.strip())
    raw = ""
    try:
        raw = call_llm(prompt)
    except Exception:  # noqa: BLE001 —— 生成失败必须走兜底，不能中断保存流程
        raw = ""
    words = parse_wake_words(raw) if raw else None
    if words:
        return words
    words = extract_wake_words(persona)
    return words or default


def openai_chat(prompt: str, api_url: str, api_key: str, model: str,
                timeout: int = 60) -> str:
    """OpenAI 兼容 chat/completions 单轮调用，返回首条消息文本。

    额度策略（老板 2026-10-11 定：**不设上限 + 适配思考型模型**）：
      · 请求里**不带 `max_tokens`** —— 交给服务商按模型自身上限走；
      · 思考型模型（deepseek 等）的 `reasoning_content` 也计入输出额度，以前写死 800
        会把正文挤空 → 上层把"空回复"当成"没生成出来"（10-10/11 同一坑踩过两次）；
      · 正文为空时退回读 `reasoning_content`，实在没有才报一句能看懂的话。
    """
    base = (api_url or "").strip().rstrip("/")
    if not base or not api_key or not model:
        raise ValueError("语言模型接口地址 / 密钥 / 模型未配置")
    url = base + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + api_key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        message = data["choices"][0]["message"] or {}
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError("模型返回格式异常: " + str(e)) from e
    text = str(message.get("content") or "").strip()
    if text:
        return text
    thought = str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
    if thought:
        return thought          # 正文空但思考有内容：思考里通常也给了答案
    finish = ""
    try:
        finish = str(data["choices"][0].get("finish_reason") or "")
    except Exception:  # noqa: BLE001
        pass
    raise ValueError(
        f"模型（{model}）返回了空内容" + (f"，finish_reason={finish}" if finish else "") +
        "。换个模型再试（换成非思考型最稳），或检查服务商那边的输出上限。")
