from __future__ import annotations

import atexit
import os
import sys
import tempfile
from pathlib import Path

import pytest


_PROTECTED_ENV = ("OKX_ADVISOR_DB", "OKX_DISABLE_NETWORK", "OKX_TEST_MODE", "OKX_PYTEST_DB")
_ORIGINAL_ENV = {
    name: (name in os.environ, os.environ.get(name))
    for name in _PROTECTED_ENV
}
_DEFAULT_DATABASE = (Path(__file__).resolve().parents[1] / "data" / "advisor.db").resolve()
_SYSTEM_TEMP = Path(tempfile.gettempdir()).resolve()
_OWNS_TEMP_DIRECTORY = False
_CLEANED = False


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _select_test_database() -> Path:
    """Return a new database path that cannot alias the user's local database."""
    global _OWNS_TEMP_DIRECTORY
    requested = os.environ.get("OKX_PYTEST_DB")
    if requested:
        candidate = Path(requested).expanduser().resolve()
        if not _is_within(candidate, _SYSTEM_TEMP):
            raise RuntimeError("OKX_PYTEST_DB must be inside the system temporary directory")
        if candidate == _DEFAULT_DATABASE:
            raise RuntimeError("pytest must never use backend/data/advisor.db")
        if candidate.exists():
            raise RuntimeError("OKX_PYTEST_DB must point to a new, non-existent database")
        if not candidate.parent.is_dir():
            raise RuntimeError("the parent directory for OKX_PYTEST_DB must already exist")
        return candidate

    directory = Path(tempfile.mkdtemp(prefix="okx-advisor-pytest-")).resolve()
    _OWNS_TEMP_DIRECTORY = True
    return directory / "advisor-test.db"


_TEST_DATABASE = _select_test_database()


def _force_test_environment() -> None:
    # These values must be established before backend.main creates its module-
    # level Database. Individual tests may temporarily monkeypatch them, but no
    # collection order may select the real user database or enable networking.
    os.environ["OKX_ADVISOR_DB"] = str(_TEST_DATABASE)
    os.environ["OKX_PYTEST_DB"] = str(_TEST_DATABASE)
    os.environ["OKX_DISABLE_NETWORK"] = "1"
    # Starlette's TestClient uses the synthetic Host header ``testserver``.
    # Keep that authority unavailable in real launches while permitting it in
    # this isolated pytest process.
    os.environ["OKX_TEST_MODE"] = "1"


def _restore_original_environment() -> None:
    for name, (was_present, value) in _ORIGINAL_ENV.items():
        if was_present:
            os.environ[name] = value or ""
        else:
            os.environ.pop(name, None)


def _cleanup_test_database() -> None:
    global _CLEANED
    if _CLEANED:
        return
    cleanup_failed = False
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(f"{_TEST_DATABASE}{suffix}").unlink(missing_ok=True)
        except OSError:
            # pytest_unconfigure runs after TestClient/SQLite contexts close.
            # Keep cleanup retryable at process exit if an unusually late
            # Windows file handle has not been released yet.
            cleanup_failed = True
    if _OWNS_TEMP_DIRECTORY:
        try:
            _TEST_DATABASE.parent.rmdir()
        except OSError:
            cleanup_failed = True
    _CLEANED = not cleanup_failed
    _restore_original_environment()


_force_test_environment()
_already_imported = sys.modules.get("backend.main")
atexit.register(_cleanup_test_database)

# Import only after the protected environment is fixed. This intentionally
# defeats test modules that mutate OKX_ADVISOR_DB during their own import and
# makes collection order irrelevant.
from backend import main as _isolated_main  # noqa: E402

if Path(_isolated_main.db.path).resolve() != _TEST_DATABASE:
    _cleanup_test_database()
    imported = " before conftest" if _already_imported is not None else ""
    raise RuntimeError(f"backend.main was imported{imported} with a non-isolated database")


def pytest_collection_finish(session: pytest.Session) -> None:
    # test_api.py historically assigns a throw-away path while it is imported.
    # backend.main is already safely initialized, and this restores a single
    # authoritative path for the remainder of the session.
    _force_test_environment()


@pytest.fixture(autouse=True)
def _isolate_every_test():
    _force_test_environment()
    yield
    _force_test_environment()


def pytest_unconfigure(config: pytest.Config) -> None:
    _cleanup_test_database()
