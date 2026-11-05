"""Load ``data/seed/dataset.yaml`` into the strict models."""

from functools import lru_cache
from pathlib import Path

import yaml

from faktur_beres.config import REPO_ROOT
from faktur_beres.dataset.models import Dataset

DATASET_PATH = REPO_ROOT / "data" / "seed" / "dataset.yaml"


def load_dataset(path: Path | str = DATASET_PATH) -> Dataset:
    """Parse and validate the dataset. Raises ``pydantic.ValidationError`` on bad data."""
    with Path(path).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Dataset.model_validate(raw)


@lru_cache
def get_dataset() -> Dataset:
    """Return the cached dataset from the default location."""
    return load_dataset()
