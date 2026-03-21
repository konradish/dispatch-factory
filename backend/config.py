"""Configuration loader for dispatch-factory backend."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class TerminalConfig:
    enabled: bool = False
    ttyd_bin: str = "ttyd"
    port_range_start: int = 7681
    port_range_end: int = 7700
    read_only: bool = True


@dataclass
class Config:
    artifacts_dir: str = "~/.local/share/dispatch"
    dispatch_bin: str = "~/.local/bin/dispatch"
    host: str = "127.0.0.1"
    port: int = 8420
    enable_controls: bool = False
    terminal: TerminalConfig = field(default_factory=TerminalConfig)

    def __post_init__(self) -> None:
        self.artifacts_dir = str(Path(self.artifacts_dir).expanduser())
        self.dispatch_bin = str(Path(self.dispatch_bin).expanduser())


def _find_config_file() -> Path | None:
    """Look for config in project root first, then home directory."""
    candidates = [
        Path(__file__).parent.parent / ".dispatch-factory.toml",
        Path.home() / ".dispatch-factory.toml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_config() -> Config:
    """Load config from TOML file, falling back to defaults."""
    path = _find_config_file()
    if path is None:
        return Config()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    terminal_raw = raw.pop("terminal", {})
    terminal = TerminalConfig(**{
        k: v for k, v in terminal_raw.items()
        if k in TerminalConfig.__dataclass_fields__
    })

    known_fields = Config.__dataclass_fields__.keys() - {"terminal"}
    cfg_kwargs = {k: v for k, v in raw.items() if k in known_fields}
    return Config(**cfg_kwargs, terminal=terminal)


# Module-level singleton — import this from other modules.
settings = load_config()
