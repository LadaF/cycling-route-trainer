"""Settings dialog: devices, rider and bike, resistance control."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QVBoxLayout,
)

from crt.config import Settings


def _spin(value: float, lo: float, hi: float, step: float, decimals: int, suffix: str = ""):
    box = QDoubleSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setDecimals(decimals)
    box.setSuffix(suffix)
    box.setValue(value)
    return box


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._settings = settings
        r, c = settings.rider, settings.control

        self.trainer = QLineEdit(settings.trainer)
        self.trainer.setPlaceholderText("e.g. Hammer 50414")
        self.heart_rate = QLineEdit(settings.heart_rate)
        self.heart_rate.setPlaceholderText("empty: no heart rate sensor")
        devices = QGroupBox("Devices (name or address)")
        form = QFormLayout(devices)
        form.addRow("Trainer", self.trainer)
        form.addRow("Heart rate", self.heart_rate)

        self.rider_mass = _spin(r.rider_mass, 30, 200, 0.5, 1, " kg")
        self.bike_mass = _spin(r.bike_mass, 3, 40, 0.1, 1, " kg")
        self.cda = _spin(r.cda, 0.15, 0.8, 0.01, 3, " m²")
        self.crr = _spin(r.crr, 0.001, 0.02, 0.0005, 4)
        self.temperature = _spin(r.temperature_c, -20, 45, 1, 0, " °C")
        self.max_speed = _spin(r.max_speed * 3.6, 20, 120, 5, 0, " km/h")
        rider = QGroupBox("Rider and bike")
        form = QFormLayout(rider)
        form.addRow("Rider mass", self.rider_mass)
        form.addRow("Bike mass", self.bike_mass)
        form.addRow("Drag area CdA", self.cda)
        form.addRow("Rolling resistance Crr", self.crr)
        form.addRow("Air temperature", self.temperature)
        form.addRow("Maximum speed (braking)", self.max_speed)

        self.min_grade = _spin(c.min_grade, -5, 10, 0.5, 1, " %")
        self.difficulty = _spin(c.difficulty * 100, 0, 100, 5, 0, " %")
        self.lookahead = _spin(c.lookahead_s, 0, 10, 0.5, 1, " s")
        control = QGroupBox("Resistance")
        form = QFormLayout(control)
        form.addRow("Minimum grade", self.min_grade)
        form.addRow("Difficulty (share of the grade)", self.difficulty)
        form.addRow("Lookahead", self.lookahead)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for widget in (devices, rider, control, buttons):
            layout.addWidget(widget)

    def result_settings(self) -> Settings:
        s = self._settings
        return replace(
            s,
            trainer=self.trainer.text().strip(),
            heart_rate=self.heart_rate.text().strip(),
            rider=replace(
                s.rider, rider_mass=self.rider_mass.value(), bike_mass=self.bike_mass.value(),
                cda=self.cda.value(), crr=self.crr.value(),
                temperature_c=self.temperature.value(), max_speed=self.max_speed.value() / 3.6,
            ),
            control=replace(
                s.control, min_grade=self.min_grade.value(),
                difficulty=self.difficulty.value() / 100, lookahead_s=self.lookahead.value(),
            ),
        )  # fmt: skip
