"""Map an adapter name in YAML to its implementation, and load configs."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Type

import yaml

from src.models import CompanyConfig
from src.sources.base import BaseSource
from src.sources.phenom import PhenomSource

ROOT = Path(__file__).resolve().parent.parent.parent

ADAPTERS: Dict[str, Type[BaseSource]] = {
    "phenom": PhenomSource,
}


def load_company_configs(directory: Path | str | None = None) -> List[CompanyConfig]:
    d = Path(directory) if directory else ROOT / "config" / "companies"
    configs: List[CompanyConfig] = []
    for path in sorted(d.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        known = {
            "name", "adapter", "enabled", "base_url", "ref_num", "locale_path",
            "lang", "poll_seconds", "filters", "keywords", "page_size",
            "fetch_details",
        }
        configs.append(
            CompanyConfig(
                name=data.get("name") or path.stem.title(),
                adapter=data.get("adapter") or "",
                base_url=data.get("base_url") or "",
                enabled=bool(data.get("enabled", True)),
                ref_num=data.get("ref_num") or "",
                locale_path=data.get("locale_path") or "global/en",
                lang=data.get("lang") or "en_global",
                poll_seconds=int(data.get("poll_seconds") or 300),
                filters=data.get("filters") or {},
                keywords=data.get("keywords") or "",
                page_size=int(data.get("page_size") or 100),
                fetch_details=bool(data.get("fetch_details", True)),
                extra={k: v for k, v in data.items() if k not in known},
            )
        )
    return configs


def build_source(config: CompanyConfig) -> BaseSource:
    cls = ADAPTERS.get(config.adapter)
    if cls is None:
        raise ValueError(
            f"Unknown adapter '{config.adapter}' for {config.name}. "
            f"Known adapters: {', '.join(sorted(ADAPTERS))}"
        )
    return cls(config)
