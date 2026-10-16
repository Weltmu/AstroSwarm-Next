#!/usr/bin/env bash
# 星巢 Nest（AstroSwarm 无头端）· 一条命令安装
#
#   curl -fsSL https://astroswarm.cn/install.sh | sudo bash
#
# 可选参数（直接跟在 bash 后面，例如 ... | sudo bash -s -- --port 8080）：
#   --version 0.2.8     指定版本（默认取官网最新）
#   --dir /opt/astroswarm   安装目录
#   --port 7860         控制台端口（默认 7860）
#   --host 127.0.0.1    监听地址（默认只本机；要公网自己套 nginx 反代）
#   --bot               顺带部署机器人运行时（首次几分钟）
#   --systemd           装成 systemd 服务（开机自启，root 下默认开）
#   --no-systemd        只装文件、不装服务
#   --account-base URL  自建账号服务地址（默认官方托管）
#   --offline           只用包内 wheels 装依赖（一个字节都不联网）
#   --keep              保留下载的压缩包（默认装完删掉）
#
# 做的事：读官网 manifest.json 拿最新版本 → 下载 + sha256 校验 → 解压到安装目录
#        → 调用包内自带的 install.sh（它内部用内置 wheels 装依赖，可零联网）
set -euo pipefail

SITE="${ASTROSWARM_SITE:-https://astroswarm.cn}"
VERSION=""
DIR="/opt/astroswarm"
PORT=""
HOST=""
WITH_BOT=0
SYSTEMD=""
ACCOUNT_BASE=""
KEEP=0

die() { echo "✗ $*" >&2; exit 1; }
say() { echo "· $*"; }

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

PASSTHRU=()
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --dir) DIR="$2"; shift 2 ;;
    --port) PORT="$2"; PASSTHRU+=(--port "$2"); shift 2 ;;
    --host) HOST="$2"; PASSTHRU+=(--host "$2"); shift 2 ;;
    --account-base) ACCOUNT_BASE="$2"; PASSTHRU+=(--account-base "$2"); shift 2 ;;
    --bot) WITH_BOT=1; PASSTHRU+=(--bot); shift ;;
    --offline) PASSTHRU+=(--offline); shift ;;
    --no-deps) PASSTHRU+=(--no-deps); shift ;;
    --wheels) PASSTHRU+=(--wheels "$2"); shift 2 ;;
    --user) PASSTHRU+=(--user "$2"); shift 2 ;;
    --systemd) SYSTEMD=1; shift ;;
    --no-systemd) SYSTEMD=0; shift ;;
    --keep) KEEP=1; shift ;;
    -h|--help) usage ;;
    *) die "不认识的参数：$1（用 -h 看用法）" ;;
  esac
done

[ "$(uname -s)" = "Linux" ] || die "这个脚本是给 Linux 用的"
case "$(uname -m)" in
  x86_64|amd64|aarch64|arm64) ;;
  *) die "暂不支持这个架构：$(uname -m)" ;;
esac

# root 下默认装服务（一条命令装完就能用）；非 root 默认只装文件
if [ -z "$SYSTEMD" ]; then
  if [ "$(id -u)" -eq 0 ]; then SYSTEMD=1; else SYSTEMD=0; fi
fi
PASSTHRU+=(--root "$DIR")
[ "$SYSTEMD" -eq 1 ] && PASSTHRU+=(--systemd)

# ---- 需要一个下载器 ----
if command -v curl >/dev/null 2>&1; then
  DL() { curl -fsSL --retry 3 --connect-timeout 20 "$1"; }
  DLO() { curl -fsSL --retry 3 --connect-timeout 20 -o "$2" "$1"; }
elif command -v wget >/dev/null 2>&1; then
  DL() { wget -q -O - "$1"; }
  DLO() { wget -q -O "$2" "$1"; }
else
  die "没找到 curl 也没找到 wget，先装一个（apt install curl / yum install curl）"
fi

# ---- 版本：不指定就按官网 manifest 里的最新版 ----
if [ -z "$VERSION" ]; then
  say "查询官网最新版本…"
  MANIFEST="$(DL "$SITE/downloads/manifest.json")" || die "拉取 manifest.json 失败，检查网络或换 --version 指定版本"
  if command -v python3 >/dev/null 2>&1; then
    VERSION="$(printf '%s' "$MANIFEST" | python3 -c '
import json,sys
d=json.load(sys.stdin)
for p in d.get("products",[]):
    if p.get("id")=="astroswarm":
        for q in p.get("platforms",[]):
            if q.get("id")=="linux-headless":
                print(q.get("version",""))
