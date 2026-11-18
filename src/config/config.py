from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class Service(BaseModel):
    name: str = "ingest"
    port: int = 8005
    bind_host: str = "127.0.0.1"


class Cache(BaseModel):
    host: str = ""
    port: int = 6379
    password: str = ""
    db: int = 0

    @property
    def url(self) -> str:
        from urllib.parse import quote

        credentials = f"default:{quote(self.password, safe='')}@" if self.password else ""
        return f"redis://{credentials}{self.host}:{self.port}/{self.db}"


class Rabbitmq(BaseModel):
    url: str = ""


class Projects(BaseModel):
    url: str = "http://projects.apps:8003"


class Auth(BaseModel):
    url: str = "http://auth.apps:8001"
    client_id: str = "ingest"
    client_secret: str = ""


class Tracing(BaseModel):
    enabled: bool = True
    otlp_endpoint: str = ""


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        env_nested_delimiter="__",
    )

    service: Service = Field(default_factory=Service)
    cache: Cache = Field(default_factory=Cache)
    rabbitmq: Rabbitmq = Field(default_factory=Rabbitmq)
    projects: Projects = Field(default_factory=Projects)
    auth: Auth = Field(default_factory=Auth)
    tracing: Tracing = Field(default_factory=Tracing)

    def missing_required(self) -> list[str]:
        required = {
            "cache.host": self.cache.host,
            "rabbitmq.url": self.rabbitmq.url,
            "auth.client_secret": self.auth.client_secret,
        }
        if self.tracing.enabled:
            required["tracing.otlp_endpoint"] = self.tracing.otlp_endpoint
        return [key for key, value in required.items() if not value]

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        path = Path(__file__).parents[2] / "config.toml"
        sources: list[PydanticBaseSettingsSource] = [EnvSettingsSource(settings_cls)]
        if path.exists():
            sources.append(TomlConfigSettingsSource(settings_cls))
        return tuple(sources)
