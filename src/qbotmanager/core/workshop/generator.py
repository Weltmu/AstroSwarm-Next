# -*- coding: utf-8 -*-
"""工坊生成流程：需求 → 方案 → 生成 → 自动检查 → 自动重写(≤2) → 沙箱干跑。

设计要点：
- 生成模型独立于「AI 大脑」（core/workshop/config.py），且强制强模型；
- 两段式：先出方案给用户看，再写代码（一次写对概率高很多）；
- 检查不过自动让 AI 重写（最多 2 次），错误全部转成大白话；
- 干跑在真沙箱里跑（动作全部拒绝），保证不会真的发消息/联网。

2026-10-13（老板：写大工程 + token 消耗要让用户心里有数）：
- **分文件模式**：工具数 ≥ CHUNK_TOOLS_THRESHOLD（或整包输出被截断）时，先出 manifest、
  再一个文件一次地写 —— 每次输出都小，长插件不会顶到模型输出上限把 JSON 截断；
- **用量记账**：每次模型调用的 usage 累加进 `usage`，随 build() 结果返回（UI 报实际消耗）。
"""
import asyncio
import json
import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

from ..agent.sandbox import run_tool
from ..agent.tool import ToolContext
from . import config as ws_config
from . import installer, prompts
from .validator import errors, plain_report, to_plain, validate_files

MODEL_TIMEOUT = 240
MAX_REPAIRS = 2
# 工具数 ≥ 这个值 → 走"分文件写"（estimator 里有同名常量，有测试钉着两处一致）
CHUNK_TOOLS_THRESHOLD = 3


class WorkshopError(RuntimeError):
    """工坊错误：plain 是给用户看的大白话，detail 是技术细节。"""

    def __init__(self, plain: str, detail: str = ""):
        super().__init__(plain)
        self.plain = plain
        self.detail = detail


# ---- 模型调用（OpenAI 兼容 + Anthropic）----


def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        req.add_header(key, value)
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 —— 只连用户自己配的服务商
        return json.loads(resp.read().decode("utf-8", "replace"))


def _add_usage(usage: dict, data: dict) -> None:
    """把服务商返回的 usage 累加进 usage{input, output, calls}（没有 usage 也只记次数）。"""
    u = ((data or {}).get("usage") or {}) if isinstance(data, dict) else {}
    tin = u.get("prompt_tokens", u.get("input_tokens", 0)) or 0
    tout = u.get("completion_tokens", u.get("output_tokens", 0)) or 0
    usage["calls"] = int(usage.get("calls") or 0) + 1
    usage["input"] = int(usage.get("input") or 0) + int(tin)
    usage["output"] = int(usage.get("output") or 0) + int(tout)


