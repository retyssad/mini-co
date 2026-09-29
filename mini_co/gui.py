"""PySide6 desktop shell for mini-co.

The UI is deliberately thin: the existing Agent remains responsible for
models, tools, MCP, hooks, permissions, sessions and sub-agents.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QTextBlockFormat, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .agent import Agent
from .checkpoints import changed_paths, diff_for, undo
from .config import Config
from .hooks import HOOKS_FILE, load_hooks
from .llm import LLM, LiteLLM
from .mcp import CONFIG_FILE as MCP_CONFIG_FILE
from .mcp import load_mcp_tools
from .permissions import Permission
from .session import delete_session, list_sessions, load_session, save_session
from .tools import ALL_TOOLS
from .tools.bash import set_tracked_cwd
from .tools.edit import _changed_files

STYLE = """
QWidget { background:#f5f7f6; color:#20312a; font:10pt 'Segoe UI'; }
QMainWindow, QSplitter, QTabWidget, QTreeView { border:0; }
QFrame#header, QFrame#composer, QFrame#sidebar, QFrame#inspector { background:#fff; }
QFrame#header { border-bottom:1px solid #dce5e0; }
QFrame#composer { border-top:1px solid #dce5e0; }
QPushButton,QToolButton { background:#fff; border:1px solid #cbd8d2; border-radius:5px; padding:6px 10px; }
QPushButton:hover,QToolButton:hover { background:#edf6f1; border-color:#79b79e; }
QPushButton#primary { background:#11765a; color:#fff; border-color:#11765a; }
QLineEdit,QTextEdit,QPlainTextEdit { background:#fff; border:1px solid #cad8d1; border-radius:5px; padding:6px; }
QTextBrowser { background:transparent; border:0; }
QTextEdit#chat { background:#f8fbfa; border:0; padding:16px; font-size:11pt; }
QListWidget::item:selected,QTreeView::item:selected { background:#d9eee6; color:#173d31; }
QTabBar::tab { padding:8px 12px; background:#f4f7f5; }
QTabBar::tab:selected { background:#fff; border-bottom:2px solid #11765a; }
QStatusBar { background:#fff; border-top:1px solid #dce5e0; color:#63736c; }
"""


def brief(name: str, args: dict) -> str:
    labels = {"read_file": "读取文件", "edit_file": "编辑文件", "write_file": "写入文件",
              "bash": "运行命令", "glob": "查找文件", "grep": "搜索代码",
              "todo_write": "更新任务", "agent": "子 Agent"}
    target = args.get("file_path") or args.get("command") or args.get("path") or ""
    return labels.get(name, name) + (": " + str(target)[:100] if target else "")


class PermissionRequest:
    def __init__(self, name: str, args: dict):
        self.name, self.args, self.answer = name, args, "deny"
        self.ready = threading.Event()


class Worker(QThread):
    token = Signal(str)
    tool_started = Signal(str, object)
    tool_finished = Signal(str, object, str)
    permission = Signal(object)
    done = Signal(str)
    error = Signal(str)
    stopped = Signal()

    def __init__(self, agent: Agent, prompt: str, parent=None):
        super().__init__(parent)
        self.agent, self.prompt = agent, prompt
        self.cancelled = threading.Event()
        self.requests: list[PermissionRequest] = []

    def stop(self):
        self.cancelled.set()
        for request in self.requests:
            request.ready.set()

    def ask(self, name: str, args: dict) -> str:
        if self.cancelled.is_set():
            raise KeyboardInterrupt
        request = PermissionRequest(name, args)
        self.requests.append(request)
        self.permission.emit(request)
        while not request.ready.wait(.1):
            if self.cancelled.is_set():
                raise KeyboardInterrupt
        self.requests.remove(request)
        return request.answer

    def run(self):
        self.agent.permission.ask = self.ask
        try:
            result = self.agent.chat(
                self.prompt, on_token=self.token.emit, on_tool=self.tool_started.emit,
                on_tool_result=self.tool_finished.emit, should_cancel=self.cancelled.is_set,
            )
            self.done.emit(result)
        except KeyboardInterrupt:
            self.stopped.emit()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
        finally:
            self.agent.permission.ask = None


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("模型与集成设置")
        self.resize(650, 560)
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        model = QWidget(); form = QFormLayout(model)
        self.provider = QLineEdit(config.provider)
        self.model = QLineEdit(config.model)
        self.base_url = QLineEdit(config.base_url or "")
        self.api_key = QLineEdit(config.api_key); self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.max_tokens = QLineEdit(str(config.max_tokens))
        self.temperature = QLineEdit(str(config.temperature))
        self.context = QLineEdit(str(config.max_context_tokens))
        for label, field in (("Provider", self.provider), ("模型", self.model), ("API 地址", self.base_url),
                             ("API Key", self.api_key), ("最大输出 token", self.max_tokens),
                             ("Temperature", self.temperature), ("上下文上限", self.context)):
            form.addRow(label, field)
        tabs.addTab(model, "模型")
        integration = QWidget(); integrations = QVBoxLayout(integration)
        self.hooks = QPlainTextEdit(self._read_json(HOOKS_FILE, {"PreToolUse": [], "PostToolUse": []}))
        self.mcp = QPlainTextEdit(self._read_json(MCP_CONFIG_FILE, {"mcpServers": {}}))
        integrations.addWidget(QLabel("hooks.json")); integrations.addWidget(self.hooks)
        integrations.addWidget(QLabel("mcp.json")); integrations.addWidget(self.mcp)
        tabs.addTab(integration, "MCP / Hooks")
        root.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    @staticmethod
    def _read_json(path: Path, fallback: dict) -> str:
        try:
            return json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False, indent=2)
        except (OSError, ValueError):
            return json.dumps(fallback, ensure_ascii=False, indent=2)

    def values(self):
        return (
            {"provider": self.provider.text().strip(), "model": self.model.text().strip(),
             "base_url": self.base_url.text().strip() or None, "api_key": self.api_key.text(),
             "max_tokens": int(self.max_tokens.text()), "temperature": float(self.temperature.text()),
             "max_context_tokens": int(self.context.text())},
            json.loads(self.hooks.toPlainText()), json.loads(self.mcp.toPlainText()),
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("mini-co")
        self.resize(1320, 820)
        self.settings = QSettings("mini-co", "Desktop")
        self.config = Config.from_env()
        self.project = Path(self.settings.value("project", str(Path.cwd())))
        if not self.project.is_dir(): self.project = Path.cwd()
        os.chdir(self.project); set_tracked_cwd(str(self.project))
        self.agent = self.make_agent()
        self.worker: Worker | None = None
        self.tool_cards: list[QFrame] = []
        self.session_id = None
        self.build_ui(); self.refresh_sessions(); self.refresh_changes(); self.refresh_tasks()

    def make_agent(self):
        cls = LiteLLM if self.config.provider == "litellm" else LLM
        llm = cls(model=self.config.model, api_key=self.config.api_key or "unused",
                  base_url=self.config.base_url, temperature=self.config.temperature,
                  max_tokens=self.config.max_tokens)
        return Agent(llm=llm, tools=[*(type(tool)() for tool in ALL_TOOLS), *load_mcp_tools()],
                     max_context_tokens=self.config.max_context_tokens, permission=Permission(),
                     hooks=load_hooks())

    def build_ui(self):
        root = QWidget(); self.setCentralWidget(root); outer = QVBoxLayout(root); outer.setContentsMargins(0,0,0,0)
        header = QFrame(); header.setObjectName("header"); top = QHBoxLayout(header)
        title = QLabel("mini-co"); title.setStyleSheet("font-size:17pt;font-weight:700;color:#173d31")
        top.addWidget(title); self.project_btn = QPushButton(self.project.name + "  ▾"); self.project_btn.clicked.connect(self.choose_project); top.addWidget(self.project_btn); top.addStretch()
        self.model_btn = QPushButton(self.config.model + "  ▾"); self.model_btn.clicked.connect(self.open_settings); top.addWidget(self.model_btn); outer.addWidget(header)
        split = QSplitter(Qt.Orientation.Horizontal); outer.addWidget(split, 1)
        side = QFrame(); side.setObjectName("sidebar"); sl = QVBoxLayout(side); sl.addWidget(QLabel("会话"))
        new = QPushButton("＋  新建会话"); new.clicked.connect(lambda: self.new_session()); sl.addWidget(new)
        self.sessions = QListWidget(); self.sessions.itemClicked.connect(self.open_session); sl.addWidget(self.sessions, 1); split.addWidget(side)
        center = QWidget(); cl = QVBoxLayout(center); cl.setContentsMargins(0,0,0,0)
        self.scroll = QTextEdit(); self.scroll.setObjectName("chat"); self.scroll.setReadOnly(True); cl.addWidget(self.scroll, 1)
        compose = QFrame(); compose.setObjectName("composer"); co = QVBoxLayout(compose)
        self.input = QTextEdit(); self.input.setPlaceholderText("描述你要完成的任务…  Ctrl+Enter 发送"); self.input.setFixedHeight(85); self.input.installEventFilter(self); co.addWidget(self.input)
        actions = QHBoxLayout(); self.plan = QPushButton("计划模式"); self.plan.setCheckable(True); self.plan.toggled.connect(self.toggle_plan); actions.addWidget(self.plan)
        self.approve = QPushButton("批准执行"); self.approve.clicked.connect(self.approve_plan); self.approve.hide(); actions.addWidget(self.approve)
        undo_btn = QPushButton("↶ 撤销修改"); undo_btn.clicked.connect(self.undo_change); actions.addWidget(undo_btn); actions.addStretch()
        self.stop = QPushButton("停止"); self.stop.clicked.connect(self.stop_worker); self.stop.hide(); actions.addWidget(self.stop)
        send_btn = QPushButton("发送"); send_btn.setObjectName("primary"); send_btn.clicked.connect(lambda: self.send()); actions.addWidget(send_btn); co.addLayout(actions); cl.addWidget(compose); split.addWidget(center)
        right = QFrame(); right.setObjectName("inspector"); rl = QVBoxLayout(right); tabs = QTabWidget(); rl.addWidget(tabs)
        self.files = QTreeView(); self.file_model = QFileSystemModel(self); self.file_model.setRootPath(str(self.project)); self.files.setModel(self.file_model); self.files.setRootIndex(self.file_model.index(str(self.project)))
        for col in range(1,4): self.files.hideColumn(col)
        tabs.addTab(self.files, "文件")
        changes = QWidget(); cv = QVBoxLayout(changes); self.change_list = QListWidget(); self.change_list.currentItemChanged.connect(self.show_diff); cv.addWidget(self.change_list, 1); self.diff = QPlainTextEdit(); self.diff.setReadOnly(True); self.diff.setFont(QFont("Consolas", 9)); cv.addWidget(self.diff, 2); tabs.addTab(changes, "改动")
        self.tasks = QListWidget(); tabs.addTab(self.tasks, "任务"); split.addWidget(right); split.setSizes([220,760,340]); self.statusBar().showMessage("就绪")
        save = QAction("保存会话", self); save.triggered.connect(self.save_current); self.addAction(save)

    def append(self, text):
        color = "#315247"
        background = "#edf5f0"
        if text.lstrip().startswith("你"):
            color = "#2266a8"
            background = "#e8f2fc"
        elif text.lstrip().startswith("▸"):
            color = "#8a6418"
            background = "#fff3da"
        elif text.lstrip().startswith("✓"):
            color = "#2c8a61"
            background = "#e6f5ec"
        elif text.lstrip().startswith("错误"):
            color = "#b34d4d"
            background = "#fff0f0"
        elif text.lstrip().startswith("系统"):
            color = "#7a5aa6"
            background = "#f2edfa"
        cursor = self.scroll.textCursor(); cursor.movePosition(cursor.MoveOperation.End)
        block = QTextBlockFormat(); block.setBackground(QColor(background))
        block.setTopMargin(7); block.setBottomMargin(7); block.setLeftMargin(10); block.setRightMargin(10)
        cursor.insertBlock(block)
        fmt = QTextCharFormat(); fmt.setForeground(QColor(color))
        cursor.insertText(text, fmt); self.scroll.setTextCursor(cursor); self.scroll.ensureCursorVisible()

    def send(self, prompt=None):
        if self.worker: return
        prompt = prompt if prompt is not None else self.input.toPlainText().strip()
        if not prompt: return
        if not self.config.api_key:
            self.open_settings()
            if not self.config.api_key: return
        self.input.clear(); self.append("\n你\n" + prompt); self.worker = Worker(self.agent, prompt, self)
        self.worker.token.connect(lambda value: self._stream(value)); self.worker.tool_started.connect(self.tool_started); self.worker.tool_finished.connect(self.tool_finished)
        self.worker.permission.connect(self.ask_permission); self.worker.done.connect(lambda result: self.append("\nmini-co\n" + (result or "")))
        self.worker.error.connect(lambda error: self.append("\n错误\n" + error)); self.worker.stopped.connect(lambda: self.append("\n系统\n任务已停止。")); self.worker.finished.connect(self.worker_finished)
        self.stop.show(); self.statusBar().showMessage("正在处理…"); self.worker.start()

    def eventFilter(self, watched, event):
        if (watched is self.input and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.send()
            return True
        return super().eventFilter(watched, event)

    def toggle_plan(self, enabled):
        self.agent.plan_mode = enabled
        self.plan.setText("计划模式已开启" if enabled else "计划模式")
        self.approve.setVisible(enabled)

    def approve_plan(self):
        self.plan.setChecked(False)
        self.send("approve")

    def _stream(self, value):
        self.scroll.setTextColor(QColor("#176b52")); self.scroll.moveCursor(self.scroll.textCursor().MoveOperation.End); self.scroll.insertPlainText(value); self.scroll.ensureCursorVisible()
    def tool_started(self, name, args): self.append("\n▸ " + brief(name, args)); self.statusBar().showMessage(brief(name, args))
    def tool_finished(self, name, args, result): self.append("  ✓ " + result[:500]); self.refresh_changes(); self.refresh_tasks()

    def ask_permission(self, request):
        dialog = QDialog(self); dialog.setWindowTitle("授权操作"); layout = QVBoxLayout(dialog); layout.addWidget(QLabel(brief(request.name, request.args)))
        preview = QPlainTextEdit(json.dumps(request.args, ensure_ascii=False, indent=2)); preview.setReadOnly(True); layout.addWidget(preview)
        buttons = QDialogButtonBox(); once = buttons.addButton("允许这次", QDialogButtonBox.ButtonRole.AcceptRole); always = buttons.addButton("本次会话始终允许", QDialogButtonBox.ButtonRole.AcceptRole); buttons.addButton("拒绝", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.clicked.connect(lambda button: (setattr(request, "answer", "once" if button == once else "always" if button == always else "deny"), request.ready.set(), dialog.accept()))
        layout.addWidget(buttons); dialog.exec()

    def worker_finished(self):
        self.worker.deleteLater(); self.worker = None; self.stop.hide(); self.save_current(); self.statusBar().showMessage("完成")
    def stop_worker(self):
        if self.worker: self.worker.stop()
    def choose_project(self):
        path = QFileDialog.getExistingDirectory(self, "选择项目目录", str(self.project))
        if path:
            self.project = Path(path); os.chdir(self.project); set_tracked_cwd(str(self.project)); self.project_btn.setText(self.project.name + "  ▾"); self.file_model.setRootPath(str(self.project)); self.files.setRootIndex(self.file_model.index(str(self.project))); self.new_session()
    def open_settings(self):
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try: values, hooks, mcp = dialog.values()
        except (ValueError, json.JSONDecodeError) as exc: QMessageBox.warning(self, "设置错误", str(exc)); return
        self.config.__dict__.update(values); self.model_btn.setText(self.config.model + "  ▾"); old = self.agent.messages; self.agent = self.make_agent(); self.agent.messages = old
        HOOKS_FILE.parent.mkdir(parents=True, exist_ok=True); HOOKS_FILE.write_text(json.dumps(hooks, ensure_ascii=False, indent=2), encoding="utf-8"); MCP_CONFIG_FILE.write_text(json.dumps(mcp, ensure_ascii=False, indent=2), encoding="utf-8")
    def save_current(self):
        if self.agent.messages: self.session_id = save_session(self.agent.messages, self.agent.llm.model, self.session_id); self.refresh_sessions()
    def refresh_sessions(self):
        self.sessions.clear()
        for item in list_sessions():
            row = QListWidgetItem(item["preview"] or item["id"]); row.setData(Qt.ItemDataRole.UserRole, item["id"]); self.sessions.addItem(row)
            delete_btn = QToolButton(); delete_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)); delete_btn.setToolTip("删除会话")
            delete_btn.clicked.connect(lambda _checked=False, sid=item["id"]: self.delete_saved_session(sid))
            delete_btn.setStyleSheet("QToolButton { color:#b65353; border:0; font-size:15px; padding:2px 7px; } QToolButton:hover { background:#fff0f0; }")
            self.sessions.setItemWidget(row, self._session_row_widget(row, delete_btn))
    def new_session(self, save_previous=True):
        if save_previous:
            self.save_current()
        self.agent = self.make_agent(); self.session_id = None; self.scroll.clear(); _changed_files.clear(); self.refresh_changes(); self.refresh_tasks()

    def _session_row_widget(self, item, delete_btn):
        row = QWidget(); layout = QHBoxLayout(row); layout.setContentsMargins(4, 2, 2, 2)
        open_btn = QPushButton(item.text()); open_btn.setToolTip(item.text())
        open_btn.setStyleSheet("QPushButton { color:#3e554b; border:0; text-align:left; padding:4px; } QPushButton:hover { background:#e8f2fc; }")
        open_btn.clicked.connect(lambda: self.open_session(item))
        layout.addWidget(open_btn, 1); layout.addWidget(delete_btn)
        return row

    def delete_saved_session(self, session_id):
        answer = QMessageBox.question(self, "删除会话", "确定删除这个会话吗？此操作不可撤销。")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if delete_session(session_id):
            if session_id == self.session_id:
                self.new_session(save_previous=False)
            self.refresh_sessions()
    def open_session(self, item):
        loaded = load_session(item.data(Qt.ItemDataRole.UserRole))
        if not loaded: return
        self.agent = self.make_agent(); self.agent.messages, model = loaded; self.agent.llm.model = model; self.session_id = item.data(Qt.ItemDataRole.UserRole); self.scroll.clear()
        for message in self.agent.messages:
            if message.get("role") in ("user", "assistant") and message.get("content"): self.append(("你" if message["role"] == "user" else "mini-co") + "\n" + message["content"])
    def refresh_changes(self):
        self.change_list.clear()
        for path in changed_paths():
            row = QListWidgetItem(Path(path).name); row.setData(Qt.ItemDataRole.UserRole, path); self.change_list.addItem(row)
        self.diff.clear()
    def show_diff(self, item, previous=None):
        self.diff.setPlainText(diff_for(item.data(Qt.ItemDataRole.UserRole)) if item else "")
    def refresh_tasks(self):
        self.tasks.clear()
        if self.agent._todo:
            for task in self.agent._todo._tasks: self.tasks.addItem(("✓ " if task["status"] == "done" else "◐ " if task["status"] == "in_progress" else "○ ") + task["content"])
    def undo_change(self):
        self.statusBar().showMessage(undo()); self.refresh_changes()


def main():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion"); app.setStyleSheet(STYLE)
    window = MainWindow(); window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
