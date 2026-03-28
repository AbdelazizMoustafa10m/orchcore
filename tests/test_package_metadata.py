from __future__ import annotations

import importlib
import importlib.metadata
import runpy
import sys

import pytest

import orchcore
from orchcore import __main__ as orchcore_main


def test_main_prints_version_and_exits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        orchcore_main.main()

    captured = capsys.readouterr()

    assert exc_info.value.code == 0
    assert captured.err == ""
    assert captured.out.strip() == f"orchcore {orchcore.__version__}"


def test_package_version_falls_back_when_metadata_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_version(_distribution: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    with monkeypatch.context() as context:
        context.setattr(importlib.metadata, "version", fake_version)
        reloaded = importlib.reload(orchcore)
        assert reloaded.__version__ == "0.0.0"

    importlib.reload(orchcore)


def test_run_module_executes_main_entrypoint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sys.modules.pop("orchcore.__main__", None)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("orchcore.__main__", run_name="__main__")

    captured = capsys.readouterr()

    assert exc_info.value.code == 0
    assert captured.err == ""
    assert captured.out.strip() == f"orchcore {orchcore.__version__}"
