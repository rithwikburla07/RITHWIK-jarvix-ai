"""Calendar and item controls using the shared task service."""
from datetime import datetime, time

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QCalendarWidget, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from ..tasks.service import describe
from .integration_panels import button, feedback, heading


class TaskDialog(QDialog):
    def __init__(self, parent, date, item=None):
        super().__init__(parent)
        self.setWindowTitle("Reschedule item" if item else "Add a task or reminder")
        self.item = item
        box = QFormLayout(self)
        self.title = QLineEdit(item.title if item else "")
        self.title.setMaxLength(500)
        self.title.setReadOnly(item is not None)
        self.kind = QComboBox()
        self.kind.addItems(("Task", "Reminder"))
        if item:
            self.kind.setCurrentText(item.type.capitalize())
            self.kind.setEnabled(False)
        self.due = QDateTimeEdit(QDateTime(datetime.combine(date, time(19))))
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("dd MMM yyyy · HH:mm")
        for label, field in (("Type", self.kind), ("Title", self.title), ("Due", self.due)):
            box.addRow(label, field)
        self.error = feedback()
        box.addRow(self.error)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self.try_save)
        actions.rejected.connect(self.reject)
        box.addRow(actions)

    def try_save(self):
        if not self.title.text().strip():
            self.error.setText("Enter a title.")
            return
        if self.kind.currentText() == "Reminder" and self.due.dateTime().toPython() <= datetime.now():
            self.error.setText("Choose a reminder time in the future.")
            return
        self.accept()


class TaskPanel(QWidget):
    def __init__(self, submit):
        super().__init__()
        self.submit = submit
        box = QVBoxLayout(self)
        heading(box, "Tasks and calendar")
        panes = QHBoxLayout()
        self.calendar = QCalendarWidget()
        self.calendar.selectionChanged.connect(self.load_date)
        panes.addWidget(self.calendar)
        self.items = QListWidget()
        self.items.setAccessibleName("Saved tasks and reminders")
        panes.addWidget(self.items, 1)
        box.addLayout(panes, 1)
        ranges = QHBoxLayout()
        for label, value in (("Today", "today"), ("Tomorrow", "tomorrow"), ("Next 7 days", "week")):
            ranges.addWidget(button(label, lambda _=False, range_=value: self.submit("show_tasks", range=range_)))
        ranges.addWidget(button("Selected date", self.load_date))
        box.addLayout(ranges)
        actions = QHBoxLayout()
        for label, callback in (
            ("Add", self.add), ("Mark done", lambda: self.change("complete_item")),
            ("Snooze 10 min", lambda: self.change("snooze", minutes=10)),
            ("Reschedule", self.reschedule), ("Review deletion", lambda: self.change("delete_item")),
        ):
            actions.addWidget(button(label, callback))
        box.addLayout(actions)
        self.status = feedback()
        self.status.setText("Choose a date or load your upcoming tasks.")
        box.addWidget(self.status)

    def load_date(self):
        self.submit("task_calendar", day=self.calendar.selectedDate().toPython())

    def add(self):
        dialog = TaskDialog(self, self.calendar.selectedDate().toPython())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            kind = dialog.kind.currentText().lower()
            self.submit("create_" + kind, kind=kind, title=dialog.title.text(), due_at=dialog.due.dateTime().toPython())

    def current(self):
        row = self.items.currentItem()
        if row is None:
            self.status.setText("Select a task or reminder.")
            return None
        return row.data(Qt.ItemDataRole.UserRole)

    def change(self, name, **details):
        item = self.current()
        if item:
            self.submit(name, id=item.id, kind=item.type, **details)

    def reschedule(self):
        item = self.current()
        if item:
            dialog = TaskDialog(self, self.calendar.selectedDate().toPython(), item)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.submit("reschedule", id=item.id, due_at=dialog.due.dateTime().toPython())

    def show_reply(self, reply):
        if "items" in reply.data:
            self.items.clear()
            now = datetime.now()
            for item in reply.data["items"]:
                row = QListWidgetItem(describe(item, now))
                row.setData(Qt.ItemDataRole.UserRole, item)
                self.items.addItem(row)
            self.status.setText(f"{self.items.count()} task or reminder records")
        elif reply.kind != "confirm":
            self.status.setText(reply.text)
