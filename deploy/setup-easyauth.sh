#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup-easyauth.sh – Idempotent Entra ID / EasyAuth setup for az-scout
# ---------------------------------------------------------------------------
# Creates (or reuses) an App Registration, Service Principal, client secret,
# API scope, and pre-authorization so that EasyAuth "just works" on the
# Container App.
#
# The script detects existing configuration at every step and only creates
# what is missing — safe to re-run at any time.
#
# Two-phase workflow:
#   Phase 1 (before deployment) — no --app-url needed:
#     Creates App Registration, Service Principal, client secret, and
#     optionally the MCP API scope. Outputs the Client ID and Secret
#     needed for the Bicep deployment.
#
#   Phase 2 (after deployment) — with --app-url or --resource-group:
#     Adds the EasyAuth callback redirect URI (requires the Container App
#     URL). Also adds VS Code redirect URIs if --enable-vscode is set.
#     Re-run the same command with the URL appended — already-configured
#     items are skipped.
#
# Usage:
#   # Phase 1 – prepare Entra ID resources (before deploying)
#   ./deploy/setup-easyauth.sh
#   ./deploy/setup-easyauth.sh --enable-mcp          # + MCP API scope
#
#   # Phase 2 – add redirect URIs (after deploying)
#   ./deploy/setup-easyauth.sh \
#     --app-url https://az-scout.<env>.<region>.azurecontainerapps.io
#
#   # Phase 2 – auto-detect URL from an existing deployment
#   ./deploy/setup-easyauth.sh --resource-group rg-az-scout
#
#   # Full one-shot (if you already know the URL)
#   ./deploy/setup-easyauth.sh \
#     --app-url https://az-scout.<env>.<region>.azurecontainerapps.io \
#     --enable-mcp \
#     --enable-vscode
#
# Options:
#   --app-url URL         Container App public URL (optional – see phases above)
#   --resource-group RG   Auto-detect --app-url from an existing deployment
#   --app-name NAME       Display name for App Registration (default: az-scout)
#   --enable-mcp          Expose API scope, pre-authorize Azure CLI, grant
#                         admin consent – needed for MCP bearer-token access
#   --enable-vscode       Add VS Code redirect URIs + enable public client
#                         flows (requires --app-url or --resource-group)
#   --rotate-secret       Force creation of a new client secret even if the
#                         app already has credentials
#   --quiet               Suppress informational output (errors still printed)
#   --help                Show this help message
#
# Prerequisites:
#   • Azure CLI (az) authenticated with permissions to manage App Registrations
#   • jq
# ---------------------------------------------------------------------------

set -euo pipefail

# ── Colours & helpers ─────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Colour

QUIET=false

info()  { $QUIET || printf "${CYAN}ℹ${NC}  %s\n" "$*"; }
ok()    { $QUIET || printf "${GREEN}✔${NC}  %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${NC}  %s\n" "$*" >&2; }
err()   { printf "${RED}✖${NC}  %s\n" "$*" >&2; }
bold()  { $QUIET || printf "${BOLD}%s${NC}\n" "$*"; }

# ── Defaults ──────────────────────────────────────────────────────────────

APP_URL=""
RESOURCE_GROUP=""
APP_NAME="az-scout"
ENABLE_MCP=false
ENABLE_VSCODE=false
ROTATE_SECRET=false

# Azure CLI well-known App ID
AZURE_CLI_APP_ID="04b07795-8ddb-461a-bbee-02f9e1bf7b46"

# ── Parse arguments ──────────────────────────────────────────────────────

usage() {
  awk '/^# Usage:/{found=1} found{if(/^# Prerequisites:/)exit; sub(/^# ?/,""); print}' "$0"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --app-url)        APP_URL="$2"; shift 2 ;;
    --resource-group) RESOURCE_GROUP="$2"; shift 2 ;;
    --app-name)       APP_NAME="$2"; shift 2 ;;
    --enable-mcp)     ENABLE_MCP=true; shift ;;
    --enable-vscode)  ENABLE_VSCODE=true; shift ;;
    --rotate-secret)  ROTATE_SECRET=true; shift ;;
    --quiet)          QUIET=true; shift ;;
    --help|-h)        usage ;;
    *) err "Unknown option: $1"; usage ;;
  esac
