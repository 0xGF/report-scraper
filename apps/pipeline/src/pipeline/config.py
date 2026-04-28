"""Typed settings loaded from environment / .env.

Every env var is declared here with a type and a default — fail fast if the
shape is wrong, and give one place to look when something's misconfigured.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = Field(default="", description="Required for classifier + normalizer")
    openai_classifier_model: str = "gpt-4o-mini"
    openai_normalizer_model: str = "gpt-4o-mini"
    # Discovery wants strong factual recall (tickers, exchange codes, IR URL
    # patterns), so we default to a flagship model. Override with
    # `OPENAI_DISCOVER_MODEL=gpt-4o` (or whatever your account has access to)
    # if `gpt-5` isn't available to you.
    openai_discover_model: str = "gpt-5"
    # Vision-LLM model for the VLM extractor — reads PDF page images
    # directly. gpt-5 vision is the production-grade choice; gpt-4o(-vision)
    # is a cheaper fallback if you'd rather pay less per page.
    openai_vlm_model: str = "gpt-5"
    # Label-drift mapper specifically — gpt-4o-mini is too conservative on
    # synonyms (mapping `"Operational profit"` ↔ `"Operating profit"`).
    # gpt-5 handles this reliably. ~5-10 calls per company total, low cost.
    openai_drift_model: str = "gpt-5"

    # Web search for closed-loop PDF discovery. When set, `report-scrape discover`
    # uses Tavily to surface real annual-report PDF URLs and populates
    # `direct_urls` automatically — works on age-gated / JS-rendered IR
    # sites where the cascade scraper can't crawl. Free tier is enough for
    # a few companies. Get a key at https://app.tavily.com/.
    tavily_api_key: str = Field(default="", description="Optional, enables PDF-grounding")

    report_scrape_data_dir: Path = _REPO_ROOT / "data"
    report_scrape_raw_dir: Path = _REPO_ROOT / "data" / "raw"
    report_scrape_cache_dir: Path = _REPO_ROOT / "data" / "cache"
    report_scrape_exports_dir: Path = _REPO_ROOT / "data" / "exports"
    report_scrape_duckdb_path: Path = _REPO_ROOT / "data" / "report-scrape.duckdb"

    log_level: str = "INFO"
    log_format: str = "console"

    scraper_user_agent: str = "report-scrape/0.1 (+contact: local)"
    scraper_timeout: int = 30
    scraper_max_retries: int = 3

    def ensure_dirs(self) -> None:
        for p in (
            self.report_scrape_data_dir,
            self.report_scrape_raw_dir,
            self.report_scrape_cache_dir,
            self.report_scrape_exports_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
