from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/container_keepalive.sh"


class ContainerKeepaliveTest(unittest.TestCase):
    def test_existing_restart_policy_is_never_changed(self) -> None:
        for policy in ("no", "always", "unless-stopped", "on-failure"):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                docker = root / "docker"
                docker.write_text(
                    '#!/bin/bash\n'
                    'printf "%s\\n" "$*" >> "$CALLS"\n'
                    'case "$1" in\n'
                    '  ps) echo charly-david-app-1 ;;\n'
                    '  inspect) echo "$POLICY" ;;\n'
                    'esac\n'
                )
                docker.chmod(0o755)
                calls = root / "calls"
                environment = {
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "CALLS": str(calls),
                    "POLICY": policy,
                    "HURZ_CONTAINER": "charly-david",
                }

                subprocess.run(["bash", str(SCRIPT)], env=environment, check=True)

                self.assertEqual(
                    [
                        "ps --filter name=charly-david --format {{.Names}}",
                        "exec charly-david-app-1 /bin/bash /host/data/hurz/scripts/boot_start.sh",
                    ],
                    calls.read_text().splitlines(),
                )

    def test_absent_container_is_not_started(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\n')
            docker.chmod(0o755)
            calls = root / "calls"
            environment = {
                **os.environ,
                "PATH": f"{root}:{os.environ['PATH']}",
                "CALLS": str(calls),
                "HURZ_CONTAINER": "charly-david",
            }

            subprocess.run(["bash", str(SCRIPT)], env=environment, check=True)

            self.assertEqual(
                ["ps --filter name=charly-david --format {{.Names}}"],
                calls.read_text().splitlines(),
            )


if __name__ == "__main__":
    unittest.main()
