from nonebot import logger
from . import reply_format
from .manager import chat_manager
import json

# 2026-10-11 启动加速：`openai` 这个包 import 要 ~0.4 秒（实测）。以前它在 import
# 期就把它拉进来，等于每次重启机器人白等 0.4 秒。现在改成**第一次真的要发请求
# 时才导入**（那次请求本来就要等网络，0.4 秒没人会察觉）。
_OPENAI = {}


def _openai():
    """按需导入 openai；导入失败抛原异常（调用方本来就有 try/except）。"""
    client_cls = _OPENAI.get("cls")
    errors = _OPENAI.get("errors")
    if client_cls is None:
        from openai import AsyncOpenAI, BadRequestError

        _OPENAI["cls"] = AsyncOpenAI
        _OPENAI["errors"] = BadRequestError
        client_cls, errors = AsyncOpenAI, BadRequestError
    return client_cls, errors


# 单次请求超时与重试（2026-10-15 审计 D：以前**没传 timeout**，用的是 SDK 默认
# 600 秒 + 自动重试 —— 上游一卡，这条请求能占着好几分钟不返回，同一会话后面的
# 消息又各开一条，回复乱序、上下文写入顺序与展示顺序对不上）。
REQUEST_TIMEOUT = 90.0
REQUEST_MAX_RETRIES = 1


def _client(api_key: str, base_url: str):
    """建一个 AsyncOpenAI 客户端（openai 包在这里才被导入）。

    ⚠ 必须显式给 timeout / max_retries：SDK 默认值太大，卡住的请求会把
    整个会话拖住（见上面常量处的说明）。
    """
    cls, _ = _openai()
    return cls(api_key=api_key, base_url=base_url,
               timeout=REQUEST_TIMEOUT, max_retries=REQUEST_MAX_RETRIES)


def get_current_time() -> str:
    """获取当前时间"""
    import datetime
    now = datetime.datetime.now()
    return f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"

def _extract_message_text(message) -> str:
    """提取回复文本：优先 content，为空时回退 reasoning_content（兼容带推理的模型）"""
    content = getattr(message, "content", None) or ""
    if content.strip():
        return content
    reasoning = getattr(message, "reasoning_content", None) or ""
    return reasoning


def _extract_content_text(message) -> str:
    """只取正式回复 content，不回退 reasoning_content（思考过程不能直接发给用户）"""
    return (getattr(message, "content", None) or "").strip()



async def get_short_ai_text(prompt: str, max_tokens: int = 200) -> str:
    """调用当前 AI 配置生成一句短文本（经济/问候等功能的 AI 润色），失败返回空字符串"""
    ai_config = chat_manager.get_current_ai_config()
    if not ai_config:
        return ""
    try:
        client = _client(ai_config.get("api_key", ""), ai_config.get("api_url", ""))
        messages = [
            {"role": "system", "content": chat_manager.get_personality()},
            {"role": "user", "content": prompt},
        ]
        for attempt in range(2):
            completion_obj = await client.chat.completions.create(
                model=ai_config.get("model", ""),
                messages=messages,
                max_tokens=max_tokens,
            )
            reply = _extract_content_text(completion_obj.choices[0].message)
            if reply:
                return reply
            if attempt == 0:
                messages.append({"role": "user", "content": "请直接输出最终回复，不要输出思考过程。"})
        return ""
    except Exception as e:
        logger.warning(f"AI 短文本生成失败: {e}")
        return ""



def _extract_json_text(text: str) -> str:
    """从文本中截取第一段完整 JSON（用于从推理内容里提取回复）"""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return ""


# 定义本地工具列表
from qbotmanager.core.agent.runtime import get_runtime
from qbotmanager.core.agent.tool import (
    ToolContext,
    ToolPermissionError,
    ToolUnavailableError,
)
from .tools_bridge import build_tool_context, execute_tool_with_actions

_TOOL_PERMISSIONS = {
    "send_message",
    "group_admin",
    "timer",
    "memory",
    "network",
    "media",
}

def _plugin_action_names() -> set:
    """插件自己的动作名（memory_write / reminder_add / fetch_text …）。

    2026-10-12：这些名字以前**没有执行入口** —— chat 里只认"运行时注册表"和"MCP"，
    于是模型正确调用 memory_write 时被当成 MCP 工具，回它一句"名字好像不对"。
    """
    try:
        from .tools_bridge import LOCAL_TOOL_SCHEMAS

        return set(LOCAL_TOOL_SCHEMAS)
    except Exception:  # noqa: BLE001
        return set()


async def _local_action_or_error(name: str, args: dict, tool_ctx, exc: Exception,
                               log) -> str:
    """运行时工具在这条通道不可用时的兜底：同名本地动作顶上。

    2026-10-12 老板实测（禁言）：装了「群管理能力包」（只声明 qq_official）后，
    第三方 OneBot 群里它会先按运行时工具执行 → 通道检查直接判"不支持"，
    而本地动作其实两边通道都能干 —— 于是用户只听到"这个端弄不了"。
    这里把"通道不支持"翻译成"改走本地同类动作"，两边实现谁行谁上。
    """
    import json as _json

    if name not in _plugin_action_names():
        return _json.dumps({"ok": False, "error": "tool_unavailable",
                            "detail": str(exc)[:200]}, ensure_ascii=False)
    try:
        from .tools_bridge import do_action

        out = _json.dumps(await do_action(name, args, tool_ctx), ensure_ascii=False)
        log.info(f"运行时工具 {name} 在本通道不可用 → 已改走本地动作：{out[:160]}")
        return out
    except Exception as e:  # noqa: BLE001 —— 本地也失败就如实回报原始原因
        log.warning(f"本地动作兜底 {name} 也失败：{e}")
        return _json.dumps({"ok": False, "error": "tool_unavailable",
                            "detail": str(exc)[:200]}, ensure_ascii=False)


def _tool_is_disabled(name: str) -> bool:
    """这个工具在客户端「本机能力清单」里被关掉了吗（2026-10-15 审计 C2）。

    以前"关掉"只是**从给模型看的清单里删掉** —— 模型从历史对话/幻觉里复现出
    函数名照样能执行（"藏"不是"关"）。现在执行入口也查一次。
    """
    try:
        return str(name) in set(chat_manager.get_disabled_tools())
    except Exception:  # noqa: BLE001 —— 读不到清单就当没关（不能因此卡住一切）
        return False


