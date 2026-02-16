"""Azure Availability Zone Mapping Viewer.

Interactive web tool to visualize how Azure maps logical availability zones
to physical zones across subscriptions in a given region.
"""

import base64
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click
import requests
from azure.identity import DefaultAzureCredential
from flask import Flask, Response, jsonify, render_template
from flask import request as flask_request

_PKG_DIR = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(_PKG_DIR / "templates"),
    static_folder=str(_PKG_DIR / "static"),
)
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Silence noisy third-party loggers
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

credential = DefaultAzureCredential()

AZURE_API_VERSION = "2022-12-01"
AZURE_MGMT_URL = "https://management.azure.com"


def _get_headers(tenant_id: str | None = None) -> dict[str, str]:
    """Get authorization headers using DefaultAzureCredential.

    When *tenant_id* is provided, the token is scoped to that tenant.
    """
    kwargs: dict[str, str] = {}
    if tenant_id:
        kwargs["tenant_id"] = tenant_id
    token = credential.get_token(f"{AZURE_MGMT_URL}/.default", **kwargs)
    return {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index() -> str:
    """Serve the main page."""
    return render_template("index.html")


def _get_default_tenant_id() -> str | None:
    """Extract the tenant ID from the current credential's token."""
    try:
        token = credential.get_token(f"{AZURE_MGMT_URL}/.default")
        # JWT payload is the second dot-separated segment, base64url-encoded
        payload = token.token.split(".")[1]
        # Pad base64 to a multiple of 4
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        tid: str | None = claims.get("tid") or claims.get("tenant_id")
        return tid
    except Exception:
        return None


def _check_tenant_auth(tenant_id: str) -> bool:
    """Return True if the credential can obtain a token for *tenant_id*."""
    try:
        credential.get_token(f"{AZURE_MGMT_URL}/.default", tenant_id=tenant_id)
        return True
    except Exception:
        return False


@app.route("/api/tenants")
def list_tenants() -> Response | tuple[Response, int]:
    """Return Azure AD tenants accessible by the current credential.

    Each tenant includes an ``authenticated`` flag indicating whether the
    current credential can obtain a valid token for that tenant.  The
    response also carries a ``defaultTenantId`` for the current auth context.
    """
    try:
        headers = _get_headers()
        url = f"{AZURE_MGMT_URL}/tenants?api-version={AZURE_API_VERSION}"
        all_tenants: list[dict] = []

        while url:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            all_tenants.extend(data.get("value", []))
            url = data.get("nextLink")

        tenant_ids = [t["tenantId"] for t in all_tenants]

        # Probe auth for each tenant concurrently
        with ThreadPoolExecutor(max_workers=min(len(tenant_ids), 8)) as pool:
            auth_results = dict(
                zip(tenant_ids, pool.map(_check_tenant_auth, tenant_ids), strict=True)
            )

        tenants = [
            {
                "id": t["tenantId"],
                "name": t.get("displayName") or t["tenantId"],
                "authenticated": auth_results.get(t["tenantId"], False),
            }
            for t in all_tenants
        ]
        default_tid = _get_default_tenant_id()
        return jsonify(
            {
                "tenants": sorted(tenants, key=lambda x: x["name"].lower()),
                "defaultTenantId": default_tid,
            }
        )
    except Exception as exc:
        logger.exception("Failed to list tenants")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/subscriptions")
def list_subscriptions() -> Response | tuple[Response, int]:
    """Return all enabled Azure subscriptions the caller has access to."""
    tenant_id = flask_request.args.get("tenantId")
    try:
        headers = _get_headers(tenant_id)
        url = f"{AZURE_MGMT_URL}/subscriptions?api-version={AZURE_API_VERSION}"
        all_subs: list[dict] = []

        while url:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            all_subs.extend(data.get("value", []))
            url = data.get("nextLink")

        subs = [
            {"id": s["subscriptionId"], "name": s["displayName"]}
            for s in all_subs
            if s.get("state") == "Enabled"
        ]
        return jsonify(sorted(subs, key=lambda x: x["name"].lower()))
    except Exception as exc:
        logger.exception("Failed to list subscriptions")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/regions")
def list_regions() -> Response | tuple[Response, int]:
    """Return regions that support availability zones.

    If no subscriptionId is provided, the first enabled subscription
    is discovered automatically.
    """
    sub_id = flask_request.args.get("subscriptionId")
    tenant_id = flask_request.args.get("tenantId")

    try:
        headers = _get_headers(tenant_id)

        # Auto-discover a subscription when none specified
        if not sub_id:
            subs_url = f"{AZURE_MGMT_URL}/subscriptions?api-version={AZURE_API_VERSION}"
            subs_resp = requests.get(subs_url, headers=headers, timeout=30)
            subs_resp.raise_for_status()
            enabled = [
                s["subscriptionId"]
                for s in subs_resp.json().get("value", [])
                if s.get("state") == "Enabled"
            ]
            if not enabled:
                return jsonify({"error": "No enabled subscriptions found"}), 404
            sub_id = enabled[0]

        url = f"{AZURE_MGMT_URL}/subscriptions/{sub_id}/locations?api-version={AZURE_API_VERSION}"
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        locations = resp.json().get("value", [])
        regions = [
            {"name": loc["name"], "displayName": loc["displayName"]}
            for loc in locations
            if loc.get("availabilityZoneMappings")
            and loc.get("metadata", {}).get("regionType") == "Physical"
        ]
        return jsonify(sorted(regions, key=lambda x: x["displayName"]))
    except Exception as exc:
        logger.exception("Failed to list regions")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/mappings")
def get_mappings() -> Response | tuple[Response, int]:
    """Return AZ logical-to-physical mappings for selected subscriptions/region."""
    region = flask_request.args.get("region")
    sub_ids_raw = flask_request.args.get("subscriptions", "")
    sub_ids = [s.strip() for s in sub_ids_raw.split(",") if s.strip()]

    tenant_id = flask_request.args.get("tenantId")

    if not region or not sub_ids:
        return (
            jsonify({"error": "Both 'region' and 'subscriptions' query parameters are required"}),
            400,
        )

    headers = _get_headers(tenant_id)
    results: list[dict] = []

    for sub_id in sub_ids:
        url = f"{AZURE_MGMT_URL}/subscriptions/{sub_id}/locations?api-version={AZURE_API_VERSION}"
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            locations = resp.json().get("value", [])

            mappings: list[dict] = []
            for loc in locations:
                if loc["name"] == region:
                    for m in loc.get("availabilityZoneMappings", []):
                        mappings.append(
                            {
                                "logicalZone": m["logicalZone"],
                                "physicalZone": m["physicalZone"],
                            }
                        )
                    break

            results.append(
                {
                    "subscriptionId": sub_id,
                    "region": region,
                    "mappings": sorted(mappings, key=lambda m: m["logicalZone"]),
                }
            )
        except Exception as exc:
            logger.warning("Error fetching mappings for subscription %s: %s", sub_id, exc)
            results.append(
                {
                    "subscriptionId": sub_id,
                    "region": region,
                    "mappings": [],
                    "error": str(exc),
                }
            )

    return jsonify(results)


@app.route("/api/skus")
def get_skus() -> Response | tuple[Response, int]:
    """Return resource SKUs with zone restrictions for a given region and subscription.

    Uses the Azure Resource SKUs API to fetch VM sizes and other resource types,
    filtering by region and extracting zone availability information.
    """
    region = flask_request.args.get("region")
    sub_id = flask_request.args.get("subscriptionId")
    tenant_id = flask_request.args.get("tenantId")
    resource_type = flask_request.args.get("resourceType", "virtualMachines")

    if not region or not sub_id:
        return (
            jsonify({"error": "Both 'region' and 'subscriptionId' query parameters are required"}),
            400,
        )

    try:
        headers = _get_headers(tenant_id)
        # Use server-side filtering for region to reduce response size
        url = (
            f"{AZURE_MGMT_URL}/subscriptions/{sub_id}/providers/"
            f"Microsoft.Compute/skus?api-version={AZURE_API_VERSION}"
            f"&$filter=location eq '{region}'"
        )

        all_skus: list[dict] = []

        # Simple retry logic with exponential backoff (max 3 attempts)
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=headers, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                all_skus.extend(data.get("value", []))
                url = data.get("nextLink")

                # Handle pagination
                while url:
                    resp = requests.get(url, headers=headers, timeout=60)
                    resp.raise_for_status()
                    data = resp.json()
                    all_skus.extend(data.get("value", []))
                    url = data.get("nextLink")
                break  # Success, exit retry loop
            except requests.ReadTimeout:
                if attempt < 2:  # Not the last attempt
                    wait_time = 2 ** attempt  # 1s, 2s exponential backoff
                    logger.warning(
                        f"SKU API timeout, retrying in {wait_time}s (attempt {attempt + 1}/3)"
                    )
                    time.sleep(wait_time)
                else:
                    raise  # Last attempt failed, re-raise

        # Filter SKUs by resource type only (region already filtered by API)
        filtered_skus = []
        for sku in all_skus:
            if sku.get("resourceType") != resource_type:
                continue

            # Extract zone information
            location_info = sku.get("locationInfo", [])
            zones_for_region = []

            for loc_info in location_info:
                if loc_info.get("location", "").lower() == region.lower():
                    zones_for_region = loc_info.get("zones", [])
                    break

            # Extract restrictions
            restrictions = []
            for restriction in sku.get("restrictions", []):
                if restriction.get("type") == "Zone":
                    restrictions.extend(restriction.get("restrictionInfo", {}).get("zones", []))

            # Extract capabilities
            capabilities = {}
            for cap in sku.get("capabilities", []):
                name = cap.get("name", "")
                value = cap.get("value", "")
                if name in ["vCPUs", "MemoryGB", "MaxDataDiskCount", "PremiumIO"]:
                    capabilities[name] = value

            filtered_skus.append(
                {
                    "name": sku.get("name"),
                    "tier": sku.get("tier"),
                    "size": sku.get("size"),
                    "family": sku.get("family"),
                    "zones": zones_for_region,
                    "restrictions": restrictions,
                    "capabilities": capabilities,
                }
            )

        return jsonify(sorted(filtered_skus, key=lambda x: x.get("name", "")))
    except requests.RequestException as exc:
        logger.exception("Failed to fetch SKUs")
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------


@click.command()
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
def main(host: str, port: int, no_open: bool, verbose: bool) -> None:
    """Run the Azure AZ Mapping Viewer."""
    if verbose:
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("werkzeug").setLevel(logging.INFO)
        logging.getLogger("azure").setLevel(logging.INFO)
    else:
        # Suppress Flask/Werkzeug startup banner in quiet mode
        import flask.cli

        flask.cli.show_server_banner = lambda *_args, **_kwargs: None

    url = f"http://{host}:{port}"
    click.echo(f"✦ az-mapping running at {click.style(url, fg='cyan', bold=True)}")
    click.echo("  Press Ctrl+C to stop.\n")

    if not no_open:
        import threading

        def _open_browser() -> None:
            try:
                import webbrowser

                webbrowser.open(url)
            except Exception:
                logger.info("Could not open browser automatically – visit %s", url)

        threading.Timer(1.0, _open_browser).start()

    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
