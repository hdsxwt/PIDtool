from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from pidtool.ai.plan import make_action, normalize_rounds, round_summary

STEP_FIELDS = (
    ("baseline_sp", "基线值", 4, False),
    ("target_sp", "目标值", 4, False),
    ("settle_s", "稳定时间", 2, True),
    ("countdown_s", "倒计时", 2, True),
    ("measure_s", "观测时长", 2, True),
)
DISTURBANCE_FIELDS = (
    ("target_sp", "目标值", 4, False),
    ("settle_s", "稳定时间", 2, True),
    ("countdown_s", "倒计时", 2, True),
    ("disturb_s", "扰动时长", 2, True),
    ("recover_s", "恢复时长", 2, True),
)


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
        self.setMinimumSize(760, 540)
        self._rounds = normalize_rounds(values.get("rounds"))
        cycles = max(1, int(values.get("cycles", 1) or 1))

        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(
            "层级：完整方案 = 测试循环 × 轮次列表；每轮依次执行若干动作，"
            "一轮结束后把全部动作结果一起发给 AI 做一次评估并给出下一组参数。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("循环次数"))
        self.cycles_spin = QtWidgets.QSpinBox()
        self.cycles_spin.setRange(1, 50)
        self.cycles_spin.setValue(cycles)
        self.cycles_spin.valueChanged.connect(self._update_summary)
        top.addWidget(self.cycles_spin)
        top.addStretch(1)
        layout.addLayout(top)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.addWidget(QtWidgets.QLabel("测试轮次（每轮一次 AI 请求）"))
        self.round_list = QtWidgets.QListWidget()
        self.round_list.currentRowChanged.connect(self._on_round_selected)
        left_layout.addWidget(self.round_list, 1)
        round_buttons = QtWidgets.QHBoxLayout()
        add_round = QtWidgets.QPushButton("添加轮次")
        add_round.clicked.connect(self._add_round)
        remove_round = QtWidgets.QPushButton("删除轮次")
        remove_round.clicked.connect(self._remove_round)
        up_round = QtWidgets.QPushButton("上移")
        up_round.clicked.connect(lambda: self._move_round(-1))
        down_round = QtWidgets.QPushButton("下移")
        down_round.clicked.connect(lambda: self._move_round(1))
        for widget in (add_round, remove_round, up_round, down_round):
            round_buttons.addWidget(widget)
        left_layout.addLayout(round_buttons)
        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.addWidget(QtWidgets.QLabel("该轮动作（按顺序执行）"))
        self.action_list = QtWidgets.QListWidget()
        self.action_list.currentRowChanged.connect(self._on_action_selected)
        right_layout.addWidget(self.action_list, 1)
        action_buttons = QtWidgets.QHBoxLayout()
        add_step = QtWidgets.QPushButton("添加阶跃")
        add_step.clicked.connect(lambda: self._add_action("step"))
        add_disturb = QtWidgets.QPushButton("添加扰动")
        add_disturb.clicked.connect(lambda: self._add_action("disturbance"))
        remove_action = QtWidgets.QPushButton("删除动作")
        remove_action.clicked.connect(self._remove_action)
        up_action = QtWidgets.QPushButton("上移")
        up_action.clicked.connect(lambda: self._move_action(-1))
        down_action = QtWidgets.QPushButton("下移")
        down_action.clicked.connect(lambda: self._move_action(1))
        for widget in (add_step, add_disturb, remove_action, up_action, down_action):
            action_buttons.addWidget(widget)
        right_layout.addLayout(action_buttons)

        self.params_group = QtWidgets.QGroupBox("动作参数")
        self.params_form = QtWidgets.QFormLayout(self.params_group)
        right_layout.addWidget(self.params_group)
        splitter.addWidget(right)
        splitter.setSizes([300, 460])

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

        self._reload_round_list(select=0)
        self._update_summary()

    def _round(self, index: int) -> dict | None:
        if 0 <= index < len(self._rounds):
            return self._rounds[index]
        return None

    def _selected_round(self) -> dict | None:
        return self._round(self.round_list.currentRow())

    def _selected_action(self) -> dict | None:
        round_spec = self._selected_round()
        if round_spec is None:
            return None
        row = self.action_list.currentRow()
        actions = round_spec["actions"]
        if 0 <= row < len(actions):
            return actions[row]
        return None

    def _reload_round_list(self, select: int = 0) -> None:
        self.round_list.blockSignals(True)
        self.round_list.clear()
        for index, round_spec in enumerate(self._rounds):
            self.round_list.addItem(
                f"{round_spec['name']}：{round_summary(round_spec)}"
            )
        self.round_list.blockSignals(False)
        if self._rounds:
            self.round_list.setCurrentRow(min(max(select, 0), len(self._rounds) - 1))
        self._on_round_selected(self.round_list.currentRow())

    def _reload_action_list(self, select: int = 0) -> None:
        self.action_list.blockSignals(True)
        self.action_list.clear()
        round_spec = self._selected_round()
        if round_spec is not None:
            for action in round_spec["actions"]:
                self.action_list.addItem(self._action_label(action))
        self.action_list.blockSignals(False)
        round_spec = self._selected_round()
        if round_spec is not None and round_spec["actions"]:
            self.action_list.setCurrentRow(min(max(select, 0), len(round_spec["actions"]) - 1))
        self._on_action_selected(self.action_list.currentRow())
        self._refresh_round_item(self.round_list.currentRow())
        self._update_summary()

    @staticmethod
    def _action_label(action: dict) -> str:
        target = float(action.get("target_sp", 0.0))
        if str(action.get("kind")) == "disturbance":
            return f"扰动：@{target:g}，施加 {float(action.get('disturb_s', 0.0)):g}s"
        baseline = float(action.get("baseline_sp", 0.0))
        return f"阶跃：{baseline:g} → {target:g}"

    def _refresh_action_item(self, row: int) -> None:
        round_spec = self._selected_round()
        if round_spec is None or not (0 <= row < len(round_spec["actions"])):
            return
        item = self.action_list.item(row)
        if item is not None:
            item.setText(self._action_label(round_spec["actions"][row]))

    def _on_round_selected(self, row: int) -> None:
        self._reload_action_list(select=0)
        self._refresh_round_item(row)
        self._update_summary()

    def _refresh_round_item(self, row: int) -> None:
        round_spec = self._round(row)
        if round_spec is None:
            return
        item = self.round_list.item(row)
        if item is not None:
            item.setText(f"{round_spec['name']}：{round_summary(round_spec)}")

    def _on_action_selected(self, row: int) -> None:
        self._rebuild_params()

    def _rebuild_params(self) -> None:
        while self.params_form.rowCount():
            self.params_form.removeRow(0)
        action = self._selected_action()
        if action is None:
            self.params_group.setEnabled(False)
            return
        self.params_group.setEnabled(True)
        fields = DISTURBANCE_FIELDS if str(action.get("kind")) == "disturbance" else STEP_FIELDS
        for key, label, decimals, is_seconds in fields:
            spin = QtWidgets.QDoubleSpinBox()
            if is_seconds:
                spin.setRange(0.1, 120.0)
            else:
                spin.setRange(-100000.0, 100000.0)
            spin.setDecimals(decimals)
            spin.setSingleStep(0.1)
            if is_seconds:
                spin.setSuffix(" s")
            spin.setValue(float(action.get(key, 0.0)))
            spin.valueChanged.connect(
                lambda value, field=key: self._on_param_changed(field, value)
            )
            self.params_form.addRow(label, spin)

    def _on_param_changed(self, key: str, value: float) -> None:
        action = self._selected_action()
        if action is None:
            return
        action[key] = float(value)
        self._refresh_action_item(self.action_list.currentRow())
        self._refresh_round_item(self.round_list.currentRow())
        self._update_summary()

    def _add_round(self) -> None:
        self._rounds.append(
            {"name": f"轮次 {len(self._rounds) + 1}", "actions": [make_action("step")]}
        )
        self._reload_round_list(select=len(self._rounds) - 1)

    def _remove_round(self) -> None:
        row = self.round_list.currentRow()
        if row < 0 or len(self._rounds) <= 1:
            return
        del self._rounds[row]
        self._reload_round_list(select=min(row, len(self._rounds) - 1))

    def _move_round(self, delta: int) -> None:
        row = self.round_list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < len(self._rounds)):
            return
        self._rounds[row], self._rounds[target] = self._rounds[target], self._rounds[row]
        self._reload_round_list(select=target)

    def _add_action(self, kind: str) -> None:
        round_spec = self._selected_round()
        if round_spec is None:
            return
        round_spec["actions"].append(make_action(kind))
        self._reload_action_list(select=len(round_spec["actions"]) - 1)

    def _remove_action(self) -> None:
        round_spec = self._selected_round()
        if round_spec is None or len(round_spec["actions"]) <= 1:
            return
        row = self.action_list.currentRow()
        if row < 0:
            return
        del round_spec["actions"][row]
        self._reload_action_list(select=min(row, len(round_spec["actions"]) - 1))

    def _move_action(self, delta: int) -> None:
        round_spec = self._selected_round()
        if round_spec is None:
            return
        row = self.action_list.currentRow()
        target = row + delta
        actions = round_spec["actions"]
        if row < 0 or not (0 <= target < len(actions)):
            return
        actions[row], actions[target] = actions[target], actions[row]
        self._reload_action_list(select=target)

    def _update_summary(self, *args) -> None:
        cycles = int(self.cycles_spin.value())
        total = len(self._rounds) * cycles
        steps = 0
        disturbances = 0
        for round_spec in self._rounds:
            for action in round_spec["actions"]:
                if str(action.get("kind")) == "disturbance":
                    disturbances += 1
                else:
                    steps += 1
        self.summary.setText(
            f"每循环 {len(self._rounds)} 轮 × {cycles} 循环 = 共 {total} 轮；"
            f"每循环动作：阶跃×{steps}、扰动×{disturbances}"
        )

    def values(self) -> dict:
        return {
            "cycles": int(self.cycles_spin.value()),
            "rounds": normalize_rounds(self._rounds),
        }


