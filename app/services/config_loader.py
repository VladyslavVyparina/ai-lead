"""Versioned configuration loader (§2.3).

Configs live in config/<domain>/<version>.yaml and are immutable once
published: to change behaviour, publish a new version file and point the
`active` marker at it. Historical runs keep the version string they used.
"""

import copy
from typing import Optional
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import CONFIG_DIR

DOMAINS = ("markets", "scoring", "pricebook", "messaging", "templates")


class ConfigNotFound(Exception):
    pass


def _domain_dir(domain: str) -> Path:
    path = CONFIG_DIR / domain
    if not path.is_dir():
        raise ConfigNotFound(f"Unknown config domain: {domain}")
    return path


def active_version(domain: str) -> str:
    marker = _domain_dir(domain) / "ACTIVE"
    if not marker.exists():
        raise ConfigNotFound(f"No ACTIVE marker for domain {domain}")
    return marker.read_text().strip()


@lru_cache(maxsize=64)
def _load_cached(domain: str, version: str) -> dict:
    path = _domain_dir(domain) / f"{version}.yaml"
    if not path.exists():
        raise ConfigNotFound(f"Config {domain}/{version} not found")
    data = yaml.safe_load(path.read_text())
    data["_version"] = version
    return data


def load(domain: str, version: Optional[str] = None) -> dict:
    """Returns a deep copy so callers can never mutate the published version."""
    version = version or active_version(domain)
    return copy.deepcopy(_load_cached(domain, version))


def list_versions(domain: str) -> list[str]:
    return sorted(p.stem for p in _domain_dir(domain).glob("*.yaml"))


def set_active(domain: str, version: str) -> None:
    """Publish/rollback: point the domain at an existing immutable version."""
    if version not in list_versions(domain):
        raise ConfigNotFound(f"Cannot activate missing config {domain}/{version}")
    (_domain_dir(domain) / "ACTIVE").write_text(version + "\n")
    _load_cached.cache_clear()
