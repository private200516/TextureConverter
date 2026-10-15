# -*- coding: utf-8 -*-
"""
ui_theme.py — 视觉主题：Soft Blob（柔和色块）体系。

配色语言取自现代产品海报：
  番茄红 #FF2D08 / 墨黑 #181820 / 纯白卡片 / 薰衣草底 #E9E9F3
  超大圆角色块 + 胶囊按钮 + 点阵与转盘装饰母题。

字体层级（见 UI_Font_Workflow.txt）：
  FONT_BRAND   品牌标题（FO76 → FO4）—— Stop 复古几何体
  FONT_STATUS  大状态字（STANDBY / CONVERTING）—— Stop
  FONT_UI      普通 UI —— Inter Regular / HarmonyOS Sans SC
  FONT_UI_MEDIUM / FONT_UI_SEMIBOLD / FONT_UI_BOLD —— 对应字重
  FONT_MONO    路径 / 数字 / 日志 / 数据 —— JetBrains Mono
中文按字族栈逐字符回退 HarmonyOS Sans SC，英文由 Inter 承担。
"""

# ---------------------------------------------------------------------------
# 调色板
# ---------------------------------------------------------------------------

MOTION_CREAM = "#f8f7f2"
MOTION_ORANGE = "#ff4b23"
MOTION_PROGRESS_MS = 600
MOTION_CONTENT_START_MS = 720
MOTION_CONTENT_FADE_MS = 180
MOTION_STARTUP_MS = 900
MOTION_SHUTDOWN_MS = 240

RED = MOTION_ORANGE
RED_DARK = "#DC3916"
INK = "#181820"
INK_SOFT = "#55555F"
PAPER = MOTION_CREAM       # Shared toolkit cream; also startup backing color
PAPER_DIM = "#E7E5DD"
LINE = "#D8D5CC"
WHITE = "#FFFFFF"
AMBER = "#F5A623"
GREEN_OK = "#35A06B"

# ---------------------------------------------------------------------------
# 字体令牌（Font Tokens）
# ---------------------------------------------------------------------------

# LEVEL 1：品牌标题（FO76 → FO4）
FONT_BRAND = "'Stop', 'Archivo Black', 'HarmonyOS Sans SC', 'Microsoft YaHei UI', sans-serif"
# LEVEL 5：特殊状态大字（STANDBY / CONVERTING）
FONT_STATUS = "'Stop', 'Archivo Black', 'HarmonyOS Sans SC', 'Microsoft YaHei UI', sans-serif"
# LEVEL 2：普通 UI（Inter / HarmonyOS Sans SC）
FONT_UI = "'Inter', 'HarmonyOS Sans SC', 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif"
FONT_UI_MEDIUM = "'Inter Medium', 'HarmonyOS Sans SC Medium', 'Inter', 'HarmonyOS Sans SC', sans-serif"
FONT_UI_SEMIBOLD = "'Inter SemiBold', 'HarmonyOS Sans SC Medium', 'Inter', 'HarmonyOS Sans SC', sans-serif"
FONT_UI_BOLD = "'Inter', 'HarmonyOS Sans SC', 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif"  # 配合 font-weight:700
# LEVEL 4：路径 / 数字 / 日志 / 数据（JetBrains Mono）
FONT_MONO = "'JetBrains Mono', 'HarmonyOS Sans SC', Consolas, 'Microsoft YaHei', monospace"

# 兼容别名（旧引用）
FONT_DISPLAY = FONT_BRAND
FONT_HEAD = FONT_UI_SEMIBOLD
FONT_BODY = FONT_UI


def stripes_svg_data(color: str = WHITE, bg: str = "transparent",
                     width: int = 10, gap: int = 10, angle: int = 45) -> bytes:
    """斜条纹 SVG（QSS border-image / 绘制备用）。"""
    period = width + gap
    return f"""<svg xmlns='http://www.w3.org/2000/svg' width='{period * 2}' height='{period * 2}'>
<rect width='100%' height='100%' fill='{bg}'/>
<g stroke='{color}' stroke-width='{width}'>
<line x1='{-period}' y1='{period * 2}' x2='{period * 2}' y2='{-period}'/>
<line x1='0' y1='{period * 2}' x2='{period * 3}' y2='{-period}'/>
</g></svg>""".encode("utf-8")


