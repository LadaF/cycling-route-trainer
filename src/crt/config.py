"""User settings: devices, rider and control parameters, kept in a TOML file."""

from __future__ import annotations

import dataclasses
import logging
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import platformdirs
import tomli_w

from crt.control.policy import ControlSettings
from crt.physics.model import RiderParams

log = logging.getLogger(__name__)


def settings_path() -> Path:
    return Path(platformdirs.user_config_dir("crt")) / "settings.toml"


def _known(cls, values: dict) -> dict:
    """Keep only the fields ``cls`` has, so old or hand-edited files still load."""
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in values.items() if k in names}


@dataclass
class Settings:
    trainer: str = ""  # name or address
    heart_rate: str = ""  # name or address; empty: no HR sensor
    course: str = ""  # the last course ridden or opened
    rider: RiderParams = field(default_factory=RiderParams)
    control: ControlSettings = field(default_factory=ControlSettings)

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or settings_path()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, tomllib.TOMLDecodeError) as exc:
            log.warning("ignoring unreadable settings %s: %s", path, exc)
            return cls()
        top = _known(cls, data)
        top["rider"] = RiderParams(**_known(RiderParams, data.get("rider", {})))
        top["control"] = ControlSettings(**_known(ControlSettings, data.get("control", {})))
        return cls(**top)

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomli_w.dumps(asdict(self)), encoding="utf-8")