done

# Strip trailing slash if provided
APP_URL="${APP_URL%/}"

# ── Pre-flight checks ────────────────────────────────────────────────────

for cmd in az jq; do
  if ! command -v "$cmd" &>/dev/null; then
    err "'$cmd' is required but not found in PATH"
    exit 1
  fi
done

# Verify Azure CLI is logged in
if ! az account show &>/dev/null; then
  err "Azure CLI is not logged in. Run 'az login' first."
  exit 1
fi

TENANT_ID=$(az account show --query tenantId -o tsv)
info "Using tenant: $TENANT_ID"

# ── Auto-detect App URL from resource group ──────────────────────────────

if [[ -n "$RESOURCE_GROUP" && -z "$APP_URL" ]]; then
  bold "── Auto-detect Container App URL ──"
  DETECTED_FQDN=$(az containerapp list \
    --resource-group "$RESOURCE_GROUP" \
    --query "[0].properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true)

  if [[ -n "$DETECTED_FQDN" && "$DETECTED_FQDN" != "None" ]]; then
    APP_URL="https://${DETECTED_FQDN}"
    ok "Detected App URL: $APP_URL"
  else
    warn "No Container App found in resource group '$RESOURCE_GROUP'"
    warn "Continuing without --app-url (redirect URIs will be skipped)"
  fi
fi

# Determine if we can configure redirect URIs
HAS_URL=false
if [[ -n "$APP_URL" ]]; then
  HAS_URL=true
fi

# Validate --enable-vscode requires a URL
if $ENABLE_VSCODE && ! $HAS_URL; then
  warn "--enable-vscode requires --app-url or --resource-group -- skipping VS Code config this run"
  ENABLE_VSCODE=false
fi

# ── Step 1: App Registration ─────────────────────────────────────────────

bold "── App Registration ──"

# Look for existing App Registration by display name
EXISTING_APP_ID=$(az ad app list \
  --display-name "$APP_NAME" \
  --query "[0].appId" -o tsv 2>/dev/null || true)

if [[ -n "$EXISTING_APP_ID" && "$EXISTING_APP_ID" != "None" ]]; then
  APP_ID="$EXISTING_APP_ID"
  ok "Found existing App Registration: $APP_ID"
else
  info "Creating App Registration '$APP_NAME'..."
  if $HAS_URL; then
    CALLBACK_URL="${APP_URL}/.auth/login/aad/callback"
    APP_ID=$(az ad app create \
      --display-name "$APP_NAME" \
      --sign-in-audience AzureADMyOrg \
      --web-redirect-uris "$CALLBACK_URL" \
      --enable-id-token-issuance true \
      --query appId -o tsv)
    ok "Created App Registration: $APP_ID (with redirect URI)"
  else
    APP_ID=$(az ad app create \
      --display-name "$APP_NAME" \
      --sign-in-audience AzureADMyOrg \
      --enable-id-token-issuance true \
      --query appId -o tsv)
    ok "Created App Registration: $APP_ID"
    info "Redirect URI will be added when you re-run with --app-url after deployment"
  fi
fi

# Ensure ID token issuance is enabled (idempotent)
ID_TOKEN_ENABLED=$(az ad app show --id "$APP_ID" \
  --query "web.implicitGrantSettings.enableIdTokenIssuance" -o tsv 2>/dev/null || echo "false")
if [[ "$ID_TOKEN_ENABLED" == "true" ]]; then
  ok "ID token issuance already enabled"
else
  info "Enabling ID token issuance..."
  az ad app update --id "$APP_ID" \
    --enable-id-token-issuance true \
    --output none
  ok "ID token issuance enabled"
fi

# ── Step 1b: Redirect URIs (requires App URL) ────────────────────────────

if $HAS_URL; then
  CALLBACK_URL="${APP_URL}/.auth/login/aad/callback"

  EXISTING_URIS=$(az ad app show --id "$APP_ID" \
    --query "web.redirectUris" -o json 2>/dev/null || echo "[]")

  if echo "$EXISTING_URIS" | jq -e --arg uri "$CALLBACK_URL" 'index($uri)' &>/dev/null; then
    ok "EasyAuth redirect URI already configured"
  else
    info "Adding EasyAuth callback redirect URI..."
    UPDATED_URIS=$(echo "$EXISTING_URIS" | jq --arg uri "$CALLBACK_URL" '. + [$uri] | unique')
    # shellcheck disable=SC2046
    az ad app update --id "$APP_ID" \
      --web-redirect-uris $(echo "$UPDATED_URIS" | jq -r '.[]') \
      --output none
    ok "Added redirect URI: $CALLBACK_URL"
  fi
else
  info "No --app-url provided -- redirect URI will be configured in phase 2"
fi

# Get the object ID (needed for Graph API calls)
APP_OBJECT_ID=$(az ad app show --id "$APP_ID" --query id -o tsv)

# ── Step 2: Service Principal ─────────────────────────────────────────────

bold "── Service Principal ──"

SP_OBJECT_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv 2>/dev/null || true)

