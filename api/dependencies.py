from __future__ import annotations

from functools import lru_cache

from shared.config import load_platform_config
from shared.data_service import BigQueryClientLike, get_bigquery_client


@lru_cache(maxsize=1)
def get_client() -> BigQueryClientLike:
    """Construct one ADC-backed client per API process."""

    config = load_platform_config()
    return get_bigquery_client(config.project, config.location)
