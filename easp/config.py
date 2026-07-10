from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


CONFIG_PATH = Path("config.json")


@dataclass
class Settings:
    theme: str = "light"

    @classmethod
    def load(cls, path: Path | str = CONFIG_PATH) -> "Settings":
        config_path = Path(path)
        if not config_path.exists():
            settings = cls()
            settings.save(config_path)
            return settings

        data = json.loads(config_path.read_text(encoding="utf-8"))
        theme = str(data.get("theme", "light")).lower()
        if theme not in {"dark", "light"}:
            theme = "light"
        return cls(theme=theme)

    def save(self, path: Path | str = CONFIG_PATH) -> None:
        payload = {"theme": self.theme}
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
