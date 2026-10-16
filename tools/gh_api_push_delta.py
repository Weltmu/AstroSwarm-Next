# -*- coding: utf-8 -*-
r"""把「某个本地提交之后」的改动，用 GitHub Git Data API 增量推到远程 main。

（本机到 github.com:443 时通时断，git push 用不了时的备用通道 —— 与 gh_api_push_1224.py
同一套：blob → tree(base_tree) → commit → PATCH refs/heads/main。）

用法：
    GH_TOKEN=<token> python tools/gh_api_push_delta.py <本地基准提交> [--repo owner/name] [--dry-run]
示例（把 export 之后的改动推上去）：
    python tools/gh_api_push_delta.py 44a1edb
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://api.github.com"
REPO = Path(r"D:\ai\QBotManager")


def api(method, path, body=None, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": "Bearer " + os.environ.get("GH_TOKEN", ""),
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "astroswarm-release-bot"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def git(*args):
    out = subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(REPO)] + list(args),
                         capture_output=True, check=True)
    return out.stdout.decode("utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_commit", help="本地基准提交（其后的改动会被推上去）")
    ap.add_argument("--repo", default="Weltmu/AstroSwarm-Next")
    ap.add_argument("--remote", default="main")
    ap.add_argument("--message", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    head = git("rev-parse", "HEAD").strip()
    raw = git("diff", "--name-status", "-M", args.base_commit, head)
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
    print(f"基准 {args.base_commit[:8]} → HEAD {head[:8]}：写入 {len(to_write)}，删除 {len(to_delete)}")
    for p in to_write:
        print("  +", p)
    if args.dry_run:
        return 0

    ref = api("GET", f"/repos/{args.repo}/git/ref/heads/{args.remote}")
    base = ref["object"]["sha"]
    base_tree = api("GET", f"/repos/{args.repo}/git/commits/{base}")["tree"]["sha"]
    print("远程 main:", base[:8], "base_tree:", base_tree[:8])

    modes = {}
    for line in git("ls-files", "-s").splitlines():
        meta, _, path = line.partition("\t")
        modes[path] = meta.split()[0]

    def make_blob(path):
        blob = (REPO / path).read_bytes()
        res = api("POST", f"/repos/{args.repo}/git/blobs",
                  {"content": base64.b64encode(blob).decode("ascii"), "encoding": "base64"})
        return path, res["sha"]

    entries = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for path, sha in pool.map(make_blob, to_write):
            entries.append({"path": path, "mode": modes.get(path, "100644"),
                            "type": "blob", "sha": sha})
    for p in to_delete:
        entries.append({"path": p, "mode": "100644", "type": "blob", "sha": None})

    tree = api("POST", f"/repos/{args.repo}/git/trees",
               {"base_tree": base_tree, "tree": entries})
    msg = args.message or git("log", "-1", "--format=%B", head)
    commit = api("POST", f"/repos/{args.repo}/git/commits",
                 {"message": msg, "tree": tree["sha"], "parents": [base]})
    print("commit:", commit["sha"][:8])
    api("PATCH", f"/repos/{args.repo}/git/refs/heads/{args.remote}",
        {"sha": commit["sha"], "force": False})
    check = api("GET", f"/repos/{args.repo}/commits/{args.remote}")
    print("回读 main:", check["sha"][:8], "→", "OK" if check["sha"] == commit["sha"] else "不一致!")
    return 0 if check["sha"] == commit["sha"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
