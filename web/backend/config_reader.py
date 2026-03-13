"""
Config reader/writer — reads and updates strategy config at runtime.
Phase 2: update config without restarting bot, with validation.
"""

import json
import copy
import importlib
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Add project root for config import
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Sentinel for distinguishing "key not found" from "value is None/0/False"
_MISSING = object()


# ── Validation Rules ──────────────────────────────────────────────
# Each rule: (min, max) or None for no validation
# Nested dicts use dotted keys: "h1.tp1_roi"

VALIDATION_RULES: dict[str, dict[str, tuple[float, float] | None]] = {
    "CHANDELIER_EXIT": {
        "enabled": None,  # bool, no range
        "period": (1, 500),
        "multiplier": (0.1, 10.0),
    },
    "RISK_MANAGEMENT": {
        "fixed_margin": (10, 50000),
        "hard_sl_percent": (1, 100),
        "max_positions_per_pair": (1, 10),
        "max_ema610_h1_positions": (0, 50),
        "ema610_margin_multiplier": (0.5, 10),
        "ema610_h4_margin_multiplier": (0.5, 10),
        "max_total_positions": (1, 100),
        "min_balance_to_trade": (10, 100000),
        "max_equity_usage_pct": (10, 100),
    },
    "TRAILING_SL": {
        "enabled": None,
        "ema_period": (1, 500),
        "timeframe": None,  # string, no range
    },
    "SMART_SL": {
        "enabled": None,
        "volume_avg_period": (1, 200),
        "volume_threshold_pct": (1, 200),
        "ema_safety_period": (1, 1000),
        "hard_sl_on_ema_break": None,
    },
    "EMA610_EXIT": {
        "h1.tp1_roi": (1, 500),
        "h1.tp2_roi": (1, 500),
        "h1.hard_sl_roi": (1, 500),
        "h1.tp1_percent": (1, 100),
        "h4.tp1_roi": (1, 500),
        "h4.tp2_roi": (1, 500),
        "h4.hard_sl_roi": (1, 500),
        "h4.tp1_percent": (1, 100),
    },
    "STANDARD_EXIT": {
        "m5.tp1_roi": (1, 500),
        "m5.tp2_roi": (1, 500),
        "m5.hard_sl_roi": (1, 500),
        "m5.tp1_percent": (1, 100),
        "m15.tp1_roi": (1, 500),
        "m15.tp2_roi": (1, 500),
        "m15.hard_sl_roi": (1, 500),
        "m15.tp1_percent": (1, 100),
        "h1.tp1_roi": (1, 500),
        "h1.tp2_roi": (1, 500),
        "h1.hard_sl_roi": (1, 500),
        "h1.tp1_percent": (1, 100),
        "h4.tp1_roi": (1, 500),
        "h4.tp2_roi": (1, 500),
        "h4.hard_sl_roi": (1, 500),
        "h4.tp1_percent": (1, 100),
    },
    "RSI_DIV_EXIT": {
        "m15.enabled": None,
        "m15.tp1_roi": (1, 500),
        "m15.tp2_roi": (1, 500),
        "m15.hard_sl_roi": (1, 500),
        "m15.tp1_percent": (1, 100),
        "m15.leverage_multiplier": (1.0, 5.0),
        "h1.enabled": None,
        "h1.tp1_roi": (1, 500),
        "h1.tp2_roi": (1, 500),
        "h1.hard_sl_roi": (1, 500),
        "h1.tp1_percent": (1, 100),
        "h1.leverage_multiplier": (1.0, 5.0),
        "h4.enabled": None,
        "h4.tp1_roi": (1, 500),
        "h4.tp2_roi": (1, 500),
        "h4.hard_sl_roi": (1, 500),
        "h4.tp1_percent": (1, 100),
        "h4.leverage_multiplier": (1.0, 5.0),
    },
    "EMA610_ENTRY": {
        "enabled": None,
        "period": (1, 1000),
        "tolerance": (0.0001, 0.1),
        "timeframe": None,
    },
    "DIVERGENCE_CONFIG": {
        "enabled": None,
        "h1_lookback": (20, 500),
        "h4_lookback": (10, 200),
        "min_swing_distance": (2, 30),
        "swing_window": (2, 10),
        "max_swing_pairs": (1, 10),
        "min_retracement_pct": (0.1, 10.0),
        "m15_scan_enabled": None,
        "m15_lookback": (50, 500),
        "m15_div_cooldown_minutes": (5, 240),
        "h1_div_cooldown_minutes": (15, 480),
        "h4_div_cooldown_minutes": (60, 1440),
        "scan_top_n": (10, 1000),
        "scan_interval": (600, 86400),
        "d1_lookback": (10, 200),
    },
    "INDICATORS": {
        "ema_fast": (1, 500),
        "ema_slow": (1, 500),
        "rsi_period": (1, 200),
        "wick_threshold": (1, 100),
    },
    "TIMEFRAMES": {
        "trend": None,
        "filter": None,
        "entry": None,
    },
    "STANDARD_ENTRY": {
        "m5.enabled": None,
        "m5.tolerance": (0, 0.02),
        "m15.enabled": None,
        "m15.tolerance": (0, 0.02),
        "h1.enabled": None,
        "h1.tolerance": (0, 0.02),
        "h4.enabled": None,
        "h4.tolerance": (0, 0.02),
    },
    "ENTRY": {
        "wick_threshold": (1, 100),
        "rsi_overbought": (50, 100),
        "rsi_oversold": (0, 50),
        "adx_threshold": (0, 50),
        "adx_period": (5, 50),
        "enable_ema610_h1": None,
        "enable_ema610_h4": None,
    },
    "SD_ENTRY_CONFIG": {
        "enabled": None,
        "wick_ratio_min": (0.2, 0.9),
        "volume_multiplier": (0.5, 3.0),
        "volume_ma_period": (5, 100),
        "m15.enabled": None,
        "m15.tp1_roi": (1, 500),
        "m15.tp2_roi": (1, 500),
        "m15.hard_sl_roi": (1, 500),
        "m15.tp1_percent": (1, 100),
        "h1.enabled": None,
        "h1.tp1_roi": (1, 500),
        "h1.tp2_roi": (1, 500),
        "h1.hard_sl_roi": (1, 500),
        "h1.tp1_percent": (1, 100),
        "h4.enabled": None,
        "h4.tp1_roi": (1, 500),
        "h4.tp2_roi": (1, 500),
        "h4.hard_sl_roi": (1, 500),
        "h4.tp1_percent": (1, 100),
    },
    "FEES": {
        "maker": (0, 0.01),
        "taker": (0, 0.01),
    },
    "DYNAMIC_PAIRS": {
        "enabled": None,
        "volume_windows.24h": (0, 200),
        "volume_windows.48h": (0, 200),
        "volume_windows.72h": (0, 200),
        "refresh_interval": (60, 86400),
        "whitelist": None,  # list, skip range
        "blacklist": None,  # list, skip range
    },
    # LEVERAGE: dynamic keys (symbol -> int), validated specially
    "LEVERAGE": "__dynamic_leverage__",
}