if [[ -n "$SP_OBJECT_ID" && "$SP_OBJECT_ID" != "None" ]]; then
  ok "Service Principal already exists: $SP_OBJECT_ID"
else
  info "Creating Service Principal…"
  SP_OBJECT_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)
  ok "Created Service Principal: $SP_OBJECT_ID"
fi

# ── Step 3: Client Secret ────────────────────────────────────────────────

bold "── Client Secret ──"

# Check if the app already has credentials
CRED_COUNT=$(az ad app credential list --id "$APP_ID" --query "length(@)" -o tsv 2>/dev/null || echo "0")

if [[ "$CRED_COUNT" -gt 0 && "$ROTATE_SECRET" == "false" ]]; then
  ok "App already has $CRED_COUNT credential(s) — skipping secret creation"
  warn "Use --rotate-secret to force creation of a new secret"
  warn "You must provide the existing secret to the Bicep deployment"
  APP_SECRET="<existing — retrieve from your records>"
else
  if [[ "$ROTATE_SECRET" == "true" && "$CRED_COUNT" -gt 0 ]]; then
    info "Rotating secret (--rotate-secret specified)…"
  else
    info "Creating client secret…"
  fi
  APP_SECRET=$(az ad app credential reset \
    --id "$APP_ID" \
    --display-name "az-scout-easyauth" \
    --query password -o tsv)
  ok "Client secret created (save it now — it cannot be retrieved later)"
fi

# ── Step 4: Expose API (MCP support) ─────────────────────────────────────

