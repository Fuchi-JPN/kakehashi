"""Kakehashi configuration management.

Config file: ~/.config/kakehashi/config.yaml (KAKEHASHI_CONFIG to override).
Permissions: 0o600 enforced on save.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

log = logging.getLogger("kakehashi.config")

DEFAULT_CONFIG_PATH = Path(os.environ.get(
    "KAKEHASHI_CONFIG",
    str(Path.home() / ".config" / "kakehashi" / "config.yaml"),
))


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8090
    api_key: str = ""


class EgressParams(BaseModel):
    merge_policy: Literal["override", "client_wins"] = "override"
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)


class EgressProvider(BaseModel):
    id: str
    name: str = ""
    protocol: Literal["openai", "anthropic"] = "openai"
    base_url: str = "http://127.0.0.1:8088/v1"
    api_key: str = ""
    api_key_env: str = ""
    model: str = "auto"
    timeout_s: int = 300
    params: EgressParams = Field(default_factory=EgressParams)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not v or len(v) > 64:
            raise ValueError("provider id must be 1..64 chars")
        return v

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        v = v.rstrip("/")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("base_url must start with http(s)://")
        return v


class EgressConfig(BaseModel):
    active_provider: str = "coderouter"
    providers: list[EgressProvider] = Field(default_factory=list)
    chain: list[Any] = Field(default_factory=list)  # future reservation

    @model_validator(mode="after")
    def _check_active(self):
        ids = {p.id for p in self.providers}
        if self.providers and self.active_provider not in ids:
            raise ValueError(f"active_provider '{self.active_provider}' not in providers {ids}")
        return self


class TranslateBackend(BaseModel):
    id: str
    name: str = ""
    protocol: Literal["openai", "anthropic"] = "openai"
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = ""
    api_key: str = ""
    api_key_env: str = ""
    timeout_s: int = 30
    enabled: bool = True


class TranslateRetry(BaseModel):
    on_status: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])
    on_timeout: bool = True
    max_attempts_per_backend: int = 1
    cooldown_s: int = 60


class TranslationPrompts(BaseModel):
    ja2en: str = (
        "You are a precise Japanese-to-English translator for software engineering chat. "
        "Translate ONLY the user text to natural English. "
        "Preserve placeholders like __KXH_0__ exactly. "
        "Do not add explanations. Do not translate code, URLs, or IDs "
        "(they are already placeholdered). Return translation only."
    )
    en2ja: str = (
        "あなたはソフトウェア開発チャット向けの正確な英日翻訳者です。"
        "ユーザーテキストのみ自然な日本語に翻訳してください。"
        "__KXH_0__等のプレースホルダは厳密に保持してください。"
        "解説を付けず、翻訳文のみ返してください。"
        "コード・URL・IDは翻訳しないでください（既にプレースホルダ化済み）。"
    )
    output_guard: str = (
        "IMPORTANT: Write ALL output in English only. All code comments, docstrings, "
        "explanations, and prose must be in English. "
        "Do NOT use Chinese (Simplified or Traditional) anywhere in the output."
    )


class CodeStringsConfig(BaseModel):
    enabled: bool = True
    min_length: int = 8


class TranslationConfig(BaseModel):
    enabled: bool = True
    default_pair: list[str] = Field(default_factory=lambda: ["ja", "en"])
    cjk_threshold: float = 0.1
    protect_patterns: list[str] = Field(
        default_factory=lambda: ["code_block", "inline_code", "url", "uuid", "path"]
    )
    backends: list[TranslateBackend] = Field(default_factory=list)
    retry: TranslateRetry = Field(default_factory=TranslateRetry)
    prompts: TranslationPrompts = Field(default_factory=TranslationPrompts)
    code_strings: CodeStringsConfig = Field(default_factory=CodeStringsConfig)
    rules: list[Any] = Field(default_factory=list)  # future reservation


class LoggingConfig(BaseModel):
    translation_log_enabled: bool = True
    translation_log_dir: str = "~/.local/share/kakehashi/logs"
    translation_log_max_mb: int = 10
    translation_log_backups: int = 5


class WebUIConfig(BaseModel):
    enabled: bool = True
    path: str = "/ui"


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    egress: EgressConfig = Field(default_factory=EgressConfig)
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    webui: WebUIConfig = Field(default_factory=WebUIConfig)


def default_config() -> AppConfig:
    return AppConfig(
        server=ServerConfig(),
        egress=EgressConfig(
            active_provider="coderouter",
            providers=[
                EgressProvider(
                    id="coderouter",
                    name="CodeRouter (local)",
                    protocol="openai",
                    base_url="http://127.0.0.1:8088/v1",
                    model="auto",
                    timeout_s=300,
                ),
            ],
        ),
        translation=TranslationConfig(
            backends=[
                TranslateBackend(
                    id="tb-local",
                    name="Local translate model",
                    base_url="http://127.0.0.1:1234/v1",
                    model="translategemma-12b",
                    timeout_s=30,
                    enabled=True,
                ),
                TranslateBackend(
                    id="tb-openrouter",
                    name="OpenRouter",
                    base_url="https://openrouter.ai/api/v1",
                    model="",
                    api_key_env="OPENROUTER_API_KEY",
                    timeout_s=45,
                    enabled=True,
                ),
            ]
        ),
    )


def resolve_secret(api_key: str = "", api_key_env: str = "") -> str:
    """Resolution order: env var -> direct key -> empty."""
    if api_key_env:
        v = os.environ.get(api_key_env, "")
        if v:
            return v
    return api_key or ""


def resolve_provider_secret(p: EgressProvider) -> str:
    return resolve_secret(p.api_key, p.api_key_env)


def resolve_backend_secret(b: TranslateBackend) -> str:
    return resolve_secret(b.api_key, b.api_key_env)


def mask_config_dict(d: dict) -> dict:
    """Mask api_key fields for API responses."""
    import copy
    out = copy.deepcopy(d)
    def _walk(o):
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if k == "api_key" and isinstance(v, str) and v:
                    o[k] = "***"
                else:
                    _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)
    _walk(out)
    return out


class ConfigStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else DEFAULT_CONFIG_PATH
        self._lock = threading.RLock()
        self._config: AppConfig
        if self.path.exists():
            self._config = self._load_file(self.path)
            self._check_perms()
        else:
            self._config = default_config()
            self.save()

    @staticmethod
    def _load_file(path: Path) -> AppConfig:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return AppConfig.model_validate(data)

    def _check_perms(self):
        try:
            mode = self.path.stat().st_mode & 0o777
            if mode != 0o600:
                log.warning("config file %s perms %o != 600", self.path, mode)
        except FileNotFoundError:
            pass

    def get(self) -> AppConfig:
        with self._lock:
            return self._config

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = self._config.model_dump()
            with open(self.path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            try:
                os.chmod(self.path, 0o600)
            except OSError as e:
                log.warning("chmod 600 failed: %s", e)

    def update(self, mutator) -> AppConfig:
        """Apply mutator(dict)->dict or (AppConfig)->AppConfig, validate, persist atomically."""
        with self._lock:
            old = self._config
            if callable(mutator):
                try:
                    result = mutator(old.model_copy(deep=True))
                except Exception:
                    raise
                new = result if isinstance(result, AppConfig) else AppConfig.model_validate(result)
            else:
                new = AppConfig.model_validate(mutator)
            self._config = new
            try:
                self.save()
            except Exception:
                self._config = old
                raise
            return self._config

    def replace(self, data: dict) -> AppConfig:
        return self.update(lambda _old: AppConfig.model_validate(data))

    def reload_from_disk(self) -> AppConfig:
        with self._lock:
            new = self._load_file(self.path)
            self._config = new
            return new
