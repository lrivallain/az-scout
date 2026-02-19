"""Unified CLI for az-scout.

Provides two subcommands:
    az-scout web  – run the web UI (FastAPI + uvicorn)
    az-scout mcp  – run the MCP server (stdio or SSE transport)

Running ``az-scout`` without a subcommand defaults to ``web``.
"""

import click

from az_scout import __version__


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="az-scout")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Azure Scout."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(web)


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind to.")
@click.option("--port", default=5001, show_default=True, help="Port to listen on.")
@click.option(
    "--no-open",
    is_flag=True,
    default=False,
    help="Don't open the browser automatically.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose logging.",
)
@click.option(
    "--reload",
    is_flag=True,
    default=False,
    help="Auto-reload on code changes (development only).",
)
@click.option(
    "--proxy-headers",
    is_flag=True,
    default=False,
    help="Trust X-Forwarded-Proto/Host headers (enable behind a reverse proxy).",
)
def web(
    host: str, port: int, no_open: bool, verbose: bool, reload: bool, proxy_headers: bool
) -> None:
    """Run the web UI (default)."""
    import logging

    import uvicorn

    from az_scout.app import _PKG_DIR, _setup_logging, app

    log_level = "info" if verbose else "warning"
    _setup_logging(level=logging.DEBUG if verbose else logging.WARNING)

    url = f"http://{host}:{port}"
    click.echo(f"✦ az-scout running at {click.style(url, fg='cyan', bold=True)}")
    if reload:
        click.echo(f"  {click.style('⟳ Auto-reload enabled', fg='yellow')}")
    click.echo("  Press Ctrl+C to stop.\n")

    if not no_open:
        import threading

        logger = logging.getLogger(__name__)

        def _open_browser() -> None:
            try:
                import webbrowser

                webbrowser.open(url)
            except Exception:
                logger.info("Could not open browser automatically – visit %s", url)

        threading.Timer(1.0, _open_browser).start()

    proxy_kwargs: dict[str, object] = {}
    if proxy_headers:
        proxy_kwargs["proxy_headers"] = True
        proxy_kwargs["forwarded_allow_ips"] = "*"

    if reload:
        uvicorn.run(
            "az_scout.app:app",
            host=host,
            port=port,
            log_level=log_level,
            reload=True,
            reload_dirs=[str(_PKG_DIR)],
            **proxy_kwargs,  # type: ignore[arg-type]
        )
    else:
        uvicorn.run(app, host=host, port=port, log_level=log_level, **proxy_kwargs)  # type: ignore[arg-type]


@cli.command()
@click.option(
    "--sse",
    is_flag=True,
    default=False,
    help="Use SSE transport instead of stdio.",
)
@click.option(
    "--port",
    default=8080,
    show_default=True,
    help="Port for SSE transport.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose logging.",
)
def mcp(sse: bool, port: int, verbose: bool) -> None:
    """Run the MCP server."""
    import logging

    from az_scout.mcp_server import mcp as mcp_server

    if verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    if sse:
        mcp_server.settings.port = port
        mcp_server.run(transport="sse")
    else:
        mcp_server.run(transport="stdio")
