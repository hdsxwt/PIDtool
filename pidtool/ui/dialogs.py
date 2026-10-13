from __future__ import annotations

from PySide6 import QtWidgets


class LLMSettingsDialog(QtWidgets.QDialog):
    def __init__(self, values: dict, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DeepSeek 设置")
        self.setMinimumWidth(440)
        form = QtWidgets.QFormLayout(self)
        self.base_edit = QtWidgets.QLineEdit(str(values.get("base_url", "https://api.deepseek.com")))
        self.model_edit = QtWidgets.QLineEdit(str(values.get("model", "")))
        self.key_edit = QtWidgets.QLineEdit(str(values.get("api_key", "")))
        self.key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        form.addRow("Base URL", self.base_edit)
        form.addRow("模型", self.model_edit)
        form.addRow("API Key", self.key_edit)
        hint = QtWidgets.QLabel("API Key 只保存在本机全局配置，不会写入固件目录")
        hint.setStyleSheet("color: #888;")
        form.addRow(hint)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        return {
            "base_url": self.base_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "api_key": self.key_edit.text().strip(),
        }
