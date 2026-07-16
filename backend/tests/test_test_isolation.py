from __future__ import annotations

import os
import tempfile
from pathlib import Path

from backend import main


def test_backend_main_uses_disposable_session_database() -> None:
    configured = Path(os.environ["OKX_ADVISOR_DB"]).resolve()
    default_database = (Path(__file__).resolve().parents[1] / "data" / "advisor.db").resolve()
    system_temp = Path(tempfile.gettempdir()).resolve()

    assert configured == Path(main.db.path).resolve()
    assert configured != default_database
    assert configured.is_relative_to(system_temp)
    assert os.environ["OKX_DISABLE_NETWORK"] == "1"
    assert os.environ["OKX_TEST_MODE"] == "1"
