"""统一深色主题：对话框与表单控件。

背景/卡片/边框/前景与 HUD 悬浮窗同一套色板（#0d1117 系），
主按钮用 setProperty("primary", True) 触发高亮样式。
"""

BG = "#0d1117"
CARD = "#161b22"
BORDER = "#30363d"
FG = "#e6edf3"
FG_DIM = "#8b949e"
ACCENT = "#1f6feb"
ACCENT_HOVER = "#388bfd"
BTN_BG = "#21262d"
BTN_HOVER = "#30363d"

DIALOG_STYLE = f"""
QDialog {{ background: {BG}; }}
QLabel {{ color: {FG}; background: transparent; }}
QLabel[dim="true"] {{ color: {FG_DIM}; }}

QCheckBox {{ color: {FG}; spacing: 6px; font-size: 13px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}

QPushButton {{
    background: {BTN_BG}; color: {FG};
    border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px 16px; font-size: 13px;
}}
QPushButton:hover {{ background: {BTN_HOVER}; }}
QPushButton:disabled {{ color: {FG_DIM}; }}
QPushButton[primary="true"] {{
    background: {ACCENT}; border-color: {ACCENT};
    color: #ffffff; font-weight: bold;
}}
QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; }}

QLineEdit {{
    background: {CARD}; color: {FG};
    border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px 8px; font-size: 13px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QFrame[card="true"] {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px;
}}
"""


def style_title(text: str) -> str:
    return (f'<span style="font-size:16px; font-weight:bold; color:{FG};">'
            f'{text}</span>')


def style_subtitle(text: str) -> str:
    return f'<span style="font-size:12px; color:{FG_DIM};">{text}</span>'


def style_section(text: str) -> str:
    return (f'<span style="font-size:11px; font-weight:bold; color:{FG_DIM};">'
            f'{text}</span>')