')"
  else
    VERSION="$(printf '%s' "$MANIFEST" | tr ',' '\n' | grep -A2 '"linux-headless"' | grep '"version"' | head -1 | sed 's/.*: *"//; s/".*//')"
  fi
  [ -n "$VERSION" ] || die "没从 manifest 里读到 Linux 版本号，请用 --version 指定"
fi
say "将安装星巢 Nest（Linux 无头端）v$VERSION"

PKG="astroswarm-linux-$VERSION.tar.gz"
URL="$SITE/downloads/$PKG"
WORK="$(mktemp -d)"
trap '[ "$KEEP" -eq 1 ] || rm -rf "$WORK"' EXIT

say "下载 $PKG …"
DLO "$URL" "$WORK/$PKG" || die "下载失败：$URL"
if DLO "$URL.sha256" "$WORK/$PKG.sha256" 2>/dev/null; then
  say "校验文件完整性…"
  ( cd "$WORK" && { sha256sum -c "$PKG.sha256" >/dev/null 2>&1 || shasum -a 256 -c "$PKG.sha256" >/dev/null 2>&1; } ) \
    || die "sha256 校验没过（下载可能不完整），重跑一次试试"
  say "校验通过 ✓"
else
  say "（官网没提供校验文件，跳过校验）"
fi

# ---- 解压到安装目录（需要权限就自动 sudo）----
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  PARENT="$(dirname "$DIR")"
  if [ ! -w "$PARENT" ] && [ ! -w "$DIR" ] 2>/dev/null; then
    command -v sudo >/dev/null 2>&1 || die "装到 $DIR 需要 root；请用 sudo 重跑，或用 --dir 指定你有权限的目录"
    SUDO="sudo"
  fi
fi

say "解压到 $DIR …"
$SUDO mkdir -p "$DIR"
$SUDO tar -xzf "$WORK/$PKG" -C "$DIR" --strip-components=1

[ -f "$DIR/install.sh" ] || die "解压后没找到 install.sh，包可能不对"

# ---- 端口预检（2026-10-04 加）----
# 老板实测踩过：服务器上已有一个星云控制台占着 7860，新装的服务起不来、
# systemd 一直重启（日志只有一句 address already in use），用户只看到"网页打不开"。
PORT_WANT="${PORT:-7860}"
if command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | grep -qE "[:.]${PORT_WANT}[[:space:]]"; then
  OWNER="$(ss -lntp 2>/dev/null | grep -E "[:.]${PORT_WANT}[[:space:]]" | head -1 | sed 's/.*users:((//; s/)).*//')"
  echo "✗ 端口 $PORT_WANT 已被占用（$OWNER）" >&2
  echo "  新装的服务会一直启动失败（日志里是 address already in use）。" >&2
  echo "  换个端口重跑，例如：" >&2
  echo "    curl -fsSL https://astroswarm.cn/downloads/install-linux | sudo bash -s -- --port 7861" >&2
  echo "  或先停掉占用它的服务再装。" >&2
  exit 1
fi

say "开始安装（依赖用包内 wheels，可零联网）…"
( cd "$DIR" && $SUDO bash install.sh "${PASSTHRU[@]}" )

echo
echo "✅ 星巢 Nest 装好了：$DIR"
echo "   程序与数据都在 $DIR（数据在 $DIR/data，备份它就等于备份了配置与登录态）"
if [ "$SYSTEMD" -eq 1 ]; then
  echo "   服务：systemctl status astroswarm（已开机自启）；改配置后 systemctl restart astroswarm"
else
  echo "   启动：$DIR/bin/astroswarm serve --host ${HOST:-127.0.0.1} --port ${PORT:-7860}"
fi
if [ "${HOST:-127.0.0.1}" = "127.0.0.1" ]; then
  echo "   打开控制台（本地开隧道，推荐）："
  echo "       ssh -L ${PORT:-7860}:127.0.0.1:${PORT:-7860} root@<服务器IP>"
  echo "       然后浏览器打开 http://127.0.0.1:${PORT:-7860}/"
  echo "   想直接用公网地址：重装时加 --host 0.0.0.0 并放行防火墙端口（明文 HTTP，建议套 nginx + HTTPS）"
else
  echo "   控制台：http://<服务器IP>:${PORT:-7860}/"
fi
echo "   卸载（保留数据）：curl -fsSL https://astroswarm.cn/downloads/uninstall-linux | sudo bash"
echo "   卸载（连数据一起删）：上面的命令再加 --all"
