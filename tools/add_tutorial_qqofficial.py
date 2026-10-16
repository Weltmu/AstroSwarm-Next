# -*- coding: utf-8 -*-
"""给官网教程页补一节「QQ 官方机器人：从 0 到能聊」。

对应历史欠账 #12：线上 tutorial.html 里 `q.qq.com` 0 命中 —— 用户装完官方通道
不知道要去平台打开「接收所有消息」，于是"群里 @ 它不回"变成无解的坑。
按官网规矩：备份 → 插入 → 打印结果（handover/snapshot/curl 由外层脚本负责）。
"""
import shutil
import time
from pathlib import Path

PAGE = Path("/opt/qbot_website/tutorial.html")
ANCHOR = '  <section class="panel rv" id="faq"'

SECTION = '''  <section class="panel rv" id="qqofficial" style="margin-top:clamp(16px,2.4vw,28px)">
    <div class="shead" style="margin-bottom:clamp(12px,1.6vw,18px)">
      <span class="snum">QQ 官方机器人 · 从 0 到能聊</span>
      <h2>接了官方机器人，为什么它不理我</h2>
      <p class="slead">官方机器人（q.qq.com 那种）和第三方协议端不一样：能不能看到群里的消息，取决于你在平台上开没开那个开关。下面按顺序走一遍就行。</p>
    </div>

    <div class="faq">
      <details><summary>第一步：把机器人建出来</summary>
        <p>去 q.qq.com 建一个机器人，在「开发设置」里抄下三样东西：<b>AppID</b>、<b>Token</b>、<b>AppSecret</b>。填到客户端「QQ」页对应的三个框里，保存后重启一次 NoneBot（页面上有按钮）。</p></details>

      <details><summary>第二步：先用沙箱联调，别急着上线</summary>
        <p>沙箱环境下只有你加进「测试成员」的 QQ 号能用它，别人 @ 它没反应是正常的。要对外用就得提交审核走正式环境，这一步平台会看你的机器人做什么、有没有 AIGC 资质。</p></details>

      <details><summary>第三步：让它能看见群里的消息（关键）</summary>
        <p>默认它只收得到「被 @ 」的消息。想让它看到群里所有聊天（能自己判断要不要接话、能记上下文），要去 q.qq.com 打开<b>「接收所有消息」</b>。开完回到客户端「QQ」页看状态卡上的<b>「群消息」</b>那一行：显示<b>全量模式</b>才算成功，还写着「仅 @ 消息」就是没生效（保存后要重启一次机器人）。</p></details>

      <details><summary>第四步：群里试一句</summary>
        <p>在群里 @ 它一次，它会回。之后<b>默认 30 秒内不用再 @</b>，接着说它就接着答；超过 30 秒或连着几句没提它，就自动断开，下次要重新 @。这个时间可以在插件商店的<b>「连续对话能力包」</b>里改（30 秒 ~ 5 分钟），也能改成「每条都让 AI 看一眼要不要接」或者干脆关掉。</p></details>

      <details><summary>它的限制（平台规则，不是软件坏了）</summary>
        <p>① 机器人主动发消息有条数和时间窗限制，私聊、群聊各不相同，所以"定时喊我起床"这类在官方通道上不一定发得出去；② 官方会对机器人发的内容做审核，被拦下时消息就发不出去；③ 群里只有 @ 它、或者你开了「接收所有消息」之后，它才看得到内容。</p></details>

      <details><summary>排查工具：官方通道体检</summary>
        <p>客户端「QQ」页有一颗<b>「官方通道体检」</b>按钮：凭证、环境（沙箱/正式）、机器人进程、平台连接、群消息模式、AI 开关逐条给结论。哪一条是 ✗ 就按它下面写的那句人话去处理。</p></details>
    </div>
  </section>

'''


def main() -> None:
    raw = PAGE.read_text(encoding="utf-8")
    if 'id="qqofficial"' in raw:
        print("已经加过了，跳过")
        return
    if ANCHOR not in raw:
        raise SystemExit("找不到插入锚点（FAQ section），先看页面结构再改")
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(PAGE, PAGE.with_name(f"tutorial.html.bak_{stamp}_qqofficial"))
    new = raw.replace(ANCHOR, SECTION + ANCHOR, 1)
    PAGE.write_text(new, encoding="utf-8")
    print("备份后缀:", stamp)
    print("新长度:", len(new), "（原", len(raw), "）")


if __name__ == "__main__":
    main()
