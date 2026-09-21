"""Content-addressed files under one directory (a docker volume in deployment)."""

import os
import tempfile
from pathlib import Path


class FilesystemDocumentStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if self.root.resolve() not in candidate.parents:
            raise ValueError("Clé de stockage hors du répertoire de contenu")
        return candidate

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def put(self, data: bytes, *, key: str) -> Path:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write next to the target then rename: a crash never leaves a half-written file.
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return target
