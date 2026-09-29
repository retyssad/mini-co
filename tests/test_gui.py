"""Desktop behavior that must not depend on an interactive display."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from mini_co import session
from mini_co.gui import MainWindow


def test_deleting_open_session_does_not_save_it_again(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.agent.messages = [{"role": "user", "content": "hello"}]
    window.session_id = session.save_session(window.agent.messages, "test-model")
    saved_id = window.session_id
    window.refresh_sessions()

    row = window.sessions.itemWidget(window.sessions.item(0))
    buttons = row.findChildren(QPushButton)
    assert len(buttons) == 1
    assert buttons[0].text() == "hello"
    window.new_session()
    row = window.sessions.itemWidget(window.sessions.item(0))
    row.findChild(QPushButton).click()
    assert window.session_id == saved_id

    window.delete_saved_session(saved_id)

    assert session.load_session(saved_id) is None
    assert window.session_id is None
    assert window.agent.messages == []
    window.close()
    app.processEvents()