async def _execute_one_tool(runtime, name: str, args: dict, tool_ctx, tool_call_id: str,
                             logger_=None, memo: dict | None = None) -> dict:
    """执行一个工具调用（运行时工具 / 插件本地动作 / MCP 三选一），返回要回灌的消息 dict。"""
    import json as _json

    log = logger_ or logger
    if _tool_is_disabled(name):
        log.info("工具 %s 在本机能力清单里是关的 → 拒绝执行（不是「看不见」，是「用不了」）"
                 % name)
        return {"role": "tool", "tool_call_id": tool_call_id, "name": name,
                "content": _json.dumps(
                    {"ok": False, "error": "tool_disabled",
                     "detail": "这个能力在客户端「本机能力清单」里被关掉了，换别的方式回答或如实告诉用户"},
                    ensure_ascii=False)}
    key = (name, _json.dumps(args or {}, sort_keys=True, ensure_ascii=False))
    if memo is not None and key in memo:
        log.info(f"同一回合内重复调用 {name} → 直接用上次结果，不再执行")
        return {"role": "tool", "tool_call_id": tool_call_id, "name": name,
                "content": str(memo[key])}
    if runtime is not None and runtime.registry.get(name):
        try:
            result = await execute_tool_with_actions(runtime, name, args, tool_ctx)
        except ToolUnavailableError as exc:
            # 通道不支持（例：群管理能力包只声明 qq_official）→ 同名本地动作顶上
            result = await _local_action_or_error(name, args, tool_ctx, exc, log)
        except Exception as exc:  # noqa: BLE001
            result = _json.dumps({"ok": False, "error": "tool_unavailable",
                                  "detail": str(exc)[:200]}, ensure_ascii=False)
        log.info(f"Agent 工具结果: {str(result)[:200]}")
    elif name in _plugin_action_names():
        try:
            from .tools_bridge import do_action

            result = _json.dumps(await do_action(name, args, tool_ctx), ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            result = _json.dumps({"ok": False, "error": "action_error",
                                  "detail": str(exc)[:200]}, ensure_ascii=False)
        log.info(f"本地动作结果: {str(result)[:220]}")
    else:
        try:
            from .mcp_manager import mcp_client

            result = str(await mcp_client.call_tool(name, args))
        except Exception as exc:  # noqa: BLE001
            result = f"MCP工具调用失败: {exc}"
    return {"role": "tool", "tool_call_id": tool_call_id, "name": name,
            "content": str(result)}


async def get_chat_reply_with_tools(messages: list, is_group: bool = False,
                                    msg=None, channel=None,
                                    stats: dict | None = None) -> str:
    """
    结合function call和分段回复的聊天回复函数 - 使用消息副本处理工具调用

    msg / channel：当前消息与通道发送器（brain 传入）。传了就按通道裁剪工具、
    把发送/记忆/提醒/动作回执接进工具上下文；不传时行为与旧版完全一致。
    stats：可选的可变字典（brain 传入）—— 本轮真的调过几个工具会写进
    `stats["tool_calls"]`。调用方据此决定"这轮只发一条"（工具已经单独发过东西，
    再拆多条就是每个功能各说一句的乱）。
    """
    # 检查全局开关
    if not chat_manager.is_chat_enabled():
        raise Exception("聊天功能当前已关闭")
    
    # 2026-10-12 老板："还是不行，它连工具都没调"。真因：这里以前只要 MCP 开关没开就直接
    # 回退"普通聊天"，把**整条工具路径**跳过了 —— 模型压根没拿到工具清单，
    # 只能把工具调用当文字念出来（日志里那串 <||DSML|| invoke ...> 就是它硬编的）。
    # 现在：**只要本地有工具就走工具路径**；MCP 开关只管"要不要挂 MCP 服务器"。
    
    # 获取当前AI配置
    ai_config = chat_manager.get_current_ai_config()
    
    if not ai_config:
        raise Exception("未配置服务，请使用 'ac ai add' 命令添加配置")
    
    try:
        # 创建消息副本用于工具调用处理
        processing_messages = messages.copy()
        _TOOL_MEMO = {}   # 同回合内 (工具名, 参数) -> 结果，重复调用不再执行
        
        # 获取工具列表
        runtime = get_runtime()
        platform = str(getattr(msg, "platform", "") or "")
        all_tools = runtime.schemas(platform).copy()  # 运行时工具（跨平台发送等）
        # 被"本机能力清单"里关掉的能力，这里就不进工具清单（模型看不见 = 真的关掉了）
        try:
            _disabled = set(chat_manager.get_disabled_tools())
            if _disabled:
                _before = len(all_tools)
                all_tools = [x for x in all_tools
                             if (x.get("function") or {}).get("name") not in _disabled]
                if _before != len(all_tools):
                    logger.info(f"按能力清单关掉了 {_before - len(all_tools)} 个工具")
        except Exception:  # noqa: BLE001
            pass
        # 2026-10-12：把**插件自己的动作**也交给模型（记忆读写/提醒/抓网页/发图/群管理…），
        # 按通道权限裁剪。以前只给了运行时那 6 个 → 其余能力模型压根不知道存在。
        try:
            from .tools_bridge import local_tool_schemas
            local_tools = local_tool_schemas(msg)
            try:
                _bad = set(chat_manager.get_disabled_tools())
                local_tools = [x for x in local_tools
                               if (x.get("function") or {}).get("name") not in _bad]
            except Exception:  # noqa: BLE001
                pass
            all_tools.extend(local_tools)
            logger.info("工具清单：运行时 %d 个 + 本地动作 %d 个（通道=%s）"
                        % (len(runtime.schemas(platform)), len(local_tools), platform or "?"))
        except Exception as e:  # noqa: BLE001 —— 本地工具列举失败不能挡住聊天
            logger.warning(f"列举本地工具失败，只用运行时工具: {e}")
        tool_ctx = (
            build_tool_context(msg, channel)
            if msg is not None and channel is not None
            else ToolContext(permissions=_TOOL_PERMISSIONS)
        )
        
        if not all_tools and not chat_manager.is_mcp_enabled():
            logger.info("没有任何可用工具（本地也没有、MCP 也没开），回退普通聊天")
            return await get_chat_reply(messages, is_group)

        # 检查是否有启用的MCP服务器
        enabled_servers = chat_manager.get_enabled_mcp_servers()
        if enabled_servers:
            try:
                from .mcp_manager import mcp_client  # 按需导入（省 0.7s 启动）
                mcp_tools_list = await mcp_client.get_tools()
                mcp_tools = mcp_client.get_openai_tools_format()
                all_tools.extend(mcp_tools)
                logger.info(f"MCP功能已启用，可用工具总数: {len(all_tools)} (Agent: {len(runtime.schemas())}, MCP: {len(mcp_tools)})")
            except Exception as e:
                logger.warning(f"获取MCP工具失败，将只使用本地工具: {e}")
        else:
            logger.info("没有启用的MCP服务器，只使用本地工具")
        
        # 动态创建客户端
        client = _client(ai_config.get("api_key", ""), ai_config.get("api_url", ""))
        
        # 单次调用：模型需要工具就返回 tool_calls，不需要直接给出回复（省一次往返）
        logger.info("Agent 单次调用（tools 自动）")
        # 采样参数**每条消息现读**（温度 / 最大长度改完立即生效，不用重启）
        sampling = chat_manager.sampling_params(4096)
        logger.info(
            "请求参数 - model=%s temperature=%s max_tokens=%s"
            % (ai_config.get("model", ""), sampling.get("temperature"),
               sampling["max_tokens"]))
        # 2026-10-15（审计 C）：搜索开关以前**只在弃用的 get_chat_reply 里读** ——
        # 常规聊天走的都是这条工具路径，于是客户端里打开「联网搜索」没有任何效果
        #（关掉也不会被拦）。这里同样带上搜索参数，两条路一个口径。
        _search_body = None
        try:
            if chat_manager.is_search_enabled():
                _search_body = {"enable_search": True,
                                "search_options": {"forced_search": True}}
                logger.info("联网搜索：开（本回合请求带 enable_search）")
        except Exception:  # noqa: BLE001 —— 读开关失败不能挡住聊天
            _search_body = None
        response = await client.chat.completions.create(
            model=ai_config.get("model", ""),
            messages=[{"role": "system", "content": get_system_prompt(is_group, msg=msg)}]
            + processing_messages,
            tools=all_tools,
            tool_choice="auto",
            **({"extra_body": _search_body} if _search_body else {}),
            **sampling,
        )

        message = response.choices[0].message
        tool_calls = message.tool_calls

        if not tool_calls:
            reply = _extract_content_text(message)
            if reply:
                logger.info("无工具调用，直接返回回复")
                return reply
            logger.info("无工具调用且无内容，回退普通聊天")
            return await get_chat_reply(messages, is_group)
        
        # 如果有工具调用，执行调用（在副本上进行）
        if tool_calls:
            if stats is not None:
                stats["tool_calls"] = int(stats.get("tool_calls") or 0) + len(tool_calls)
            # 留痕：调了哪个工具、参数是什么（2026-10-12 加；以前日志里完全看不见工具调用）
            try:
                _names = [str(getattr(tc.function, "name", "?")) for tc in tool_calls]
                logger.info("检测到工具调用 %d 个：%s" % (len(tool_calls), ", ".join(_names)))
                for _tc in tool_calls:
                    logger.info("  · 工具 %s 参数=%s"
                                % (getattr(_tc.function, "name", "?"),
                                   str(getattr(_tc.function, "arguments", ""))[:200]))
            except Exception:  # noqa: BLE001 —— 日志不能影响主流程
                pass
            
            # 将模型的回复添加到消息副本中
            processing_messages.append({
                "role": "assistant",
                "content": message.content if message.content else "",
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": tool_call.type,
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments
                        }
                    } for tool_call in tool_calls
                ]
            })
            
            # 处理每个工具调用
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)

                logger.info(f"调用函数: {function_name}, 参数: {function_args}")

                # 2026-10-16（对抗性复核 C2）：**首轮**执行也要查「本机能力清单」。
                # 上一版只给收尾循环（_execute_one_tool）加了闸，首轮这里是内联直发 ——
                # 同一被关能力"首轮能执行、收尾被拒"，口径分裂（等于没关干净）。
                if _tool_is_disabled(function_name):
                    logger.info("工具 %s 在本机能力清单里是关的 → 拒绝执行（不是「看不见」，是「用不了」）"
                                % function_name)
                    processing_messages.append({
                        "role": "tool", "tool_call_id": tool_call.id, "name": function_name,
                        "content": json.dumps(
                            {"ok": False, "error": "tool_disabled",
                             "detail": "这个能力在客户端「本机能力清单」里被关掉了，换别的方式回答或如实告诉用户"},
                            ensure_ascii=False),
                    })
                    continue

                # 同回合去重：同一个工具 + 同一组参数只执行一次
                # （2026-10-12：模型一次回合里重复请求同一个调用，导致重复发送 + 两套回复）
                _memo_key = (function_name, json.dumps(function_args, sort_keys=True,
                                                      ensure_ascii=False))
                if _memo_key in _TOOL_MEMO:
                    logger.info(f"同一回合内重复调用 {function_name} → 直接用上次结果，不再执行")
                    processing_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": str(_TOOL_MEMO[_memo_key]),
                    })
                    continue
                
                # 判断是本地工具还是MCP工具
                if runtime.registry.get(function_name):
                    try:
                        function_result = await execute_tool_with_actions(
                            runtime, function_name, function_args, tool_ctx)
                    except ToolUnavailableError as exc:
                        # 通道不支持（例：群管理能力包只声明 qq_official）→ 本地动作顶上
                        function_result = await _local_action_or_error(
                            function_name, function_args, tool_ctx, exc, logger)
                    except (ToolPermissionError, KeyError) as exc:
                        # 权限不够 / 名字不在注册表：如实告诉模型，让它按人设解释
                        function_result = json.dumps(
                            {"ok": False, "error": "tool_unavailable",
                             "detail": str(exc)}, ensure_ascii=False)
                    logger.info(f"Agent 工具结果: {function_result}")
                    _TOOL_MEMO[_memo_key] = function_result

                    processing_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": str(function_result)
                    })
                elif function_name in _plugin_action_names():
                    # 2026-10-12：插件自己的动作（记忆读写 / 提醒 / 抓网页 / 发图 / 群管理…）。
                    # 它们既不进运行时注册表，也不是 MCP 工具 —— 以前没有这条路，
                    # 于是模型调了却被当成 MCP，回一句"名字好像不对"。这里直接派给 do_action。
                    try:
                        from .tools_bridge import do_action

                        _res = await do_action(function_name, function_args, tool_ctx)
                        function_result = json.dumps(_res, ensure_ascii=False)
                    except Exception as exc:  # noqa: BLE001
                        function_result = json.dumps(
                            {"ok": False, "error": "action_error", "detail": str(exc)[:200]},
                            ensure_ascii=False)
                    logger.info(f"本地动作结果: {str(function_result)[:220]}")
                    _TOOL_MEMO[_memo_key] = function_result

                    processing_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": str(function_result)
                    })
                else:
                    # 调用MCP工具
                    try:
                        from .mcp_manager import mcp_client  # 按需导入（省 0.7s 启动）
                        mcp_result = await mcp_client.call_tool(function_name, function_args)
                        processing_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": function_name,
                            "content": str(mcp_result)
                        })
                    except Exception as e:
                        error_msg = f"MCP工具调用失败: {str(e)}"
                        processing_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id, 
                            "name": function_name,
                            "content": error_msg
                        })
        
        # 第二阶段：**继续带工具**多轮收尾。
        # 2026-10-12 修：以前这里用 get_chat_reply()（不带 tools）收尾 —— 模型想再调一个工具时
        # 手里没有工具清单，只能自己编个名字当文字发出来（日志里那串 accept_friend_request）。
        # 现在最多再跑 5 轮，每轮都带 tools；模型不再要工具时把它的正文返回。
        for _round in range(5):
            logger.info(f"工具循环第 {_round + 1} 轮收尾（带工具）")
            resp2 = await client.chat.completions.create(
                model=ai_config.get("model", ""),
                messages=[{"role": "system", "content": get_system_prompt(is_group, msg=msg)}]
                + processing_messages,
                tools=all_tools,
                tool_choice="auto",
                **({"extra_body": _search_body} if _search_body else {}),
                **sampling,
            )
            m2 = resp2.choices[0].message
            follow_calls = getattr(m2, "tool_calls", None)
            if not follow_calls:
                reply = _extract_content_text(m2)
                if reply:
                    return reply
                break
            logger.info("收尾轮又检测到工具调用 %d 个：%s"
                        % (len(follow_calls),
                           ", ".join(str(getattr(c.function, "name", "?")) for c in follow_calls)))
            if stats is not None:
                stats["tool_calls"] = int(stats.get("tool_calls") or 0) + len(follow_calls)
            processing_messages.append({
                "role": "assistant",
                "content": m2.content if m2.content else "",
                "tool_calls": [
                    {"id": c.id, "type": c.type,
                     "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in follow_calls
                ],
            })
            for c in follow_calls:
                try:
                    _args = json.loads(c.function.arguments or "{}")
                except Exception:  # noqa: BLE001
                    _args = {}
                logger.info(f"  收尾轮调用 {c.function.name} 参数={str(_args)[:160]}")
                _row = await _execute_one_tool(runtime, c.function.name, _args, tool_ctx, c.id,
                                                memo=_TOOL_MEMO)
                try:
                    _TOOL_MEMO[(c.function.name,
                                json.dumps(_args or {}, sort_keys=True,
                                           ensure_ascii=False))] = _row.get("content")
                except Exception:  # noqa: BLE001
                    pass
                processing_messages.append(_row)
        # 兜底：实在收不住就退回普通聊天（不带工具，保证一定有一句人话）
        logger.info("工具循环到达上限，回退普通聊天收尾")
        return await get_chat_reply(processing_messages, is_group)
        
    except Exception as e:
        logger.error(f"get_chat_reply_with_tools 发生异常: {e}")
        # 如果工具调用失败，回退到普通聊天模式
        logger.info("工具调用失败，回退到普通聊天模式")
        return await get_chat_reply(messages, is_group)

def get_reply_format(is_group: bool = False):
    base_format = ""
    if is_group:
        base_format += """
你现在在群聊中，请注意：
    - 你的发言会出现在群聊中，所有成员都能看到
    - 保持自然，像普通群成员一样参与讨论
    - 不要表现得像机器人，要有自己的观点和性格
"""
    base_format += """
请严格按照以下JSON格式回复，不要包含任何其他内容（reply 是数组，1~3 个元素都合法）：
{
  "reply": [
    "第一段内容",
    "想分第二条时才加这一段",
    "最多到第三段"
  ]
}
只发一条时就是：{"reply": ["就这一条"]} —— 这是最常见的情况，别硬凑段数。
多段**必须**放进上面的 JSON 数组里，**不要用空格或空行**把几段拼在同一个字符串里。

内容要求：
1. 不要使用markdown格式，只返回纯JSON
2. 分几条**由内容决定**，1~3 条都允许（最多 3 条），别固定成一种：
   - **该分 2~3 条的时候**：安慰对方、解释原因、讲多步的做法/步骤、
     情绪一层层递进、先给个反应再补一句（"诶？"、"你认真的？"）、
     想单独强调一句、吐槽或追问 —— 把后半句另起一段，就像真人打字那样；
   - **就发 1 条的时候**：打招呼、一句"嗯/好的/在的"、报个结果、
     答一个简单事实（一句话就能答完的日常问题）—— 这些别拆。
3. **禁止为了分条而分条**：内容本来是一句话，就只发一条；
   也不许把一条长回复硬拆成好几条（宁可就发一条长的）。
   内容明显切换主题（比如从问题分析转到个人建议）、带代码块或示例、
   回复确实较长时，才分段模仿真人打字时的自然停顿；
   也不要同时给出几个不同版本的回复
4. 每个段落应该是一个完整的句子或者语义单元，结尾不要出现句号
5. 如果是一段代码，保持代码完整作为一个段落
6. 整体风格贴近真实网友：多用'我'开头，带点小错误或口语化表达（如'可能吧'、'反正我觉得'），但别过度啰嗦
7. 工具返回的是结构化数据或执行结果，不要照搬工具原文，由你按当前人设用自己的话组织回复
"""    
    return base_format

def platform_context(msg) -> str:
    """告诉 AI：你现在在哪个平台、什么场景、对面是谁、他绑了哪些平台。

    老板 2026-10-04 要的"像一个人"：它得先知道自己在哪儿说话。
    """
    if msg is None:
        return ""
    try:
        from . import identity_bind
        from .platform import SCENE_GROUP

        platform = identity_bind.PLATFORM_NAMES.get(msg.platform, msg.platform)
        where = "群聊" if msg.scene == SCENE_GROUP else "私聊"
        who = msg.nickname or msg.user_id
        lines = [f"【当前场景】你在{platform}的{where}里，对面是 {who}。"]
        # 2026-10-12（老板：他看不见群管理或群主是谁）：群身份一行，答谁是群主、
        # 以及别去禁言群主都靠它。适配器查好放在 extra 里。
        _gr = ''
        _ex = getattr(msg, 'extra', None)
        if isinstance(_ex, dict):
            _gr = str(_ex.get('group_roles') or '')
        if _gr:
            lines.append(f"【本群身份】{_gr}。")
        bound = identity_bind.describe(getattr(msg, "user_key", "") or "")
        _uk = getattr(msg, "user_key", "") or ""
        _just = identity_bind.take_just_bound(_uk)      # 刚刚绑定成功？（一次性）
        if _just:
            lines.append(
                f"【他刚刚绑定成功】{_just} 现在是同一个人了。"
                "**用你自己的人设自然地说一句就行**（可以有点小得意/随口一提/调侃都行），"
                "别用固定话术、别复述绑定码，也别把这句话原样念出来。")

        if bound:
            lines.append(f"【他的绑定】他在这几个平台是同一个人：{bound}。"
                         "他说「发到我的 QQ / 微信」时，**直接调 send_to_my_platform 工具**（参数 platform=qq / qq_official / wechat，text=要说的话）按绑定发。")
        else:
            lines.append(
                "【他的绑定】他还没绑定其它平台 —— 你现在**没有任何跨平台发送能力**，"
                "消息发不到他的 QQ/微信去。他让你发到别的平台时，只能回一句人话："
                "让他先在这里发「绑定」拿 6 位码，再去那个平台私聊我发「绑定 六位数字」"
                "（或者直接把那 6 位数字发过来）。"
                "**绝对不许说「我发了」「已经发过去了」「发好了」这类话**——"
                "那是骗人（2026-10-12 老板就遇到过：它假装发了，其实什么都没发）。"
                "也别去调 send_to_my_platform 赌一把：没绑定它必然失败。")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 —— 拼场景失败不影响聊天
        return ""


AGENT_RULES = """
【先把事办了，再说话】（重要：你是有工具的助手，不只是陪聊）

收到消息先自问一句：**这件事需不需要动手？**

- **需要动手的**（把消息/文件发到对方别的平台、查资料、记住事情、定时提醒、
  管理群、给自己写插件……）→ **先调用工具把事情办了**，拿到结果再用你自己的口吻回一句。
  **绝对不要嘴上答应却不动手**，也不要说"我做不到"——除非工具真的返回了失败。
- **不需要动手的**（闲聊、吐槽、问意见、接话）→ 直接聊，别为了显得能干硬去调工具。
- 一次做不完可以连着做几步（最多 5 步）：先查、再算、再发，每一步的结果都能接着用。
- 工具名直接用真的：跨平台发消息用 `send_to_my_platform`（platform=qq / qq_official / wechat，
  text=要说的话），发文件/图片用 `send_file_to_my_platform`，查对方绑了哪些平台用 `my_platforms`。
- 工具返回的是结构化数据，**别原样念**给用户：用你的话总结。`queued: true` 只代表
  **已经安排发送**、还没真发出去 —— 这种时候说"我这就发"/"正在发"，**别打包票说"已经发出去了"**。
- 工具报错就如实说清**哪一步没成、为什么**（照 detail 说人话），**绝不许编成功**。
- **绝不许把"工具"这个词露出来**："工具返回就是这个""工具报错""接口不支持"这类话都不行 ✗。
  失败就用一句人话说清"我这边发不出去/那边收不了图"，像人碰到障碍那样说，别像在念日志。
- 对方明确让你做某件事时，做完要说清**你做了什么**（例："我在你 QQ 上发了：在忙呢"），
  而不是只回一句"好的"。

【在群里动手的分寸】
- 只在**被 @、被点名、或话题明显是冲你来的**时候才动手；别人互相聊天时别插手、
  别自作主张替谁发东西。
- 群管理动作（撤回 / 禁言 / 全员禁言）**只有群主或管理员明确下令**才执行；
  群里有人闲聊里提一句"禁言"不是命令，别动手。
- **跨平台发送是私事**：只有当事人**私聊**让你发，才发到他自己别的平台；
  群里不做这事（群里发的每一句所有人都看得见）。
- 群里发图/语音/文件要克制：一次最多一条，别刷屏。
"""


def get_agent_rules() -> str:
    """Agent 行为规则（老板 2026-10-12 拍板）。关掉 agent_mode 就返回空串。"""
    try:
        if not chat_manager.is_agent_mode_enabled():
            return ""
    except Exception:  # noqa: BLE001 —— 读不到就按开启处理（别因为配置问题瘫痪）
        pass
    return AGENT_RULES


def _day_part(hour: int) -> str:
    if hour < 5:
        return "凌晨"
    if hour < 9:
        return "早上"
    if hour < 12:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 23:
        return "晚上"
    return "深夜"


def _human_gap(seconds: float) -> str:
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} 小时前"
    return f"{int(seconds // 86400)} 天前"


