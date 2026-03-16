from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Project scanning — comma-separated paths
    scan_paths: str = ""  # e.g. "~/Desktop,~/Projects"

    # Claude Code
    claude_binary: str = "claude"
    claude_model: str = "opus"
    claude_max_turns: int = 2000
    nightly_batch_size: int = 10  # projects per nightly dream
    cluster_timeout_sec: int = 2700  # 45 min per cluster
    synthesis_timeout_sec: int = 3600  # 60 min for synthesis

    # Optional — Wiki.js
    wiki_enabled: bool = False
    wiki_update_script: str = ""
    wiki_export_script: str = ""

    # Optional — ElizaOS/Milady
    milady_enabled: bool = False
    milady_api_url: str = "http://localhost:2138"

    # Internal paths
    state_dir: Path = Path(__file__).parent.parent / "state"
    dreams_dir: Path = Path(__file__).parent.parent / "dreams"
    host: str = "0.0.0.0"
    port: int = 8160

    model_config = {"env_prefix": "DREAM_", "env_file": ".env", "env_file_encoding": "utf-8"}

    def get_scan_paths(self) -> list[Path]:
        """Parse DREAM_SCAN_PATHS into list of resolved Paths."""
        if not self.scan_paths:
            return []
        paths = []
        for p in self.scan_paths.split(","):
            p = p.strip()
            if p:
                resolved = Path(p).expanduser().resolve()
                if resolved.is_dir():
                    paths.append(resolved)
        return paths

    def is_configured(self) -> bool:
        """Check if setup has been run (scan_paths is set)."""
        return bool(self.scan_paths.strip())


settings = Settings()
