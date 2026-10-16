# -*- coding: utf-8 -*-
r"""用 GitHub Git Data API 把 release-1224 的提交推到 Weltmu/AstroSwarm:main。

为什么不用 git push：本机网络到 github.com:443 不通（api.github.com 是通的）。
本脚本 = git push 的等价操作：
  1) 对 368 个变更文件逐个建 blob（base64，并行）；
  2) 用 base_tree(origin/main 的树) + 新条目建 tree（重命名/删除用 sha:null）；
  3) 建 commit（父 = 现 main）；
  4) PATCH refs/heads/main（非强推）。
用法：GH_TOKEN=<token> python tools/gh_api_push_1224.py [--dry-run]
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://api.github.com"
REPO = "Weltmu/AstroSwarm"
WT = Path(r"D:\ai\pub-work-1224")
MSG_FILE = Path(r"D:\ai\QBotManager\work\commit_msg_1224.txt")
DRY = "--dry-run" in sys.argv
TOKEN = os.environ.get("GH_TOKEN", "")


def api(method: str, path: str, body=None, timeout: int = 120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": "Bearer " + TOKEN,
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "astroswarm-release-bot"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def git(*args: str) -> str:
    out = subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(WT)] + list(args),
                         capture_output=True, check=True)
    return out.stdout.decode("utf-8")


def main() -> int:
    if not TOKEN:
        print("[x] 需要 GH_TOKEN")
        return 2

    head = git("rev-parse", "HEAD").strip()
    base = git("rev-parse", "origin/main").strip()
    base_tree = api("GET", f"/repos/{REPO}/git/commits/{base}")["tree"]["sha"]
    print(f"HEAD={head[:8]}  base={base[:8]}  base_tree={base_tree[:8]}")

    # 变更清单（含重命名/删除）
    raw = git("diff", "--name-status", "-M", "origin/main", "HEAD")
    to_write, to_delete = [], []
    for line in raw.splitlines():
        parts = line.split("\t")
        st = parts[0]
        if st.startswith("R") or st.startswith("C"):
            to_delete.append(parts[1])
            to_write.append(parts[2])
        elif st == "D":
            to_delete.append(parts[1])
        else:
            to_write.append(parts[1])
    print(f"写入 {len(to_write)} 个，删除 {len(to_delete)} 个")

    # 文件 mode（100644 / 100755）
    modes = {}
    for line in git("ls-files", "-s").splitlines():
        meta, _, path = line.partition("\t")
        modes[path] = meta.split()[0]

    def make_blob(path: str):
        blob = (WT / path).read_bytes()
        res = api("POST", f"/repos/{REPO}/git/blobs",
                  {"content": base64.b64encode(blob).decode("ascii"), "encoding": "base64"})
        return path, res["sha"]

    entries = []
    if DRY:
        print("dry-run：只列不传")
        for p in to_write[:10]:
            print("  +", p)
        return 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, (path, sha) in enumerate(pool.map(make_blob, to_write), 1):
            entries.append({"path": path, "mode": modes.get(path, "100644"),
                            "type": "blob", "sha": sha})
            if i % 50 == 0:
                print(f"  blob {i}/{len(to_write)}")
    for p in to_delete:
        entries.append({"path": p, "mode": "100644", "type": "blob", "sha": None})

    tree = api("POST", f"/repos/{REPO}/git/trees",
               {"base_tree": base_tree, "tree": entries})
    print("tree:", tree["sha"][:8])
    msg = MSG_FILE.read_text(encoding="utf-8")
    commit = api("POST", f"/repos/{REPO}/git/commits",
                 {"message": msg, "tree": tree["sha"], "parents": [base]})
    print("commit:", commit["sha"][:8])
    ref = api("PATCH", f"/repos/{REPO}/git/refs/heads/main",
              {"sha": commit["sha"], "force": False})
    print("main ->", ref["object"]["sha"][:8])
    # 回读核对
    check = api("GET", f"/repos/{REPO}/commits/main")
    print("回读 main:", check["sha"][:8], "|", check["commit"]["message"].splitlines()[0][:60])
    return 0 if check["sha"] == commit["sha"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