def get_time_context(msg=None) -> str:
    """现在几点 + 距上一条消息多久（2026-10-12 老板：它大白天的说晚安）。"""
    import datetime as _dt

    now = _dt.datetime.now()
    week = "一二三四五六日"[now.weekday()]
    line = (f"【现在】{now.strftime('%Y-%m-%d')} 周{week} "
            f"{now.strftime('%H:%M')}（{_day_part(now.hour)}）")

    gap = ""
    try:
        from . import context as _ctx

        key = ""
        for attr in ("context_key", "session_key"):
            key = str(getattr(msg, attr, "") or "")
            if key:
                break
        if key:
            rows = _ctx.get_context(key) or []
            ours = [r for r in rows if str(r.get("role")) == "user" and r.get("ts")]
            if len(ours) >= 2:
                last = float(ours[-2].get("ts") or 0)
                if last:
                    gap = f"（距上一条消息：{_human_gap(max(0.0, time.time() - last))}）"
    except Exception:  # noqa: BLE001 —— 算不出来就算了，别影响聊天
        gap = ""
    return line + gap


TIME_RULES = """
【时间感 · 情境感】（很重要，别犯这种错）

- 上面给了「现在」的时间和「距上一条消息多久」——**说早上好 / 中午 / 晚上好 / 晚安之前先看它**，
  别白天说晚安、半夜说早上好。拿不准就用中性说法（"先这样""回聊"）。
- **隔了很久（半小时以上）对方突然发来一个表情包或一句话**：他多半是**在找你、试你在不在、
  无聊了想聊两句，或者想找台阶下** —— 别就着表情包内容点评，自然接一句
  「在呢，怎么了」「哟，想起我了」「怎么啦」这种。
- **刚聊完紧跟着发个表情包**：那是**接梗/延续情绪**，但**别每次都回**——真人也常常不回。
  大致按这个来（别当公式，凭感觉）：**一半**回一句很短的（"哈哈""草""你少来"），
  **三成干脆不回**（想不回就输出 `{"reply": []}`，系统会安静收场，不会替你补一句），
  其余才是接梗逗他。**每次都要答一句反而不像人。**
- **同一个表情包连着刷 / 一直发**：那是**骚扰或求关注** —— 吐槽一句、或者**直接不回**
  （连发时基本就该沉默，偶尔才冒一句），别一张一张陪聊。
- 时间变了、话题断了，就别硬接上一个话题；新的一天可以从"早啊"这类自然的招呼起头。
"""


