import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from unittest import TestCase


class SessionLogArchiveTest(TestCase):
    def test_a_start_keeps_the_previous_runs_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy(Path(__file__).resolve().parents[1] / "scripts/start_paper_session.sh", scripts)
            (scripts / "_session_watchdog.sh").write_text("echo new run\n")
            (root / ".env").write_text("PAPER_TRADE_ONLY=1\n")
            (root / "tmp").mkdir()
            (root / "tmp/paper_session.log").write_text("refresh landed\n")
            os.utime(root / "tmp/paper_session.log", (0, 1_761_372_000))
            subprocess.run(["bash", str(scripts / "start_paper_session.sh")], cwd=root,
                           capture_output=True, text=True, check=True)
            archived = root / "tmp/session_logs/paper_session-20251025T060000Z.log"
            self.assertEqual("refresh landed\n", archived.read_text())
            deadline = time.time() + 5
            while not (root / "tmp/paper_session.log").read_text() and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual("new run\n", (root / "tmp/paper_session.log").read_text())
