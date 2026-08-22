"""Load client settings from HOTEL-CHATBOT-CLIENT/.env.

Do not log ANTHROPIC_API_KEY. The browser never reads this module.
MCP child process paths are fixed in env — never taken from user input.
"""

import shutil
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CLIENT_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=CLIENT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        default="claude-sonnet-4-20250514",
        alias="ANTHROPIC_MODEL",
    )
    mcp_server_python: Path = Field(
        default=Path(sys.executable),
        alias="MCP_SERVER_PYTHON",
    )
    mcp_server_script: Path = Field(
        default=CLIENT_ROOT.parent / "HOTEL-CHATBOT-MCP" / "server.py",
        alias="MCP_SERVER_SCRIPT",
    )
    mcp_server_cwd: Path = Field(
        default=CLIENT_ROOT.parent / "HOTEL-CHATBOT-MCP",
        alias="MCP_SERVER_CWD",
    )
    razorpay_key_id: str = Field(default="", alias="RAZORPAY_KEY_ID")
    razorpay_key_secret: str = Field(default="", alias="RAZORPAY_KEY_SECRET")

    @field_validator("mcp_server_python", mode="before")
    @classmethod
    def _expand_python(cls, value: str) -> Path:
        raw = str(value).strip()
        path = Path(raw).expanduser()
        if path.is_absolute():
            return path
        found = shutil.which(raw)
        if found:
            return Path(found)
        return (CLIENT_ROOT / path).absolute()

    @field_validator("mcp_server_script", "mcp_server_cwd", mode="before")
    @classmethod
    def _expand_path(cls, value: str) -> Path:
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = (CLIENT_ROOT / path).absolute()
        return path

    def require_api_key(self) -> str:
        """Fail fast when Claude is about to be called and the key is still empty."""
        key = (self.anthropic_api_key or "").strip()
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is missing. Add it to HOTEL-CHATBOT-CLIENT/.env "
                "and never paste it in the chat UI."
            )
        return key

    def validate_mcp_paths(self) -> None:
        if not self.mcp_server_python.exists():
            raise FileNotFoundError(f"MCP python not found: {self.mcp_server_python}")
        if not self.mcp_server_script.exists():
            raise FileNotFoundError(f"MCP server.py not found: {self.mcp_server_script}")
        if not self.mcp_server_cwd.is_dir():
            raise FileNotFoundError(f"MCP cwd not found: {self.mcp_server_cwd}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_mcp_paths()
    return settings
