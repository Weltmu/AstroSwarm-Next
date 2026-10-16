# 手机远程控制 DSH（星巢开发）

手机上打开 **`https://desktop-i3819ar.taile5e8ca.ts.net/`** 就能直接用 DSH 界面跟我对话/下指令。
（手机需要开着 Tailscale；建议"添加到主屏幕"当 App 用。）

## 这条链路是怎么搭的

```
手机 → Tailscale（只你自己的 tailnet，不走公网）
     → https://desktop-i3819ar.taile5e8ca.ts.net
     → 本机转发器 127.0.0.1:19389（tools/phone_relay.py，计划任务 AstroSwarm-PhoneRelay 开机自启）
     → 注入 Host + 签名 Cookie → 127.0.0.1:19387（DSH 自带界面）
```

## 为什么要这个转发器

- DSH 的 Web 界面**只允许绑定 `127.0.0.1`**，并在代码里**明确拒绝 `--host 0.0.0.0`**
  （理由：那等于把"在这台电脑上执行任意命令"的能力开放到网络）。
- 它的认证是**绑定域名的签名 Cookie**（`dsh-auth-<sha256(Host)>`），
  所以电脑浏览器里的登录状态换个域名就失效——手机会被 401 挡住。
- 转发器用 DSH 自己存在 `~/.dsh/.credentials.yaml` 里的签名密钥，
  给 `127.0.0.1:19387` 签一个 7 天 Cookie，本机转发时注入。**手机端不需要 token**，
  且 DSH 重启后依然有效（密钥是持久化的）。

## 安全边界（重要）

- 只暴露在**你自己的 tailnet** 内，公网访问不到（Tailscale Serve，不是 Funnel）。
- 但"能打开这个界面"＝**能在这台电脑上执行任意命令**。所以：
  **不要把你 tailnet 的访问权给别人的设备**，也不要用 Funnel 暴露到公网。
- 关闭方法：`tailscale serve reset`，并在"任务计划程序"里删除 `AstroSwarm-PhoneRelay`。

## 自检 / 排查

```powershell
python tools/phone_relay.py --check     # 密钥、目标端口是否正常
tailscale serve status                  # 看通道是否在
curl.exe -k -o NUL -w "%{http_code}`n" https://desktop-i3819ar.taile5e8ca.ts.net/
```