# Leverage range limits
LEVERAGE_RANGE = (1, 125)

# Fields that should be displayed as read-only on the frontend
READONLY_FIELDS: dict[str, set[str]] = {
    "TIMEFRAMES": {"trend", "filter", "entry"},
}


def _validate_value(section: str, key: str, value: Any) -> str | None:
    """
    Validate a single config value against rules.
    Returns error message string or None if valid.
    """
    rules = VALIDATION_RULES.get(section, {})
    if not isinstance(rules, dict):
        return None  # dynamically-validated section
    rule = rules.get(key)

    if rule is None:
        # No range validation (bool, string, list)
        return None

    min_val, max_val = rule
    if not isinstance(value, (int, float)):
        return f"{section}.{key}: expected number, got {type(value).__name__}"

    if value < min_val or value > max_val:
        return f"{section}.{key}: value {value} out of range [{min_val}, {max_val}]"

    return None


def _get_nested_value(config_dict: dict, key: str, default=_MISSING) -> Any:
    """Get value from potentially nested dict using dotted key.
    Returns _MISSING sentinel if key not found (distinguishes from None/0/False).
    """
    parts = key.split(".")
    current = config_dict
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _set_nested_value(config_dict: dict, key: str, value: Any) -> None:
    """Set value in potentially nested dict using dotted key."""
    parts = key.split(".")
    current = config_dict
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value


