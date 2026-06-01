"""Smoke test: verify project scaffold and core dependencies are importable."""


def test_imports() -> None:
    """All base dependencies must be importable."""
    import requests  # noqa: F401
    import pandas  # noqa: F401
    import pydantic  # noqa: F401
    import yaml  # noqa: F401
    import httpx  # noqa: F401
    import bs4  # noqa: F401
    import lxml  # noqa: F401
    import pytest  # noqa: F401


def test_directory_structure() -> None:
    """Required directories must exist."""
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent
    required = [
        "config",
        "adapters",
        "engine",
        "ledgers",
        "memory",
        "reports",
        "data",
        "scripts",
        "tests",
        "data/raw",
        "data/normalized",
        "data/snapshots",
    ]
    for d in required:
        assert (base / d).is_dir(), f"Missing directory: {d}"


def test_pyproject_exists() -> None:
    """pyproject.toml must exist at project root."""
    from pathlib import Path

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert pyproject.is_file(), "pyproject.toml not found"


def test_git_repo_initialized() -> None:
    """Git repo must be initialized."""
    from pathlib import Path

    git_dir = Path(__file__).resolve().parent.parent / ".git"
    assert git_dir.is_dir(), ".git directory not found"