# ---------------------------------------------------------------------------
# 全局 QSS
# ---------------------------------------------------------------------------

def app_stylesheet(lang: str = "zh") -> str:
    # 模块标题 / 页签字号与字距（两种语言各自微调，整体比例不变）
    title_size = "11px"
    title_spacing = "1px" if lang == "en" else "2px"
    tab_spacing = "0px" if lang == "en" else "1px"
    tab_size = "12px" if lang == "en" else "13px"
    return f"""
/* ============ 全局 ============ */
QWidget {{
    background: {PAPER};
    color: {INK};
    font-family: {FONT_UI};
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {PAPER}; }}

/* ============ 分组卡片（白色圆角） ============ */
QGroupBox {{
    background: {WHITE};
    border: none;
    border-radius: 18px;
    margin-top: 20px;
    padding: 12px 12px 12px 12px;
    font-weight: 700;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    top: 0px;
    padding: 3px 14px;
    border-radius: 9px;
    background: {RED};
    color: {WHITE};
    font-family: {FONT_UI_BOLD};
    font-weight: 700;
    font-size: {title_size};
    letter-spacing: {title_spacing};
}}

/* ============ 输入框（路径：等宽） ============ */
QLineEdit {{
    background: {PAPER};
    border: 2px solid transparent;
    border-radius: 12px;
    padding: 8px 12px;
    font-family: {FONT_MONO};
    font-size: 13px;
    selection-background-color: {RED};
    selection-color: {WHITE};
}}
QLineEdit:hover {{ background: {WHITE}; }}
QLineEdit:focus {{ border: 2px solid {RED}; background: {WHITE}; }}

/* ============ 按钮（胶囊，Inter Bold / 鸿蒙 Bold） ============ */
QPushButton {{
    background: {INK};
    color: {WHITE};
    border: none;
    border-radius: 18px;
    padding: 9px 20px;
    font-family: {FONT_UI_BOLD};
    font-weight: 700;
    letter-spacing: 1px;
}}
QPushButton:hover {{ background: {INK_SOFT}; }}
QPushButton:pressed {{ background: {RED}; }}
QPushButton:disabled {{ background: {PAPER_DIM}; color: {INK_SOFT}; }}

QPushButton#primary {{
    background: {RED};
    color: {WHITE};
    font-family: {FONT_UI_BOLD};
    font-weight: 700;
    font-size: 15px;
    letter-spacing: 1px;
    padding: 11px 36px;
    border-radius: 22px;
}}
QPushButton#primary:hover {{ background: {RED_DARK}; }}
QPushButton#primary:pressed {{ background: {INK}; }}
QPushButton#primary:disabled {{ background: {PAPER_DIM}; color: {INK_SOFT}; }}

QPushButton#ghost {{
    background: {PAPER};
    color: {INK};
    border-radius: 14px;
    padding: 4px 6px;
    letter-spacing: 0px;
}}
QPushButton#ghost:hover {{ background: {INK}; color: {WHITE}; }}

/* ============ 单选 / 复选 ============ */
QRadioButton, QCheckBox {{ spacing: 8px; background: transparent; }}
QRadioButton::indicator, QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 2px solid {LINE};
    background: {PAPER};
}}
QRadioButton::indicator {{ border-radius: 10px; }}
QCheckBox::indicator {{ border-radius: 6px; }}
QRadioButton::indicator:checked {{
    background: {RED}; border: 2px solid {RED};
}}
QCheckBox::indicator:checked {{
    background: {RED};
    image: none;
    border: 2px solid {RED};
}}
QRadioButton::indicator:unchecked:hover,
QCheckBox::indicator:unchecked:hover {{ border-color: {RED}; }}
QRadioButton::indicator:checked:disabled,
QCheckBox::indicator:checked:disabled {{ background: {LINE}; border-color: {LINE}; }}

/* ============ 下拉 / 数字框（格式与数值：等宽） ============ */
QComboBox, QDoubleSpinBox {{
    background: {PAPER};
    border: 2px solid transparent;
    border-radius: 10px;
    padding: 6px 10px;
    font-family: {FONT_MONO};
}}
QComboBox:hover, QDoubleSpinBox:hover {{ background: {WHITE}; }}
QComboBox:focus, QDoubleSpinBox:focus {{ border-color: {RED}; background: {WHITE}; }}
QComboBox::drop-down {{
    border: none; width: 28px;
    background: {INK};
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {WHITE};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {WHITE};
    border: none;
    border-radius: 10px;
    selection-background-color: {RED};
    selection-color: {WHITE};
    outline: none;
}}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {INK}; border: none; width: 18px;
}}
QDoubleSpinBox::up-button {{ border-top-right-radius: 8px; }}
QDoubleSpinBox::down-button {{ border-bottom-right-radius: 8px; }}
QDoubleSpinBox::up-arrow {{
    image: none;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid {WHITE};
}}
QDoubleSpinBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {WHITE};
}}

/* ============ 页签（胶囊页签 + 白色圆角面板） ============ */
QTabWidget::pane {{
    border: none;
    background: {WHITE};
    border-radius: 18px;
    top: 8px;
}}
QTabBar::tab {{
    background: transparent;
    color: {INK_SOFT};
    border: none;
    border-radius: 14px;
    padding: 8px 22px;
    margin-right: 4px;
    margin-bottom: 4px;
    font-family: {FONT_UI_SEMIBOLD};
    font-size: {tab_size};
    letter-spacing: {tab_spacing};
}}
QTabBar::tab:selected {{
    background: {RED};
    color: {WHITE};
    font-family: {FONT_UI_BOLD};
    font-weight: 700;
}}
QTabBar::tab:hover:!selected {{ background: {PAPER_DIM}; color: {INK}; }}

/* ============ Advanced settings accordion ============ */
QToolBox {{ background: {PAPER}; border: none; }}
QToolBox > QAbstractButton {{ min-height: 34px; }}
QToolBox::tab {{
    background: {WHITE}; color: {INK_SOFT}; border: 1px solid {LINE};
    border-radius: 10px; padding: 1px 14px; margin: 2px 0;
    font-family: {FONT_UI_SEMIBOLD}; font-weight: 600;
}}
QToolBox::tab:selected {{ background: {RED}; color: {WHITE}; border-color: {RED}; }}
QToolBox::tab:hover {{ border-color: {RED}; }}
QToolBox QScrollArea {{ background: {PAPER}; border: none; }}

/* ============ 文本框（日志：等宽） ============ */
QTextEdit {{
    background: {INK};
    color: {PAPER};
    border: none;
    border-radius: 14px;
    padding: 8px;
    font-family: {FONT_MONO};
    font-size: 12px;
}}

/* ============ 标签 ============ */
QLabel {{ background: transparent; }}
QLabel#hint {{ color: {INK_SOFT}; font-size: 11px; }}
QLabel#statValue {{
    font-family: {FONT_MONO};
    font-weight: 700;
    font-size: 23px;
}}
QLabel#statLabel {{
    font-size: 10px;
    letter-spacing: 1px;
    color: {INK_SOFT};
    font-weight: 700;
}}
QLabel#currentFile {{
    font-family: {FONT_MONO};
    font-size: 12px;
    color: {INK_SOFT};
}}

/* ============ 提示气泡 ============ */
QToolTip {{
    background: {INK};
    color: {PAPER};
    border: none;
    border-radius: 8px;
    padding: 6px 10px;
}}

/* ============ 滚动条（细圆角） ============ */
QScrollBar:vertical {{
    background: transparent; width: 10px; border: none;
    margin: 4px 2px 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {LINE}; min-height: 30px; border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: {RED}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; border: none;
    margin: 2px 4px 2px 4px;
}}
QScrollBar::handle:horizontal {{
    background: {LINE}; min-width: 30px; border-radius: 5px;
}}
QScrollBar::handle:horizontal:hover {{ background: {RED}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""