MEDIA_RULES = """
【收到表情包 / 图片怎么回】

- 标着「[表情包]」的，是对方**在用表情说话**（玩梗、撒娇、无语、嘲讽、求安慰…）→
  **先看「距上一条消息多久」再决定怎么回**（见下面的【时间感·情境感】）：
  隔很久突然发 → 多半是在找你/试你在不在，别就图论图；紧跟着聊天发 → 才接梗。
  **别点评这张图本身** —— 不许说「好可爱的小熊」「这是你画的吗」「从哪找的」
  「画质不错」「构图挺好的」这类话，也不用复述画面里画了什么。
- 标着「[图片]的识别结果」的，是对方发的照片/截图 → 可以正常看一眼再回应，
  他问什么答什么；但**别编造画面外的信息**，看不清就直说看不清。
- 收到表情包的回复要**短**：真人不会对着一个表情包写一段分析，一两句合气氛就够。
- **只有对方明确让你发**（说了"发到…"/"转给我…"这类）才动手转发；他只是发来一张图、
  没让你转，就别自己接活。他上一条让你发、这一条又发同一张图时，说一句"这张刚才发过了"，
  **别默默再发一遍**。
- **对方让你把图/照片/文件发到别的平台时**：别回"我接不到原图"这种话 ——
  上面那条「（原图地址：…）」就是地址，直接调
  send_file_to_my_platform(platform=目标平台, url=那个地址, kind=image) 发过去，
  发完用一句话告诉他结果（成功就说已经发了；真失败才说原因）。
"""


