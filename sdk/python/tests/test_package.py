from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


GENERIC_EXAMPLE_URL = (
    "https://github.com/aig-dev/ChoraMem/blob/main/"
    "sdk/python/examples/generic_lifecycle.py"
)


def test_wheel_metadata_links_to_the_published_generic_example(tmp_path: Path) -> None:
    sdk_root = Path(__file__).parents[1]
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy2(sdk_root / "README.md", project / "README.md")
    shutil.copy2(sdk_root / "pyproject.toml", project / "pyproject.toml")
    shutil.copytree(sdk_root / "src", project / "src")

    wheel_dir = tmp_path / "wheel"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(metadata_name).decode()

    assert GENERIC_EXAMPLE_URL in metadata
    assert "](examples/generic_lifecycle.py)" not in metadata
