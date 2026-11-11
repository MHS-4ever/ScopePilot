from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    api_key: str
    database_url: str


def get_settings() -> Settings:
    api_key = os.getenv("SCOPEPILOT_API_KEY", "")
    if not api_key:
        raise RuntimeError("SCOPEPILOT_API_KEY must be set")

    return Settings(
        api_key=api_key,
        database_url=os.getenv(
            "SCOPEPILOT_DATABASE_URL", "sqlite:///./data/scopepilot.db"
        ),
    )