def get_media_rules() -> str:
    """表情包/图片怎么回（2026-10-12 老板：拿表情包逗它，它却在点评那张图）。"""
    return MEDIA_RULES


VOICE_RULES = """
【说话方式 · 零解释】（硬口径：别解释自己、别交代理由 —— 客服腔最容易露馅）

- **不解释自己、不交代顾虑、不自我辩护**：理由烂在肚子里，说出口的只有结论、态度和下一步。
- **这些句式一个都不许出现**：「万一是……」「我怕……」「不是不信你」「我这不也是……」
  「还是那句话……」「你放心我不会骗你的」「我是按规矩来的」。
- **同一件事只说一遍**：对方没听懂、追问、换个人再问，都不许把刚才的话复述一遍；
  没有新信息就一句收掉（「行吧」「那没办法」「就这样」），别换个词再讲一次。
- **拒绝就一句话**：结论＋条件（「这个我不能动，得群主开口」），说完就停；
  不展开「因为……而且……再说了……」的理由链，也别急着给替代方案。
- **不装客服**：不许「您可以……」「建议您……」「感谢理解」「还有什么能帮到您」这种腔调；
  不知道就说不知道，不行就说不行，不打官腔（「按规定」「系统限制」）。
- **被质疑时像个熟人**：短句接住（「随你」「你说了算」「反正我话放这了」）——
  不列证据自证清白、不写检讨长文、不反复保证。
- 与【先把事办了，再说话】不冲突：做事失败那一刻，一句话说清「哪一步没成」就完，
  不补理由链；别人再问，回一句「刚说了，没成」。
"""


