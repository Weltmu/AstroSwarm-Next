# -*- coding: utf-8 -*-
"""收尾：官网 1.2.19 更新日志 + 交接记录（只追加，不改历史行）。"""
import json
import shutil
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website")
MANIFEST = ROOT / "downloads" / "manifest.json"
HANDOVER = ROOT / ".coord" / "handover.md"

NOTE = ("发布 v1.2.19：微信通道不再需要开通验证，装上就能扫码用（官方仍提供代为部署与长期维护的收费服务）。"
        "修好了 QQ 官方机器人「群里 @ 它一个字都不说」——模型没按 JSON 格式回时，插件原来会把整条回复丢掉，"
        "现在解析不出就照原文发。插件商店改成卡片式：分类色块、一句话说明直接显示、顶部搜索框、"
        "已装显示「✓已装」+「配置」。插件参数弹窗重做：参数名改成中文并独占一行，窄窗口下也不再和输入框叠字。"
        "新上架「连续对话能力包」（免 @ 连续对话：超时断开 / 读群记录判断，可关）。"
        "另外修了后台扫子进程的死循环（会让界面像卡死）和界面每 2 秒读盘 9 次的问题。")


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(MANIFEST, MANIFEST.with_name(f"manifest.json.bak_{stamp}_notes"))
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    updates = data.setdefault("updates", [])
    today = time.strftime("%Y-%m-%d")
    updates = [u for u in updates if not (u.get("date") == today and "1.2.19" in str(u.get("text"))) ]
    updates.insert(0, {"date": today, "product": "星群 桌面端", "text": NOTE})
    data["updates"] = updates
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print("更新日志已加，条数:", len(updates))

    line = (f"- {time.strftime('%Y-%m-%d %H:%M')} 客户端会话：发布星群 v1.2.19 —— "
            f"上传 downloads/AstroSwarm_Setup_1.2.19.exe（sha256 9690f3b9…）与 "
            f"downloads/AstroSwarm_v1.2.19_Portable_20261001.zip（sha256 72c150a2…）；"
            f"改 downloads/manifest.json（astroswarm 版本 → 1.2.19、两个 Windows 平台条目、"
            f"notes 与 updates 各加一条）；改 plugins/market.json 加 session-chat 并重签 "
            f"market.json.sig（用 /opt/license/sign_market.py）；"
            f"上传 /opt/qbot_private/tool_packs/session-chat-1.0.0.zip。"
            f"原因：微信通道免费开放、QQ 群聊不回话修复、插件商店与参数弹窗改版、新能力包上架。"
            f"回滚：manifest.json.bak_{stamp} / manifest.json.bak_{stamp}_notes、"
            f"plugins/market.json.bak_20261001_2139 与同名 .sig 备份。\n")
    with HANDOVER.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print("交接记录已追加一行")


if __name__ == "__main__":
    main()
