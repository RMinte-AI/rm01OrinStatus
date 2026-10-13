"""Run the installer entry with fake commands and deployment stages disabled."""
import os
from pathlib import Path
import subprocess

import pytest


BASH_POPEN = subprocess.Popen
ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="Installer requires a non-root user")

FAKE_CONDA = r'''#!/bin/bash
printf '%s|%s\n' "$0" "$*" >> "$CONDA_TEST_LOG"
[[ "$*" == "shell.bash hook" ]] || exit 90
printf '%s\n' 'conda() { printf "activated|%s\n" "$*" >> "$CONDA_TEST_LOG"; }'
'''


def executable(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


def installer_entry(tmp_path, location):
    home = tmp_path / "home with spaces"
    home.mkdir()
    (home / ".profile").write_text("# existing user settings\n")
    commands = tmp_path / "commands"
    for name in ("curl", "systemctl", "tegrastats"):
        executable(commands / name, "#!/bin/bash\nexit 90\n")
    conda_path = None
    if location == "PATH":
        conda_path = commands / "conda"
        # A second installation must not override the one explicitly in PATH.
        executable(home / "miniconda3/bin/conda", FAKE_CONDA)
    elif location:
        conda_path = home / location / "bin/conda"
    if conda_path:
        executable(conda_path, FAKE_CONDA)
    (tmp_path / "pyproject.toml").write_text("# project fixture\n")
    log = tmp_path / "conda-calls.txt"
    # Retain the real main/check_requirements/init_conda functions, excluding
    # the top-level ERR trap and argument parsing. No installer action runs here.
    functions = (ROOT / "scripts/install.sh").read_text().split("# 设置错误处理", 1)[0]
    entry = functions + '''
SERVICE_HOST="$DEFAULT_HOST"
SERVICE_PORT="$DEFAULT_PORT"
setup_conda_env() { conda activate "$CONDA_ENV_NAME"; }
install_package() { :; }
create_service() { :; }
start_and_verify_service() { :; }
create_management_script() { :; }
show_completion_summary() { :; }
main
'''
    child = BASH_POPEN(["/bin/bash", "-c", entry], cwd=tmp_path,
                       env={"HOME": str(home), "PATH": str(commands), "CONDA_TEST_LOG": str(log)},
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = child.communicate("\n", timeout=5)
    assert not (home / ".bashrc").exists()
    assert (home / ".profile").read_text() == "# existing user settings\n"
    return child.returncode, stdout + stderr, log.read_text().splitlines() if log.exists() else [], conda_path


@pytest.mark.parametrize("location", ["PATH", "miniconda3", "anaconda3", "miniforge3"])
def test_installer_loads_conda_before_activation(tmp_path, location):
    code, output, calls, conda_path = installer_entry(tmp_path, location)
    assert code == 0, output
    assert calls == [str(conda_path) + "|shell.bash hook", "activated|activate tegrastats-api"]


def test_installer_missing_conda_reports_failure(tmp_path):
    code, output, calls, _ = installer_entry(tmp_path, None)
    assert code == 1 and "未找到Conda" in output
    assert not calls
