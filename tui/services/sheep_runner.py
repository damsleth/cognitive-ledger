"""Shell wrapper for the sheep script."""

import subprocess
from pathlib import Path


class SheepRunner:
    """Runs sheep script commands."""

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.script = root_dir / "scripts" / "sheep"

    def _run(self, *args: str) -> tuple[int, str]:
        """Run sheep with arguments, return (exit_code, output)."""
        result = subprocess.run(
            [str(self.script), *args],
            capture_output=True,
            text=True,
            cwd=self.root_dir,
        )
        return result.returncode, result.stdout + result.stderr

    def status(self) -> str:
        """Run sheep status, return output."""
        _, output = self._run("status")
        return output

    def lint(self) -> tuple[int, str]:
        """Run sheep lint, return (exit_code, output)."""
        return self._run("lint")

    def index(self) -> str:
        """Run sheep index, return output."""
        _, output = self._run("index")
        return output

    def sleep(self) -> str:
        """Run sheep sleep (checklist), return output."""
        _, output = self._run("sleep")
        return output
