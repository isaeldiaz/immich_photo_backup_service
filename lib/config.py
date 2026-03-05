"""Configuration loader for Immich backup system."""

import json
from pathlib import Path
from typing import Any, Dict, Optional


class Config:
    """Read-only configuration with dot-notation access."""

    def __init__(self, config_file: str = "config.json"):
        self.config_file = Path(config_file)
        self._config: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if not self.config_file.exists():
            raise RuntimeError(f"Config file not found: {self.config_file}")
        try:
            with open(self.config_file, "r") as f:
                self._config = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            raise RuntimeError(f"Failed to load config from {self.config_file}: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value using dot notation (e.g., 'immich.api_url')."""
        value = self._config
        for k in key.split("."):
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def get_full_config(self) -> Dict[str, Any]:
        """Return a copy of the full config dict."""
        return self._config.copy()
