"""Workflow editor with typed fields and visible run results."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..workflows.loader import FOLDER_KEYS, MAX_STEPS, STEP_FIELDS, TASK_RANGES, validate_step


class StepDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Add a workflow step")
        box = QVBoxLayout(self)
        self.kind = QComboBox()
        for key in STEP_FIELDS:
            self.kind.addItem(key.replace("_", " ").capitalize(), key)
        box.addWidget(self.kind)
        self.form = QFormLayout()
        box.addLayout(self.form)
        self.kind.currentIndexChanged.connect(self.rebuild)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        box.addWidget(self.problem)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.try_accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.rebuild()

    def rebuild(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields = {}
        for key, type_ in STEP_FIELDS[self.kind.currentData()].items():
            if key in {"folder", "range"}:
                field = QComboBox()
                field.addItems(FOLDER_KEYS if key == "folder" else TASK_RANGES)
            elif type_ is int:
                field = QSpinBox()
                lo, hi = {"seconds": (1, 30), "minutes": (1, 480), "level": (0, 100)}[key]
                field.setRange(lo, hi)
            else:
                field = QLineEdit()
                field.setMaxLength(2000 if key == "url" else 200)
            self.fields[key] = field
            self.form.addRow(key.capitalize(), field)

    def try_accept(self):
        step = {"type": self.kind.currentData()}
        for key, field in self.fields.items():
            if isinstance(field, QSpinBox):
                value = field.value()
            elif isinstance(field, QComboBox):
                value = field.currentText()
            else:
                value = field.text()
            step[key] = value
        try:
            self.step = validate_step(step, 1)
        except ValueError as exc:
            self.problem.setText(str(exc))
            return
        self.accept()


class WorkflowEditor(QWidget):
    def __init__(self, service, submit, stop):
        super().__init__()
        self.service, self.submit = service, submit
        self.steps = []
        self.original = ""
        box = QVBoxLayout(self)
        title = QLabel("Workflows")
        title.setObjectName("title")
        box.addWidget(title)
        bar = QHBoxLayout()
        self.saved = QComboBox()
        self.saved.setAccessibleName("Saved workflows")
        self.saved.currentTextChanged.connect(self.load)
        bar.addWidget(self.saved, 1)
        for label, callback in (
            ("New", self.new), ("Run", self.run), ("Stop", stop),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            bar.addWidget(button)
        box.addLayout(bar)
        self.name = QLineEdit()
        self.name.setMaxLength(40)
        self.name.setPlaceholderText("Workflow name")
        self.name.setAccessibleName("Workflow name")
        box.addWidget(self.name)
        self.list = QListWidget()
        self.list.setAccessibleName("Workflow steps")
        box.addWidget(self.list, 2)
        actions = QHBoxLayout()
        for label, callback in (
            ("Add step", self.add), ("Remove step", self.remove),
            ("Move up", lambda: self.move_step(-1)), ("Move down", lambda: self.move_step(1)),
            ("Save workflow", self.save),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        box.addLayout(actions)
        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumBlockCount(100)
        self.status.setAccessibleName("Workflow progress and results")
        box.addWidget(self.status, 1)
        self.reload()

    def reload(self, selected=""):
        old = self.saved.blockSignals(True)
        self.saved.clear()
        if self.service:
            self.saved.addItems(self.service.names())
        if selected:
            self.saved.setCurrentText(selected)
        self.saved.blockSignals(old)
        self.load(self.saved.currentText())

    def load(self, name):
        value = self.service.workflows.get(name.casefold()) if self.service else None
        self.original = value["name"] if value else ""
        self.steps = [dict(step) for step in value["steps"]] if value else []
        self.name.setText(self.original)
        self.refresh()

    def new(self):
        self.saved.setCurrentIndex(-1)
        self.load("")
        self.name.setFocus()

    def refresh(self, selected=-1):
        self.list.clear()
        for step in self.steps:
            title = step["type"].replace("_", " ").capitalize()
            arguments = " · ".join(str(step[key]) for key in STEP_FIELDS[step["type"]])
            self.list.addItem(f"{title}  {arguments}".rstrip())
        if selected >= 0:
            self.list.setCurrentRow(selected)

    def add(self):
        if len(self.steps) >= MAX_STEPS:
            self.status.setPlainText(f"You can add up to {MAX_STEPS} steps.")
            return
        dialog = StepDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.steps.append(dialog.step)
            self.refresh(len(self.steps) - 1)

    def remove(self):
        row = self.list.currentRow()
        if row >= 0:
            del self.steps[row]
            self.refresh(min(row, len(self.steps) - 1))

    def move_step(self, delta):
        row = self.list.currentRow()
        target = row + delta
        if row >= 0 and 0 <= target < len(self.steps):
            self.steps[row], self.steps[target] = self.steps[target], self.steps[row]
            self.refresh(target)

    def save(self):
        self.submit("workflow_save", original=self.original,
                    workflow={"name": self.name.text(), "steps": [dict(step) for step in self.steps]})

    def run(self):
        name = self.saved.currentText()
        if name:
            self.status.clear()
            self.submit("run_workflow", workflow=name)
        else:
            self.status.setPlainText("Save and select a workflow first.")

    def show_step(self, step):
        self.status.appendPlainText(f"{step.index} · {step.kind.replace('_', ' ')} · {step.status} · {step.detail}")

    def show_result(self, reply):
        if reply.key == "workflow.saved":
            self.reload(reply.data["name"])
        self.status.setPlainText(reply.text)
