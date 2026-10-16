# -*- coding: utf-8 -*-
"""把「连续对话能力包」上架到官网插件市场。

只做三件事（都可回滚）：
1. 备份 market.json / market.json.sig；
2. 往 plugins 列表里插入 session-chat 条目（免费、官方能力包）；
3. 重新签名（私钥在签发站，客户端 fail closed —— 不签客户端就直接拒绝加载）。

用法：/opt/license/venv/bin/python /tmp/add_session_pack.py
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

MARKET = Path("/opt/qbot_website/plugins/market.json")
SIG = Path("/opt/qbot_website/plugins/market.json.sig")
SIGNER = Path("/opt/license/sign_market.py")
VENV_PY = "/opt/license/venv/bin/python"

ENTRY = {
    "id": "session-chat",
    "name": "连续对话能力包",
    "version": "1.0.0",
    "kind": "behavior-pack",
    "category": "官方能力包",
    "tier": "free",
    "description": "群里 @ 过它之后，一段时间内不用再 @ 也能接着聊；也可以改成让 AI 读群聊天记录判断要不要接话，"
                   "或者干脆关掉。默认被打招呼后 30 秒内继续聊，到点或连着几句无关就自动断开。",
    "url": "https://astroswarm.cn/api/account/plugins/zip?pid=session-chat",
    "sha256": "a674c2e9a971b3485067d5e599fe8c5b2ab97981b58463111c633ee25a52a07d",
    "adapters": ["qq_official", "wechat_ilink"],
    "permissions": [],
    "updated_at": int(time.time()),
}


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(MARKET, MARKET.with_name(f"market.json.bak_{stamp}"))
    if SIG.exists():
        shutil.copy2(SIG, SIG.with_name(f"market.json.sig.bak_{stamp}"))

    data = json.loads(MARKET.read_text(encoding="utf-8"))
    plugins = data["plugins"] if isinstance(data, dict) else data
    plugins = [p for p in plugins if str(p.get("id") or "") != ENTRY["id"]]
    plugins.append(ENTRY)
    plugins.sort(key=lambda p: (str(p.get("category") or ""), str(p.get("id") or "")))
    if isinstance(data, dict):
        data["plugins"] = plugins
    else:
        data = plugins
    MARKET.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print("已插入 session-chat，条目数:", len(plugins))

    out = subprocess.run([VENV_PY, str(SIGNER), str(MARKET)],
                         capture_output=True, text=True)
    print(out.stdout.strip() or out.stderr.strip())
    if out.returncode != 0:
        raise SystemExit("签名失败，market.json 已改但没生效（客户端会拒绝加载），"
                         "可以用 market.json.bak_%s 回滚" % stamp)
    print("备份后缀:", stamp)


if __name__ == "__main__":
    main()
