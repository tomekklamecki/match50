"""Small local .env fallback; real environment variables take precedence."""
import os
import shlex


def environment_value(name, path, default=""):
    if name in os.environ:
        return os.environ[name]
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.removeprefix("export ").partition("=")
            if separator and key.strip() == name:
                parts = shlex.split(value, comments=True)
                return parts[0] if parts else default
    return default
