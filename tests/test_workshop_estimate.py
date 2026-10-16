# -*- coding: utf-8 -*-
"""工坊估算 + 大工程分文件生成（2026-10-13 老板要的）。

覆盖：
- 工程量/token/费用预估：小需求 vs 大需求、单行短文案、价格表命中；
- usage 记账：call_model 把服务商返回的 usage 累加；
- 分文件模式：manifest + 每文件一次，组装正确；
- 截断兜底：整包输出被截断 → 自动改分文件写；
- 常量同步：generator 和 estimator 的阈值一致。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if not os.environ.get("QBM_POINTER_DIR"):
    os.environ["QBM_POINTER_DIR"] = str(tempfile.mkdtemp(prefix="qbm_wsest_ptr_"))

from qbotmanager.core.workshop import estimator, generator  # noqa: E402

GOOD_TOOL = '''
import json


def handle(ctx, args):
    city = str(args.get("city") or "").strip()
    if not city:
        return json.dumps({"ok": False, "error": "missing_city"}, ensure_ascii=False)
    return json.dumps({"ok": True, "city": city}, ensure_ascii=False)
'''


class _S:
    """工坊只用 root（配置从 root/workshop/config.json 读）。"""

    def __init__(self, root):
        self.root = Path(root)


def _settings(tmp, provider="deepseek"):
    root = Path(tmp)
    (root / "workshop").mkdir(parents=True, exist_ok=True)
    (root / "workshop" / "config.json").write_text(json.dumps({
        "provider": provider, "api_key": "sk-test", "model": "deepseek-chat",
    }), encoding="utf-8")
    return _S(root)


def _good_files():
    manifest = {
        "id": "weather_demo", "name": "查天气", "version": "1.0.0",
        "kind": "tool-pack", "description": "查天气的插件",
        "adapters": ["qq_official", "wechat_ilink"], "permissions": ["network"],
        "sandbox": True,
        "tools": [{
            "name": "check_weather",
            "description": "用户问天气的时候用",
            "parameters": {"type": "object",
                           "properties": {"city": {"type": "string", "description": "城市"}},
                           "required": ["city"]},
            "permissions": ["network"], "lifecycle": "none",
        }],
    }
    return {"manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2),
            "tools/check_weather.py": GOOD_TOOL}


def _plan(tools=1):
    return {"id": "weather_demo", "name": "查天气", "what": "查城市天气",
            "tools": [{"name": f"check_weather_{i}", "desc": "查天气时用",
                       "example_args": {"city": "北京"}} for i in range(tools)],
            "adapters": ["qq_official", "wechat_ilink"], "permissions": ["network"],
            "channel_note": "QQ 和微信私聊都能用", "steps": ["调接口", "返回数据"]}


def test_estimate_small_and_large(tmp_path):
    s = _settings(tmp_path)
    small = estimator.estimate(s, "每天提醒我喝水")
    assert small["level"] == "small" and small["tools"] == 1
    big = estimator.estimate(s, "做一个多用户记账系统：签到、打工、金币排行、历史统计",
                             _plan(tools=3))
    assert big["level"] == "large" and big["mode"] == "chunked"
    assert big["tokens"]["total"][0] < big["tokens"]["total"][1]
    assert big["money"] and big["money"]["min"] < big["money"]["max"], \
        "配了 deepseek 应该给出参考费用范围"


def test_format_estimate_and_short_line(tmp_path):
    s = _settings(tmp_path)
    est = estimator.estimate(s, "做一个成语接龙小游戏，记录每个人得分", _plan(tools=2))
    text = estimator.format_estimate(est)
    for needle in ("工程量", "tokens", "¥", "参考价"):
        assert needle in text
    short = estimator.format_estimate(est, short=True)
    assert "tokens" in short and "工程量" in short and "\n" not in short


def test_estimate_survives_unconfigured_provider(tmp_path):
    s = _settings(tmp_path, provider="")
    est = estimator.estimate(s, "查天气")
    assert est["tokens"]["total"][0] > 0
    assert est["money"] is None


def test_call_model_accumulates_usage(monkeypatch, tmp_path):
    s = _settings(tmp_path)
    monkeypatch.setattr(generator, "_post_json",
                        lambda url, payload, headers, timeout: {
                            "choices": [{"message": {"content": "{\"ok\":true}"},
                                         "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 120, "completion_tokens": 80}})
    usage = {}
    out = generator.call_model(s, "sys", "hi", usage=usage)
    assert out == '{"ok":true}'
    assert usage == {"calls": 1, "input": 120, "output": 80}


def test_generate_files_chunked_assembles(monkeypatch, tmp_path):
    s = _settings(tmp_path)
    manifest = {
        "id": "demo", "name": "示例", "version": "1.0.0", "kind": "tool-pack",
        "description": "示例", "adapters": ["qq_official"], "permissions": [],
        "tools": [{"name": "ping", "description": "ping 一下",
                   "parameters": {"type": "object", "properties": {}}}],
    }
    code = ("```python\nimport json\n\n\ndef handle(ctx, args):\n"
            "    return json.dumps({\"ok\": True}, ensure_ascii=False)\n```")

    def fake_call(settings, system, user, timeout=240, budget=0, usage=None):
        if "架构师" in system:
            return json.dumps(manifest, ensure_ascii=False)
        return code

    monkeypatch.setattr(generator, "call_model", fake_call)
    files = generator.generate_files_chunked(s, "做个 ping", _plan(tools=1))
    assert set(files) == {"manifest.json", "tools/ping.py"}
    assert files["tools/ping.py"].startswith("import json")
    assert json.loads(files["manifest.json"])["sandbox"] is True


def test_build_falls_back_to_chunked_on_truncation(monkeypatch, tmp_path):
    s = _settings(tmp_path)
    calls = {"chunked": 0}

    def boom(settings, need, plan, usage=None):
        raise generator.WorkshopError(
            "AI 返回的 JSON 不完整（多半是被截断了），重试或换个模型。", "JSONDecodeError")

    def fake_chunked(settings, need, plan, usage=None):
        calls["chunked"] += 1
        return _good_files()

    monkeypatch.setattr(generator, "generate_files", boom)
    monkeypatch.setattr(generator, "generate_files_chunked", fake_chunked)
    res = generator.build(s, "查天气", plan=_plan(tools=1), do_dry_run=False)
    assert calls["chunked"] == 1, "整包被截断后没有自动改分文件写"
    assert res["mode"] == "chunked"
    assert res["ok"] is True
    assert "usage" in res


def test_small_plan_stays_single_shot(monkeypatch, tmp_path):
    s = _settings(tmp_path)
    calls = {"single": 0}

    def fake_single(settings, need, plan, usage=None):
        calls["single"] += 1
        return _good_files()

    def fake_chunked(settings, need, plan, usage=None):
        raise AssertionError("小插件不该走分文件模式")

    monkeypatch.setattr(generator, "generate_files", fake_single)
    monkeypatch.setattr(generator, "generate_files_chunked", fake_chunked)
    res = generator.build(s, "查天气", plan=_plan(tools=1), do_dry_run=False)
    assert calls["single"] == 1 and res["mode"] == "single" and res["ok"] is True


def test_chunk_threshold_synced():
    assert generator.CHUNK_TOOLS_THRESHOLD == estimator.CHUNK_TOOLS_THRESHOLD
