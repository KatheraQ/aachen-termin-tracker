from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class Anliegen:
    id: str
    label_de: str
    label_zh: str


@dataclass
class AppCfg:
    base_url: str
    secret_key: str
    host: str
    port: int


@dataclass
class ScraperCfg:
    target_url: str
    poll_interval_min_seconds: int
    poll_interval_max_seconds: int
    headless: bool
    user_agent: str
    behoerde_label: str


@dataclass
class SmtpCfg:
    host: str
    port: int
    username: str
    password: str
    from_address: str


@dataclass
class LegalImpressum:
    name: str
    address: str
    email: str


@dataclass
class LegalCfg:
    impressum: LegalImpressum
    datenschutz_contact: str


@dataclass
class Config:
    app: AppCfg
    scraper: ScraperCfg
    smtp: SmtpCfg
    anliegen: List[Anliegen]
    legal: LegalCfg


def load_config(path: Path = CONFIG_PATH) -> Config:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Config(
        app=AppCfg(**raw["app"]),
        scraper=ScraperCfg(**raw["scraper"]),
        smtp=SmtpCfg(**raw["smtp"]),
        anliegen=[Anliegen(**a) for a in raw["anliegen"]],
        legal=LegalCfg(
            impressum=LegalImpressum(**raw["legal"]["impressum"]),
            datenschutz_contact=raw["legal"]["datenschutz_contact"],
        ),
    )
