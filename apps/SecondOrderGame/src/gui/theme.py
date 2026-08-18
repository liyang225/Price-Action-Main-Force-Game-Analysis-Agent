"""PA Agent-derived stylesheet for the standalone parameter workbench."""

from src.gui import tokens

WORKBENCH_QSS = """
QWidget { background: __APP_BG__; color: __TEXT_PRIMARY__; font: 13px __UI_FONT__; }
QMainWindow, QDialog { background: #0C0E11; }
QToolTip { background: #22272F; color: #E8ECF1; border: 1px solid #333A45; padding: 4px; }
QFrame#TopBar, QFrame#SideRail, QFrame#StatusBar { background: #12151A; border: none; }
QLabel#Title { font-size: 20px; font-weight: 600; }
QLabel#Subtle, QLabel#PreviewStatus { color: #9AA5B1; }
QLabel#Disclaimer { background: #2A2418; color: #E5BE69; border: 1px solid #6A5528; border-radius: 4px; padding: 7px 10px; font-weight: 600; }
QLabel#ValidationError { background: #2C171B; color: #FF8792; border: 1px solid #6D2B34; border-radius: 4px; padding: 7px 10px; }
QPushButton { background: #181C22; border: 1px solid #333A45; border-radius: 4px; padding: 6px 12px; min-height: 20px; }
QPushButton:hover { background: #22272F; border-color: #4A7EBB; }
QPushButton:focus { border: 1px solid #5B8CC9; }
QPushButton:disabled { color: #646E7A; background: #12151A; border-color: #22272F; }
QPushButton#Primary { background: #29496D; border-color: #4A7EBB; font-weight: 600; }
QPushButton#Primary:hover { background: #345B86; }
QTabWidget::pane { border: 1px solid #22272F; top: -1px; }
QTabBar::tab { background: #12151A; color: #9AA5B1; padding: 8px 14px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #E8ECF1; border-bottom-color: #4A7EBB; }
QTabBar::tab:hover { background: #181C22; }
QComboBox, QDoubleSpinBox, QSpinBox { background: #181C22; border: 1px solid #333A45; border-radius: 4px; padding: 4px 8px; min-height: 22px; }
QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus { border-color: #4A7EBB; }
QComboBox QAbstractItemView { background: #181C22; selection-background-color: #29496D; }
QSlider::groove:horizontal { height: 5px; background: #333A45; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #4A7EBB; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; background: #E8ECF1; border: 2px solid #4A7EBB; border-radius: 7px; }
QTableWidget, QListWidget, QPlainTextEdit { background: #12151A; alternate-background-color: #15191F; border: 1px solid #22272F; gridline-color: #22272F; selection-background-color: #29496D; selection-color: #FFFFFF; }
QTableWidget, QPlainTextEdit { font-family: __MONO_FONT__; }
QHeaderView::section { background: #181C22; color: #9AA5B1; border: none; border-right: 1px solid #22272F; border-bottom: 1px solid #333A45; padding: 6px; font-weight: 600; }
QListWidget::item { padding: 7px 9px; border-bottom: 1px solid #181C22; }
QListWidget::item:selected { background: #29496D; }
QSplitter::handle { background: #22272F; width: 1px; height: 1px; }
QGroupBox { border: 1px solid #22272F; border-radius: 4px; margin-top: 12px; padding-top: 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #9AA5B1; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #12151A; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #333A45; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #4A7EBB; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: #12151A; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: #333A45; border-radius: 4px; min-width: 28px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""

WORKBENCH_QSS = (
    WORKBENCH_QSS.replace("__APP_BG__", tokens.APP_BG)
    .replace("__TEXT_PRIMARY__", tokens.TEXT_PRIMARY)
    .replace("__UI_FONT__", tokens.UI_FONT)
    .replace("__MONO_FONT__", tokens.MONO_FONT)
)