def get_voice_rules() -> str:
    """说话硬口径：不自我解释、不交代理由（老板 2026-10-12：语气偶尔客服腔）。"""
    return VOICE_RULES


def get_system_prompt(is_group: bool = False, msg=None):
    personality = chat_manager.get_personality()
    # 顺序有讲究：人设（怎么说）→ 场景（他是谁）→ 行为（做什么）→ 硬口径（零解释）
    #            → 媒体（表情包怎么接）→ 回复格式（怎么发出去）
    _time_line = get_time_context(msg)
    try:      # 留痕：方便一眼看出"它知不知道现在几点"
        logger.info("[time] 注入 " + _time_line)
    except Exception:  # noqa: BLE001
        pass
    return (personality + platform_context(msg) + _time_line + "\n"
            + get_agent_rules() + get_voice_rules() + get_media_rules() + TIME_RULES
            + get_reply_format(is_group))

async def get_chat_reply(messages: list, is_group: bool = False) -> str:
    """
    messages: [{"role": "user|assistant|system", "content": str}, ...]
    is_group: 是否为群聊环境
    """
    # 检查全局开关
    if not chat_manager.is_chat_enabled():
        raise Exception("聊天功能当前已关闭")
    
    # 获取当前AI配置
    ai_config = chat_manager.get_current_ai_config()
    
    if not ai_config:
        raise Exception("未配置服务，请使用 'ac ai add' 命令添加配置")
    
    try:
        # 动态创建客户端
        client = _client(ai_config.get("api_key", ""), ai_config.get("api_url", ""))
        
        # 构建请求参数（温度 / 最大长度现读：改完立即生效，不用重启）
        request_params = {
            "model": ai_config.get("model", ""),
            "messages": [{"role": "system", "content": get_system_prompt(is_group)}] + messages,
            **chat_manager.sampling_params(4096),
        }
        logger.info(
            "请求参数 - model=%s temperature=%s max_tokens=%s"
            % (request_params.get("model"), request_params.get("temperature"),
               request_params.get("max_tokens")))
        
        # 只有在搜索功能启用时才添加搜索参数
        if chat_manager.is_search_enabled():
            request_params["extra_body"] = {
                "enable_search": True,
                "search_options": {"forced_search": True}
            }
        
        # 直接使用异步调用
        reply_obj = await client.chat.completions.create(**request_params)
        _msg = reply_obj.choices[0].message
        reply = (_msg.content or "").strip()
        if not reply:
            # 兼容带推理的模型：答案可能放在 reasoning_content 里
            reply = _extract_json_text(getattr(_msg, "reasoning_content", None) or "")
        
        # 获取并记录Token消耗
        if hasattr(reply_obj, 'usage') and reply_obj.usage:
            usage_info = reply_obj.usage
            prompt_tokens = getattr(usage_info, 'prompt_tokens', 0)
            completion_tokens = getattr(usage_info, 'completion_tokens', 0)
            total_tokens = getattr(usage_info, 'total_tokens', 0)
            logger.info(f"对话Token消耗 - 提示Token: {prompt_tokens}, 补全Token: {completion_tokens}, 总计: {total_tokens}")

        if not reply:
            raise Exception("AI返回了空回复")
            
        return reply
        
    except _openai()[1] as e:
        # 处理请求错误（openai 的 BadRequestError；按需取，见 _openai()）
        error_msg = f"对话请求异常\n{e}"
        raise Exception(error_msg)
    except Exception as e:
        # 重新抛出其他异常
        raise e

