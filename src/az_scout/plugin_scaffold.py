"""Plugin scaffold generator used by CLI and development wrappers."""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned


def to_camel_case(value: str) -> str:
    parts = [part for part in value.split("_") if part]
    return "".join(part[:1].upper() + part[1:] for part in parts)


@dataclass
class _UI:
    use_rich: bool

    def info(self, message: str) -> None:
        print(message)

    def error(self, message: str) -> None:
        print(message, file=sys.stderr)

    def prompt(self, message: str, default_value: str) -> str:
        response = input(f"{message} [{default_value}]: ").strip()
        return response or default_value

    def confirm(self, message: str, default: bool = False) -> bool:
        default_label = "Y/n" if default else "y/N"
        response = input(f"{message} [{default_label}]: ").strip().lower()
        if not response:
            return default
        return response in {"y", "yes"}


class _RichUI(_UI):
    def __init__(self) -> None:
        from rich.console import Console

        super().__init__(use_rich=True)
        self.console = Console()

    def info(self, message: str) -> None:
        self.console.print(message)

    def error(self, message: str) -> None:
        self.console.print(message, style="bold red")

    def prompt(self, message: str, default_value: str) -> str:
        from rich.prompt import Prompt

        answer = cast(str, Prompt.ask(message, default=default_value))
        return answer.strip()

    def confirm(self, message: str, default: bool = False) -> bool:
        from rich.prompt import Confirm

        return bool(Confirm.ask(message, default=default))

    def banner(self, scaffold_dir: Path) -> None:
        from rich.panel import Panel

        self.console.print(
            Panel.fit(
                f"[bold cyan]az-scout plugin scaffold generator[/bold cyan]\n"
                f"Scaffold source: [green]{scaffold_dir}[/green]",
                border_style="cyan",
            )
        )

    def summary(
        self,
        *,
        location: Path,
        package_name: str,
        module_name: str,
        plugin_slug: str,
    ) -> None:
        from rich.table import Table

        table = Table(
            title="Plugin scaffold created",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("Location", str(location))
        table.add_row("Package", package_name)
        table.add_row("Module", module_name)
        table.add_row("Slug", plugin_slug)
        self.console.print(table)


def _build_ui(prefer_rich: bool) -> _UI:
    if not prefer_rich:
        return _UI(use_rich=False)
    try:
        return _RichUI()
    except Exception:
        return _UI(use_rich=False)


def _replace_in_file(path: Path, replacements: list[tuple[str, str]]) -> None:
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return

    updated = original
    for old, new in replacements:
        updated = updated.replace(old, new)

    if updated != original:
        path.write_text(updated, encoding="utf-8")


def _replace_with_regex(path: Path, regex_replacements: list[tuple[str, str]]) -> None:
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return

    updated = original
    for pattern, replacement in regex_replacements:
        updated = re.sub(pattern, replacement, updated, flags=re.MULTILINE)

    if updated != original:
        path.write_text(updated, encoding="utf-8")


def _resolve_value(
    provided_value: str | None,
    *,
    prompt_label: str,
    default_value: str,
    non_interactive: bool,
    ui: _UI,
) -> str:
    if provided_value is not None:
        return provided_value.strip() or default_value
    if non_interactive:
        return default_value
    return ui.prompt(prompt_label, default_value)


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def create_plugin_scaffold(
    *,
    display_name: str | None = None,
    plugin_slug: str | None = None,
    package_name: str | None = None,
    github_owner: str | None = None,
    github_repo: str | None = None,
    output_dir: Path | None = None,
    non_interactive: bool = False,
    assume_yes: bool = False,
    prefer_rich: bool = True,
) -> int:
    """Create a plugin scaffold from docs/plugin-scaffold.

    When ``non_interactive`` is True, missing values are filled from defaults.
    """
    ui = _build_ui(prefer_rich=prefer_rich)

    repo_root = _resolve_repo_root()
    scaffold_dir = repo_root / "docs" / "plugin-scaffold"

    if not scaffold_dir.is_dir():
        ui.error(f"Scaffold not found at: {scaffold_dir}")
        return 1

    if isinstance(ui, _RichUI):
        ui.banner(scaffold_dir)
    else:
        ui.info("\naz-scout plugin scaffold generator")
        ui.info(f"Scaffold source: {scaffold_dir}\n")

    display_name_resolved = _resolve_value(
        display_name,
        prompt_label="Plugin display name",
        default_value="My Plugin",
        non_interactive=non_interactive,
        ui=ui,
    )

    default_slug = slugify(display_name_resolved) or "my-plugin"
    plugin_slug_resolved = _resolve_value(
        plugin_slug,
        prompt_label="Plugin slug (kebab-case, no az-scout- prefix)",
        default_value=default_slug,
        non_interactive=non_interactive,
        ui=ui,
    )

    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", plugin_slug_resolved):
        ui.error(
            f"Invalid plugin slug: '{plugin_slug_resolved}'. "
            "Use kebab-case (letters, numbers, hyphens)."
        )
        return 1

    module_suffix = plugin_slug_resolved.replace("-", "_")
    package_name_resolved = _resolve_value(
        package_name,
        prompt_label="Python package name",
        default_value=f"az-scout-{plugin_slug_resolved}",
        non_interactive=non_interactive,
        ui=ui,
    )

    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", package_name_resolved):
        ui.error(f"Invalid package name: '{package_name_resolved}'.")
        return 1

    if not package_name_resolved.startswith("az-scout-"):
        ui.error("Package name should usually start with 'az-scout-' for discoverability.")
        if not assume_yes:
            if non_interactive:
                return 1
            if not ui.confirm("Continue with non-standard package name?", default=False):
                return 1

    github_owner_resolved = _resolve_value(
        github_owner,
        prompt_label="GitHub owner/org",
        default_value="your-org",
        non_interactive=non_interactive,
        ui=ui,
    )
    github_repo_resolved = _resolve_value(
        github_repo,
        prompt_label="GitHub repository name",
        default_value=package_name_resolved,
        non_interactive=non_interactive,
        ui=ui,
    )

    module_name = f"az_scout_{module_suffix}"
    entrypoint_key = module_suffix
    class_name = f"{to_camel_case(module_suffix)}Plugin"
    asset_prefix = plugin_slug_resolved
    tool_function = f"{module_suffix}_tool"

    if class_name == "Plugin":
        ui.error(f"Could not derive a plugin class name from slug '{plugin_slug_resolved}'.")
        return 1

    default_target = (Path.cwd() / package_name_resolved).resolve()
    target_dir = output_dir or Path(
        _resolve_value(
            None,
            prompt_label="Output directory for the new plugin",
            default_value=str(default_target),
            non_interactive=non_interactive,
            ui=ui,
        )
    )
    target_dir = target_dir.expanduser().resolve()

    if target_dir.exists():
        has_content = any(target_dir.iterdir())
        if has_content and not assume_yes:
            if non_interactive:
                ui.error(f"Target directory exists and is not empty: {target_dir}")
                ui.error("Re-run with --yes to allow overwrite.")
                return 1
            if not ui.confirm(
                f"Target directory exists and is not empty: {target_dir}. "
                "Overwrite existing files in this directory?",
                default=False,
            ):
                return 1
    else:
        target_dir.mkdir(parents=True, exist_ok=True)

    ui.info("\nGenerating plugin scaffold...\n")

    ignore = shutil.ignore_patterns(".git", "__pycache__", ".ruff_cache", ".DS_Store")
    shutil.copytree(scaffold_dir, target_dir, dirs_exist_ok=True, ignore=ignore)

    src_dir = target_dir / "src"
    old_module_dir = src_dir / "az_scout_example"
    new_module_dir = src_dir / module_name
    if old_module_dir.exists():
        if new_module_dir.exists():
            shutil.rmtree(new_module_dir)
        old_module_dir.rename(new_module_dir)

    renames = [
        (
            new_module_dir / "static" / "js" / "example-tab.js",
            new_module_dir / "static" / "js" / f"{asset_prefix}-tab.js",
        ),
        (
            new_module_dir / "static" / "html" / "example-tab.html",
            new_module_dir / "static" / "html" / f"{asset_prefix}-tab.html",
        ),
        (
            new_module_dir / "static" / "css" / "example.css",
            new_module_dir / "static" / "css" / f"{asset_prefix}.css",
        ),
    ]
    for old_path, new_path in renames:
        if old_path.exists():
            old_path.rename(new_path)

    replacements = [
        ("az-scout-example", package_name_resolved),
        ("az_scout_example", module_name),
        ("ExamplePlugin", class_name),
        ("example_tool", tool_function),
        ("example-tab", f"{asset_prefix}-tab"),
        ("example.css", f"{asset_prefix}.css"),
        ("example-", f"{asset_prefix}-"),
        ("plugin-tab-example", f"plugin-tab-{plugin_slug_resolved}"),
        ("/plugins/example/", f"/plugins/{plugin_slug_resolved}/"),
        ("Example Plugin", display_name_resolved),
        ('label="Example"', f'label="{display_name_resolved}"'),
        ('PLUGIN_NAME = "example"', f'PLUGIN_NAME = "{plugin_slug_resolved}"'),
        ('id="example"', f'id="{plugin_slug_resolved}"'),
        ('name = "example"', f'name = "{plugin_slug_resolved}"'),
        (
            "https://github.com/az-scout/az-scout-example/issues",
            f"https://github.com/{github_owner_resolved}/{github_repo_resolved}/issues",
        ),
        (
            "https://github.com/az-scout/az-scout-example",
            f"https://github.com/{github_owner_resolved}/{github_repo_resolved}",
        ),
        ("https://pypi.org/p/az-scout-example", f"https://pypi.org/p/{package_name_resolved}"),
    ]

    regex_replacements = [
        (
            r'^example\s*=\s*"[a-zA-Z0-9_]+:plugin"$',
            f'{entrypoint_key} = "{module_name}:plugin"',
        ),
    ]

    for path in target_dir.rglob("*"):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        _replace_in_file(path, replacements)
        _replace_with_regex(path, regex_replacements)

    if isinstance(ui, _RichUI):
        ui.summary(
            location=target_dir,
            package_name=package_name_resolved,
            module_name=module_name,
            plugin_slug=plugin_slug_resolved,
        )
    else:
        ui.info("Plugin scaffold created successfully.\n")
        ui.info(f"Location: {target_dir}")
        ui.info(f"Package:  {package_name_resolved}")
        ui.info(f"Module:   {module_name}")
        ui.info(f"Slug:     {plugin_slug_resolved}\n")

    ui.info("Next steps:")
    ui.info(f"  cd '{target_dir}'")
    ui.info("  uv sync --group dev")
    ui.info("  uv pip install -e .")
    ui.info("  uv run pytest\n")
    ui.info("Then run az-scout in your main workspace to verify plugin discovery.")
    return 0


def main() -> int:
    """CLI entry for direct Python invocation of the scaffold generator."""
    return create_plugin_scaffold(prefer_rich=True)
