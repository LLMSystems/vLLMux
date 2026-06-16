"""Runner heartbeat: keeps the streamed log alive during quiet phases."""
import time

import pytest

from app.runners_common import heartbeat

pytestmark = pytest.mark.unit


def test_heartbeat_emits_liveness(tmp_path, capsys):
    with heartbeat(str(tmp_path), interval=0.05, count_glob=None):
        time.sleep(0.18)
    out = capsys.readouterr().out
    assert "running" in out and "elapsed" in out


def test_heartbeat_counts_prediction_rows(tmp_path, capsys):
    preds = tmp_path / "predictions" / "model"
    preds.mkdir(parents=True)
    (preds / "ds.jsonl").write_text("a\nb\nc\n", encoding="utf-8")
    with heartbeat(str(tmp_path), interval=0.05):
        time.sleep(0.18)
    assert "3 samples completed" in capsys.readouterr().out


def test_heartbeat_silent_after_exit(tmp_path, capsys):
    with heartbeat(str(tmp_path), interval=0.05, count_glob=None):
        pass
    capsys.readouterr()  # drain
    time.sleep(0.12)
    assert "running" not in capsys.readouterr().out  # thread stopped
