"""ntfy 订阅指引对话框：把"手机上要做什么"一次说清。

首次生成主题时自动弹出一次（订阅关系刚建立，正是用户最需要指引的时刻）；
之后随时可从托盘菜单"手机通知（ntfy）订阅指引"再次打开。
主题即凭证：每个可复制项旁边都有复制按钮，最后给"发送测试推送"验证链路。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..notify import NtfyNotifier
from .theme import DIALOG_STYLE, FG_DIM, style_section, style_subtitle, style_title


def _card() -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setProperty("card", True)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(8)
    return card, lay


class NotifyGuideDialog(QDialog):
    def __init__(self, notifier: NtfyNotifier, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("手机通知（ntfy）订阅指引"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(DIALOG_STYLE)
        self._notifier = notifier

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        title = QLabel(style_title("📱 " + tr("手机通知（ntfy）")))
        title.setTextFormat(Qt.TextFormat.RichText)
        subtitle = QLabel(style_subtitle(
            tr("此框只在首次生成主题时自动弹出一次；之后可从托盘菜单再次打开")))
        subtitle.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(title)
        lay.addWidget(subtitle)

        # ① 订阅：收推送
        sub_card, sub_lay = _card()
        sub_lay.addWidget(self._section(tr("① 订阅（收额度重置 / 访问地址推送）")))
        sub_lay.addWidget(self._hint(tr(
            "手机安装 ntfy App → 添加订阅下面这个主题；"
            "或在手机浏览器直接打开订阅链接，按提示跳转 App")))
        sub_lay.addLayout(self._copy_row(tr("主题"), notifier.topic))
        sub_lay.addLayout(self._copy_row(tr("订阅链接"), notifier.subscribe_url))
        lay.addWidget(sub_card)

        # ② 反向触发：要访问地址
        cmd_topic = notifier.topic + "-cmd"
        cmd_card, cmd_lay = _card()
        cmd_lay.addWidget(self._section(tr("② 反向触发（想用手机看时，主动要地址）")))
        cmd_lay.addWidget(self._hint(tr(
            "在 ntfy App 里再订阅下面的命令主题。想用手机看仪表盘时向它发送 url "
            "——电脑回推当前地址，点通知直达网页；发送 列表 查看各额度提醒开关；"
            "发送 kimi5、spark 这类关键词可直接开/关对应窗口的重置提醒")))
        cmd_lay.addLayout(self._copy_row(tr("命令主题"), cmd_topic))
        lay.addWidget(cmd_card)

        # ③ 验证链路
        test_card, test_lay = _card()
        test_lay.addWidget(self._section(tr("③ 验证")))
        self._test_btn = QPushButton(tr("发送测试推送"))
        self._test_btn.setToolTip(tr("手机上应立刻收到一条通知；收不到说明订阅没配对"))
        self._test_btn.clicked.connect(self._send_test)
        test_lay.addWidget(self._test_btn)
        lay.addWidget(test_card)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close = QPushButton(tr("完成"))
        close.setProperty("primary", True)
        close.setDefault(True)
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        lay.addLayout(btns)
        self.adjustSize()

    # ---------- 构建 ----------

    @staticmethod
    def _section(text: str) -> QLabel:
        lbl = QLabel(style_section(text))
        lbl.setTextFormat(Qt.TextFormat.RichText)
        return lbl

    @staticmethod
    def _hint(text: str) -> QLabel:
        lbl = QLabel(f"<span style='color:{FG_DIM}'>{text}</span>")
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        return lbl

    @staticmethod
    def _copy_row(name: str, value: str) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel(f"<b>{name}</b>：<span style='color:{FG_DIM}'>{value}</span>")
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setWordWrap(True)
        btn = QPushButton(tr("复制"))
        btn.clicked.connect(lambda _=False, v=value, b=btn: NotifyGuideDialog._copy(v, b))
        row.addWidget(lbl, stretch=1)
        row.addWidget(btn)
        return row

    @staticmethod
    def _copy(value: str, btn: QPushButton) -> None:
        QGuiApplication.clipboard().setText(value)
        btn.setText(tr("已复制"))

    # ---------- 动作 ----------

    def _send_test(self) -> None:
        ok = self._notifier.publish(
            "codex-quota",
            tr("🔔 测试推送：订阅成功！额度重置、手机访问地址都会推到这里。"),
            click=self._notifier.subscribe_url)
        self._test_btn.setText(tr("已发送 ✓（看手机）") if ok
                               else tr("发送失败（检查网络/ntfy 服务）"))