if $ENABLE_MCP; then
  bold "── API Scope (MCP) ──"

  # Check for existing identifier URI
  IDENTIFIER_URIS=$(az ad app show --id "$APP_ID" \
    --query "identifierUris" -o json 2>/dev/null || echo "[]")

  EXPECTED_URI="api://$APP_ID"
  if echo "$IDENTIFIER_URIS" | jq -e "index(\"$EXPECTED_URI\")" &>/dev/null; then
    ok "Application ID URI already set: $EXPECTED_URI"
  else
    info "Setting Application ID URI…"
    az ad app update --id "$APP_ID" \
      --identifier-uris "$EXPECTED_URI" \
      --output none
    ok "Set Application ID URI: $EXPECTED_URI"
  fi

  # Check for existing user_impersonation scope
  EXISTING_SCOPES=$(az rest --method GET \
    --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
    --query "api.oauth2PermissionScopes" -o json 2>/dev/null || echo "[]")

  SCOPE_ID=$(echo "$EXISTING_SCOPES" | jq -r '.[] | select(.value == "user_impersonation") | .id' 2>/dev/null || true)

  if [[ -n "$SCOPE_ID" ]]; then
    ok "user_impersonation scope already exists: $SCOPE_ID"
  else
    SCOPE_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
    info "Adding user_impersonation scope…"

    # Merge with any existing scopes
    NEW_SCOPES=$(echo "$EXISTING_SCOPES" | jq --arg id "$SCOPE_ID" '. + [{
      "adminConsentDescription": "Access az-scout",
      "adminConsentDisplayName": "Access az-scout",
      "id": $id,
      "isEnabled": true,
      "type": "User",
      "userConsentDescription": "Access az-scout on your behalf",
      "userConsentDisplayName": "Access az-scout",
      "value": "user_impersonation"
    }]')

    az rest --method PATCH \
      --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
      --body "$(jq -n --argjson scopes "$NEW_SCOPES" '{"api": {"oauth2PermissionScopes": $scopes}}')" \
      --output none
    ok "Added user_impersonation scope: $SCOPE_ID"
  fi

  # ── Pre-authorize Azure CLI ──

  bold "── Pre-authorize Azure CLI ──"

  EXISTING_PREAUTH=$(az rest --method GET \
    --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
    --query "api.preAuthorizedApplications" -o json 2>/dev/null || echo "[]")

  CLI_ALREADY=$(echo "$EXISTING_PREAUTH" | jq -r \
    --arg cli "$AZURE_CLI_APP_ID" '.[] | select(.appId == $cli) | .appId' 2>/dev/null || true)

  if [[ -n "$CLI_ALREADY" ]]; then
    ok "Azure CLI already pre-authorized"
  else
    info "Pre-authorizing Azure CLI…"

    NEW_PREAUTH=$(echo "$EXISTING_PREAUTH" | jq \
      --arg cli "$AZURE_CLI_APP_ID" \
      --arg scope "$SCOPE_ID" \
      '. + [{"appId": $cli, "delegatedPermissionIds": [$scope]}]')

    az rest --method PATCH \
      --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
      --body "$(jq -n --argjson preauth "$NEW_PREAUTH" '{"api": {"preAuthorizedApplications": $preauth}}')" \
      --output none
    ok "Azure CLI pre-authorized"
  fi

  # ── Admin consent ──

  bold "── Admin Consent ──"

  CLI_SP_ID=$(az ad sp show --id "$AZURE_CLI_APP_ID" --query id -o tsv 2>/dev/null || true)
  APP_SP_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv 2>/dev/null || true)

  if [[ -z "$CLI_SP_ID" || "$CLI_SP_ID" == "None" ]]; then
    warn "Azure CLI Service Principal not found in tenant — skipping admin consent"
    warn "This is unusual; re-run after 'az login' with a tenant admin account"
  elif [[ -z "$APP_SP_ID" || "$APP_SP_ID" == "None" ]]; then
    warn "App Service Principal not found — skipping admin consent"
  else
    # Check for existing OAuth2 permission grant
    EXISTING_GRANT=$(az rest --method GET \
      --uri "https://graph.microsoft.com/v1.0/oauth2PermissionGrants?\$filter=clientId eq '$CLI_SP_ID' and resourceId eq '$APP_SP_ID'" \
      --query "value[0].id" -o tsv 2>/dev/null || true)

    if [[ -n "$EXISTING_GRANT" && "$EXISTING_GRANT" != "None" ]]; then
      ok "Admin consent already granted (grant ID: $EXISTING_GRANT)"
    else
      info "Granting admin consent for Azure CLI…"
      az rest --method POST \
        --uri "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" \
        --body "$(jq -n \
          --arg clientId "$CLI_SP_ID" \
          --arg resourceId "$APP_SP_ID" \
          '{
            "clientId": $clientId,
            "consentType": "AllPrincipals",
            "resourceId": $resourceId,
            "scope": "user_impersonation"
          }')" \
        --output none 2>/dev/null && \
        ok "Admin consent granted" || \
        warn "Could not grant admin consent — you may need DelegatedPermissionGrant.ReadWrite.All permission"
    fi
  fi
fi

# ── Step 5: VS Code redirect URIs ────────────────────────────────────────

