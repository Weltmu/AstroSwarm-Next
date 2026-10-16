#!/usr/bin/env bash
# 星巢 Nest（AstroSwarm 无头端）· 一条命令卸载
#
#   curl -fsSL https://astroswarm.cn/downloads/uninstall-linux | sudo bash
#
# 默认行为（稳）：停服务 → 关开机自启 → 删掉程序目录 → **保留数据目录**（配置、登录态、
# 记忆），并把数据路径打印出来。要连数据一起删，加 --all（会再问一次）。
#
# 可选参数：
#   --dir /opt/astroswarm   安装目录（默认 /opt/astroswarm）
#   --all                   连数据（config.json / 登录态 / 记忆）一起删
#   --keep-data             明确表示保留数据（默认就是，写出来更清楚）
#   --yes                   不问，直接执行（脚本化用）
#   --no-systemd            不去碰 systemd（目录不是用 --systemd 装的）
#
# 安全规矩：只删"看起来确实是星群安装目录"的地方 —— 目录里必须能找到
# VERSION 或 app/astroswarm_linux 或 venv/bin/python，否则拒绝执行。
set -euo pipefail

DIR="/opt/astroswarm"
PURGE=0
ASSUME_YES=0
TOUCH_SYSTEMD=1
UNIT="/etc/systemd/system/astroswarm.service"

die() { echo "✗ $*" >&2; exit 1; }
say() { echo "· $*"; }

usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --all) PURGE=1; shift ;;
    --keep-data) PURGE=0; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --no-systemd) TOUCH_SYSTEMD=0; shift ;;
    -h|--help) usage ;;
    *) die "不认识的参数：$1（用 -h 看用法）" ;;
  esac
done

[ "$(uname -s)" = "Linux" ] || die "这个脚本是给 Linux 用的"

# ---- 必须 root（要动 systemd 和 /opt）----
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || die "卸载需要 root，请用 sudo 重跑"
  say "需要 root，自动用 sudo 重新执行…"
  exec sudo bash "$0" --dir "$DIR" \
    $([ "$PURGE" -eq 1 ] && echo --all) \
    $([ "$ASSUME_YES" -eq 1 ] && echo --yes) \
    $([ "$TOUCH_SYSTEMD" -eq 0 ] && echo --no-systemd)
fi

[ -d "$DIR" ] || die "没找到安装目录 $DIR（装到别处了就用 --dir 指定）"

# ---- 安全校验：确认这真的是星群安装目录，不是随便一个目录 ----
MARK=0
for probe in "$DIR/VERSION" "$DIR/app/astroswarm_linux" "$DIR/venv/bin/python" "$DIR/install.sh"; do
  [ -e "$probe" ] && MARK=1 && break
done
[ "$MARK" -eq 1 ] || die "$DIR 看起来不是星巢的安装目录（没有 VERSION / app/astroswarm_linux / venv），为安全起见不删。确认无误可以用 --dir 指到正确目录"

DATA="$DIR/data"
echo "将执行："
echo "  1) 停止并关闭服务（$UNIT）"
echo "  2) 删除程序目录：$DIR"
if [ "$PURGE" -eq 1 ]; then
  echo "  3) **连数据一起删**：$DATA（配置 / 登录态 / 记忆，删了不可恢复）"
else
  echo "  3) 保留数据：$DATA（要连数据一起删，加 --all）"
fi
if [ "$ASSUME_YES" -ne 1 ]; then
  printf "确认继续？输入 yes 回车："
  read -r ANSWER
  [ "$ANSWER" = "yes" ] || { echo "已取消，什么都没动。"; exit 0; }
fi

# ---- 1) 服务 ----
if [ "$TOUCH_SYSTEMD" -eq 1 ] && command -v systemctl >/dev/null 2>&1; then
  if [ -f "$UNIT" ]; then
    say "停止服务…"
    systemctl stop astroswarm.service 2>/dev/null || true
    systemctl disable astroswarm.service >/dev/null 2>&1 || true
    rm -f "$UNIT"
    systemctl daemon-reload 2>/dev/null || true
    say "服务已移除"
  else
    say "没找到 systemd 服务（可能本来就不是用 --systemd 装的）"
  fi
fi

# ---- 2) 程序目录 + 数据处理 ----
# 先处理数据（默认保留）：同盘 mv 是瞬时的，比"复制一份再删"安全也快
KEEP_DIR="${ASTROSWARM_DATA_KEEP:-/opt/astroswarm-data}"
if [ "$PURGE" -eq 1 ]; then
  say "数据也一起删（$DATA）…"
else
  if [ -d "$DATA" ]; then
    if [ -e "$KEEP_DIR" ]; then
      KEEP_DIR="$KEEP_DIR-$(date +%Y%m%d_%H%M)"
    fi
    say "把数据挪到 $KEEP_DIR（保留）…"
    mv "$DATA" "$KEEP_DIR"
  else
    KEEP_DIR=""
  fi
fi

say "删除程序目录 $DIR …"
[ "$DIR" != "/" ] || die "拒绝把根目录当安装目录"
rm -rf "$DIR"

if [ "$PURGE" -ne 1 ]; then
  echo
  echo "✅ 星巢 Nest 卸载完成"
  if [ -n "${KEEP_DIR:-}" ]; then
    echo "   数据保留在：$KEEP_DIR"
    echo "   彻底删除数据：rm -rf $KEEP_DIR"
    echo "   重装后想恢复：把 $KEEP_DIR 里的内容拷回 <安装目录>/data（例如 /opt/astroswarm/data）"
  else
    echo "   （没发现数据目录，没动任何数据）"
  fi
  echo "   想重装：curl -fsSL https://astroswarm.cn/downloads/install-linux | sudo bash"
  exit 0
fi

echo
echo "✅ 星巢 Nest 卸载完成（程序和数据都已删除）"
echo "   想重装：curl -fsSL https://astroswarm.cn/downloads/install-linux | sudo bash"
