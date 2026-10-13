"""Service settings: MOTU_* environment variables over an optional, git-ignored config.local over the defaults."""
from dataclasses import dataclass
from pathlib import Path

# KEY=VALUE lines (shell- and systemd-EnvironmentFile-compatible); see config.local.example
LOCAL_CONFIG = Path(__file__).resolve().parents[1] / "config.local"


def read_local_config(path) -> dict:
    """The KEY=VALUE pairs of a config.local file; {} when there is none. '#' comments and blank lines are skipped."""
    if path is None or not Path(path).is_file():
        return {}
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    port_name: str = "828 COM"
    host: str = "0.0.0.0"
    http_port: int = 8280

    @classmethod
    def from_env(cls, environ) -> "Settings":
        port = int(environ.get("MOTU_HTTP_PORT", cls.http_port))
        if not 1 <= port <= 65535:
            raise ValueError(f"MOTU_HTTP_PORT must be 1-65535, got {port}")
        return cls(
            port_name=environ.get("MOTU_PORT_NAME", cls.port_name),
            host=environ.get("MOTU_HTTP_HOST", cls.host),
            http_port=port,
        )
