import fenced_retry
from fenced_retry import cli


def test_package_imports():
    assert fenced_retry.__version__ == "0.0.1"


def test_cli_version_flag_exits_zero():
    try:
        cli.main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
