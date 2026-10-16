# -*- coding: utf-8 -*-
"""修两处配置（先备份）：
1. 打开识图（模型实测能识别图片）；
2. 群聊模式从 api（每条都让 AI 判断要不要接话 → 很闹）改成 timeout（默认安静）。
"""
import json
import shutil
import time
from pathlib import Path

CFG = Path(r"D:\AstroSwarm 星群\机器人\bot\config\ai\aichat_manager.json")


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(CFG, CFG.with_name(f"aichat_manager.json.bak_{stamp}_quietvision"))
    data = json.loads(CFG.read_text(encoding="utf-8"))
    before = {k: data.get(k) for k in ("image_recognition_enabled", "session_mode")}
    data["image_recognition_enabled"] = True
    data["session_mode"] = "timeout"
    data.setdefault("session_expire_seconds", 30)
    CFG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    after = {k: data.get(k) for k in ("image_recognition_enabled", "session_mode")}
    print("备份后缀:", stamp)
    print("改前:", before)
    print("改后:", after)


if __name__ == "__main__":
    main()