def call_model(settings, system: str, user: str, timeout: int = MODEL_TIMEOUT,
               budget: int = 0, usage: dict | None = None) -> str:
    """按工坊配置调模型；失败抛 WorkshopError（大白话 + 细节）。

    额度策略（老板 2026-10-11 定）：**不设上限** —— 默认请求里**不带 `max_tokens`**，
    交给服务商按模型自身上限走。只有两种例外才显式给数值：
      · Anthropic 的 `/v1/messages` **必填** `max_tokens` → 给一个大值；
      · 服务商嫌数值太大报错 → 依次降到下一个档（见 _BUDGET_LADDER）。
    思考型模型适配：
      · 正文为空时先退回读 `reasoning_content` / Anthropic 的 `thinking` 分片
        （思考里通常也写了 JSON，交给 parse_json 抠）；
      · 正文与思考都空 → 用显式大额度**重试一次**（有些服务商默认额度极小）；
      · 仍然空才报错，报错里带模型名与 finish_reason，并提示换模型。
    """
    cfg = ws_config.resolved(settings)
    key = str(cfg.get("provider") or "")
    if key not in ws_config.PROVIDER_KEYS:
        raise WorkshopError(
            f"插件工坊还没配模型（必须用强模型：{ws_config.AGENT_CAPABLE_HINT}）。")
    if not cfg.get("api_key"):
        raise WorkshopError("插件工坊的 API Key 是空的，先在工坊页里填上。")
    api_url = str(cfg.get("api_url") or "").rstrip("/")
    model = str(cfg.get("model") or "")
    protocol = str(cfg.get("protocol") or "")
    finish = ""
    payload: dict = {"model": model, "messages": [{"role": "user", "content": user}]}
    headers = {"Authorization": "Bearer " + cfg["api_key"]}
    url = api_url + "/chat/completions"
    if protocol == "anthropic":
        # Anthropic 必填 max_tokens：用大值，不吃额度限制
        payload = {"model": model, "max_tokens": budget or _ANTHROPIC_BUDGET,
                   "system": system, "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
        url = api_url + "/v1/messages"
    else:
        payload["temperature"] = 0.3
        payload["messages"] = [{"role": "system", "content": system},
                               {"role": "user", "content": user}]
        if budget > 0:
            payload["max_tokens"] = budget      # 0 = 不限制：请求里干脆不带
    try:
        data = _post_json(url, payload, headers, timeout)
        if protocol == "anthropic":
            parts = data.get("content") or []
            text = "".join(str(p.get("text") or "") for p in parts
                           if isinstance(p, dict))
            finish = str(data.get("stop_reason") or "")
        else:
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            finish = str(choice.get("finish_reason") or "")
            text = str(message.get("content") or "")
    except WorkshopError:
        raise
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        # 服务商嫌 max_tokens 太大 → 按档位降一次再试
        lower = _next_budget(budget if budget > 0 else _BUDGET_LADDER[0])
        if lower and _looks_like_budget_error(detail):
            return call_model(settings, system, user, timeout, budget=lower, usage=usage)
        raise WorkshopError(
            "连不上生成模型。请检查工坊的 API Key / 网络 / 模型名是否对。",
            detail[:300]) from exc

    if usage is not None:
        _add_usage(usage, data)
    thought = ""
    if not text.strip():
        # 正文空：思考型模型的典型症状（内容写在 reasoning/thinking 里）
        if protocol == "anthropic":
            parts = (data.get("content") or []) if isinstance(data, dict) else []
            thought = "".join(str(p.get("thinking") or "") for p in parts
                              if isinstance(p, dict))
        else:
            message = ((data.get("choices") or [{}])[0].get("message") or {}) \
                if isinstance(data, dict) else {}
            thought = str(message.get("reasoning_content") or message.get("reasoning") or "")
        if thought.strip():
            return thought          # 思考里通常也写了 JSON，交给 parse_json 抠
        # 正文与思考都空 → 有些服务商默认额度极小，用显式大额度重试一次
        if budget == 0:
            return call_model(settings, system, user, timeout,
                              budget=_BUDGET_LADDER[0], usage=usage)
        raise WorkshopError(
            f"模型（{model or '未填'}）连续两次返回空内容"
            f"{'，finish_reason=' + finish if finish else ''}。"
            "换个模型再试（工坊里换成非思考型最稳），或检查服务商那边的输出上限设置。")
    return text


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

# ---- 额度策略（老板 2026-10-11：不设上限 + 适配思考型模型）----
# 常规请求**不带 max_tokens**（交给服务商按模型自身上限走）；
# 只有在"必须给值"或"服务商嫌大"时才用到这组档位。
_BUDGET_LADDER = (32768, 16384, 8192, 4096)     # 依次降档
_ANTHROPIC_BUDGET = 32768                        # /v1/messages 必填 max_tokens


def _next_budget(current: int) -> int:
    """给下一次重试挑一个更小的额度；已经到底就返回 0（放弃重试）。"""
    for value in _BUDGET_LADDER:
        if value < current:
            return value
    return 0


def _looks_like_budget_error(detail: str) -> bool:
    """判断服务商报错是不是"max_tokens 不合法/超限"。"""
    low = (detail or "").lower()
    marks = ("max_tokens", "max tokens", "max_output_tokens", "maximum",
             "too large", "exceed", "invalid_request_error")
    return any(m in low for m in marks)


def parse_json(text: str) -> dict:
    """从模型输出里抠出 JSON（容忍 ```json 代码块和前后废话）。"""
    raw = str(text or "").strip()
    match = _FENCE.search(raw)
    if match:
        raw = match.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        raise WorkshopError("AI 没有按格式返回 JSON，重试或换个模型。", raw[:400])
    chunk = raw[start:end + 1]
    try:
        data = json.loads(chunk)
    except Exception as exc:  # noqa: BLE001
        raise WorkshopError("AI 返回的 JSON 不完整（多半是被截断了），重试或换个模型。",
                            f"{exc} | {chunk[:300]}") from exc
    if not isinstance(data, dict):
        raise WorkshopError("AI 返回的顶层不是 JSON 对象。", chunk[:300])
    return data


# ---- 已有插件：查重用的工具名 / id（只读 manifest，不加载代码）----


def collect_existing(settings) -> dict:
    tools, ids = set(), set()
    root = installer.packs_root(settings)
    if root.exists():
        for pack in sorted(root.iterdir()):
            manifest = pack / "manifest.json"
            if not manifest.exists():
                continue
            ids.add(pack.name)
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                for tool in (data.get("tools") or []):
                    if isinstance(tool, dict) and tool.get("name"):
                        tools.add(str(tool["name"]))
            except Exception:  # noqa: BLE001
                continue
    return {"tools": sorted(tools), "ids": sorted(ids)}


# ---- 三个步骤 ----


def make_plan(settings, need: str, channels: list, usage: dict | None = None) -> dict:
    existing = collect_existing(settings)
    cfg = ws_config.resolved(settings)
    prompt = prompts.plan_prompt(need, existing["tools"], channels)
    text = call_model(settings, prompts.PLAN_SYSTEM, prompt, usage=usage)
    plan = parse_json(text)
    plan["id"] = installer.unique_id(settings, str(plan.get("id") or "ai_pack"))
    plan.setdefault("name", plan["id"])
    plan.setdefault("what", "")
    plan.setdefault("tools", [])
    plan.setdefault("adapters", [])
    plan.setdefault("permissions", [])
    plan["model"] = f"{cfg.get('provider_name')} · {cfg.get('model')}"
    return plan


def files_from_payload(payload: dict) -> dict:
    """把模型给的 {manifest, tools} 组装成 {相对路径: 内容}。"""
    manifest = payload.get("manifest")
    tools = payload.get("tools")
    if not isinstance(manifest, dict) or not isinstance(tools, dict):
        raise WorkshopError("AI 输出的插件结构不对（缺 manifest 或 tools）。",
                            json.dumps(payload, ensure_ascii=False)[:400])
    manifest.setdefault("kind", "tool-pack")
    manifest.setdefault("version", "1.0.0")
    manifest["sandbox"] = True
    files = {"manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2)}
    for name, code in tools.items():
        fname = str(name).replace("\\", "/").split("/")[-1]
        if not fname.endswith(".py"):
            fname += ".py"
        files[f"tools/{fname}"] = str(code)
    for tool in (manifest.get("tools") or []):
        if isinstance(tool, dict) and tool.get("name"):
            files.setdefault(f"tools/{tool['name']}.py", "import json\n\n\n"
                             "def handle(ctx, args):\n"
                             "    return json.dumps({\"ok\": False, "
                             "\"error\": \"not_implemented\"}, ensure_ascii=False)\n")
    return files


def generate_files(settings, need: str, plan: dict, usage: dict | None = None) -> dict:
    existing = collect_existing(settings)
    text = call_model(settings, prompts.CODE_SYSTEM,
                      prompts.code_prompt(need, plan, existing["tools"]), usage=usage)
    return files_from_payload(parse_json(text))


def generation_mode(plan: dict) -> str:
    """工具数 ≥ 阈值 → "chunked"（分文件写）；否则 "single"（整包一次写成）。"""
    tools = [t for t in ((plan or {}).get("tools") or [])
             if isinstance(t, dict) and t.get("name")]
    return "chunked" if len(tools) >= CHUNK_TOOLS_THRESHOLD else "single"


_CODE_FENCE = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", re.S)


def _extract_code(text: str, name: str) -> str:
    """从"只写一个文件"的输出里抠出代码（代码块优先；模型又回 JSON 也尽力抠）。"""
    raw = str(text or "").strip()
    match = _CODE_FENCE.search(raw)
    if match:
        return match.group(1).strip("\r\n")
    if raw.startswith("{"):
        try:
            data = parse_json(raw)
            tools = data.get("tools") or {}
            for key, value in tools.items():
                if str(name) in str(key) or str(key).strip() in (name, name + ".py"):
                    return str(value)
            if len(tools) == 1:
                return str(next(iter(tools.values())))
        except WorkshopError:
            pass
    return raw


def generate_manifest(settings, need: str, plan: dict, usage: dict | None = None) -> dict:
    """分文件模式第一步：只出 manifest.json（输出小、不截断）。"""
    text = call_model(settings, prompts.MANIFEST_SYSTEM,
                      prompts.manifest_prompt(need, plan), usage=usage)
    data = parse_json(text)
    if not isinstance(data.get("tools"), list) or not data["tools"]:
        raise WorkshopError("AI 输出的 manifest 不完整（tools 为空），重试或换个模型。",
                            json.dumps(data, ensure_ascii=False)[:300])
    return data


def generate_file(settings, need: str, plan: dict, name: str, files: list,
                  issues: list | None = None, usage: dict | None = None) -> str:
    """分文件模式第二步：只写 tools/<name>.py 这一个文件。"""
    text = call_model(settings, prompts.FILE_SYSTEM,
                      prompts.file_prompt(need, plan, name, files, issues),
                      usage=usage)
    return _extract_code(text, name)


def generate_files_chunked(settings, need: str, plan: dict,
                           usage: dict | None = None) -> dict:
    """分文件写：manifest 1 次 + 每个工具 1 次（大工程的兜底/默认路径）。"""
    manifest = generate_manifest(settings, need, plan, usage=usage)
    manifest.setdefault("kind", "tool-pack")
    manifest.setdefault("version", "1.0.0")
    manifest["sandbox"] = True
    files = {"manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2)}
    names = [str(t.get("name")) for t in (manifest.get("tools") or [])
             if isinstance(t, dict) and t.get("name")]
    if not names:
        names = [str(t.get("name")) for t in (plan.get("tools") or [])
                 if isinstance(t, dict) and t.get("name")]
    for name in names:
        files[f"tools/{name}.py"] = generate_file(
            settings, need, plan, name, sorted(files), usage=usage)
    return files


def repair_files(settings, files: dict, issues: list, usage: dict | None = None,
                 chunked: bool = False, need: str = "", plan: dict | None = None) -> dict:
    if chunked:
        return _repair_chunked(settings, need, plan or {}, files, issues, usage=usage)
    plain = to_plain(issues)[:12]
    technical = [{"code": i.get("code"), "detail": str(i.get("detail"))[:200]}
                 for i in issues if i.get("detail")][:12]
    text = call_model(settings, prompts.REPAIR_SYSTEM,
                      prompts.repair_prompt(files, plain + technical), usage=usage)
    return files_from_payload(parse_json(text))


def _repair_chunked(settings, need: str, plan: dict, files: dict, issues: list,
                    usage: dict | None = None) -> dict:
    """分文件模式的重写：manifest 重出一次 + 逐个工具重写（输出都小，不会截断）。"""
    plain = to_plain(issues)[:12]
    out = {}
    manifest = generate_manifest(settings, need, plan, usage=usage)
    manifest.setdefault("kind", "tool-pack")
    manifest.setdefault("version", "1.0.0")
    manifest["sandbox"] = True
    out["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2)
    for rel in sorted(k for k in files if str(k).startswith("tools/")):
        name = str(rel).split("/")[-1].rsplit(".", 1)[0]
        out[rel] = generate_file(settings, need, plan, name,
                                 sorted(set(files) | set(out)), plain, usage=usage)
    return out


def dry_run(files: dict, plan: dict, timeout: int = 12) -> list:
    """在真沙箱里跑一遍：动作全部拒绝，只验证"能跑、返回 JSON"。"""
    issues = []
    tmp = Path(tempfile.mkdtemp(prefix="ws_dry_"))
    try:
        for rel, content in files.items():
            path = tmp / str(rel).replace("\\", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content), encoding="utf-8")
        ctx = ToolContext(
            permissions={"send_message", "group_admin", "timer", "memory",
                         "network", "media"},
            platform="qq_official", scene="group", user_id="0", group_id="0",
            nickname="测试", is_superuser=True)

        async def host(action, params, ctx_):
            return {"ok": False, "error": "dry_run", "action": action}

        examples = {}
        for tool in (plan.get("tools") or []):
            if isinstance(tool, dict) and tool.get("name"):
                examples[str(tool["name"])] = tool.get("example_args") or {}
        names = [t.get("name") for t in (plan.get("tools") or [])
                 if isinstance(t, dict) and t.get("name")]
        if not names:
            names = [p.stem for p in (tmp / "tools").glob("*.py")]
        for name in names:
            args = examples.get(str(name)) or {}

            async def _one():
                return await run_tool(tmp, str(name), args, ctx, host,
                                      timeout=timeout)
            try:
                out = asyncio.run(_one())
            except Exception as exc:  # noqa: BLE001
                issues.append({"level": "error", "code": "dry_run_failed",
                               "plain": f"工具 {name} 试跑报错（跑不起来）。",
                               "detail": str(exc)[:300]})
                continue
            try:
                data = json.loads(out)
            except Exception:  # noqa: BLE001
                issues.append({"level": "error", "code": "dry_run_bad_json",
                               "plain": f"工具 {name} 没有返回合法 JSON。",
                               "detail": str(out)[:200]})
                continue
            if not isinstance(data, dict) or "ok" not in data:
                issues.append({"level": "error", "code": "dry_run_no_ok",
                               "plain": f"工具 {name} 的返回值缺少 ok 字段。",
                               "detail": str(data)[:200]})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return issues


def build(settings, need: str, plan: dict | None = None, channels: list | None = None,
          on_log=None, max_repairs: int = MAX_REPAIRS,
          do_dry_run: bool = True) -> dict:
    """跑完整条流水线（不安装）；返回 {"ok", "plan", "files", "issues", ...}。"""
    log = on_log or (lambda msg: None)
    ok, why = ws_config.is_ready(settings)
    if not ok:
        raise WorkshopError(why)
    channels = list(channels or [])
    if plan is None:
        log("正在让 AI 出方案…")
        usage: dict = {}
        plan = make_plan(settings, need, channels, usage=usage)
    else:
        usage = {}
    log(f"方案：{plan.get('name')} · {plan.get('what') or ''}")
    mode = generation_mode(plan)
    _tools_n = len([t for t in (plan.get("tools") or [])
                    if isinstance(t, dict) and t.get("name")])
    log("正在生成插件代码…（%s，%d 个工具）"
        % ("分文件写" if mode == "chunked" else "整包一次写成", max(1, _tools_n)))
    if mode == "chunked":
        files = generate_files_chunked(settings, need, plan, usage=usage)
    else:
        try:
            files = generate_files(settings, need, plan, usage=usage)
        except WorkshopError as exc:
            # 大工程整包输出会顶到模型输出上限、JSON 被截断 → 自动改分文件写一遍
            if "截断" not in (exc.plain + exc.detail):
                raise
            log("整包输出被截断 → 自动改成分文件写…")
            mode = "chunked"
            files = generate_files_chunked(settings, need, plan, usage=usage)
    issues = []
    attempts = 0
    while attempts <= max_repairs:
        existing = collect_existing(settings)
        issues = validate_files(files, set(existing["tools"]))
        if not errors(issues):
            break
        attempts += 1
        if attempts > max_repairs:
            break
        log(f"自动检查发现 {len(errors(issues))} 个问题，让 AI 改第 {attempts} 遍…")
        try:
            files = repair_files(settings, files, errors(issues), usage=usage,
                                 chunked=(mode == "chunked"), need=need, plan=plan)
        except WorkshopError as exc:
            raise WorkshopError("AI 重写失败：" + exc.plain, exc.detail) from exc
    if errors(issues):
        manifest = {}
        try:
            manifest = json.loads(files.get("manifest.json") or "{}")
        except Exception:  # noqa: BLE001
            manifest = {}
        return {"ok": False, "plan": plan, "files": files, "issues": issues,
                "attempts": attempts, "report": plain_report(manifest)
                if manifest else {}, "need": need, "mode": mode, "usage": usage}
    if do_dry_run:
        log("正在沙箱里试跑…")
        dry = dry_run(files, plan)
        issues = issues + dry
        if errors(dry) and attempts < max_repairs:
            attempts += 1
            log("试跑没过，让 AI 再改一遍…")
            files = repair_files(settings, files, errors(dry), usage=usage,
                                 chunked=(mode == "chunked"), need=need, plan=plan)
            existing = collect_existing(settings)
            issues = validate_files(files, set(existing["tools"])) + dry_run(files, plan)
    try:
        manifest = json.loads(files.get("manifest.json") or "{}")
    except Exception:  # noqa: BLE001
        manifest = {}
    return {
        "ok": not errors(issues),
        "plan": plan,
        "files": files,
        "issues": issues,
        "attempts": attempts,
        "report": plain_report(manifest) if manifest else {},
        "need": need,
        "model": ws_config.resolved(settings).get("model") or "",
        "mode": mode,
        "usage": usage,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
