"""MkDocs hook: pre- and post-build tasks (changelog copy, catalog page generation)."""

import logging
from pathlib import Path

_LINK_REWRITES: dict[str, str] = {
    "(docs/plugins.md)": "(plugins/index.md)",
    "(docs/plugin-scaffold/)": "(plugins/scaffold.md)",
}

log = logging.getLogger("mkdocs.hooks.build")


def on_pre_build(config: dict, **kwargs: object) -> None:  # type: ignore[type-arg]
    """Copy generated docs files from project root sources."""
    docs_dir = Path(config["docs_dir"])
    root = docs_dir.parent

    changelog_src = root / "CHANGELOG.md"
    changelog_dst = docs_dir / "_changelog.md"
    if changelog_src.exists():
        changelog_content = changelog_src.read_text(encoding="utf-8")
        for source, target in _LINK_REWRITES.items():
            changelog_content = changelog_content.replace(source, target)
        changelog_dst.write_text(changelog_content, encoding="utf-8")
        log.info("Copied %s → %s", changelog_src, changelog_dst)

    # # Store catalog source path for on_post_build
    # catalog_src = root / "src" / "az_scout" / "static" / "html" / "catalog.html"
    # if catalog_src.exists():
    #     config["_catalog_src"] = str(catalog_src)


def on_post_build(config: dict, **kwargs: object) -> None:  # type: ignore[type-arg]
    """Generate standalone catalog page directly in the built site."""
    root = Path(config["docs_dir"]).parent
    catalog_src = root / "src" / "az_scout" / "static" / "html" / "catalog.html"
    if not catalog_src.exists():
        return

    wrapper = Path(__file__).with_name("catalog_wrapper.html")
    template = wrapper.read_text(encoding="utf-8")
    fragment = catalog_src.read_text(encoding="utf-8")
    standalone = template.replace("<!-- CATALOG_FRAGMENT -->", fragment)

    site_dir = Path(config["site_dir"])
    dst = site_dir / "catalog.html"
    dst.write_text(standalone, encoding="utf-8")
    log.info("Generated standalone catalog page → %s", dst)
