import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest import TestCase


class BootStartTest(TestCase):
    def test_bot_is_the_only_service_started_and_healthy_start_is_silent(self):
        for healthy in (True, False):
            with self.subTest(healthy=healthy), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                scripts = root / "scripts"
                scripts.mkdir()
                shutil.copy(Path(__file__).resolve().parents[1] / "scripts/boot_start.sh", scripts)
                (scripts / "start_paper_session.sh").write_text(
                    'echo "${1:-start}" >> calls\n'
                    'if [[ "$1" == status ]]; then exit "$STATUS"; fi\n'
                    'if readlink /proc/$$/fd/9 | grep -q boot_start.lock; then exit 42; fi\n'
                )
                resolver = root / "getent"
                resolver.write_text("#!/bin/sh\nexit 0\n")
                resolver.chmod(0o755)
                result = subprocess.run(["bash", str(scripts / "boot_start.sh")],
                                        env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                                             "STATUS": "0" if healthy else "1"},
                                        capture_output=True, text=True, check=True)
                self.assertEqual("", result.stdout)
                self.assertEqual(["status"] if healthy else ["status", "start"],
                                 (root / "calls").read_text().splitlines())
                if healthy:
                    self.assertFalse((root / "tmp/boot_start.log").exists())
                if not healthy:
                    log = (root / "tmp/boot_start.log").read_text()
                    self.assertNotIn("No such file", log)
                    self.assertNotIn("dashboard", log)
