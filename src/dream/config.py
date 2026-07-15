"""DREAM runtime configuration loaded from environment variables."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable

from dream.curators.llm_backend import OpenAICuratorBackend, SemanticCuratorBackend
from dream.review.backend import DeterministicReviewBackend, ReviewBackend
from dream.review.llm_backend import OpenAIReviewBackend


@dataclass(frozen=True)
class DreamSettings:
    home: str = "~/.dream"
    review_backend: str = "deterministic"
    review_model: str = ""
    review_base_url: str | None = None
    review_api_key: str = ""
    review_max_completion_tokens: int = 2000
    curator_backend: str = "inherit"
    curator_model: str = ""
    curator_base_url: str | None = None
    curator_api_key: str = ""
    curator_max_completion_tokens: int = 3000


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid .env entry at line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"invalid .env key at line {line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _positive_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be positive")
    return parsed


def load_settings(path: Path | None = None) -> DreamSettings:
    """Load settings from a dotenv file, with process environment taking priority."""

    file_values = _read_env_file(path) if path is not None else {}

    def value(name: str, default: str = "") -> str:
        return os.environ.get(name, file_values.get(name, default)).strip()

    review_backend = value("DREAM_REVIEW_BACKEND", "deterministic").lower()
    if review_backend not in {"deterministic", "openai"}:
        raise ValueError("DREAM_REVIEW_BACKEND must be deterministic or openai")

    curator_backend = value("DREAM_CURATOR_BACKEND", "inherit").lower()
    if curator_backend not in {"inherit", "deterministic", "openai"}:
        raise ValueError(
            "DREAM_CURATOR_BACKEND must be inherit, deterministic, or openai"
        )

    return DreamSettings(
        home=value("DREAM_HOME", "~/.dream"),
        review_backend=review_backend,
        review_model=value("DREAM_REVIEW_MODEL"),
        review_base_url=value("DREAM_REVIEW_BASE_URL") or None,
        review_api_key=value("DREAM_LLM_API_KEY"),
        review_max_completion_tokens=_positive_int(
            value("DREAM_REVIEW_MAX_COMPLETION_TOKENS", "2000"),
            "DREAM_REVIEW_MAX_COMPLETION_TOKENS",
        ),
        curator_backend=curator_backend,
        curator_model=value("DREAM_CURATOR_MODEL"),
        curator_base_url=value("DREAM_CURATOR_BASE_URL") or None,
        curator_api_key=value("DREAM_CURATOR_LLM_API_KEY"),
        curator_max_completion_tokens=_positive_int(
            value("DREAM_CURATOR_MAX_COMPLETION_TOKENS", "3000"),
            "DREAM_CURATOR_MAX_COMPLETION_TOKENS",
        ),
    )


def build_review_backend(
    settings: DreamSettings,
    *,
    client_factory: Callable[..., object] | None = None,
) -> ReviewBackend:
    if settings.review_backend == "deterministic":
        return DeterministicReviewBackend()
    if not settings.review_model:
        raise ValueError("DREAM_REVIEW_MODEL is required for the openai backend")
    if not settings.review_api_key:
        raise ValueError("missing LLM API key: DREAM_LLM_API_KEY")
    if client_factory is None:
        from openai import OpenAI

        client_factory = OpenAI
    client_kwargs: dict[str, object] = {"api_key": settings.review_api_key}
    if settings.review_base_url:
        client_kwargs["base_url"] = settings.review_base_url
    client = client_factory(**client_kwargs)
    return OpenAIReviewBackend(
        client=client,
        model=settings.review_model,
        max_completion_tokens=settings.review_max_completion_tokens,
    )


def build_curator_backend(
    settings: DreamSettings,
    *,
    client_factory: Callable[..., object] | None = None,
) -> SemanticCuratorBackend | None:
    backend = settings.curator_backend
    if backend == "deterministic":
        return None
    if backend == "inherit" and settings.review_backend == "deterministic":
        return None
    model = settings.curator_model or settings.review_model
    if not model:
        raise ValueError(
            "DREAM_CURATOR_MODEL or DREAM_REVIEW_MODEL is required for LLM curation"
        )
    api_key = settings.curator_api_key or settings.review_api_key
    if not api_key:
        raise ValueError(
            "missing LLM API key: DREAM_CURATOR_LLM_API_KEY or DREAM_LLM_API_KEY"
        )
    if client_factory is None:
        from openai import OpenAI

        client_factory = OpenAI
    base_url = settings.curator_base_url or settings.review_base_url
    client_kwargs: dict[str, object] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = client_factory(**client_kwargs)
    return OpenAICuratorBackend(
        client=client,
        model=model,
        max_completion_tokens=settings.curator_max_completion_tokens,
    )
