# -*- coding: utf-8 -*-
"""AI 大脑页：接口配置 + 平台开关 + 模型/MCP/记忆/身份绑定摘要。"""
import json
import logging
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ...core import agent_profile, ai_config
from ...core import bot
from ...core import message_store
from ..theme import TEXT_3
from ..widgets import EmptyState, FoldSection, GlassPanel
from .common import ModelFetchController, PageContext, Worker, make_row
from ..widgets_switch import CollapseGroup, iOSSwitchRow, upgrade_checkboxes
from ..segmented import SegmentedPill

logger = logging.getLogger("qbotmanager")


def _split_csv(text: str) -> list:
    return [x.strip() for x in (text or "").replace("，", ",").split(",") if x.strip()]


_AGENT_RESTART_KEYS = (
    "agent_profile_enabled", "agent_profile_id", "agent_wake_words",
    "agent_owner_openid", "vision_api_url", "vision_api_key", "vision_model",
    "llm_api_url", "llm_api_key", "llm_model",
)


def _agent_cfg_snapshot(s):
    """记录影响机器人启动加载的智能体/模型配置快照（用于自动重启判断）。"""
    llm = ai_config.current_config(s)
    return {
        "agent_profile_enabled": bool(getattr(s, "agent_profile_enabled", False)),
        "agent_profile_id": str(getattr(s, "agent_profile_id", "") or ""),
        "agent_wake_words": list(getattr(s, "agent_wake_words", []) or []),
        "agent_owner_openid": str(getattr(s, "agent_owner_openid", "") or ""),
        "vision_api_url": str(getattr(s, "vision_api_url", "") or ""),
        "vision_api_key": str(getattr(s, "vision_api_key", "") or ""),
        "vision_model": str(getattr(s, "vision_model", "") or ""),
        "llm_api_url": str(llm.get("api_url") or ""),
        "llm_api_key": str(llm.get("api_key") or ""),
        "llm_model": str(llm.get("model") or ""),
    }


# 真正"必须重启一次"的两个键（2026-10-15）：档案开关 / 档案 id 决定的是
# **机器人进程启动时要不要加载李清菡插件**（插件在 import 期判定 _ENABLED）。
# 热配置刷了也不管用，两个方向都会坏：
#   · 打开档案：程序侧把 QQ/微信交给档案（live gates），AI 插件按条热读立刻停，
#     可档案插件在启动时已经判定"没启用"、压根没注册 → **谁都不回话**；
#   · 关掉档案：AI 插件热恢复处理 QQ/微信，李清菡的 handler 还在 → **同一条消息双回复**。
_PROFILE_RESTART_KEYS = ("agent_profile_enabled", "agent_profile_id")


def _profile_restart_required(before, after) -> bool:
    """档案开关/id 变了就必须重启一次机器人（见上面说明）。"""
    return any(before.get(k) != after.get(k) for k in _PROFILE_RESTART_KEYS)


def _agent_restart_required(before, after) -> bool:
    """档案/模型相关配置变化时需要重启机器人才会真正载入。"""
    return any(before.get(k) != after.get(k) for k in _AGENT_RESTART_KEYS)


class PersonaSegment(SegmentedPill):
    """人设来源胶囊（预设 / 自定义）——通用控件 SegmentedPill 的预设版本。

    2026-10-11：实现搬到 `qbotmanager/ui/segmented.py`（QQ 通道切换要复用同一套外观），
    这里只保留老板要的那套文案与提示，原调用点（`PersonaSegment()` / `.changed` /
    `.set_current()`）完全不变。
    """

    def __init__(self, labels=("预设", "自定义"), parent=None):
        super().__init__(("preset", "custom"), labels, parent,
                         accessible_name="人设来源切换",
                         tooltip="预设 = 下载好的智能体档案；自定义 = 自己写或导入的人设。两段互斥。")