if $ENABLE_VSCODE; then
  bold "── VS Code Integration ──"

  EXISTING_URIS=$(az ad app show --id "$APP_ID" \
    --query "web.redirectUris" -o json 2>/dev/null || echo "[]")

  VSCODE_URIS=("http://localhost" "https://vscode.dev/redirect")
  URIS_TO_ADD=()

  for uri in "${VSCODE_URIS[@]}"; do
    if echo "$EXISTING_URIS" | jq -e "index(\"$uri\")" &>/dev/null; then
      ok "Redirect URI already present: $uri"
    else
      URIS_TO_ADD+=("$uri")
    fi
  done

  if [[ ${#URIS_TO_ADD[@]} -gt 0 ]]; then
    info "Adding VS Code redirect URIs…"
    UPDATED_URIS=$(echo "$EXISTING_URIS" | jq --args '. + $ARGS.positional | unique' -- "${URIS_TO_ADD[@]}")
    az ad app update --id "$APP_ID" \
      --web-redirect-uris $(echo "$UPDATED_URIS" | jq -r '.[]') \
      --output none
    ok "Added redirect URIs: ${URIS_TO_ADD[*]}"
  fi

  # Enable public client flows
  IS_PUBLIC=$(az ad app show --id "$APP_ID" \
    --query "isFallbackPublicClient" -o tsv 2>/dev/null || echo "false")

  if [[ "$IS_PUBLIC" == "true" ]]; then
    ok "Public client flows already enabled"
  else
    info "Enabling public client flows…"
    az ad app update --id "$APP_ID" \
      --is-fallback-public-client true \
      --output none
    ok "Public client flows enabled"
  fi

  # Add public client redirect URIs (separate from web redirect URIs)
  EXISTING_PUBLIC_URIS=$(az ad app show --id "$APP_ID" \
    --query "publicClient.redirectUris" -o json 2>/dev/null || echo "[]")

  PUBLIC_URIS_TO_ADD=()
  for uri in "${VSCODE_URIS[@]}"; do
    if echo "$EXISTING_PUBLIC_URIS" | jq -e "index(\"$uri\")" &>/dev/null; then
      ok "Public client redirect URI already present: $uri"
    else
      PUBLIC_URIS_TO_ADD+=("$uri")
    fi
  done

  if [[ ${#PUBLIC_URIS_TO_ADD[@]} -gt 0 ]]; then
    info "Adding public client redirect URIs…"
    az ad app update --id "$APP_ID" \
      --public-client-redirect-uris $(printf '%s ' "${PUBLIC_URIS_TO_ADD[@]}") \
      --output none
    ok "Added public client redirect URIs"
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────

echo ""
bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bold "  EasyAuth setup complete"
bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "App Registration:  $APP_NAME"
info "Client ID:         $APP_ID"
info "Tenant ID:         $TENANT_ID"
if [[ "${APP_SECRET:-}" != "<existing — retrieve from your records>" ]]; then
  printf "${YELLOW}⚠${NC}  Client Secret:    %s\n" "$APP_SECRET"
  echo ""
  warn "Save the secret above — it cannot be retrieved later!"
fi

# Show what was done and what remains
if ! $HAS_URL; then
  echo ""
  bold "Redirect URI not yet configured (no --app-url provided)."
  bold "After deploying, complete setup by re-running:"
  echo ""
  RERUN_CMD="  ./deploy/setup-easyauth.sh --app-url https://<your-app>.<region>.azurecontainerapps.io"
  if $ENABLE_MCP; then RERUN_CMD+=" --enable-mcp"; fi
  echo "$RERUN_CMD"
  echo ""
  bold "Or auto-detect from the resource group:"
  echo ""
  RERUN_CMD="  ./deploy/setup-easyauth.sh --resource-group <rg-name>"
  if $ENABLE_MCP; then RERUN_CMD+=" --enable-mcp"; fi
  echo "$RERUN_CMD"
fi

echo ""
bold "Next step — deploy with EasyAuth enabled:"
echo ""
cat <<EOF
  az deployment group create \\
    -g <resource-group> \\
    -f deploy/main.bicep \\
    -p readerSubscriptionIds='["<sub-id>"]' \\
    -p enableAuth=true \\
    -p authClientId='$APP_ID' \\
    -p authClientSecret='<secret>'
EOF

if $ENABLE_MCP; then
  echo ""
  bold "MCP token command:"
  echo ""
  echo "  az account get-access-token --resource api://$APP_ID --query accessToken -o tsv"
fi

if $ENABLE_VSCODE && $HAS_URL; then
  echo ""
  bold "VS Code MCP configuration (.vscode/mcp.json):"
  echo ""
  cat <<EOF
  {
    "servers": {
      "az-scout": {
        "type": "streamableHttp",
        "url": "${APP_URL}/mcp",
        "headers": {
          "Authorization": "Bearer \${microsoft_entra_id:${APP_ID}}"
        }
      }
    }
  }
EOF
fi
echo ""
