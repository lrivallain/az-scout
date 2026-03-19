// MSAL.js authentication for az-scout OBO flow.
// Loaded conditionally when /api/auth/config returns {enabled: true}.
// Depends on msal-browser loaded from CDN.
// biome-ignore lint/complexity/useArrowFunction: IIFE pattern for module encapsulation
(function () {

    let msalInstance = null;
    let activeAccount = null;
    let authConfig = null;
    // Per-tenant token cache — used to persist MFA-satisfied tokens
    // so they survive the acquireTokenSilent round-trip.
    const _tenantTokens = {};  // { tenantId: { token, expiresOn } }
    // Per-tenant direct ARM tokens (MFA fallback — bypass OBO entirely)
    const _directArmTokens = {};  // { tenantId: { token, expiresOn } }

    // ---- Public API (used by app.js) ----
    window.azScoutAuth = {
        /** Initialize auth — call once on page load. */
        async init() {
            try {
                const resp = await fetch("/api/auth/config");
                authConfig = await resp.json();
                if (!authConfig.enabled) return;

                // Load MSAL.js from CDN
                await loadScript(
                    "https://cdn.jsdelivr.net/npm/@azure/msal-browser@4/lib/msal-browser.min.js"
                );

                msalInstance = new msal.PublicClientApplication({
                    auth: {
                        clientId: authConfig.clientId,
                        authority: authConfig.authority,
                        redirectUri: window.location.origin,
                        clientCapabilities: ["CP1"],
                    },
                    cache: { cacheLocation: "sessionStorage" },
                });

                await msalInstance.initialize();

                // Handle redirect callback
                const response = await msalInstance.handleRedirectPromise();
                if (response) {
                    activeAccount = response.account;
                    msalInstance.setActiveAccount(activeAccount);
                }

                // Check for existing session
                if (!activeAccount) {
                    const accounts = msalInstance.getAllAccounts();
                    if (accounts.length > 0) {
                        activeAccount = accounts[0];
                        msalInstance.setActiveAccount(activeAccount);
                    }
                }

                renderAuthUI();
            } catch (err) {
                console.warn("Auth init failed:", err);
            }
        },

        /** Get access token for API calls (silent or interactive). */
        async getToken() {
            if (!msalInstance || !activeAccount) return null;
            try {
                const result = await msalInstance.acquireTokenSilent({
                    scopes: authConfig.scopes,
                    account: activeAccount,
                });
                return result.accessToken;
            } catch {
                // Silent failed — try interactive
                try {
                    const result = await msalInstance.acquireTokenPopup({
                        scopes: authConfig.scopes,
                    });
                    activeAccount = result.account;
                    msalInstance.setActiveAccount(activeAccount);
                    renderAuthUI();
                    return result.accessToken;
                } catch (err) {
                    console.error("Token acquisition failed:", err);
                    return null;
                }
            }
        },

        /** Check if a user is signed in. */
        isSignedIn() {
            return !!activeAccount;
        },

        /** Check if OBO auth is enabled. */
        isEnabled() {
            return authConfig?.enabled === true;
        },

        /** Get the signed-in user's name. */
        getUserName() {
            return activeAccount?.name || activeAccount?.username || "";
        },

        /** Get the signed-in user's home tenant ID. */
        getHomeTenantId() {
            return activeAccount?.tenantId || "";
        },

        /** Get a token scoped to a specific tenant (re-authenticates if needed). */
        async getTokenForTenant(tenantId) {
            if (!msalInstance || !activeAccount || !tenantId) return null;
            // Check local cache first (populated by reacquireWithClaims)
            const cached = _tenantTokens[tenantId];
            if (cached && cached.expiresOn > Date.now()) {
                return cached.token;
            }
            try {
                // Try silent token for the target tenant using the app's scope
                const result = await msalInstance.acquireTokenSilent({
                    scopes: authConfig.scopes,
                    account: activeAccount,
                    authority: `https://login.microsoftonline.com/${tenantId}`,
                });
                return result.accessToken;
            } catch {
                // Silent failed — need interactive login for this tenant
                try {
                    const result = await msalInstance.acquireTokenPopup({
                        scopes: authConfig.scopes,
                        account: activeAccount,
                        authority: `https://login.microsoftonline.com/${tenantId}`,
                    });
                    return result.accessToken;
                } catch (err) {
                    console.error(`Token for tenant ${tenantId} failed:`, err);
                    return null;
                }
            }
        },

        /** Re-acquire token with claims challenge (triggers MFA popup). */
        async reacquireWithClaims(tenantId, claims) {
            if (!msalInstance || !activeAccount) return null;
            try {
                const authority = tenantId
                    ? `https://login.microsoftonline.com/${tenantId}`
                    : authConfig.authority;
                // If claims is empty, force a fresh login to trigger MFA
                const popupRequest = {
                    scopes: authConfig.scopes,
                    account: activeAccount,
                    authority,
                };
                if (claims) {
                    popupRequest.claims = claims;
                } else {
                    // No claims from Azure AD — force full re-auth
                    popupRequest.prompt = "login";
                }
                const result = await msalInstance.acquireTokenPopup(popupRequest);
                // Cache this MFA-satisfied token so getTokenForTenant finds it
                const tid = tenantId || "_home_";
                _tenantTokens[tid] = {
                    token: result.accessToken,
                    // MSAL expiresOn is a Date; keep 2 min margin
                    expiresOn: (result.expiresOn?.getTime?.() || (Date.now() + 3600_000)) - 120_000,
                };
                return result.accessToken;
            } catch (err) {
                console.error("MFA re-authentication failed:", err);
                return null;
            }
        },

        /**
         * Acquire a token directly for ARM (bypassing OBO).
         * Used when the target tenant's CA policy requires MFA for ARM and
         * OBO can't relay the claims challenge.
         */
        async acquireDirectArmToken(tenantId) {
            if (!msalInstance || !activeAccount || !tenantId) return false;
            try {
                const result = await msalInstance.acquireTokenPopup({
                    scopes: ["https://management.azure.com/.default"],
                    account: activeAccount,
                    authority: `https://login.microsoftonline.com/${tenantId}`,
                });
                _directArmTokens[tenantId] = {
                    token: result.accessToken,
                    expiresOn: (result.expiresOn?.getTime?.() || (Date.now() + 3600_000)) - 120_000,
                };
                console.log(`Direct ARM token acquired for tenant ${tenantId}`);
                return true;
            } catch (err) {
                console.error(`Direct ARM token for tenant ${tenantId} failed:`, err);
                return false;
            }
        },

        /** Get cached direct ARM token for a tenant (or null). */
        getDirectArmToken(tenantId) {
            const cached = _directArmTokens[tenantId];
            if (cached && cached.expiresOn > Date.now()) return cached.token;
            return null;
        },

        /** Returns true if OBO is enabled but user is not signed in. */
        requiresLogin() {
            return authConfig?.enabled === true && !activeAccount;
        },

        /** Trigger login (called from sign-in screen). */
        async login() {
            return login();
        },
    };

    // ---- Login / Logout ----
    async function login() {
        if (!msalInstance) return;
        try {
            console.log("MSAL: starting loginPopup with scopes:", authConfig.scopes);
            const result = await msalInstance.loginPopup({
                scopes: authConfig.scopes,
                prompt: "select_account",
            });
            console.log("MSAL: login success, account:", result.account?.username);
            activeAccount = result.account;
            msalInstance.setActiveAccount(activeAccount);
            renderAuthUI();
            // Reload the entire page to reinitialize with user identity
            window.location.reload();
        } catch (err) {
            console.error("Login failed:", err);
            // Show error on sign-in screen
            const screen = document.getElementById("obo-signin-screen");
            if (screen) {
                let errDiv = screen.querySelector(".auth-error");
                if (!errDiv) {
                    errDiv = document.createElement("div");
                    errDiv.className = "auth-error alert alert-danger mt-3";
                    errDiv.style.maxWidth = "420px";
                    screen.querySelector("div")?.appendChild(errDiv);
                }
                const msg = err.errorMessage || err.message || String(err);
                errDiv.innerHTML = `<strong>Sign-in failed:</strong> ${msg}`;
            }
        }
    }

    function logout() {
        if (!msalInstance) return;
        activeAccount = null;
        // Clear all cached tokens
        for (const k of Object.keys(_tenantTokens)) delete _tenantTokens[k];
        for (const k of Object.keys(_directArmTokens)) delete _directArmTokens[k];
        msalInstance.clearCache();
        localStorage.removeItem("azscout_tenant");
        window.location.reload();
    }

    function hideSignInScreen() {
        const screen = document.getElementById("obo-signin-screen");
        if (screen) screen.style.display = "none";
        const main = document.getElementById("main-content");
        if (main) main.style.display = "";
    }

    // ---- UI ----
    function renderAuthUI() {
        const container = document.getElementById("auth-container");
        if (!container) return;

        if (!authConfig?.enabled) {
            container.style.display = "none";
            return;
        }

        container.style.display = "";
        if (activeAccount) {
            const name = activeAccount.name || activeAccount.username;
            const email = activeAccount.username || "";
            container.innerHTML = `
                <span class="navbar-text text-body-secondary me-2 small"
                      data-bs-toggle="tooltip" data-bs-placement="bottom"
                      data-bs-title="${email}">
                    <i class="bi bi-person-fill"></i> ${name}
                </span>
                <button class="btn btn-outline-secondary btn-sm" id="auth-logout-btn">
                    <i class="bi bi-box-arrow-right"></i> Sign out
                </button>`;
            document.getElementById("auth-logout-btn")
                ?.addEventListener("click", logout);
            // Initialize Bootstrap tooltip on the user name
            const tooltipEl = container.querySelector("[data-bs-toggle='tooltip']");
            if (tooltipEl && window.bootstrap?.Tooltip) {
                new bootstrap.Tooltip(tooltipEl);
            }
        } else {
            container.innerHTML = `
                <button class="btn btn-primary btn-sm" id="auth-login-btn">
                    <i class="bi bi-box-arrow-in-right"></i> Sign in
                </button>`;
            document.getElementById("auth-login-btn")
                ?.addEventListener("click", login);
        }
    }

    // ---- Helpers ----
    function loadScript(src) {
        return new Promise((resolve, reject) => {
            if (document.querySelector(`script[src="${src}"]`)) {
                resolve();
                return;
            }
            const s = document.createElement("script");
            s.src = src;
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }
})();