class BrainPage(QWidget):
    """AI 大脑页。

    refresh_status 只更新状态摘要，绝不重置接口配置/平台开关控件，
    否则主窗口的定时刷新会把用户正在编辑的内容“复原”。
    """

    def __init__(self, ctx: PageContext):
        super().__init__()
        self.ctx = ctx
        self._loaded_persona = ""
        self._loaded_agent_cfg = {}
        self._ai_configured = False
        self._beginner = False
        self._syncing_mode = False        # 文档/控件回填期间别再触发切换副作用
        self._build_ui()
        self._upgrade_toggles()
        self.load_config()
        self.refresh_status()

    def _upgrade_toggles(self):
        """把页面上的复选框升级成 iOS 式开关行（2026-10-11 老板要求）。

        只升级"独占一行"的：横排多个复选框（如 QQ/微信/飞书/纸飞机）保持原样，
        避免一排四个整行开关。危险开关（主动搭话）带二次确认。
        """
        try:
            n = upgrade_checkboxes(self)
            # 主动搭话那个单独包一层，带上二次确认
            # 主动搭话要带二次确认：**不能重复包裹**（包两次会在卡片左上角留残影），
            # 先找已经包好的那一行补提示；确实没包过时再按索引插进去。
            chk = getattr(self, "chk_interject", None)
            if chk is not None:
                row = None
                for r in self.findChildren(iOSSwitchRow):
                    if getattr(r, "chk", None) is chk:
                        row = r
                        break
                if row is not None:
                    row._danger = getattr(self, "_interject_danger", "")
                    row.setToolTip(row._danger)
                    self._switch_interject = row
                else:
                    lay = chk.parentWidget().layout() if chk.parentWidget() else None
                    idx = lay.indexOf(chk) if lay is not None else -1
                    if idx >= 0:
                        row = iOSSwitchRow(chk, parent=chk.parentWidget(),
                                           danger_confirm=getattr(self, "_interject_danger", ""))
                        lay.removeWidget(chk)
                        lay.insertWidget(idx, row)
                        self._switch_interject = row
                        n += 1
            self._switch_count = n
        except Exception as e:  # noqa: BLE001 —— 开关升级失败不能挡住页面
            try:
                self.ctx.show_toast("开关样式升级失败：" + str(e))
            except Exception:  # noqa: BLE001
                pass

    def _build_ui(self):
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)

        title = QLabel("AI 大脑")
        title.setObjectName("pageTitle")
        sub = QLabel("QQ / 微信 / 飞书 / 纸飞机共用一个 AI 大脑。")
        sub.setObjectName("pageSub")
        outer.addWidget(title)
        outer.addWidget(sub)

        # ---- 接口配置 + 平台开关 ----
        config_panel = GlassPanel(strong=True)
        cv = QVBoxLayout(config_panel)
        cv.setContentsMargins(18, 14, 18, 16)
        cv.setSpacing(10)
        cfg_lbl = QLabel("接口配置")
        cfg_lbl.setObjectName("sectionTitle")
        cv.addWidget(cfg_lbl)
        cfg_tip = QLabel("选服务商自动填地址；填好密钥点「获取模型」刷新。")
        cfg_tip.setToolTip("模型 / 密钥 / 地址改完立即生效，不用重启机器人。")
        cfg_tip.setWordWrap(True)
        cfg_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        cv.addWidget(cfg_tip)

        platform_row = QHBoxLayout()
        platform_lbl = QLabel("平台启用")
        platform_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.chk_qq = QCheckBox("QQ")
        self.chk_wechat = QCheckBox("微信")
        self.chk_feishu = QCheckBox("飞书")
        self.chk_telegram = QCheckBox("纸飞机")
        for chk in (self.chk_qq, self.chk_wechat, self.chk_feishu, self.chk_telegram):
            chk.setToolTip("关掉后 AI 大脑不再处理该平台的消息。")
        platform_row.addWidget(platform_lbl)
        platform_row.addSpacing(6)
        platform_row.addWidget(self.chk_qq)
        platform_row.addWidget(self.chk_wechat)
        platform_row.addWidget(self.chk_feishu)
        platform_row.addWidget(self.chk_telegram)
        platform_row.addStretch(1)
        cv.addLayout(platform_row)

        provider_row = QHBoxLayout()
        provider_lbl = QLabel("服务商")
        provider_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.ai_provider_combo = QComboBox()
        for p in ai_config.AI_PROVIDERS:
            self.ai_provider_combo.addItem(p["name"], p["key"])
        self.ai_provider_combo.setToolTip("选大厂服务商自动填地址与模型，也可自定义。")
        provider_row.addWidget(provider_lbl)
        provider_row.addWidget(self.ai_provider_combo, 1)
        cv.addLayout(provider_row)

        url_row = QHBoxLayout()
        url_lbl = QLabel("接口地址")
        url_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.ai_url_edit = QLineEdit()
        self.ai_url_edit.setPlaceholderText("https://api.deepseek.com")
        url_row.addWidget(url_lbl)
        url_row.addWidget(self.ai_url_edit, 1)
        cv.addLayout(url_row)

        model_row = QHBoxLayout()
        model_lbl = QLabel("模型")
        model_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        # 模型名不再写死：服务商上新/改名很快，预设只作兜底，列表按密钥实时拉
        self.ai_model_edit = QComboBox()
        self.ai_model_edit.setEditable(True)
        self.ai_model_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        _model_line = self.ai_model_edit.lineEdit()
        if _model_line is not None:
            _model_line.setPlaceholderText("填完密钥后点「获取模型」拉取，也可直接手填")
        model_row.addWidget(model_lbl)
        model_row.addWidget(self.ai_model_edit, 1)
        self.btn_fetch_models = QPushButton("获取模型")
        self.btn_fetch_models.setObjectName("ghost")
        self.btn_fetch_models.setToolTip("拉取该服务商可用的模型列表")
        model_row.addWidget(self.btn_fetch_models)
        cv.addLayout(model_row)
        self.ai_model_tip = QLabel("")
        self.ai_model_tip.setWordWrap(True)
        self.ai_model_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        cv.addWidget(self.ai_model_tip)

        key_row = QHBoxLayout()
        key_lbl = QLabel("API 密钥")
        key_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.ai_key_edit = QLineEdit()
        self.ai_key_edit.setEchoMode(QLineEdit.Password)
        self.ai_key_edit.setPlaceholderText("sk-...")
        key_row.addWidget(key_lbl)
        key_row.addWidget(self.ai_key_edit, 1)
        cv.addLayout(key_row)

        # 填完密钥/地址自动拉一次，也可以点「获取模型」重拉
        self._suppress_fetch = False
        self._model_fetcher = ModelFetchController(
            self, self.ai_model_edit, self.ai_model_tip, self.btn_fetch_models,
            url_getter=lambda: self.ai_url_edit.text(),
            key_getter=lambda: self.ai_key_edit.text(),
            cache_getter=self._models_from_cache,
            cache_setter=self._cache_models)
        self.btn_fetch_models.clicked.connect(
            lambda: self._model_fetcher.start("manual"))
        self.ai_key_edit.editingFinished.connect(
            lambda: self._model_fetcher.start("auto"))
        self.ai_url_edit.editingFinished.connect(
            lambda: self._model_fetcher.start("auto"))

        save_row = QHBoxLayout()
        self.btn_save = QPushButton("保存并测试")
        self.btn_save.setToolTip(
            "保存（模型/密钥/开关/人设改完立即生效，不用重启机器人）；"
            "能拉到模型说明配置可用")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self._save_config)
        save_row.addWidget(self.btn_save)
        # 「需要重启」的改动（插件代码 / 换通道 / 能力包 / MCP）都点这个按钮。
        # 口径一句话：设置改了立即生效；代码改了才需要重启，且点一下就能重启。
        self.btn_restart_bot = QPushButton("重启机器人")
        self.btn_restart_bot.setObjectName("ghost")
        self.btn_restart_bot.setToolTip(
            "装/卸插件、换 QQ 通道、MCP 服务器这类改动要重启一次才生效。\n"
            "只重启机器人进程（不碰 NapCat / QQ），大约 10 秒内离线，之后自动重连。")
        self.btn_restart_bot.clicked.connect(self._restart_bot)
        save_row.addWidget(self.btn_restart_bot)
        save_row.addStretch(1)
        cv.addLayout(save_row)
        outer.addWidget(config_panel)

        # ---- 还没填密钥时：本页只留「先填 AI 配置」，其余板块先不显示 ----
        self.notice_panel = GlassPanel(strong=True)
        nv = QVBoxLayout(self.notice_panel)
        nv.setContentsMargins(18, 16, 18, 18)
        nv.setSpacing(8)
        n_title = QLabel("先填 AI 配置")
        n_title.setObjectName("sectionTitle")
        nv.addWidget(n_title)
        n_body = QLabel("先选服务商、填密钥并保存，下面的设置才会出现。")
        n_body.setToolTip("密钥只存在你本机，不会上传。")
        n_body.setWordWrap(True)
        n_body.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        nv.addWidget(n_body)
        outer.addWidget(self.notice_panel)

        # ---- 人设（一张卡）：预设档案 / 自定义人设 + 谁在生效 + 主动聊天 -------------
        # 2026-10-07 老板：以前这里是三张卡（智能体档案 / 人设工坊（本地）/ 人设与主动聊天），
        # 功能重合、设置框离得远，一眼看不出「谁在生效」。现在并成一张，顶部一个胶囊
        # 分段控件切「预设 / 自定义」，下面固定放主动聊天那几项（跟人设来源无关）。
        persona_panel = GlassPanel(strong=True)
        self._persona_panel = persona_panel
        pv = QVBoxLayout(persona_panel)
        pv.setContentsMargins(18, 14, 18, 16)
        pv.setSpacing(10)
        p_lbl = QLabel("人设与主动聊天")
        p_lbl.setObjectName("sectionTitle")
        pv.addWidget(p_lbl)
        p_tip = QLabel(
            "先选人设从哪来：用下载好的档案（预设），还是自己写 / 导入一份（自定义）；"
            "人设改完立即生效。")
        p_tip.setWordWrap(True)
        p_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        pv.addWidget(p_tip)

        seg_row = QHBoxLayout()
        self.persona_seg = PersonaSegment()
        self.persona_seg.changed.connect(self._on_persona_seg)
        seg_row.addWidget(self.persona_seg)
        seg_row.addStretch(1)
        pv.addLayout(seg_row)

        # 谁在生效：两种形态都显示（本地人设《…》/ 预设档案 …/ 手写人设）
        self.persona_mode_lbl = QLabel("当前生效：手写人设")
        self.persona_mode_lbl.setWordWrap(True)
        self.persona_mode_lbl.setToolTip("这里说的是机器人现在真正照谁的性格说话。")
        pv.addWidget(self.persona_mode_lbl)
        # 切换时改了什么的实话（不静默改状态）
        self.persona_switch_hint = QLabel("")
        self.persona_switch_hint.setWordWrap(True)
        self.persona_switch_hint.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        pv.addWidget(self.persona_switch_hint)

        # 唤醒词（群聊"被叫到称呼就回应"的触发词）：2026-10-12 老板报「找不到了」——
        # v1.2.15 把它收进「预设」面板，切到「自定义」整块隐藏。它跟人设来源是两件事，
        # 挪到胶囊下面常显；配一行小字说清它作用在「预设」档案上。
        wake_note = QLabel("群里有人喊这些称呼，不用 @ 也会回应；填完保存立即生效，留空 = 只认 @ 和引用。")
        wake_note.setWordWrap(True)
        wake_note.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        pv.addWidget(wake_note)
        wake_row = QHBoxLayout()
        wake_lbl = QLabel("唤醒词")
        wake_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.agent_wake_edit = QLineEdit()
        self.agent_wake_edit.setPlaceholderText("逗号分隔，如：可奈,奈姐；留空 = 群里只靠 @ / 引用触发")
        self.agent_wake_edit.textEdited.connect(self._on_wake_edited)
        wake_row.addWidget(wake_lbl)
        wake_row.addWidget(self.agent_wake_edit, 1)
        self.btn_wake_generate = QPushButton("按人格生成")
        self.btn_wake_generate.setObjectName("ghost")
        self.btn_wake_generate.setToolTip("按人格文本生成唤醒词；没配模型时按名字提取。")
        self.btn_wake_generate.clicked.connect(self._start_wake_generation)
        wake_row.addWidget(self.btn_wake_generate)
        pv.addLayout(wake_row)
        self.chk_wake_auto = QCheckBox("人格变化时自动重生成唤醒词")
        self.chk_wake_auto.setToolTip("手动改过唤醒词后，这里会自动关闭。")
        pv.addWidget(self.chk_wake_auto)

        # 预设态：档案开关 + 档案选择 + 会话标识 / 视觉模型（唤醒词挪到上面常显，其余是原「智能体档案」卡的内容）
        self._agent_panel = QWidget()
        av = QVBoxLayout(self._agent_panel)
        av.setContentsMargins(0, 0, 0, 0)
        av.setSpacing(10)
        a_tip = QLabel("挑个档案打开开关，就有群管、表情、游戏等功能。")
        a_tip.setToolTip("唤醒词用于群聊，不用 @ 也能叫醒；私聊直接说。")
        a_tip.setWordWrap(True)
        a_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        av.addWidget(a_tip)

        self.chk_agent_profile = QCheckBox("启用档案")
        self.chk_agent_profile.setToolTip("开启后按档案启用群管、语音、图片、游戏。")
        self.chk_agent_profile.toggled.connect(self._on_agent_profile_toggled)
        av.addWidget(self.chk_agent_profile)

        profile_row = QHBoxLayout()
        profile_lbl = QLabel("档案选择")
        profile_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.agent_profile_combo = QComboBox()
        for prof in agent_profile.list_profiles():
            self.agent_profile_combo.addItem(prof["name"], prof["id"])
        self.agent_profile_combo.setToolTip("星群助手 = 默认；李清菡 = 完整功能。")
        profile_row.addWidget(profile_lbl)
        profile_row.addWidget(self.agent_profile_combo, 1)
        av.addLayout(profile_row)

        # 一个档案都没有时不摆空白：给一句实话
        self.agent_profile_empty = QLabel("还没有下载档案，去「插件工坊」看看")
        self.agent_profile_empty.setWordWrap(True)
        self.agent_profile_empty.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.agent_profile_empty.setVisible(self.agent_profile_combo.count() == 0)
        av.addWidget(self.agent_profile_empty)

        owner_row = QHBoxLayout()
        # 「你同学」是李清菡人设里对老板的称呼，不能当界面文案（老板看到一脸问号）
        owner_lbl = QLabel("你的会话标识")
        owner_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.agent_owner_edit = QLineEdit()
        self.agent_owner_edit.setPlaceholderText("从「消息中心」复制你的会话标识贴这里")
        self.agent_owner_edit.setToolTip("留空则群里谁都能用禁言等群管命令。")
        owner_row.addWidget(owner_lbl)
        owner_row.addWidget(self.agent_owner_edit, 1)
        av.addLayout(owner_row)

        vision_tip = QLabel("视觉模型（图片描述/生成；密钥留空 = 不启用）")
        vision_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        av.addWidget(vision_tip)
        v_url_row = QHBoxLayout()
        v_url_lbl = QLabel("视觉服务地址")
        v_url_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.vision_url_edit = QLineEdit()
        self.vision_url_edit.setPlaceholderText("https://api.siliconflow.cn/v1")
        v_url_row.addWidget(v_url_lbl)
        v_url_row.addWidget(self.vision_url_edit, 1)
        av.addLayout(v_url_row)
        v_model_row = QHBoxLayout()
        v_model_lbl = QLabel("视觉模型")
        v_model_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.vision_model_edit = QLineEdit()
        self.vision_model_edit.setPlaceholderText("Qwen/Qwen3-Omni-30B-A3B-Instruct")
        v_model_row.addWidget(v_model_lbl)
        v_model_row.addWidget(self.vision_model_edit, 1)
        av.addLayout(v_model_row)
        v_key_row = QHBoxLayout()
        v_key_lbl = QLabel("视觉密钥")
        v_key_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.vision_key_edit = QLineEdit()
        self.vision_key_edit.setEchoMode(QLineEdit.Password)
        self.vision_key_edit.setPlaceholderText("sk-...")
        v_key_row.addWidget(v_key_lbl)
        v_key_row.addWidget(self.vision_key_edit, 1)
        av.addLayout(v_key_row)
        # 预设态容器收尾：这一整块就是「预设」那一段显示的内容
        pv.addWidget(self._agent_panel)

        # ---- 自定义态：本地人设包（原来「人设工坊（本地）」卡里的全部内容） ----
        self._workshop_panel = QWidget()
        wv = QVBoxLayout(self._workshop_panel)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(10)
        w_tip = QLabel("导入人设包：先下载模板填写，再导入文件。")
        w_tip.setToolTip("启用后会关闭预设档案，避免双人格。")
        w_tip.setWordWrap(True)
        w_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        wv.addWidget(w_tip)
        ws_btns = QHBoxLayout()
        ws_btns.setSpacing(6)
        self.btn_persona_template = QPushButton("下载模板")
        self.btn_persona_template.setObjectName("ghost")
        self.btn_persona_template.setMinimumHeight(30)
        self.btn_persona_template.clicked.connect(self._download_persona_template)
        self.btn_persona_import = QPushButton("导入人设")
        self.btn_persona_import.setObjectName("primary")
        self.btn_persona_import.setMinimumHeight(30)
        self.btn_persona_import.clicked.connect(self._import_persona)
        self.btn_persona_guide = QPushButton("模板说明")
        self.btn_persona_guide.setObjectName("ghost")
        self.btn_persona_guide.setMinimumHeight(30)
        self.btn_persona_guide.clicked.connect(self._show_persona_guide)
        self.btn_persona_refresh = QPushButton("刷新")
        self.btn_persona_refresh.setObjectName("ghost")
        self.btn_persona_refresh.setMinimumHeight(30)
        self.btn_persona_refresh.clicked.connect(self._refresh_persona_list)
        ws_btns.addWidget(self.btn_persona_template)
        ws_btns.addWidget(self.btn_persona_guide)
        ws_btns.addWidget(self.btn_persona_import)
        ws_btns.addWidget(self.btn_persona_refresh)
        ws_btns.addStretch(1)
        wv.addLayout(ws_btns)
        self.persona_list = QListWidget()
        self.persona_list.setMaximumHeight(110)
        wv.addWidget(self.persona_list)
        ws_act = QHBoxLayout()
        ws_act.setSpacing(6)
        self.btn_persona_activate = QPushButton("启用")
        self.btn_persona_activate.setObjectName("ghost")
        self.btn_persona_activate.setMinimumHeight(30)
        self.btn_persona_activate.clicked.connect(self._activate_persona)
        self.btn_persona_deactivate = QPushButton("停用本地人设")
        self.btn_persona_deactivate.setObjectName("ghost")
        self.btn_persona_deactivate.setMinimumHeight(30)
        self.btn_persona_deactivate.clicked.connect(self._deactivate_persona)
        self.btn_persona_uninstall = QPushButton("卸载")
        self.btn_persona_uninstall.setObjectName("ghost")
        self.btn_persona_uninstall.setMinimumHeight(30)
        self.btn_persona_uninstall.clicked.connect(self._uninstall_persona)
        ws_act.addWidget(self.btn_persona_activate)
        ws_act.addWidget(self.btn_persona_deactivate)
        ws_act.addWidget(self.btn_persona_uninstall)
        ws_act.addStretch(1)
        wv.addLayout(ws_act)
        # 人格设定：老板要求两端一模一样（控制台那张卡里就有这个框），所以这里也放一个
        # 可编辑的多行框 +「保存人设」。写的是**同一个字段、同一个函数**
        # （ai_config.save_personality → 机器人侧 aichat_manager.json 的 personality），
        # 不另造一份存储；「设置 → 高级 → 人格设定」那块保留，两边改的是同一份。
        persona_row = QHBoxLayout()
        persona_lbl = QLabel("人格设定")
        persona_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        persona_lbl.setAlignment(Qt.AlignTop)
        self.personality_edit = QPlainTextEdit()
        self.personality_edit.setPlaceholderText("例如：回答简洁直接，不废话…")
        self.personality_edit.setToolTip(
            "这里写什么，机器人就按什么演；留空 = 内置默认。和「设置 → 高级 → 人格设定」是同一份。")
        self.personality_edit.setFixedHeight(96)
        persona_row.addWidget(persona_lbl)
        persona_row.addWidget(self.personality_edit, 1)
        wv.addLayout(persona_row)
        persona_btns = QHBoxLayout()
        persona_btns.setSpacing(6)
        self.btn_persona_save = QPushButton("保存人设")
        self.btn_persona_save.setObjectName("primary")
        self.btn_persona_save.setMinimumHeight(30)
        self.btn_persona_save.setToolTip("只保存这一项人格设定，不动接口配置")
        self.btn_persona_save.clicked.connect(self._save_persona_text)
        persona_btns.addWidget(self.btn_persona_save)
        persona_hint = QLabel("也可以去「设置 → 高级 → 人格设定」改，两边是同一份。")
        persona_hint.setWordWrap(True)
        persona_hint.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        persona_btns.addWidget(persona_hint, 1)
        wv.addLayout(persona_btns)
        self.persona_save_hint = QLabel("")
        self.persona_save_hint.setWordWrap(True)
        self.persona_save_hint.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        wv.addWidget(self.persona_save_hint)
        # 自定义态容器收尾：这一整块就是「自定义」那一段显示的内容
        pv.addWidget(self._workshop_panel)

        # ---- 主动聊天（跟人设从哪来无关，两种形态都显示） ----
        pro_lbl = QLabel("主动聊天（跟上面选哪种人设无关）")
        pro_lbl.setWordWrap(True)
        pro_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        pv.addWidget(pro_lbl)

        self.chk_proactive = QCheckBox("启用主动聊天")
        self.chk_proactive.setToolTip("机器人会不定时找人聊天；发不出去会跳过。")
        pv.addWidget(self.chk_proactive)
        proactive_tip = QLabel(
            "平台限制：QQ 有额度；微信要 30 分钟内说过话。")
        proactive_tip.setWordWrap(True)
        proactive_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        pv.addWidget(proactive_tip)

        # 群聊回话总开关：默认开（群里被 @ 就回）。以前群白名单默认为空 =
        # 一个群都不回，用户装完在群里怎么 @ 都没反应，界面也没入口 —— 这里补上。
        self.chk_group_chat = QCheckBox("允许在群里回话")
        self.chk_group_chat.setToolTip("关掉后不在任何群里说话；打开则所有群都回。")
        pv.addWidget(self.chk_group_chat)

        # 识图开关（2026-10-03 加）：以前这个开关只有插件配置里有，界面上根本没有，
        # 大部分人（包括老板）一直是关的 —— 表现就是"发表情包/照片它说看不到"。
        self.chk_vision = QCheckBox("能看图片和表情包")
        self.chk_vision.setToolTip("用 AI 大脑当前的模型识图；模型不支持图片时无效。")
        pv.addWidget(self.chk_vision)

        # 群里发引用/聊天记录/图片时，把这些内容也喂给 AI（不然它只看得到一句纯文本）
        self.chk_rich_parse = QCheckBox("引用、记录、链接也一起给它看")
        self.chk_rich_parse.setToolTip("关掉只剩纯文本，引用和卡片里的内容都会丢。")
        pv.addWidget(self.chk_rich_parse)

        # 聊天里自学（2026-10-04）：不会的事，AI 自己写个插件装上
        self.chk_self_forge = QCheckBox("不会的事自己写插件（只有你能触发）")
        self.chk_self_forge.setToolTip("只有你能触发：它自己写个插件装上，再回你。")
        pv.addWidget(self.chk_self_forge)

        # 跨平台互通（2026-10-04 内置基础功能）：发消息/发文件到用户自己的另一个平台
        self.chk_cross_platform = QCheckBox("跨平台互通（发到我的微信/QQ）")
        self.chk_cross_platform.setToolTip("只能发给你自己绑定的平台，别人收不到。")
        pv.addWidget(self.chk_cross_platform)
        self.cross_status_lbl = QLabel("绑定状态：还没绑定（发「绑定」拿码）")
        self.cross_status_lbl.setToolTip("在任意平台私聊发「绑定」，拿码后去另一个平台发「绑定 六位数字」。")
        self.cross_status_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.cross_status_lbl.setWordWrap(True)
        pv.addWidget(self.cross_status_lbl)

        # 群列表：插件收到过消息的群会自动列在这里，勾选=允许它在这个群回话。
        # 一个都不勾（或全勾）= 不限群，和默认行为一致；不用手抄 openid。
        self.group_list_lbl = QLabel("允许回话的群：不限群（默认）")
        self.group_list_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.group_list_lbl.setWordWrap(True)
        self.group_list_lbl.setToolTip("取消勾选某个群，就只有勾上的群能回话。")
        pv.addWidget(self.group_list_lbl)
        self.group_list_box = QWidget()
        self._group_rows_layout = QVBoxLayout(self.group_list_box)
        self._group_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._group_rows_layout.setSpacing(4)
        pv.addWidget(self.group_list_box)

        targets_row = QHBoxLayout()
        targets_lbl = QLabel("目标用户")
        targets_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.ai_proactive_targets = QLineEdit()
        self.ai_proactive_targets.setPlaceholderText("多个用逗号分隔；留空 = 不主动找人")
        self.ai_proactive_targets.setToolTip("到「消息中心」复制对方的会话标识。")
        targets_row.addWidget(targets_lbl)
        targets_row.addWidget(self.ai_proactive_targets, 1)
        pv.addLayout(targets_row)

        groups_row = QHBoxLayout()
        groups_lbl = QLabel("搭话群聊")
        groups_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.ai_proactive_groups = QLineEdit()
        self.ai_proactive_groups.setPlaceholderText("多个用逗号分隔；留空 = 不搭话")
        self.ai_proactive_groups.setToolTip("先在群里发条消息，机器人收到后就能识别该群。")
        groups_row.addWidget(groups_lbl)
        groups_row.addWidget(self.ai_proactive_groups, 1)
        pv.addLayout(groups_row)

        nums_grid = QGridLayout()
        nums_grid.setHorizontalSpacing(12)
        nums_grid.setVerticalSpacing(6)
        # 2026-10-11：数值项收进可折叠分组（老板反馈"占比太乱"），标题行显示当前值摘要。
        self._sp_interval_min = self._spin("间隔最小(分)", "ai_interval_min", 1, 1440, 30)
        self._sp_interval_max = self._spin("间隔最大(分)", "ai_interval_max", 1, 1440, 90)
        self._sp_cooldown = self._spin("冷却(分)", "ai_cooldown", 1, 1440, 30)
        self._sp_quiet_start = self._spin("免打扰开始(时)", "ai_quiet_start", 0, 23, 0)
        self._sp_quiet_end = self._spin("免打扰结束(时)", "ai_quiet_end", 0, 23, 8)
        for _i, _w in enumerate((self._sp_interval_min, self._sp_interval_max, self._sp_cooldown)):
            nums_grid.addWidget(_w, 0, _i)
        nums_grid.addWidget(self._sp_quiet_start, 1, 0)
        nums_grid.addWidget(self._sp_quiet_end, 1, 1)
        nums_grid.setColumnStretch(3, 1)
        nums_holder = QWidget()
        nums_holder.setLayout(nums_grid)
        self._freq_group = CollapseGroup(
            "主动聊天频率", nums_holder,
            summary=lambda: (f"间隔 {self.ai_interval_min.value()}–{self.ai_interval_max.value()} 分"
                             f" · 冷却 {self.ai_cooldown.value()} 分"
                             f" · 免打扰 {self.ai_quiet_start.value()}–{self.ai_quiet_end.value()} 时"),
            collapsed=True)
        # 注意：_spin() 返回的是外壳，真正的 QSpinBox 挂在 self.ai_* 上（_spin 里 setattr 的）
        for _sp in (self.ai_interval_min, self.ai_interval_max, self.ai_cooldown,
                    self.ai_quiet_start, self.ai_quiet_end):
            _sp.valueChanged.connect(lambda _v: self._freq_group.refresh_summary())
        pv.addWidget(self._freq_group)

        # ---- 群里主动搭话（内核在插件里，以前没有任何界面入口）----
        self.chk_interject = QCheckBox("允许在群里主动搭话")
        self.chk_interject.setToolTip("需在 q.qq.com 开启「接收所有消息」。")
        # 危险开关（老板 2026-10-11 定：二级确认）——开启前说清后果
        self._interject_danger = ("开启后它会自己找群里的人说话（按上面的概率与间隔），"
                                  "群里所有人都能看到。想停就把它关掉，或把上面「搭话参数」里的概率调低。")
        pv.addWidget(self.chk_interject)

        # 免@连续对话：参数放在「连续对话能力包」里（插件商店装上后，
        # 「已装能力包 → 配置」里改），这里只给一行说明指路。
        session_hint = QLabel("免@连续对话：在「连续对话能力包」里配置。")
        session_hint.setWordWrap(True)
        session_hint.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        pv.addWidget(session_hint)
        interject_grid = QGridLayout()
        interject_grid.setHorizontalSpacing(12)
        interject_grid.setVerticalSpacing(6)
        self._sp_prob = self._spin("搭话概率(%)", "ai_interject_probability", 1, 100, 30)
        self._sp_imin = self._spin("搭话间隔最小(分)", "ai_interject_min", 1, 1440, 30)
        self._sp_imax = self._spin("搭话间隔最大(分)", "ai_interject_max", 1, 1440, 90)
        self._sp_ihist = self._spin("参考最近条数", "ai_interject_history", 1, 50, 10)
        interject_grid.addWidget(self._sp_prob, 0, 0)
        interject_grid.addWidget(self._sp_imin, 0, 1)
        interject_grid.addWidget(self._sp_imax, 0, 2)
        interject_grid.addWidget(self._sp_ihist, 1, 0)
        interject_grid.setColumnStretch(3, 1)
        interject_holder = QWidget()
        interject_holder.setLayout(interject_grid)
        self._interject_group = CollapseGroup(
            "搭话参数", interject_holder,
            summary=lambda: (f"概率 {self.ai_interject_probability.value()}%"
                             f" · 间隔 {self.ai_interject_min.value()}–{self.ai_interject_max.value()} 分"
                             f" · 参考 {self.ai_interject_history.value()} 条"),
            collapsed=True)
        for _sp in (self.ai_interject_probability, self.ai_interject_min,
                    self.ai_interject_max, self.ai_interject_history):
            _sp.valueChanged.connect(lambda _v: self._interject_group.refresh_summary())
        pv.addWidget(self._interject_group)
        outer.addWidget(persona_panel)
        # 卡片建好后先把形态摆对（进页面时不该先闪一下另一个形态）
        self.set_persona_mode("preset" if self.ctx.settings.agent_profile_enabled else "custom",
                              animate=False)

        # ---- 记忆与 MCP ----
        mem_panel = GlassPanel(strong=True)
        self._mem_panel = mem_panel
        mv = QVBoxLayout(mem_panel)
        mv.setContentsMargins(18, 14, 18, 16)
        mv.setSpacing(10)
        m_lbl = QLabel("记忆与 MCP")
        m_lbl.setObjectName("sectionTitle")
        mv.addWidget(m_lbl)
        # 自动通过好友申请 + Agent 模式（老板 2026-10-12）
        # —— **能力不在就不显示开关**（他的原话：插件没装，AI 大脑的开关是不是该隐藏）
        self.chk_auto_friend = None
        if ai_config.has_capability(self.ctx.settings, "approve_friend"):
            self.chk_auto_friend = QCheckBox("自动通过好友申请（卖家/客服用）")
            self.chk_auto_friend.setToolTip(
                "打开后：有人申请加机器人好友，它直接通过（不再问你）。"
                "默认关闭——开着等于谁加都通过。")
            self.chk_auto_friend.setChecked(
                ai_config.read_auto_approve_friend(self.ctx.settings))
            self.chk_auto_friend.toggled.connect(
                lambda on: ai_config.save_auto_approve_friend(self.ctx.settings, on))
            mv.addWidget(self.chk_auto_friend)
        self.chk_agent_mode = None
        if ai_config.has_capability(self.ctx.settings, "send_to_my_platform"):
            self.chk_agent_mode = QCheckBox("Agent 模式：先动手办事，再说话")
            self.chk_agent_mode.setToolTip(
                "开启后它会真的去调工具（发到别的平台、记事、提醒、抓网页、发图…），"
                "办完再用人设的口吻回你；关掉就只聊天不动手。")
            self.chk_agent_mode.setChecked(ai_config.read_agent_mode(self.ctx.settings))
            self.chk_agent_mode.toggled.connect(
                lambda on: ai_config.save_agent_mode(self.ctx.settings, on))
            mv.addWidget(self.chk_agent_mode)
        self.chk_memory = QCheckBox("启用全局记忆（默认开启，AI 会记住长期事项）")
        mv.addWidget(self.chk_memory)
        # 内置记忆三档（无 / 基础 / 智能）：与下面那行复选框是同一份配置，
        # 选「无」会自动把复选框也取消（ai_config.save_memory_level 里统一）。
        mem_level_row = QHBoxLayout()
        mem_level_row.setSpacing(8)
        mem_level_lbl = QLabel("记忆档位")
        mem_level_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        mem_level_row.addWidget(mem_level_lbl)
        self.cmb_memory_level = QComboBox()
        self.cmb_memory_level.setMinimumHeight(30)
        for opt in ai_config.memory_level_info()["options"]:
            self.cmb_memory_level.addItem(
                f"{opt['label']}", opt["value"])
            self.cmb_memory_level.setItemData(
                self.cmb_memory_level.count() - 1, opt["hint"], Qt.ToolTipRole)
        self.cmb_memory_level.currentIndexChanged.connect(self._on_memory_level_changed)
        self.cmb_memory_level.setToolTip(
            "记忆档位改完立即生效，不用重启机器人：无 = 不记不读，"
            "基础 = 记事实和日记，智能 = 再加权重/承诺/时间线/隐私过滤。")
        mem_level_row.addWidget(self.cmb_memory_level, 1)
        mv.addLayout(mem_level_row)
        self.mem_level_hint = QLabel("")
        self.mem_level_hint.setWordWrap(True)
        self.mem_level_hint.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        mv.addWidget(self.mem_level_hint)
        self.mem_level_hint.setToolTip("改完立即生效，不用重启机器人。")
        # 「现在记住了什么」：读的就是内置记忆库（list_memories）
        mem_peek_row = QHBoxLayout()
        mem_peek_row.setSpacing(6)
        self.btn_mem_peek = QPushButton("现在记住了什么")
        self.btn_mem_peek.setObjectName("ghost")
        self.btn_mem_peek.setMinimumHeight(30)
        self.btn_mem_peek.clicked.connect(self._peek_builtin_memory)
        mem_peek_row.addWidget(self.btn_mem_peek)
        mem_peek_row.addStretch(1)
        mv.addLayout(mem_peek_row)
        self.mem_peek_list = QListWidget()
        self.mem_peek_list.setMaximumHeight(130)
        self.mem_peek_list.setVisible(False)
        mv.addWidget(self.mem_peek_list)
        self.chk_mcp = QCheckBox("启用 MCP 工具（供 AI 调用外部服务器）")
        mv.addWidget(self.chk_mcp)
        mcp_row = QHBoxLayout()
        mcp_lbl = QLabel("MCP 服务器")
        mcp_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        mcp_lbl.setAlignment(Qt.AlignTop)
        self.mcp_json_edit = QPlainTextEdit()
        self.mcp_json_edit.setPlaceholderText(
            '{\n  "服务器名": {"type": "sse", "enabled": true, "url": "http://127.0.0.1:8000/sse", "headers": {}}\n}')
        self.mcp_json_edit.setFixedHeight(96)
        mcp_row.addWidget(mcp_lbl)
        mcp_row.addWidget(self.mcp_json_edit, 1)
        mv.addLayout(mcp_row)
        mcp_hint = QLabel(
            "照上面的示例格式填写；MCP 服务器要重启一次机器人才会真正连上"
            "（界面上可直接点「重启机器人」）。")
        mcp_hint.setWordWrap(True)
        mcp_hint.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        mv.addWidget(mcp_hint)

        mem_tip = QLabel("李清菡档案的记忆库，可导出、删除、清空。")
        mem_tip.setToolTip("和下面的全局记忆是两份，互不影响。")
        mem_tip.setWordWrap(True)
        mem_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        mv.addWidget(mem_tip)
        self.memory_list = QListWidget()
        self.memory_list.setMaximumHeight(130)
        mv.addWidget(self.memory_list)
        mem_btns = QHBoxLayout()
        mem_btns.setSpacing(6)
        self.btn_memory_refresh = QPushButton("刷新")
        self.btn_memory_refresh.setObjectName("ghost")
        self.btn_memory_refresh.setMinimumHeight(30)
        self.btn_memory_refresh.clicked.connect(self._refresh_memory_list)
        self.btn_memory_export = QPushButton("导出记忆")
        self.btn_memory_export.setObjectName("ghost")
        self.btn_memory_export.setMinimumHeight(30)
        self.btn_memory_export.clicked.connect(self._export_memory)
        self.btn_memory_delete = QPushButton("删除选中")
        self.btn_memory_delete.setObjectName("ghost")
        self.btn_memory_delete.setMinimumHeight(30)
        self.btn_memory_delete.clicked.connect(self._delete_memory)
        self.btn_memory_clear = QPushButton("清空")
        self.btn_memory_clear.setObjectName("ghost")
        self.btn_memory_clear.setMinimumHeight(30)
        self.btn_memory_clear.clicked.connect(self._clear_memory)
        mem_btns.addWidget(self.btn_memory_refresh)
        mem_btns.addWidget(self.btn_memory_export)
        mem_btns.addWidget(self.btn_memory_delete)
        mem_btns.addWidget(self.btn_memory_clear)
        mem_btns.addStretch(1)
        # 记忆 / 知识库这些管理动作收进折叠区，首屏只留开关与说明（功能一个没少）
        self._mem_actions = QWidget()
        ma = QVBoxLayout(self._mem_actions)
        ma.setContentsMargins(0, 0, 0, 0)
        ma.setSpacing(8)
        ma.addLayout(mem_btns)

        # ---- 内置 AI 插件的全局记忆（另一个文件，与控制台同一份）----
        self.ai_mem_tip = QLabel("全局记忆：所有平台共用，可导出、删除、清空。")
        self.ai_mem_tip.setWordWrap(True)
        self.ai_mem_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        mv.addWidget(self.ai_mem_tip)
        self.ai_mem_list = QListWidget()
        self.ai_mem_list.setMaximumHeight(130)
        mv.addWidget(self.ai_mem_list)
        ai_mem_btns = QHBoxLayout()
        ai_mem_btns.setSpacing(8)
        self.btn_ai_mem_refresh = QPushButton("刷新")
        self.btn_ai_mem_refresh.setObjectName("ghost")
        self.btn_ai_mem_refresh.setMinimumHeight(30)
        self.btn_ai_mem_refresh.clicked.connect(self._refresh_ai_memory)
        self.btn_ai_mem_export = QPushButton("导出")
        self.btn_ai_mem_export.setObjectName("ghost")
        self.btn_ai_mem_export.setMinimumHeight(30)
        self.btn_ai_mem_export.clicked.connect(self._export_ai_memory)
        self.btn_ai_mem_delete = QPushButton("删除选中")
        self.btn_ai_mem_delete.setObjectName("ghost")
        self.btn_ai_mem_delete.setMinimumHeight(30)
        self.btn_ai_mem_delete.clicked.connect(self._delete_ai_memory)
        self.btn_ai_mem_clear = QPushButton("清空全局")
        self.btn_ai_mem_clear.setObjectName("ghost")
        self.btn_ai_mem_clear.setMinimumHeight(30)
        self.btn_ai_mem_clear.clicked.connect(self._clear_ai_memory)
        ai_mem_btns.addWidget(self.btn_ai_mem_refresh)
        ai_mem_btns.addWidget(self.btn_ai_mem_export)
        ai_mem_btns.addWidget(self.btn_ai_mem_delete)
        ai_mem_btns.addWidget(self.btn_ai_mem_clear)
        ai_mem_btns.addStretch(1)
        ma.addLayout(ai_mem_btns)

        kb_tip = QLabel("知识库：上传 txt / md 文档，AI 会自己检索。")
        kb_tip.setWordWrap(True)
        kb_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        mv.addWidget(kb_tip)
        self.kb_list = QListWidget()
        self.kb_list.setMaximumHeight(90)
        mv.addWidget(self.kb_list)
        kb_btns = QHBoxLayout()
        kb_btns.setSpacing(6)
        self.btn_kb_add = QPushButton("添加文档")
        self.btn_kb_add.setObjectName("primary")
        self.btn_kb_add.setMinimumHeight(30)
        self.btn_kb_add.clicked.connect(self._kb_add)
        self.btn_kb_refresh = QPushButton("刷新")
        self.btn_kb_refresh.setObjectName("ghost")
        self.btn_kb_refresh.setMinimumHeight(30)
        self.btn_kb_refresh.clicked.connect(self._refresh_kb_list)
        self.btn_kb_delete = QPushButton("删除选中")
        self.btn_kb_delete.setObjectName("ghost")
        self.btn_kb_delete.setMinimumHeight(30)
        self.btn_kb_delete.clicked.connect(self._kb_delete)
        kb_btns.addWidget(self.btn_kb_add)
        kb_btns.addWidget(self.btn_kb_refresh)
        kb_btns.addWidget(self.btn_kb_delete)
        kb_btns.addStretch(1)
        ma.addLayout(kb_btns)
        mv.addWidget(FoldSection("记忆 / 知识库管理（导出 · 删除 · 清空）　▾",
                                 "记忆 / 知识库管理（导出 · 删除 · 清空）　▴",
                                 self._mem_actions))
        outer.addWidget(mem_panel)

        # ---- 状态摘要（只读） ----
        self.summary_panel = GlassPanel()
        sv = QVBoxLayout(self.summary_panel)
        sv.setContentsMargins(18, 14, 18, 16)
        self.summary_labels = {}
        keys = [
            ("model", "当前模型"),
            ("mcp", "MCP"),
            ("memory_global", "全局记忆"),
            ("memory_users", "用户记忆"),
            ("identity", "身份绑定"),
        ]
        for key, label in keys:
            val = QLabel("—")
            val.setWordWrap(True)
            val.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
            self.summary_labels[key] = val
            sv.addLayout(make_row(label, val))
        btns = QHBoxLayout()
        btn_env = QPushButton("打开 .env")
        btn_env.clicked.connect(lambda: os.startfile(str(self.ctx.settings.bot_env_file)))
        btn_plugins = QPushButton("打开插件目录")
        btn_plugins.clicked.connect(lambda: os.startfile(str(self.ctx.settings.plugins_dir)))
        btns.addWidget(btn_env)
        btns.addWidget(btn_plugins)
        btns.addStretch(1)
        _path_actions = QWidget()
        pa = QVBoxLayout(_path_actions)
        pa.setContentsMargins(0, 0, 0, 0)
        pa.setSpacing(6)
        pa.addLayout(btns)
        sv.addWidget(FoldSection("打开目录 / .env　▾", "打开目录 / .env　▴", _path_actions))
        outer.addWidget(self.summary_panel)

        self.empty = EmptyState("加载中", "")
        outer.addWidget(self.empty, 1)

        self.ai_provider_combo.currentIndexChanged.connect(self._on_ai_provider_changed)
        self.scroll.setWidget(content)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.scroll)
        self.set_beginner(bool(self.ctx.settings.beginner_mode))

    def closeEvent(self, event):  # noqa: N802 —— Qt 命名
        """关窗口前等模型拉取线程收尾，避免 Qt 强杀线程把进程带崩。"""
        fetcher = getattr(self, "_model_fetcher", None)
        if fetcher is not None:
            try:
                fetcher.wait(3.0)
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)

    def set_beginner(self, on: bool):
        """小白模式：AI 大脑只保留接口配置、人设与状态摘要，隐藏档案/MCP/未接入平台。

        顺带把「还没配密钥就整页只留配置提示」的门禁算进来：两个条件都满足才显示，
        不然切一次小白模式就把门禁顶开了。

        2026-10-07 三块并成一张「人设与主动聊天」卡后：这张卡在小白模式下**照旧显示**
        （以前也是「人设与主动聊天」这一块显示、另外两块藏起来），卡里的「预设 / 自定义」
        两段由胶囊切，不再各自受小白模式控制。
        """
        on = bool(on)
        self._beginner = on
        for w in (getattr(self, "_mem_panel", None),):
            if w is not None:
                w.setVisible((not on) and self._ai_configured)
        persona = getattr(self, "_persona_panel", None)
        if persona is not None:
            persona.setVisible(self._ai_configured)
        self.chk_feishu.setVisible(not on)
        self.chk_telegram.setVisible(not on)

    def _spin(self, label: str, attr: str, lo: int, hi: int, default: int) -> QWidget:
        """创建一个“标签在上、数字在下”的整数输入框，固定宽度不会被挤压。"""
        w = QWidget()
        w.setFixedWidth(118)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        lbl.setAlignment(Qt.AlignHCenter)
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(default)
        spin.setFixedWidth(96)
        spin.setAlignment(Qt.AlignCenter)
        setattr(self, attr, spin)
        lay.addWidget(lbl)
        lay.addWidget(spin, 0, Qt.AlignHCenter)
        return w

    # ------------------------------------------------------------ 配置
    def load_config(self):
        """把已保存的 AI 配置与平台开关填入控件（仅页面初始化时调用）。"""
        s = self.ctx.settings
        self._suppress_fetch = True
        platforms = getattr(s, "ai_platforms", None)
        if not isinstance(platforms, dict):
            platforms = {}
        self.chk_qq.setChecked(platforms.get("qq", True))
        self.chk_wechat.setChecked(platforms.get("wechat", True))
        self.chk_feishu.setChecked(platforms.get("feishu", True))
        self.chk_telegram.setChecked(platforms.get("telegram", True))

        cfg = ai_config.current_config(s)
        self.ai_url_edit.setText(str(cfg.get("api_url") or ""))
        self.ai_key_edit.setText(str(cfg.get("api_key") or ""))
        key = ai_config.provider_key_for_url(str(cfg.get("api_url") or ""))
        idx = self.ai_provider_combo.findData(key)
        self.ai_provider_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._fill_model_combo(str(cfg.get("api_url") or ""),
                               str(cfg.get("model") or ""))

        # 主动聊天（人格设定在「设置 → 高级」里编辑，这里不再回填）
        pro = ai_config.read_proactive(s)
        self.chk_proactive.setChecked(pro["enabled"])
        self.ai_proactive_targets.setText(", ".join(pro["targets"]))
        self.ai_proactive_groups.setText(", ".join(pro["groups"]))
        self.ai_interval_min.setValue(pro["interval_min"])
        self.ai_interval_max.setValue(pro["interval_max"])
        self.ai_cooldown.setValue(pro["cooldown"])
        self.ai_quiet_start.setValue(pro["quiet_start"])
        self.ai_quiet_end.setValue(pro["quiet_end"])

        # 群聊回话总开关（默认开）+ 富内容解析开关（默认开）+ 识图开关（默认开）
        self.chk_group_chat.setChecked(ai_config.read_group_chat_enabled(s))
        self.chk_rich_parse.setChecked(ai_config.read_rich_parse_enabled(s))
        self.chk_vision.setChecked(ai_config.read_image_recognition(s))
        self.chk_self_forge.setChecked(ai_config.read_self_forge(s))
        self.chk_cross_platform.setChecked(ai_config.read_cross_platform(s))
        self._refresh_cross_status()
        # 群里主动搭话（插件内核 interject_*）
        interject = ai_config.read_interject(s)
        self.chk_interject.setChecked(interject["enabled"])
        self.ai_interject_probability.setValue(int(round(interject["probability"] * 100)))
        self.ai_interject_min.setValue(interject["interval_min"])
        self.ai_interject_max.setValue(interject["interval_max"])
        self.ai_interject_history.setValue(interject["history_count"])
        self._refresh_group_rows()

        # 记忆与 MCP（程序内设置）
        self.chk_memory.setChecked(ai_config.read_memory_enabled(s))
        mem_level = ai_config.read_memory_level(s)
        idx = self.cmb_memory_level.findData(mem_level)
        # 回填时别把"换档即存盘"那条热重载路径当成用户操作（会平白写一次配置）
        self._mem_level_loading = True
        try:
            self.cmb_memory_level.setCurrentIndex(idx if idx >= 0 else
                                                  self.cmb_memory_level.findData("basic"))
            self._on_memory_level_changed()
        finally:
            self._mem_level_loading = False
        mcp = ai_config.read_mcp(s)
        self.chk_mcp.setChecked(mcp["enabled"])
        self.mcp_json_edit.setPlainText(
            json.dumps(mcp["servers"], ensure_ascii=False, indent=2)
            if mcp["servers"] else "")
        # 智能体档案（预设那一段）+ 本地人设（自定义那一段）：形态与状态行一起回填
        self._syncing_mode = True
        try:
            self.chk_agent_profile.setChecked(s.agent_profile_enabled)
        finally:
            self._syncing_mode = False
        idx = self.agent_profile_combo.findData(s.agent_profile_id)
        self.agent_profile_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.agent_wake_edit.setText(", ".join(s.agent_wake_words))
        self.chk_wake_auto.setChecked(s.agent_wake_auto)
        self.agent_owner_edit.setText(s.agent_owner_openid)
        self.vision_url_edit.setText(s.vision_api_url)
        self.vision_model_edit.setText(s.vision_model)
        self.vision_key_edit.setText(s.vision_api_key)
        self._loaded_persona = ai_config.read_personality(s)
        # 自定义态的人格设定框：和「设置 → 高级 → 人格设定」读的是同一份
        self.personality_edit.setPlainText(self._loaded_persona)
        self.persona_save_hint.setText("")
        self._loaded_agent_cfg = _agent_cfg_snapshot(s)
        self._suppress_fetch = False
        self._refresh_persona_list()
        self.set_persona_mode(
            "preset" if s.agent_profile_enabled else "custom", animate=False)
        self._apply_config_gate()

    def _on_ai_provider_changed(self):
        key = self.ai_provider_combo.currentData() or "custom"
        for p in ai_config.AI_PROVIDERS:
            if p["key"] == key:
                if p["url"]:
                    self.ai_url_edit.setText(p["url"])
                if p["model"]:
                    self._fill_model_combo(p["url"], p["model"])
                break
        if not self._suppress_fetch:
            # 服务商换了，直接按新地址 + 已填密钥拉一次真实列表
            self._model_fetcher.start("auto")

    # ------------------------------------------------------------ 模型列表
    def _models_from_cache(self, url: str) -> list:
        """上次拉到的模型列表（换了服务商也能立刻有得选）。"""
        cache = getattr(self.ctx.settings, "ai_models_cache", None)
        if not isinstance(cache, dict):
            return []
        return list(cache.get(ai_config.normalize_api_url(url)) or [])

    def _cache_models(self, url: str, models: list):
        s = self.ctx.settings
        cache = getattr(s, "ai_models_cache", None)
        if not isinstance(cache, dict):
            cache = {}
        cache[ai_config.normalize_api_url(url)] = [str(m) for m in models][:200]
        s.ai_models_cache = cache
        try:
            s.save()
        except Exception:  # noqa: BLE001 —— 只是缓存，写不进去不拦用户
            pass

    def _fill_model_combo(self, url: str, model: str):
        """回显模型下拉：当前模型排第一 + 上次拉到的列表。"""
        names = self._models_from_cache(url)
        model = str(model or "").strip()
        if model and model not in names:
            names.insert(0, model)
        self.ai_model_edit.blockSignals(True)
        try:
            self.ai_model_edit.clear()
            for name in names:
                self.ai_model_edit.addItem(name)
            self.ai_model_edit.setEditText(model)
        finally:
            self.ai_model_edit.blockSignals(False)

    # ------------------------------------------------------------ 配置门禁
    def _apply_config_gate(self):
        """没填 API 密钥时，本页只留「接口配置 + 先填 AI 配置」提示。

        用户 2026-09-29：AI 还没配好时，人设/记忆/主动聊天先摆一屏只会让人
        不知道从哪下手。保存过密钥就照旧全显示。
        """
        configured = bool(str(ai_config.current_config(self.ctx.settings)
                               .get("api_key") or "").strip())
        self._ai_configured = configured
        self.set_beginner(bool(self.ctx.settings.beginner_mode))
        self.notice_panel.setVisible(not configured)
        # 状态摘要不归小白模式管，单独跟着门禁走
        summary = getattr(self, "summary_panel", None)
        if summary is not None:
            summary.setVisible(configured)

    def is_ai_configured(self) -> bool:
        """给测试/外部看：当前是不是已经配过密钥。"""
        return bool(getattr(self, "_ai_configured", False))

    def _refresh_group_rows(self):
        """列出"收到过消息的群"，勾选 = 允许在这个群回话。

        全勾（或还没有任何群）= 不限群，写空名单，和默认行为一致；
        只勾一部分才写白名单 —— 用户不用手抄 openid。
        """
        s = self.ctx.settings
        rows = ai_config.known_groups(s)
        allowed = {str(x) for x in ai_config.allowed_groups(s)}
        while self._group_rows_layout.count():
            item = self._group_rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._group_checkboxes = {}
        if not rows:
            self.group_list_lbl.setText("允许回话的群：不限群（默认）")
            return
        if allowed:
            self.group_list_lbl.setText(
                f"只允许勾选的 {len(allowed)} 个群")
        else:
            self.group_list_lbl.setText("允许回话的群：不限群（默认）")
        for row in rows:
            openid = row["openid"]
            box = QCheckBox(
                f"群 {openid[:8]}… · 最近 {ai_config.short_time(row['last_ts']) or '—'}"
                f" · 收过 {row['count']} 条")
            box.setChecked((not allowed) or (openid in allowed))
            box.toggled.connect(self._on_group_toggled)
            self._group_rows_layout.addWidget(box)
            self._group_checkboxes[openid] = box

    def _on_group_toggled(self):
        """勾选变化就存盘：全勾 = 不限群（空名单），否则只允许勾选的群。"""
        s = self.ctx.settings
        known = [r["openid"] for r in ai_config.known_groups(s)]
        checked = [oid for oid, box in self._group_checkboxes.items() if box.isChecked()]
        allowed = [] if len(checked) == len(known) else checked
        if not ai_config.save_allowed_groups(s, allowed):
            self.ctx.show_toast("保存失败：请检查机器人目录权限")
            return
        self._refresh_group_rows()
        self.ctx.show_toast("已更新，立即生效（无需重启）")

    def _save_config(self):
        s = self.ctx.settings
        s.ai_platforms = {
            "qq": self.chk_qq.isChecked(),
            "wechat": self.chk_wechat.isChecked(),
            "feishu": self.chk_feishu.isChecked(),
            "telegram": self.chk_telegram.isChecked(),
        }
        s.agent_profile_enabled = self.chk_agent_profile.isChecked()
        s.agent_profile_id = str(self.agent_profile_combo.currentData()
                                 or agent_profile.DEFAULT_PROFILE_ID)
        s.agent_wake_words = _split_csv(self.agent_wake_edit.text())
        s.agent_wake_auto = self.chk_wake_auto.isChecked()
        s.agent_owner_openid = self.agent_owner_edit.text().strip()
        s.vision_api_url = self.vision_url_edit.text().strip()
        s.vision_api_key = self.vision_key_edit.text().strip()
        s.vision_model = self.vision_model_edit.text().strip()
        ai_ok = ai_config.save_config(
            s,
            api_key=self.ai_key_edit.text().strip(),
            api_url=self.ai_url_edit.text().strip(),
            model=self.ai_model_edit.currentText().strip(),
        )

        # 人格设定在「设置 → 高级 → 人格设定」里保存；这里不再重复写，避免互相覆盖
        persona_ok = True
        pro_ok = ai_config.save_proactive(
            s,
            enabled=self.chk_proactive.isChecked(),
            targets=_split_csv(self.ai_proactive_targets.text()),
            groups=_split_csv(self.ai_proactive_groups.text()),
            interval_min=self.ai_interval_min.value(),
            interval_max=self.ai_interval_max.value(),
            cooldown=self.ai_cooldown.value(),
            quiet_start=self.ai_quiet_start.value(),
            quiet_end=self.ai_quiet_end.value(),
        )
        memory_ok = ai_config.save_memory_enabled(
            s, self.chk_memory.isChecked())
        # 记忆档位（无/基础/智能）：与复选框写同一份配置，后写以档位为准
        level_ok = ai_config.save_memory_level(
            s, self.cmb_memory_level.currentData() or "basic")
        group_ok = ai_config.save_group_chat_enabled(
            s, self.chk_group_chat.isChecked())
        rich_ok = ai_config.save_rich_parse_enabled(
            s, self.chk_rich_parse.isChecked())
        vision_ok = ai_config.save_image_recognition(
            s, self.chk_vision.isChecked())
        forge_ok = ai_config.save_self_forge(
            s, self.chk_self_forge.isChecked())
        cross_ok = ai_config.save_cross_platform(
            s, self.chk_cross_platform.isChecked())
        interject_ok = ai_config.save_interject(s, {
            "enabled": self.chk_interject.isChecked(),
            "probability": self.ai_interject_probability.value() / 100.0,
            "interval_min": self.ai_interject_min.value(),
            "interval_max": self.ai_interject_max.value(),
            "history_count": self.ai_interject_history.value(),
        })
        session_ok = True      # 免@连续对话的参数归「连续对话能力包」，这里不再写
        try:
            servers = json.loads(self.mcp_json_edit.toPlainText().strip() or "{}")
        except ValueError as e:
            self.ctx.show_toast("MCP 配置不是合法 JSON：" + str(e))
            return
        try:
            servers = ai_config.normalize_mcp_servers(servers)
        except ValueError as e:
            # 逐项校验（与无头端 console_ext.mcp_save 同一套规则）：以前只查「是不是对象」，
            # type 写错 / stdio 没填 command 都照样存盘，机器人重启后 MCP 起不来只能自己猜
            self.ctx.show_toast("MCP 配置有问题：" + str(e))
            return
        mcp_ok = ai_config.save_mcp(
            s, self.chk_mcp.isChecked(), servers)
        try:
            s.save()
        except OSError as e:
            self.ctx.show_toast("保存 AI 配置失败: " + str(e))
            return
        self.refresh_status()
        self._apply_config_gate()
        # 保存后把形态与状态行重新对齐（用户在预设态勾/取消了档案开关）
        self.set_persona_mode(
            "preset" if self.chk_agent_profile.isChecked() else "custom", animate=True)
        fails = []
        if not ai_ok:
            fails.append("接口")
        if not persona_ok:
            fails.append("人设")
        if not pro_ok:
            fails.append("主动聊天")
        if not memory_ok:
            fails.append("记忆")
        if not level_ok:
            fails.append("记忆档位")
        if not group_ok:
            fails.append("群聊回话")
        if not rich_ok:
            fails.append("引用/图片解析")
        if not interject_ok:
            fails.append("群里搭话")
        if not session_ok:
            fails.append("免@连续对话")
        if not mcp_ok:
            fails.append("MCP")
        # 平台开关 / 记忆档位这一批**改完立即生效**（2026-10-11 热重载）：
        # 把 .env + 有效开关快照整个刷一遍，机器人进程每条消息热读它。
        # 以前这里是"改完重启生效"，白等一次重启。
        try:
            bot.refresh_hot_config(s)
        except Exception as e:  # noqa: BLE001 —— 刷快照失败不该让保存报错
            logger.warning("刷新机器人热配置失败（开关改动要重启才生效）: %s", e)
        self.ctx.show_toast(
            (("AI 配置已保存并**立即生效**（平台开关/记忆档位/人设都不用重启），"
              "正在拉取模型列表验证连通…" if ai_ok
              else "AI 配置已保存并立即生效（接口那几项写入失败，见下）")
             + ("" if not fails else f"（{'、'.join(fails)}写入失败，请检查目录权限）")))
        if ai_ok:
            # 「保存并测试」＝保存 + 真拉一次服务商模型列表
            # （拉得到就说明密钥 / 地址可用，拉不到会在提示里说明原因）
            self._model_fetcher.start("manual")
        before = dict(self._loaded_agent_cfg)
        after = _agent_cfg_snapshot(s)
        self._loaded_agent_cfg = after
        if _profile_restart_required(before, after):
            # 档案开关/id 变了：只有重启一次才真的切得过去（见 _PROFILE_RESTART_KEYS）。
            # ⚠ 必须把 before/after 传进去：上面 1302 行刚把 _loaded_agent_cfg 刷成 after，
            # 不传就是"自己跟自己比"→ 恒判无变化 → 死分支（2026-10-16 复核抓的）。
            self._restart_agent_after_switch("已保存", before=before, after=after)
        elif _agent_restart_required(before, after):
            self._auto_restart_agent()
        self._maybe_auto_wake_generation(s)

    # ------------------------------------------------------------ 人设形态（胶囊）
    def set_persona_mode(self, mode: str, animate: bool = True):
        """切换卡片形态：preset = 用预设档案，custom = 用本地人设/手写。

        只改显示与滑块位置（回填状态、点胶囊都走这里），**不改配置** ——
        真正改配置的是 _on_persona_seg（用户点了胶囊）。
        """
        mode = "preset" if mode == "preset" else "custom"
        self.persona_seg.set_current(mode, animate=animate)
        agent = getattr(self, "_agent_panel", None)
        if agent is not None:
            agent.setVisible(mode == "preset")
        workshop = getattr(self, "_workshop_panel", None)
        if workshop is not None:
            workshop.setVisible(mode == "custom")
        self._refresh_persona_status()

    def _on_persona_seg(self, mode: str):
        """用户点了胶囊（或按了左右方向键）：切换即互斥，并且一定说清改了什么。"""
        s = self.ctx.settings
        if mode == "preset":
            had_local = bool(str(getattr(s, "local_persona_id", "") or ""))
            name = self._active_persona_name()
            if had_local:
                from ...core import persona_workshop
                try:
                    persona_workshop.deactivate_persona(s)
                except Exception as e:  # noqa: BLE001 —— 停用失败也要接着把档案打开
                    self.ctx.show_toast("停用本地人设失败：" + str(e))
            s.agent_profile_enabled = True
            s.save()
            self.chk_agent_profile.setChecked(True)
            self._refresh_persona_list()
            self.persona_switch_hint.setText(
                f"已切到预设：本地人设《{name}》已停用，改用档案里的人格。"
                if had_local else "已切到预设：按档案走，本地人设保持停用。")
            self._restart_agent_after_switch("已切到预设档案")
        else:
            had_profile = bool(self.chk_agent_profile.isChecked() or s.agent_profile_enabled)
            s.agent_profile_enabled = False
            s.save()
            self.chk_agent_profile.setChecked(False)
            self.persona_switch_hint.setText(
                "已切到自定义：预设档案已停用，人格以本地人设 /「设置 → 高级 → 人格设定」里那段为准。"
                if had_profile else
                "已切到自定义：预设档案本来就是关的，人格以本地人设 /「设置 → 高级 → 人格设定」里那段为准。")
            self._restart_agent_after_switch("已切到自定义")
        self.set_persona_mode(mode)

    def _on_agent_profile_toggled(self, on: bool):
        """档案开关：打开 = 切到预设那一段并停用本地人设；关掉 = 回到自定义。"""
        if self._syncing_mode:
            return
        self._syncing_mode = True
        try:
            if on:
                self._on_persona_seg("preset")
            else:
                self._on_persona_seg("custom")
        finally:
            self._syncing_mode = False

    def _active_persona_name(self) -> str:
        """当前启用的本地人设名字（读不到就退回 id，再读不到给「本地人设」）。"""
        pid = str(getattr(self.ctx.settings, "local_persona_id", "") or "")
        if not pid:
            return ""
        try:
            from ...core import persona_workshop
            for p in persona_workshop.list_personas(self.ctx.settings):
                if str(p.get("id")) == pid:
                    return str(p.get("name") or pid)
        except Exception:  # noqa: BLE001 —— 列表读不到也要能把状态行说清楚
            pass
        return pid

    def _refresh_persona_status(self):
        """状态行：本地人设 > 预设档案 > 手写人设（与后端一致，二者互斥）。"""
        lbl = getattr(self, "persona_mode_lbl", None)
        if lbl is None:
            return
        name = self._active_persona_name()
        if name:
            lbl.setText(f"当前生效：本地人设《{name}》")
        elif self.chk_agent_profile.isChecked():
            prof = str(self.agent_profile_combo.currentText() or "星群助手")
            lbl.setText(f"当前生效：预设档案 {prof}")
        else:
            lbl.setText("当前生效：手写人设")

    def _save_persona_text(self):
        """保存「人格设定」这一项：与「设置 → 高级 → 人格设定」同一个字段、同一个函数。

        老板要求两端一致（控制台那张卡的自定义态里就有这个框），所以这里也放一个；
        写的是 ai_config.save_personality（机器人侧 aichat_manager.json 的 personality），
        不另造一份存储 —— 否则「设置」那边和这里又会各说各话。
        """
        text = self.personality_edit.toPlainText()
        if not ai_config.save_personality(self.ctx.settings, text):
            self.persona_save_hint.setText("保存人设失败：机器人目录可能没有写入权限。")
            self.ctx.show_toast("保存人设失败：机器人目录可能没有写入权限")
            return
        self._loaded_persona = ai_config.read_personality(self.ctx.settings)
        self.persona_save_hint.setText("人设已保存，立即生效（无需重启）。")
        self.ctx.show_toast("人设已保存，立即生效（无需重启）")

    # ------------------------------------------------------------ 智能体档案
    def _restart_bot(self):
        """「重启机器人」按钮：只重启机器人进程（不碰 NapCat / QQ）。

        口径：设置改了立即生效；**代码改了才需要重启**（装/卸插件、换通道、
        能力包、MCP 服务器），点一下就能重启。
        """
        if self.ctx.tasks is None:
            self.ctx.show_toast("请到「全局管理」里点重启机器人")
            return
        from ...tasks.workers import RestartBotTask

        self.ctx.show_toast("正在重启机器人（约 15 秒，期间 QQ 短暂离线）…")
        self.ctx.tasks.submit(
            "重启机器人", "service", RestartBotTask,
            retryable=True, manager=self.ctx.manager,
        )

    def _restart_agent_after_switch(self, what: str, before=None, after=None):
        """切换人设形态/保存后：多数场景热刷即可，**档案开关/id 例外见下**。

        人设/唤醒词/模型这些是热读的（机器人每条消息 `maybe_reload()`），
        不再重启，别白等 15 秒还打断群里的对话。
        但**档案开关 / 档案 id 变了必须重启一次** —— 李清菡插件在 import 期
        判定 _ENABLED、注册 handler，热配置救不了（2026-10-15 审计 liqinghan/A）：
        打开后谁都不回话、关掉后双回复。

        `before/after` 允许由调用方传入快照：保存路径在调用**之前**已经刷新过
        `_loaded_agent_cfg`，这里若不收外部快照就会"自己跟自己比"，永远判不出
        变化（2026-10-16 对抗性复核抓到的死分支：先勾启用档案、再选李清菡、
        保存 → 一条重启提示都不弹）。
        """
        s = self.ctx.settings
        if before is None or after is None:
            before = dict(self._loaded_agent_cfg)
            after = _agent_cfg_snapshot(s)
        self._loaded_agent_cfg = after
        if _profile_restart_required(before, after):
            # 档案开关/id 变了：热刷配置救不了（插件是 import 期注册的）——
            # 以前这里只回一句"立即生效（无需重启）"，实际是"打开后谁都不回话 /
            # 关掉后双回复"（2026-10-15 审计 liqinghan/A）。
            self.ctx.show_toast(f"{what}：档案开关变了，重启一次机器人生效（约 15 秒）")
            self._restart_bot()
            return
        if not _agent_restart_required(before, after):
            self.ctx.show_toast(f"{what}（配置没变）")
            return
        try:
            bot.refresh_hot_config(s)      # 主人 QQ / 唤醒词 立即生效
        except Exception as e:  # noqa: BLE001
            logger.warning("刷新机器人热配置失败: %s", e)
        self.ctx.show_toast(f"{what}，立即生效（无需重启）")

    def _auto_restart_agent(self):
        """档案/模型相关配置变化后：**刷热配置，不再自动重启机器人**（2026-10-11）。

        这些字段（人设 / 模型 / 唤醒词 / 主人 / 视觉）现在全是热读的，
        机器人每条消息 `maybe_reload()` 就拿到新值；以前这里会重启一次，
        白白打断群里正在进行的对话。

        例外：**档案开关 / 档案 id 变了必须重启一次** —— 那走 `_restart_agent_after_switch`
        （李清菡插件是 import 期注册的，热配置救不了，见 `_PROFILE_RESTART_KEYS`）。
        """
        try:
            bot.refresh_hot_config(self.ctx.settings)
        except Exception as e:  # noqa: BLE001
            logger.warning("刷新机器人热配置失败: %s", e)
        self.ctx.show_toast("已保存，立即生效（无需重启）")

    def _on_wake_edited(self, _text):
        """手动改唤醒词 = 关闭自动覆盖，防止下次保存冲掉人工维护的词。"""
        self.chk_wake_auto.setChecked(False)

    # ------------------------------------------------------------ 人设工坊（本地）
    def _download_persona_template(self):
        from ...core import persona_workshop
        path, _ = QFileDialog.getSaveFileName(
            self, "保存人设模板",
            str(os.path.join(os.path.expanduser("~"), "persona-card-template.json")),
            "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(persona_workshop.template_text())
        except OSError as e:
            self.ctx.show_toast("保存模板失败：" + str(e))
            return
        self.ctx.show_toast("模板已保存，填好后点「导入人设」")

    def _show_persona_guide(self):
        """打开模板说明文档（图文教程）。"""
        from ...core import persona_workshop
        dlg = QDialog(self)
        dlg.setWindowTitle("人格卡模板说明")
        dlg.resize(680, 720)
        lay = QVBoxLayout(dlg)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(persona_workshop.guide_text())
        lay.addWidget(edit)
        dlg.show()

    def _import_persona(self):
        from ...core import persona_workshop
        path, _ = QFileDialog.getOpenFileName(
            self, "导入人设", "",
            "人设包 (*.json *.zip);;JSON 人格卡 (*.json);;ZIP 人设包 (*.zip)")
        if not path:
            return
        worker = Worker(self)
        worker.finished.connect(self._on_persona_imported)
        threading.Thread(
            target=lambda: worker.run(
                lambda: persona_workshop.install_persona(self.ctx.settings, path)),
            daemon=True,
        ).start()

    def _on_persona_imported(self, res):
        if isinstance(res, dict) and res.get("ok"):
            self.ctx.show_toast(f"人设已导入：{res.get('name')}，选中后点「启用」")
        else:
            detail = (res.get("error") if isinstance(res, dict) else "") or "导入失败"
            self.ctx.show_toast("导入失败：" + str(detail))
        self._refresh_persona_list()

    def _refresh_persona_list(self):
        from ...core import persona_workshop
        self.persona_list.clear()
        for p in persona_workshop.list_personas(self.ctx.settings):
            mark = "（启用中）" if p["active"] else ""
            tools = "、".join(p["tools"]) if p["tools"] else "无工具"
            item = QListWidgetItem(
                f"{p['name']} v{p['version']}{mark}  [工具：{tools}]")
            item.setData(Qt.UserRole, p["id"])
            item.setToolTip(p["summary"])
            self.persona_list.addItem(item)
        self._refresh_persona_status()

    def _activate_persona(self):
        from ...core import persona_workshop
        item = self.persona_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择一个人设")
            return
        pid = str(item.data(Qt.UserRole) or "")
        try:
            res = persona_workshop.activate_persona(self.ctx.settings, pid)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "启用失败", str(e))
            return
        self._loaded_persona = ai_config.read_personality(self.ctx.settings)
        # 启用本地人设会把编译好的人格写进人格字段：框里要跟着显示，不然用户以为没生效
        self.personality_edit.setPlainText(self._loaded_persona)
        # 启用本地人设 = 走自定义那一段（后端也把档案关掉了，界面要跟着动，别静默）
        self._syncing_mode = True
        try:
            self.chk_agent_profile.setChecked(False)
        finally:
            self._syncing_mode = False
        self.ctx.settings.agent_profile_enabled = False
        self.ctx.settings.save()
        self.set_persona_mode("custom")
        self._refresh_persona_list()
        self.persona_switch_hint.setText(
            f"已启用本地人设《{res.get('name')}》：预设档案已停用。")
        self.ctx.show_toast(f"已启用人设：{res.get('name')}，重启机器人后生效")

    def _deactivate_persona(self):
        from ...core import persona_workshop
        try:
            persona_workshop.deactivate_persona(self.ctx.settings)
        except OSError as e:
            self.ctx.show_toast("停用失败：" + str(e))
            return
        self._refresh_persona_list()
        # 停用会把人格恢复成内置默认：框里跟着回到那一份
        self._loaded_persona = ai_config.read_personality(self.ctx.settings)
        self.personality_edit.setPlainText(self._loaded_persona)
        self.persona_switch_hint.setText("已停用本地人设：改回按档案或手写人格走。")
        self.ctx.show_toast("已停用本地人设")

    def _uninstall_persona(self):
        from ...core import persona_workshop
        item = self.persona_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择一个人设")
            return
        pid = str(item.data(Qt.UserRole) or "")
        ret = QMessageBox.question(
            self, "卸载人设", f"确定卸载「{item.text()}」吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            persona_workshop.uninstall_persona(self.ctx.settings, pid)
        except OSError as e:
            self.ctx.show_toast("卸载失败：" + str(e))
            return
        self._refresh_persona_list()
        self.ctx.show_toast("人设已卸载")

    # ------------------------------------------------------------ 记忆时间线
    def _memory_db(self):
        p = self.ctx.settings.root / "bot" / "data" / "liqinghan" / "agent.db"
        return p if p.exists() else None

    def _backup_memory_db(self) -> str:
        """删除/清空前把记忆库整份复制一份（与内置 AI 全局记忆同一套做法）。

        sqlite 里删掉就是真删掉了，事后没法从日志里还原 —— 先留 .bak 才敢让用户点删除。
        """
        import shutil

        dbp = self._memory_db()
        if dbp is None:
            return ""
        bak = dbp.with_name(f"{dbp.name}.bak.{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            shutil.copy2(dbp, bak)
            return str(bak)
        except OSError:
            return ""

    def _refresh_memory_list(self):
        import sqlite3
        self.memory_list.clear()
        dbp = self._memory_db()
        if dbp is None:
            self.memory_list.addItem(
                "（暂无记忆库：启用李清菡 / 本地人设并聊过天后会生成）")
            return
        try:
            conn = sqlite3.connect(str(dbp))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT m.id, m.qq, m.fact, m.ts, u.nickname FROM memories m "
                "LEFT JOIN users u ON u.qq=m.qq ORDER BY m.ts DESC LIMIT 200"
            ).fetchall()
            conn.close()
        except Exception as e:  # noqa: BLE001
            self.memory_list.addItem("（读取记忆失败：" + str(e) + "）")
            return
        for r in rows:
            ts = (time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"]))
                  if r["ts"] else "?")
            nick = r["nickname"] or r["qq"] or "?"
            item = QListWidgetItem(f"[{ts}] {nick}：{str(r['fact'])[:80]}")
            item.setData(Qt.UserRole, r["id"])
            item.setToolTip(str(r["fact"]))
            self.memory_list.addItem(item)

    def _export_memory(self):
        import json as _json
        import sqlite3
        dbp = self._memory_db()
        if dbp is None:
            self.ctx.show_toast("暂无记忆库")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出记忆",
            str(os.path.join(os.path.expanduser("~"), "星群记忆导出.json")),
            "JSON 文件 (*.json)")
        if not path:
            return
        try:
            conn = sqlite3.connect(str(dbp))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT m.qq, m.fact, m.ts, u.nickname FROM memories m "
                "LEFT JOIN users u ON u.qq=m.qq ORDER BY m.ts DESC").fetchall()
            conn.close()
            data = {
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "count": len(rows),
                "items": [dict(r) for r in rows],
            }
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            self.ctx.show_toast("导出失败：" + str(e))
            return
        self.ctx.show_toast(f"已导出 {len(rows)} 条记忆")

    def _delete_memory(self):
        import sqlite3
        item = self.memory_list.currentItem()
        if not item or item.data(Qt.UserRole) is None:
            QMessageBox.information(self, "提示", "请先选择一条记忆")
            return
        dbp = self._memory_db()
        if dbp is None:
            return
        backup = self._backup_memory_db()
        try:
            conn = sqlite3.connect(str(dbp))
            conn.execute("DELETE FROM memories WHERE id=?",
                         (item.data(Qt.UserRole),))
            conn.commit()
            conn.close()
        except Exception as e:  # noqa: BLE001
            self.ctx.show_toast("删除失败：" + str(e))
            return
        self._refresh_memory_list()
        self.ctx.show_toast(
            f"已删除该条记忆（原库已备份为 {os.path.basename(backup)}）" if backup
            else "已删除该条记忆")

    def _clear_memory(self):
        import sqlite3
        dbp = self._memory_db()
        if dbp is None:
            return
        ret = QMessageBox.question(
            self, "清空记忆",
            "确定清空全部记忆吗？\n清空前会自动把记忆库整份备份一份（agent.db.bak.<时间>）。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        backup = self._backup_memory_db()
        try:
            conn = sqlite3.connect(str(dbp))
            conn.execute("DELETE FROM memories")
            conn.commit()
            conn.close()
        except Exception as e:  # noqa: BLE001
            self.ctx.show_toast("清空失败：" + str(e))
            return
        self._refresh_memory_list()
        self.ctx.show_toast(
            f"记忆已清空（原库已备份为 {os.path.basename(backup)}）" if backup
            else "记忆已清空（未能备份，请检查目录权限）")

    # ------------------------------------------------------ 内置 AI 全局记忆
    def _refresh_ai_memory(self):
        """读内置 AI 插件的全局记忆（另一个文件，与控制台同一份）。"""
        from ...core import ai_memory

        self.ai_mem_list.clear()
        try:
            info = ai_memory.summary(self.ctx.settings)
        except ValueError as exc:
            # 文件坏了必须说出来，不能显示成「没有记忆」（否则保存会覆盖掉）
            self.ai_mem_tip.setText("内置 AI 记忆读取失败：" + str(exc))
            self.ctx.show_toast("内置 AI 记忆读取失败，见说明文字")
            return
        self.ai_mem_tip.setText(
            f"内置 AI 记忆（全局）：共 {info['total']} 条"
            f"（全局 {info['global_count']} 条、{len(info['users'])} 个用户）"
            f" · {info['file']}")
        if not info["total"]:
            self.ai_mem_list.addItem("（还没有记忆；AI 聊到值得长期记住的事才会写进来）")
            return
        for idx, entry in enumerate(info["global"]):
            item = QListWidgetItem("· " + ai_memory.item_text(entry))
            item.setData(Qt.UserRole, ("global", "", idx))
            self.ai_mem_list.addItem(item)
        for group in info["users"]:
            head = QListWidgetItem(f"— 用户 {group['user']}（{group['count']} 条）")
            head.setData(Qt.UserRole, None)
            self.ai_mem_list.addItem(head)
            base = group["count"] - len(group["items"])
            for idx, entry in enumerate(group["items"]):
                item = QListWidgetItem("   · " + ai_memory.item_text(entry))
                item.setData(Qt.UserRole, ("user", group["user"], base + idx))
                self.ai_mem_list.addItem(item)

    def _export_ai_memory(self):
        from ...core import ai_memory

        try:
            _name, body = ai_memory.export_text(self.ctx.settings, "md")
        except ValueError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出内置 AI 记忆",
            os.path.join(os.path.expanduser("~"), "星群AI记忆时间线.md"),
            "Markdown (*.md);;JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
        except OSError as exc:
            self.ctx.show_toast("导出失败：" + str(exc))
            return
        self.ctx.show_toast("已导出到 " + path)

    def _delete_ai_memory(self):
        from ...core import ai_memory

        item = self.ai_mem_list.currentItem()
        if not item or item.data(Qt.UserRole) is None:
            QMessageBox.information(self, "提示", "请先选择一条记忆（标题行和设备行不能删）")
            return
        scope, user, index = item.data(Qt.UserRole)
        if not self._confirm_ai_memory("删除这条记忆", f"确定删除这条记忆吗？\n\n{item.text().strip()}"):
            return
        try:
            res = ai_memory.delete(self.ctx.settings, scope, user, index)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self._refresh_ai_memory()
        self.ctx.show_toast(f"已删除（原文件已备份到 {os.path.basename(res['backup'] or '（未备份）')}）")

    def _clear_ai_memory(self):
        from ...core import ai_memory

        if not self._confirm_ai_memory(
                "清空内置 AI 全局记忆",
                "将清空内置 AI 插件的「全局记忆」（用户记忆不动），改动前会自动备份。\n\n确定继续吗？"):
            return
        try:
            res = ai_memory.clear(self.ctx.settings, "global")
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "清空失败", str(exc))
            return
        self._refresh_ai_memory()
        self.ctx.show_toast(f"已清空 {res['removed']} 条全局记忆")

    def _confirm_ai_memory(self, title: str, text: str) -> bool:
        ret = QMessageBox.question(self, title, text,
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return ret == QMessageBox.Yes

    # ------------------------------------------------------ 内置记忆三档
    def _on_memory_level_changed(self, *args):
        """换档：**立即存盘 + 立即生效**（不用点保存、更不用重启机器人）。

        老板 2026-10-11 的原话就是"记忆档次改完必须重启"这件事最痛，所以这里
        不学同一页其它控件那样"等点保存"——换档即生效，并给出明确的提示。
        """
        level = self.cmb_memory_level.currentData() or "basic"
        hint = ai_config.MEMORY_LEVEL_HINTS.get(level, "")
        self.mem_level_hint.setText(hint)
        # 「无」档 = 不记不读：顺手把上面那个开关也摆成一致的样子（保存时统一收口）
        if level == "off":
            self.chk_memory.setChecked(False)
        if getattr(self, "_mem_level_loading", False):
            return          # 回填控件时不要触发保存
        s = self.ctx.settings
        try:
            ok = ai_config.save_memory_level(s, level)
            if ok:
                bot.write_live_gates(s)      # 机器人侧热读的快照立刻跟上
        except Exception as e:  # noqa: BLE001 —— 存不下来要说出来，不能装作生效了
            logging.getLogger("qbotmanager").warning("记忆档位保存失败: %s", e)
            self.ctx.show_toast("记忆档位保存失败：请检查机器人目录权限")
            return
        if not ok:
            self.ctx.show_toast("记忆档位保存失败：请检查机器人目录权限")
            return
        label = ai_config.MEMORY_LEVEL_LABELS.get(level, level)
        self.ctx.show_toast(f"记忆档位已切到「{label}」，立即生效（无需重启）")

    def _peek_builtin_memory(self):
        """「现在记住了什么」：直接读内置记忆库（list_memories）。"""
        self.mem_peek_list.clear()
        try:
            from ...core import memory as memory_mod
        except ImportError as exc:
            self.mem_peek_list.addItem(f"（内置记忆模块不可用：{exc}）")
            self.mem_peek_list.setVisible(True)
            return
        level = memory_mod.level()
        try:
            info = memory_mod.stats()
        except Exception as exc:  # noqa: BLE001 —— 读库失败要说出来，不能显示成「没有记忆」
            self.mem_peek_list.addItem("（读记忆库失败：" + str(exc) + "）")
            self.mem_peek_list.setVisible(True)
            return
        if level == "off":
            self.mem_peek_list.addItem("（档位是「无」：不记也不读）")
            self.mem_peek_list.setVisible(True)
            return
        self.mem_peek_list.addItem(
            f"档位：{ai_config.MEMORY_LEVEL_LABELS.get(level, level)}｜"
            f"共 {info.get('total', 0)} 条（承诺 {info.get('promises', 0)} · "
            f"印象档 {info.get('impressions', 0)} · 已过期 {info.get('expired', 0)}）")
        db = Path(str(info.get("db") or ""))
        self.mem_peek_list.addItem(f"库文件：{db}")
        if not db.is_file():
            self.mem_peek_list.addItem("（还没有记忆库：聊过天、或写进第一条记忆后才会生成）")
            self.mem_peek_list.setVisible(True)
            return
        shown = 0
        # 逐人列出（库里按 qq 分行）；一个人也没有就如实说
        try:
            import sqlite3
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            people = [r["qq"] for r in conn.execute(
                "SELECT DISTINCT qq FROM memories WHERE expired=0 ORDER BY qq LIMIT 20")]
            for qq in people:
                rows = conn.execute(
                    "SELECT fact, kind, weight FROM memories WHERE qq=? AND expired=0 "
                    "ORDER BY id DESC LIMIT 5", (qq,)).fetchall()
                self.mem_peek_list.addItem(f"—— {qq}（{len(rows)} 条，最多显示 5 条）——")
                for r in rows:
                    self.mem_peek_list.addItem(
                        f"  [{r['kind']}/权重{r['weight']}] {str(r['fact'])[:60]}")
                    shown += 1
            conn.close()
        except Exception as exc:  # noqa: BLE001
            self.mem_peek_list.addItem("（列条目失败：" + str(exc) + "）")
        if not shown:
            self.mem_peek_list.addItem("（库里还没有条目）")
        self.mem_peek_list.setVisible(True)

    # ------------------------------------------------------------ 本地知识库
    def _refresh_kb_list(self):
        from ...core import knowledge_base
        self.kb_list.clear()
        docs = knowledge_base.list_documents(self.ctx.settings)
        if not docs:
            self.kb_list.addItem("（暂无文档，点「添加文档」上传 txt / md）")
            return
        for doc in docs:
            item = QListWidgetItem(f"{doc['title']}（{doc['chunks']} 段）")
            item.setData(Qt.UserRole, doc["id"])
            self.kb_list.addItem(item)

    def _kb_add(self):
        from ...core import knowledge_base
        path, _ = QFileDialog.getOpenFileName(
            self, "添加知识库文档", "",
            "文本 / Markdown (*.txt *.md);;所有文件 (*.*)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            knowledge_base.add_document(
                self.ctx.settings, os.path.basename(path), text)
        except Exception as e:  # noqa: BLE001
            self.ctx.show_toast("添加失败：" + str(e))
            return
        self._refresh_kb_list()
        self.ctx.show_toast("文档已加入本地知识库，重启机器人后 AI 可检索")

    def _kb_delete(self):
        from ...core import knowledge_base
        item = self.kb_list.currentItem()
        if not item or item.data(Qt.UserRole) is None:
            QMessageBox.information(self, "提示", "请先选择一篇文档")
            return
        try:
            knowledge_base.delete_document(
                self.ctx.settings, str(item.data(Qt.UserRole)))
        except OSError as e:
            self.ctx.show_toast("删除失败：" + str(e))
            return
        self._refresh_kb_list()
        self.ctx.show_toast("文档已删除")

    def _maybe_auto_wake_generation(self, s):
        """人格变化且开了自动生成时，保存后后台重新生成唤醒词。"""
        persona = ai_config.read_personality(s).strip()
        if (not s.agent_profile_enabled or not s.agent_wake_auto
                or not persona
                or persona == str(getattr(self, "_loaded_persona", "")).strip()):
            return
        self._start_wake_generation()

    def _start_wake_generation(self):
        """按人格生成唤醒词：语言模型一次调用，失败自动走正则/默认词兜底。"""
        s = self.ctx.settings
        cfg = ai_config.current_config(s)
        api_url = self.ai_url_edit.text().strip() or str(cfg.get("api_url") or "")
        api_key = self.ai_key_edit.text().strip() or str(cfg.get("api_key") or "")
        model = self.ai_model_edit.currentText().strip() or str(cfg.get("model") or "")
        persona = ai_config.read_personality(self.ctx.settings).strip()
        if not persona:
            self.ctx.show_toast("请先填写人格设定")
            return
        profile_id = str(self.agent_profile_combo.currentData()
                         or agent_profile.DEFAULT_PROFILE_ID)
        prof = agent_profile.load_profile(profile_id)
        default = list(prof["default_wake_words"]) if prof else []
        worker = Worker(self)
        worker.finished.connect(lambda res: self._on_wake_generated(res, default))
        threading.Thread(
            target=lambda: worker.run(lambda: {
                "words": agent_profile.generate_wake_words(
                    persona,
                    lambda prompt: agent_profile.openai_chat(
                        prompt, api_url, api_key, model),
                    default=default),
            }),
            daemon=True,
        ).start()
        self.btn_wake_generate.setEnabled(False)
        self.ctx.show_toast("正在按人格生成唤醒词...")

    def _on_wake_generated(self, res, default):
        self.btn_wake_generate.setEnabled(True)
        words = res.get("words") if isinstance(res, dict) else None
        if words:
            self.agent_wake_edit.setText(", ".join(words))
            self.chk_wake_auto.setChecked(True)
            self.ctx.show_toast("唤醒词已生成，可手动修改")
            return
        msg = str(res.get("error") or "无结果") if isinstance(res, dict) else "无结果"
        self.ctx.show_toast("唤醒词生成失败：" + msg + "，已保留原唤醒词")

    # ------------------------------------------------------------ 状态
    def _refresh_cross_status(self):
        """显示"谁和谁算同一个人"（读机器人侧的绑定文件，只读展示）。"""
        try:
            from qbotmanager.core import ai_config as _cfg

            groups = _cfg.read_identity_bindings(self.ctx.settings)
        except Exception:  # noqa: BLE001
            groups = []
        if not groups:
            self.cross_status_lbl.setText("绑定状态：还没绑定（在微信或 QQ 上发「绑定」拿码）")
            return
        names = {
            "qq": "QQ", "qq_official": "QQ 官方", "wechat": "微信",
            "feishu": "飞书", "telegram": "纸飞机",
        }
        parts = []
        for group in groups[:3]:
            labels = []
            for key in group:
                head = str(key).split(":", 1)[0]
                label = names.get(head, head)
                if label not in labels:
                    labels.append(label)
            parts.append(" ↔ ".join(labels))
        more = f"（还有 {len(groups) - 3} 组）" if len(groups) > 3 else ""
        self.cross_status_lbl.setText("绑定状态：" + "；".join(parts) + more)
    def refresh_status(self):
        s = self.ctx.settings
        summary = message_store.brain_summary(s)
        installed = (s.plugins_dir / "ai").is_dir()
        cfg = ai_config.current_config(s)
        configured = bool(cfg.get("api_key") and cfg.get("api_url") and cfg.get("model"))
        self.summary_panel.setVisible(installed)
        self.empty.setVisible(not installed)
        if not installed:
            self.empty.set_content(
                "AI 插件还没装好",
                "重启一次机器人就会自动装好。")
            return
        if not getattr(s, "ai_enabled", True):
            self.summary_panel.setVisible(False)
            self.empty.setVisible(True)
            self.empty.set_content(
                "AI 插件已停用",
                "在「设置」页打开 AI 插件总开关即可。")
            return
        if not configured:
            self.summary_panel.setVisible(False)
            self.empty.setVisible(True)
            self.empty.set_content(
                "还没配置 AI",
                "在上方选服务商、填密钥并保存。")
            return
        self.summary_labels["model"].setText(summary["model"] or "未配置")
        mcp = ("已开启" if summary["mcp_enabled"] else "未开启") + \
              f" · {summary['mcp_servers']} 个服务器"
        self.summary_labels["mcp"].setText(mcp)
        self.summary_labels["memory_global"].setText(f"{summary['memory_global']} 条")
        self.summary_labels["memory_users"].setText(f"{summary['memory_users']} 个用户")
        self.summary_labels["identity"].setText(f"{summary['identity_binds']} 组绑定")
        # 首页那两行统计的就是这份内置 AI 记忆，顺手把列表也刷新，别让两处对不上
        try:
            self._refresh_ai_memory()
        except Exception:  # noqa: BLE001 —— 刷新失败不该让整页报错
            pass