class ConfigReader:
    """Read and update trading strategy configuration."""

    SECTIONS = [
        "LEVERAGE", "FEES", "RISK_MANAGEMENT",
        "TRAILING_SL", "CHANDELIER_EXIT", "SMART_SL",
        "STANDARD_ENTRY", "STANDARD_EXIT", "EMA610_EXIT", "EMA610_ENTRY",
        "RSI_DIV_EXIT", "DIVERGENCE_CONFIG", "SD_ENTRY_CONFIG",
        "INDICATORS", "TIMEFRAMES", "ENTRY", "DYNAMIC_PAIRS",
    ]

    def __init__(self):
        self._config_module = None
        self._defaults: dict[str, Any] = {}
        self._config_file = PROJECT_ROOT / "data" / "config.json"
        self._load_config()

    def _load_config(self):
        """Import/reimport the config module and apply overrides from JSON."""
        try:
            if "src.trading.futures.config" in sys.modules:
                self._config_module = importlib.reload(
                    sys.modules["src.trading.futures.config"]
                )
            else:
                from src.trading.futures import config
                self._config_module = config

            # Snapshot defaults on first load
            if not self._defaults:
                self._snapshot_defaults()
            
            # Apply overrides from JSON file if it exists
            self._apply_json_overrides()
            
        except ImportError as e:
            logger.error(f"Cannot import config: {e}")
            self._config_module = None

    def _apply_json_overrides(self):
        """Load config.json and apply values to the in-memory config module."""
        if not self._config_file.exists():
            return

        try:
            with open(self._config_file, 'r') as f:
                saved_config = json.load(f)
            
            if not isinstance(saved_config, dict):
                logger.error("config.json is not a dictionary")
                return

            # Apply saved values to the current config module
            count = 0
            for section, values in saved_config.items():
                if not hasattr(self._config_module, section):
                    continue
                
                # Get the target section dict from the module
                module_section = getattr(self._config_module, section)
                if not isinstance(module_section, dict):
                    continue
                    
                # Update recursively/deeply
                # For simplicity in this structure, top-level sections are dicts 
                # and we can just update them, but let's be careful about nested keys if needed.
                # Since the config structure is section -> key -> value, a simple update might lose keys 
                # if we replaced the whole dict. But here we should probably update keys.
                
                # Actually, the saved config should be the full state of that section ideally, 
                # or at least the diff. But to be safe, let's update individual keys 
                # to preserve any new defaults that might have been added to code but not in JSON.
                
                # But wait, if user deletes a key in JSON (not possible via UI usually), 
                # we might want to respect that? No, UI sends full updates usually or partials.
                # Let's assume JSON contains the "desired state" for modified sections.
                
                # We'll traverse and update leaf nodes to preserve structure
                self._recursive_update(module_section, values)
                count += 1
                
            logger.info(f"Applied config overrides from {self._config_file} ({count} sections)")
            
        except Exception as e:
            logger.error(f"Failed to load config.json: {e}")

    def _recursive_update(self, target: dict, source: dict):
        """Recursively update target dict with source values.

        Only updates keys that already exist in target (from config.py defaults).
        Stale keys in config.json that were removed from config.py are skipped.
        """
        for k, v in source.items():
            if k not in target:
                logger.debug(f"Skipping stale config key: {k}")
                continue
            if isinstance(v, dict) and isinstance(target[k], dict):
                self._recursive_update(target[k], v)
            else:
                target[k] = v

    def _snapshot_defaults(self):
        """Save a deep copy of initial config as defaults."""
        if not self._config_module:
            return
        for section in self.SECTIONS:
            val = getattr(self._config_module, section, None)
            if val is not None:
                self._defaults[section] = copy.deepcopy(val)

    def get_all(self) -> dict:
        """Get all config sections as a dict (deep copy)."""
        if not self._config_module:
            return {}

        result = {}
        for section in self.SECTIONS:
            val = getattr(self._config_module, section, None)
            if val is not None:
                result[section] = copy.deepcopy(val)
        return result

    def get_defaults(self) -> dict:
        """Get the original default config values."""
        return copy.deepcopy(self._defaults)

    def get_validation_rules(self) -> dict:
        """Get validation rules for frontend to use."""
        result = {}
        for section, rules in VALIDATION_RULES.items():
            # Skip dynamically-validated sections (e.g., LEVERAGE)
            if not isinstance(rules, dict):
                continue
            section_rules = {}
            for key, rule in rules.items():
                if rule is not None:
                    section_rules[key] = {"min": rule[0], "max": rule[1]}
                else:
                    section_rules[key] = None
            result[section] = section_rules
        return result

    def update(self, updates: dict) -> dict:
        """
        Update config values at runtime with validation.

        Uses copy-validate-apply pattern:
        1. Deep copy current config
        2. Validate all changes against rules
        3. Only apply if ALL validations pass

        Args:
            updates: Dict of {section: {key: value}} to update.

        Returns:
            Result dict with success status, changed values, and errors.
        """
        if not self._config_module:
            return {"success": False, "error": "Config module not loaded"}

        # Phase 1: Validate all changes first (no mutation)
        changed = {}
        errors = []
        pending_changes: list[tuple[str, str, Any, Any]] = []  # (section, key, old, new)

        # Track LEVERAGE full-replacement separately
        leverage_replacement: dict | None = None

        for section, values in updates.items():
            config_dict = getattr(self._config_module, section, None)
            if config_dict is None:
                errors.append(f"Unknown config section: {section}")
                continue
            if not isinstance(config_dict, dict):
                errors.append(f"Section {section} is not a dict, cannot update")
                continue
            if not isinstance(values, dict):
                errors.append(f"Values for {section} must be a dict")
                continue

            # ── LEVERAGE: dynamic keys, full replacement ──
            if section == "LEVERAGE":
                min_lev, max_lev = LEVERAGE_RANGE
                lev_errors = []
                new_leverage = {}
                for sym, lev_val in values.items():
                    sym_upper = sym.upper()
                    if not isinstance(lev_val, (int, float)):
                        lev_errors.append(f"LEVERAGE.{sym_upper}: expected number, got {type(lev_val).__name__}")
                        continue
                    lev_int = int(lev_val)
                    if lev_int < min_lev or lev_int > max_lev:
                        lev_errors.append(f"LEVERAGE.{sym_upper}: {lev_int} out of range [{min_lev}, {max_lev}]")
                        continue
                    new_leverage[sym_upper] = lev_int

                if "DEFAULT" not in new_leverage and "default" not in new_leverage:
                    lev_errors.append("LEVERAGE must have a 'default' key")

                # Normalize "DEFAULT" → "default"
                if "DEFAULT" in new_leverage:
                    new_leverage["default"] = new_leverage.pop("DEFAULT")

                if lev_errors:
                    errors.extend(lev_errors)
                else:
                    leverage_replacement = new_leverage
                continue

            # ── Standard sections ──
            # Check readonly fields
            readonly = READONLY_FIELDS.get(section, set())

            for key, new_val in values.items():
                # Block readonly fields
                if key in readonly:
                    errors.append(f"{section}.{key} is read-only")
                    continue

                # Handle nested keys (e.g., "h1.tp1_roi")
                old_val = _get_nested_value(config_dict, key)
                if old_val is _MISSING:
                    errors.append(f"Unknown key {section}.{key}")
                    continue

                # Type check: new value must match old value type
                if not isinstance(new_val, type(old_val)):
                    if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
                        new_val = type(old_val)(new_val)
                    elif isinstance(old_val, bool) and isinstance(new_val, bool):
                        pass  # bool ok
                    else:
                        errors.append(
                            f"Type mismatch for {section}.{key}: "
                            f"expected {type(old_val).__name__}, got {type(new_val).__name__}"
                        )
                        continue

                # Range validation
                validation_error = _validate_value(section, key, new_val)
                if validation_error:
                    errors.append(validation_error)
                    continue

                # Skip if value unchanged
                if old_val == new_val:
                    continue

                pending_changes.append((section, key, old_val, new_val))

        # Phase 2: If any validation errors, abort ALL changes
        if errors:
            return {
                "success": False,
                "changed": {},
                "errors": errors,
            }

        # Phase 3: Atomic apply via copy-then-swap
        # Deep copy affected sections, apply changes to copies, then swap atomically
        sections_to_update: dict[str, Any] = {}
        for section, key, old_val, new_val in pending_changes:
            if section not in sections_to_update:
                sections_to_update[section] = copy.deepcopy(
                    getattr(self._config_module, section)
                )
            _set_nested_value(sections_to_update[section], key, new_val)
            logger.info(f"Config updated: {section}.{key} = {old_val} -> {new_val}")

            if section not in changed:
                changed[section] = {}
            changed[section][key] = {"old": old_val, "new": new_val}

        # Apply LEVERAGE full replacement if present
        if leverage_replacement is not None:
            old_leverage = copy.deepcopy(getattr(self._config_module, "LEVERAGE"))
            sections_to_update["LEVERAGE"] = leverage_replacement
            changed["LEVERAGE"] = {"__full__": {"old": old_leverage, "new": leverage_replacement}}
            logger.info(f"Config updated: LEVERAGE (full replacement, {len(leverage_replacement)} symbols)")

        # Swap all sections atomically (single-threaded, so this is safe)
        for section, new_dict in sections_to_update.items():
            setattr(self._config_module, section, new_dict)

        # Phase 4: Persist to JSON
        self._save_to_file()

        return {
            "success": True,
            "changed": changed,
            "errors": [],
        }

    def _save_to_file(self):
        """Save current config state to JSON file."""
        try:
            current_config = self.get_all()
            
            # Ensure data directory exists
            self._config_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self._config_file, 'w') as f:
                json.dump(current_config, f, indent=2)
                
            logger.info(f"Config saved to {self._config_file}")
        except Exception as e:
            logger.error(f"Failed to save config to file: {e}")

    def reset_to_defaults(self, sections: list[str] | None = None) -> dict:
        """
        Reset config sections to their original default values.

        Args:
            sections: List of section names to reset. None = reset all.

        Returns:
            Result dict with success status and reset sections.
        """
        if not self._config_module:
            return {"success": False, "error": "Config module not loaded"}
        if not self._defaults:
            return {"success": False, "error": "No defaults saved"}

        target_sections = sections if sections else list(self._defaults.keys())
        reset_sections = []

        for section in target_sections:
            if section not in self._defaults:
                continue
            default_val = copy.deepcopy(self._defaults[section])
            setattr(self._config_module, section, default_val)
            reset_sections.append(section)
            logger.info(f"Config reset to defaults: {section}")

            logger.info(f"Config reset to defaults: {section}")

        # Update the file to reflect resets
        self._save_to_file()

        return {
            "success": True,
            "reset_sections": reset_sections,
        }
