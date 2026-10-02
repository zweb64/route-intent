from importlib.metadata import version

import pytest

from route_intent.cli import main


def test_version_prints_package_version_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"route-intent {version('route-intent')}"


def test_unknown_argument_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--no-such-option"])

    assert excinfo.value.code != 0
    assert "--no-such-option" in capsys.readouterr().err


def test_no_arguments_is_a_usage_error(capsys):
    # SPEC.md §5: usage errors exit 2 and must not look like success.
    assert main([]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage: route-intent" in captured.err
