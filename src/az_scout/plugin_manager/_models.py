"""Data classes for the plugin manager."""

from dataclasses import dataclass, field


@dataclass
class GitHubRepo:
    owner: str
    repo: str


@dataclass
class PluginValidationResult:
    ok: bool
    owner: str
    repo: str
    repo_url: str
    ref: str
    source: str = "github"  # "github" or "pypi"
    resolved_sha: str | None = None
    distribution_name: str | None = None
    version: str | None = None  # resolved PyPI version
    entry_points: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class InstalledPluginRecord:
    distribution_name: str
    repo_url: str
    ref: str
    resolved_sha: str
    entry_points: dict[str, str]
    installed_at: str  # ISO-8601
    actor: str
    source: str = "github"  # "github" or "pypi"
    last_checked_at: str | None = None
    latest_ref: str | None = None
    latest_sha: str | None = None
    update_available: bool | None = None


@dataclass
class RecommendedPlugin:
    """A plugin recommended for installation."""

    name: str
    description: str
    source: str  # "pypi" or "github"
    url: str = ""
    version: str = ""
