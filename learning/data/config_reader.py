"""Read-only access to bot configuration files."""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ConfigReader:
    """Read current bot config without modifying it."""

    def __init__(self, config_path: Path, overrides_path: Path | None = None):
        self._config_path = config_path
        self._overrides_path = overrides_path

    def _load_json(self, path: Path) -> dict:
        """Load JSON file, return empty dict on failure."""
        try:
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read %s: %s", path, e)
        return {}

    def load(self) -> dict:
        """Load config with overrides applied."""
        config = self._load_json(self._config_path)
        if self._overrides_path:
            overrides = self._load_json(self._overrides_path)
            config = {**config, **overrides}
        return config

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by dot-separated key path."""
        config = self.load()
        parts = key.split(".")
        current = config
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def get_exit_params(self) -> dict:
        """Get all exit parameters (TP/SL/CE) in a flat dict."""
        config = self.load()
        return {
            "standard_m15_tp1": config.get("STANDARD_EXIT", {}).get("m15", {}).get("tp1_roi", 20),
            "standard_m15_tp2": config.get("STANDARD_EXIT", {}).get("m15", {}).get("tp2_roi", 40),
            "standard_m15_hard_sl": config.get("STANDARD_EXIT", {}).get("m15", {}).get("hard_sl_roi", -20),
            "standard_h1_tp1": config.get("STANDARD_EXIT", {}).get("h1", {}).get("tp1_roi", 30),
            "standard_h1_tp2": config.get("STANDARD_EXIT", {}).get("h1", {}).get("tp2_roi", 60),
            "standard_h1_hard_sl": config.get("STANDARD_EXIT", {}).get("h1", {}).get("hard_sl_roi", -25),
            "standard_h4_tp1": config.get("STANDARD_EXIT", {}).get("h4", {}).get("tp1_roi", 50),
            "standard_h4_tp2": config.get("STANDARD_EXIT", {}).get("h4", {}).get("tp2_roi", 100),
            "standard_h4_hard_sl": config.get("STANDARD_EXIT", {}).get("h4", {}).get("hard_sl_roi", -40),
            "chandelier_period": config.get("CHANDELIER_EXIT", {}).get("period", 34),
            "chandelier_multiplier": config.get("CHANDELIER_EXIT", {}).get("multiplier", 1.75),
        }
