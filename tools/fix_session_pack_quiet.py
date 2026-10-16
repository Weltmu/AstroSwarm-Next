# -*- coding: utf-8 -*-
"""把「连续对话能力包」的用户配置改成安静模式（timeout）。

老板 2026-10-03：群聊每条都被接话、很闹。原因是这个包的 config.json 里
mode=api（"读群聊天记录判断"= 群里每条消息都让 AI 看一眼要不要接，费 token 也爱插话）。
改回 timeout：被 @ 之后的一段时间内才接着聊，其余一律不理。
"""
import json
import shutil
import time
from pathlib import Path

PACK = Path(r"D:\AstroSwarm 星群\机器人\tool_packs\session-chat")
CFG = PACK / "config.json"


def main() -> None:
    if not CFG.exists():
        raise SystemExit(f"没有这个文件：{CFG}")
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(CFG, CFG.with_name(f"config.json.bak_{stamp}_quiet"))
    data = json.loads(CFG.read_text(encoding="utf-8"))
    before = dict(data)
    data["mode"] = "timeout"           # 安静：只有 @ 过才接着聊
    data.setdefault("enabled", True)
    data.setdefault("expire_seconds", 30)
    data.setdefault("unrelated_limit", 5)
    CFG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("备份后缀:", stamp)
    print("改前:", before)
    print("改后:", data)


if __name__ == "__main__":
    main()
