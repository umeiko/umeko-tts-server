"""One model scheduler per data directory, including across ASGI worker processes."""

import os


class ProcessLock:
    def __init__(self, data_dir):
        self.path = data_dir / ".server.lock"
        self.file = None

    def acquire(self):
        self.file = self.path.open("a+b")
        if not self.path.stat().st_size:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError(
                "This data directory already has a running TTS scheduler; use one ASGI worker"
            ) from None

    def release(self):
        if self.file:
            self.file.close()
            self.file = None
