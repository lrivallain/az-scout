// Server-side authentication UI for az-scout OBO flow.
// No client-side token management — login/logout are standard page navigations.
// The server manages sessions via HTTP-only cookies.
// biome-ignore lint/complexity/useArrowFunction: IIFE pattern for module encapsulation
(function () {

    let _authConfig = null;
    let _userInfo = null;  // { authenticated, name, email, tenantId }

    window.azScoutAuth = {
        /** Initialize auth — check server session. */
        async init() {
            try {
                const configResp = await fetch("/api/auth/config");
                _authConfig = await configResp.json();
                if (!_authConfig.enabled) {
                    // No OBO — single-user mode, everyone is admin
                    document.body.classList.add("is-admin");
                    return;
                }

                const meResp = await fetch("/api/auth/me");
                _userInfo = await meResp.json();

                renderAuthUI();

                // Set admin class on body for CSS-based UI visibility
                if (_userInfo?.isAdmin) {
                    document.body.classList.add("is-admin");
                }

                // In OBO mode, replace tenant selector with fixed tenant label
                if (_userInfo?.authenticated && _userInfo?.tenantId) {
                    const tenantSection = document.getElementById("tenant-section");
                    const tenantSelect = document.getElementById("tenant-select");
                    if (tenantSection && tenantSelect) {
                        const name = _userInfo.tenantName || _userInfo.tenantId.slice(0, 8) + "\u2026";
                        tenantSelect.innerHTML = `<option value="${_userInfo.tenantId}">${name}</option>`;
                        tenantSelect.value = _userInfo.tenantId;
                        tenantSelect.disabled = true;
                        tenantSelect.classList.add("no-arrow");
                    }
                }
            } catch (err) {
                console.warn("Auth init failed:", err);
            }
        },

        /** Check if a user is signed in. */
        isSignedIn() {
            return _userInfo?.authenticated === true;
        },

        /** Check if OBO auth is enabled. */
        isEnabled() {
            return _authConfig?.enabled === true;
        },

        /** Returns true if OBO is enabled but user is not signed in. */
        requiresLogin() {
            return _authConfig?.enabled === true && !_userInfo?.authenticated;
        },

        /** Get the signed-in user's name. */
        getUserName() {
            return _userInfo?.name || _userInfo?.email || "";
        },

        /** Get the signed-in user's home tenant ID. */
        getHomeTenantId() {
            return _userInfo?.tenantId || "";
        },

        /** Check if the current user has Admin role (home tenant only). */
        isAdmin() {
            // When OBO is not enabled, everyone is admin (single-user mode)
            if (!_authConfig?.enabled) return true;
            return _userInfo?.isAdmin === true;
        },

        /** Navigate to server-side login. */
        login(tenant) {
            const url = tenant ? `/auth/login?tenant=${encodeURIComponent(tenant)}` : "/auth/login";
            window.location.href = url;
        },
    };

    function renderAuthUI() {
        const container = document.getElementById("auth-container");
        if (!container) return;

        if (!_authConfig?.enabled) {
            container.style.display = "none";
            return;
        }

        container.style.display = "";
        if (_userInfo?.authenticated) {
            const name = _userInfo.name || _userInfo.email || "";
            const email = _userInfo.email || "";
            container.innerHTML = `
                <span class="navbar-text text-body-secondary me-2 small"
                      data-bs-toggle="tooltip" data-bs-placement="bottom"
                      data-bs-title="${email}">
                    <i class="bi bi-person-fill"></i> ${name}
                </span>
                <a href="/auth/logout" class="btn btn-outline-secondary btn-sm">
                    <i class="bi bi-box-arrow-right"></i> Sign out
                </a>`;
            const tooltipEl = container.querySelector("[data-bs-toggle='tooltip']");
            if (tooltipEl && window.bootstrap?.Tooltip) {
                new bootstrap.Tooltip(tooltipEl);
            }
        } else {
            container.innerHTML = `
                <a href="/auth/login" class="btn btn-primary btn-sm">
                    <i class="bi bi-box-arrow-in-right"></i> Sign in
                </a>`;
        }
    }
})();
