"""Aviso de versão nova: só releases vX.Y.Z publicados e mais novos contam."""

import tomllib
from pathlib import Path

from pokemon_companion.version_check import current_version, newer_release


def test_newer_release_ignores_data_drafts_prereleases_and_older_tags():
    releases = [
        {"tag_name": "data", "html_url": "d"},
        {"tag_name": "v0.3.0", "html_url": "draft", "draft": True},
        {"tag_name": "v0.2.5", "html_url": "pre", "prerelease": True},
        {"tag_name": "v0.2.0", "html_url": "https://x/v0.2.0"},
        {"tag_name": "v0.1.0", "html_url": "old"},
    ]
    assert newer_release(releases, "0.1.0") == ("v0.2.0", "https://x/v0.2.0")
    assert newer_release(releases, "0.2.0") is None
    assert newer_release(releases, "0.10.0") is None  # compara números, não texto


def test_current_version_comes_from_the_package_metadata():
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert current_version() == pyproject["project"]["version"]
