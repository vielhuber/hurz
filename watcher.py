import subprocess, sys, time, signal, os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

SCRIPT = "script.py"

class RestartOnChange(FileSystemEventHandler):
    def __init__(self, runner): self.runner = runner; self.last = 0
    def on_modified(self, e):
        if e.src_path.endswith(".py") and time.time() - self.last > 1:
            self.last = time.time()
            print(f"🔁 File changed: {e.src_path}")
            self.runner.restart()

class Runner:
    def __init__(self, script): self.script = script; self.proc = None
    def start(self):
        print(f"🚀 Starting {self.script}")
        self.proc = subprocess.Popen([sys.executable, self.script])
    def stop(self):
        if self.proc and self.proc.poll() is None:
            print("🛑 Sending SIGINT...")
            self.proc.send_signal(signal.SIGINT)
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
    def restart(self): self.stop(); self.start()
    def watch(self):
        obs = Observer()
        obs.schedule(RestartOnChange(self), ".", recursive=True)
        obs.start()
        try:
            self.start()
            while True:
                time.sleep(1)
                if self.proc and self.proc.poll() is not None:
                    code = self.proc.returncode
                    if code == 0:
                        print("✅ Script exited cleanly. Shutting down watcher.")
                        break
                    print(f"💥 Script crashed (exit {code}). Restarting...")
                    self.start()
        except KeyboardInterrupt:
            print("👋 Ctrl+C – stopping everything.")
        finally:
            obs.stop(); obs.join(); self.stop()
            print("🧼 Watcher exited.")
            sys.exit(0)

if __name__ == "__main__":
    Runner(SCRIPT).watch()
