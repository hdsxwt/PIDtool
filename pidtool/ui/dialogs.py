from __future__ import annotations

from PySide6 import QtWidgets

KIND_ITEMS = (("阶跃实验", "step"), ("扰动实验", "disturbance"))


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


class AutotunePlanDialog(QtWidgets.QDialog):
    def __init__(self, values: dict, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("自动调参方案")
        self.setMinimumWidth(520)
        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(
            "每一行是一轮测试方案；总轮数 = 行数 × 循环次数。"
            "扰动轮需要人工施加阻力，并会提示你确认停止。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list = QtWidgets.QListWidget()
        layout.addWidget(self.list, 1)

        row = QtWidgets.QHBoxLayout()
        add_step = QtWidgets.QPushButton("添加阶跃")
        add_step.clicked.connect(lambda: self._add_row("step"))
        add_disturb = QtWidgets.QPushButton("添加扰动")
        add_disturb.clicked.connect(lambda: self._add_row("disturbance"))
        remove_btn = QtWidgets.QPushButton("删除选中")
        remove_btn.clicked.connect(self._remove_selected)
        up_btn = QtWidgets.QPushButton("上移")
        up_btn.clicked.connect(lambda: self._move_selected(-1))
        down_btn = QtWidgets.QPushButton("下移")
        down_btn.clicked.connect(lambda: self._move_selected(1))
        for widget in (add_step, add_disturb, remove_btn, up_btn, down_btn):
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)

        form = QtWidgets.QFormLayout()
        self.loops_spin = QtWidgets.QSpinBox()
        self.loops_spin.setRange(1, 50)
        self.loops_spin.setValue(int(values.get("loops", 1)))
        self.loops_spin.valueChanged.connect(self._update_summary)
        self.disturb_spin = QtWidgets.QDoubleSpinBox()
        self.disturb_spin.setRange(0.5, 120.0)
        self.disturb_spin.setValue(float(values.get("disturb_s", 5.0)))
        self.disturb_spin.setDecimals(1)
        self.disturb_spin.setSuffix(" s")
        self.recover_spin = QtWidgets.QDoubleSpinBox()
        self.recover_spin.setRange(0.5, 120.0)
        self.recover_spin.setValue(float(values.get("recover_s", 4.0)))
        self.recover_spin.setDecimals(1)
        self.recover_spin.setSuffix(" s")
        form.addRow("循环次数", self.loops_spin)
        form.addRow("扰动时长", self.disturb_spin)
        form.addRow("恢复时长", self.recover_spin)
        layout.addLayout(form)

        self.summary = QtWidgets.QLabel()
        layout.addWidget(self.summary)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for kind in values.get("plan", ["step"]):
            self._add_row(str(kind))
        if self.list.count() == 0:
            self._add_row("step")
        self._update_summary()

    def _add_row(self, kind: str) -> None:
        item = QtWidgets.QListWidgetItem()
        combo = QtWidgets.QComboBox()
        for label, value in KIND_ITEMS:
            combo.addItem(label, value)
        combo.setCurrentIndex(0 if kind != "disturbance" else 1)
        combo.currentIndexChanged.connect(self._update_summary)
        item.setSizeHint(combo.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, combo)
        self._update_summary()

    def _row_kind(self, row: int) -> str:
        combo = self.list.itemWidget(self.list.item(row))
        return str(combo.currentData()) if combo is not None else "step"

    def _plan(self) -> list[str]:
        return [self._row_kind(row) for row in range(self.list.count())]

    def _remove_selected(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            if self.list.count() == 0:
                self._add_row("step")
            self._update_summary()

    def _move_selected(self, delta: int) -> None:
        row = self.list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.list.count()):
            return
        kinds = self._plan()
        kinds[row], kinds[target] = kinds[target], kinds[row]
        self.list.clear()
        for kind in kinds:
            self._add_row(kind)
        self.list.setCurrentRow(target)

    def _update_summary(self, *args) -> None:
        plan = self._plan()
        steps = plan.count("step")
        disturbances = plan.count("disturbance")
        total = len(plan) * self.loops_spin.value()
        self.summary.setText(
            f"每循环：阶跃×{steps}、扰动×{disturbances}；共 {total} 轮"
        )

    def values(self) -> dict:
        return {
            "plan": self._plan(),
            "loops": int(self.loops_spin.value()),
            "disturb_s": float(self.disturb_spin.value()),
            "recover_s": float(self.recover_spin.value()),
        }
