"""The package imports and reports a version. Nothing else is built yet."""

import cra_assistant


def test_package_imports() -> None:
    assert cra_assistant.__version__


def test_version_is_a_dotted_string() -> None:
    assert cra_assistant.__version__.count(".") == 2