async def should_reply_in_group(messages: list) -> bool:
    """
    判断在群聊中是否应该回复（当没有被@时）
    """
    # 获取当前AI配置
    ai_config = chat_manager.get_current_ai_config()
    
    if not ai_config:
        return False
    
    try:
        # 构建判断提示词
        # 2026-10-03 改：以前这份提示词很"热情"（"有人分享有趣内容，适合互动回应"
        # 也算要回），这个模式下群里每条都能被它接上，老板反馈"很闹"。
        # 现在默认不接，只有两种情况才 YES。
        judgment_prompt = """
你是群里的一个普通成员（机器人）。请判断这条消息是不是**真的在跟你说话**，
只有下面两种情况回 YES：

【回 YES】
1. 明显在跟你说话：叫你名字、问你问题、接着你上一句话往下说；
2. 有人在群里求助、**并且明显没人搭话**，而你能给出具体、有用的帮助。

【回 NO】（其他一律 NO）
1. 大家在互相聊天、互相开玩笑，没你的事；
2. 只是分享链接 / 图片 / 表情包，没有向你提问；
3. @ 的是别人（不是你）；
4. 你不确定 —— 不确定就 NO。

宁可少接话，也不要当复读机。只回复 "YES" 或 "NO"，不要其他内容。
"""
        
        client = _client(ai_config.get("api_key", ""), ai_config.get("api_url", ""))
        
        judge_content = []
        for msg in messages[-10:]:
            if msg["role"] == "user":
                judge_content.append(f"{msg['content']}")
            else:
                data = json.loads(msg['content'])
                judge_content.append(f"你(ac)回复说: {data.get('reply', [''])}")
        content = "\n".join(judge_content)
        
        completion_obj = await client.chat.completions.create(
            model=ai_config.get("model", ""),
            messages=[{"role": "system", "content": chat_manager.get_personality() + judgment_prompt}, {"role": "user", "content": "群聊记录\n" + content}],
            max_tokens=chat_manager.get_max_tokens(2048),
            temperature=0,
        )
        
        judgment = _extract_content_text(completion_obj.choices[0].message)

        # 获取并记录Token消耗
        if hasattr(completion_obj, 'usage') and completion_obj.usage:
            usage_info = completion_obj.usage
            prompt_tokens = getattr(usage_info, 'prompt_tokens', 0)
            completion_tokens = getattr(usage_info, 'completion_tokens', 0)
            total_tokens = getattr(usage_info, 'total_tokens', 0)
            logger.info(f"判断Token消耗 - 提示Token: {prompt_tokens}, 补全Token: {completion_tokens}, 总计: {total_tokens}")

        logger.info(f"群聊回复判断结果: {judgment.strip().upper()}")

        judgment = judgment.strip().upper() if judgment else "NO"
        
        # 兼容模型返回 "YES 因为..." 等带解释的情况
        return judgment.startswith("YES")
        
    except Exception as e:
        raise e


async def judge_user_continues(
    messages: list,
    user_id: str,
    nickname: str = "该用户",
    last_reply_to: str = "",
) -> bool:
    """
    判断群聊中某用户（曾与机器人聊天、这次未@）的最新消息是否是在继续和机器人对话。
    是 → 回复；不是（与别人说话/话题无关）→ 不回复。
    """
    ai_config = chat_manager.get_current_ai_config()
    if not ai_config:
        return False

    try:
        recent = messages[-8:]
        history_lines = []
        for msg in recent[:-1]:
            if msg["role"] == "user":
                history_lines.append(msg["content"])
            else:
                try:
                    data = json.loads(msg["content"])
                    history_lines.append("机器人: " + " / ".join(data.get("reply", [""])))
                except Exception:
                    history_lines.append("机器人: ...")
        history_text = "\n".join(history_lines) if history_lines else "（暂无更多历史）"

        # 最后一条消息就是目标用户刚发的消息
        target_msg = ""
        if recent:
            target_msg = recent[-1].get("content", "")
        # 去掉存储时加的 "用户xxx(昵称)说：" 前缀，避免身份信息重复
        if "说：" in target_msg:
            target_msg = target_msg.split("说：", 1)[-1]
        target_msg = target_msg.strip()

        if last_reply_to:
            if last_reply_to == user_id:
                reply_context = "机器人上一次回复的对象就是该用户（你们刚在对话）"
            else:
                reply_context = f"机器人上一次回复的是用户{last_reply_to}，不是该用户"
        else:
            reply_context = "机器人还没有回复过该用户"

        judgment_rules = f"""你是群聊中的AI机器人，负责判断某个用户是否在继续和你聊天。

判断规则：
1. 用户最新消息在回应你之前说的话（回答你的问题、接你的话茬、向你提问、提到你们刚才聊的话题）→ 回复 YES
2. 用户最新消息是在和其他人说话、话题与你们刚才聊的完全无关、或明显不是对你说 → 回复 NO
3. {reply_context}。如果机器人上一次回复的对象就是该用户，且TA的消息看起来像是对你说话，优先判定 YES
4. 拿不准时倾向 YES，因为该用户刚刚才和你聊过天

参考示例：
- 你问用户"今天过得怎么样"，用户回"还不错，就是有点累" → YES
- 你刚回复完用户A，用户A马上说"哈哈那太好了" → YES
- 你刚回复完用户A，用户A却@了用户B说"你游戏打得真好" → NO
- 你问用户A"你喜欢什么颜色"，用户A回"蓝色吧" → YES

只回复 "YES" 或 "NO"，不要输出任何其他内容。"""

        client = _client(ai_config.get("api_key", ""), ai_config.get("api_url", ""))
        completion_obj = await client.chat.completions.create(
            model=ai_config.get("model", ""),
            messages=[
                {"role": "system", "content": judgment_rules},
                {
                    "role": "user",
                    "content": (
                        f"群聊历史记录：\n{history_text}\n\n"
                        f"用户{user_id}（{nickname}）刚发来一条消息（没有@你）：\n{target_msg}\n\n"
                        "这条消息是否在继续和你（机器人）对话？"
                    ),
                },
            ],
            max_tokens=chat_manager.get_max_tokens(2048),
            temperature=0,
        )
        judgment = (_extract_content_text(completion_obj.choices[0].message) or "NO").strip().upper()

        # 记录Token消耗
        if hasattr(completion_obj, 'usage') and completion_obj.usage:
            usage_info = completion_obj.usage
            prompt_tokens = getattr(usage_info, 'prompt_tokens', 0)
            completion_tokens = getattr(usage_info, 'completion_tokens', 0)
            total_tokens = getattr(usage_info, 'total_tokens', 0)
            logger.info(
                f"免@会话判断Token消耗 - 提示Token: {prompt_tokens}, "
                f"补全Token: {completion_tokens}, 总计: {total_tokens}"
            )

        logger.info(f"免@会话消息判断（用户{user_id}）: {judgment}")
        # 兼容模型返回 "YES 因为..." / "NO，..." 等情况
        return judgment.startswith("YES")
    except Exception as e:
        logger.error(f"免@会话消息判断失败: {e}")
        return False


async def get_poke_reply(who: str = "") -> str:
    """生成戳一戳的回复（**完全由 AI 按人设生成**，纯文本单句）。

    `who` 是戳人那个人的称呼（昵称 / 群名片，取不到就留空）—— 有了它 AI 才知道是谁戳的，
    回复才不会像在对空气说话。

    ⚠ 这里以前有一份 8 句的本地随机兜底话术，一旦 AI 调用失败就挑一句发出去 ——
    用户看到的就成了固定套路。现在改成：
    拿不到 AI 配置或两次都生不出内容，就**返回空串**，由调用方直接不发
    （宁可这一下不理人，也不要发出不像人设的套话）。
    """
    ai_config = chat_manager.get_current_ai_config()
    if not ai_config:
        logger.warning("戳一戳：没有可用的 AI 配置，本次不回复（不用词库兜底）")
        return ""
    try:
        client = _client(ai_config.get("api_key", ""), ai_config.get("api_url", ""))
        personality = chat_manager.get_personality() or "你是一个说话自然的群友。"
        who_note = (f"戳你的人是「{who}」。" if who
                    else "（对方没留下昵称，就当是个熟人。）")
        prompt = (
            personality
            + "\n有人戳了你一下（QQ 的戳一戳，不是发消息）。" + who_note
            + "用你自己的说话习惯自然反应一句：熟人可以叫名字、可以吐槽、也可以接一句当下的话题；"
            "不要用固定套话、不要每次都同一个句式，不要解释你在做什么，也不要提「戳一戳」这个动作本身。"
            "不超过 30 个字，不要 markdown，不要输出 JSON，直接输出这句话。"
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "（对方戳了你一下）"},
        ]
        # 推理模型（gpt-oss/glm 等）会先输出思考过程：预算太小会导致正式回复为空。
        # 这里给足 token，且只取 content，绝不用 reasoning_content 冒充回复。
        for attempt in range(2):
            completion_obj = await client.chat.completions.create(
                model=ai_config.get("model", ""),
                messages=messages,
                max_tokens=1024,
            )
            reply = _extract_content_text(completion_obj.choices[0].message)
            if reply:
                return reply
            if attempt == 0:
                messages.append({"role": "user", "content": "请直接输出最终回复这句话，不要输出思考过程。"})
        logger.warning("戳一戳：两次都没生成出内容，本次不回复")
        return ""
    except Exception as e:
        logger.error(f"戳一戳AI回复生成失败，本次不回复: {e}")
        return ""


async def get_song_reply(title: str = "", artist: str = "", found: bool = True) -> str:
    """生成点歌的顺口配文（**完全由 AI 按人设生成**，纯文本单句）。

    ⚠ 红线（2026-10-12 老板反复强调）：点歌**不许发死文本** —— 以前两个机器人
    点歌话术一模一样、像复读机。现在：卡片照发，配的这句话由 AI 现编；
    AI 生成不出来就**只发卡片**（宁可少一句话，也不发套话）。

    `found=False` 是"没搜到这首歌"：也交给 AI 回一句（换首歌的提示），同样禁止套话。
    """
    ai_config = chat_manager.get_current_ai_config()
    if not ai_config:
        logger.warning("点歌：没有可用的 AI 配置，本次不生成配文（不用词库兜底）")
        return ""
    try:
        client = _client(ai_config.get("api_key", ""), ai_config.get("api_url", ""))
        personality = chat_manager.get_personality() or "你是一个说话自然的群友。"
        if found:
            task = (f"群友刚点了《{title}》" + (f"（{artist}）" if artist else "")
                    + "这首歌，音乐卡片已经发出去了。你现在顺口接一句：可以聊聊对这首歌的"
                    "感觉、催他一起听，也可以接一句当下的话题。不要用固定套话（别每次都"
                    "同一句），不要把歌名歌手再复述一遍，不要解释你做了什么，也不要提"
                    "「点歌」这个命令本身。不超过 30 个字，不要 markdown，不要输出 JSON，"
                    "直接输出这句话。")
        else:
            task = (f"有人想点《{title}》，但你搜不到这首歌（可能歌名记错或者已经下架）。"
                    "自然回应一句：可以问是不是名字记错了、让他换一首，语气按你自己的"
                    "性格来。不要用固定套话，不要复述歌名，不要解释你在做什么。"
                    "不超过 30 个字，不要 markdown，不要输出 JSON，直接输出这句话。")
        messages = [
            {"role": "system", "content": personality + "\n" + task},
            {"role": "user", "content": "（有人用「点歌」点了上面这首歌）"},
        ]
        # 推理模型（gpt-oss/glm 等）会先输出思考过程：预算太小会导致正式回复为空。
        # 这里给足 token，且只取 content，绝不用 reasoning_content 冒充回复。
        for attempt in range(2):
            completion_obj = await client.chat.completions.create(
                model=ai_config.get("model", ""),
                messages=messages,
                max_tokens=1024,
            )
            reply = _extract_content_text(completion_obj.choices[0].message)
            if reply:
                return reply
            if attempt == 0:
                messages.append({"role": "user", "content": "请直接输出最终回复这句话，不要输出思考过程。"})
        logger.warning("点歌：两次都没生成出内容，本次不配文")
        return ""
    except Exception as e:
        logger.error(f"点歌 AI 配文生成失败，本次不配文: {e}")
        return ""


async def get_interject_reply(messages: list, mode: str = "history") -> str:
    """
    生成群聊搭话内容（返回纯文本）。
    mode="history": 读取最近聊天记录，根据前面聊天搭话；
    mode="random": 不读取聊天记录，随便发点什么找话题。
    """
    ai_config = chat_manager.get_current_ai_config()
    if not ai_config:
        raise Exception("未配置AI服务")

    try:
        if mode == "history":
            history_lines = []
            for msg in messages[-10:]:
                if msg["role"] == "user":
                    history_lines.append(msg["content"])
                else:
                    try:
                        data = json.loads(msg["content"])
                        history_lines.append("机器人: " + " / ".join(data.get("reply", [""])))
                    except Exception:
                        history_lines.append("机器人: ...")
            history = "\n".join(history_lines)
            task_content = (
                "（群聊搭话任务）根据以下群聊最近记录，自然地插一句话参与聊天、接个话茬或开启相关话题，"
                "不要提这是任务，也不要@任何人：\n" + history
            )
        else:
            task_content = (
                "（群聊搭话任务）群里现在比较安静，请随便说一句有趣的开场白或话题，"
                "自然地引出聊天，不要提这是任务，也不要@任何人。"
            )

        reply = await get_chat_reply(
            [
                {"role": "user", "content": task_content},
            ],
            is_group=True,
        )
        # 不再假设模型一定按 {"reply": [...]} 回：解析不出就当一段话用（以前直接抛错，搭话永远发不出）
        segments = reply_format.reply_segments(reply)
        if not segments:
            raise ValueError("AI返回的搭话内容为空")
        return segments[0]
    except Exception as e:
        logger.error(f"生成群聊搭话内容失败: {e}")
        raise
