/* ===================================================================
   Azure Scout – Core  (shared state, theme, init, API helpers)
   See also: az-mapping.js, planner.js, chat.js
   =================================================================== */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let subscriptions = [];                     // [{id, name}] – all subs for current tenant
let regions = [];                           // [{name, displayName}]
let tenants = [];                           // [{id, name, authenticated}]

function emitContextEvent(name, detail) {
    document.dispatchEvent(new CustomEvent(name, { detail }));
}

// ---------------------------------------------------------------------------
// Theme management  (Bootstrap uses data-bs-theme)
// ---------------------------------------------------------------------------
function getEffectiveTheme() {
    const stored = localStorage.getItem("theme");
    if (stored === "dark" || stored === "light") return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
    document.documentElement.setAttribute("data-bs-theme", theme);
    const iconDark = document.getElementById("icon-dark");
    const iconLight = document.getElementById("icon-light");
    if (iconDark && iconLight) {
        iconDark.classList.toggle("d-none", theme === "dark");
        iconLight.classList.toggle("d-none", theme !== "dark");
    }
    // Switch highlight.js theme stylesheet
    const hljsLink = document.getElementById("hljs-theme");
    if (hljsLink) {
        const variant = theme === "dark" ? "atom-one-dark" : "atom-one-light";
        hljsLink.href = `https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/${variant}.min.css`;
    }
}

function toggleTheme() {
    const next = getEffectiveTheme() === "dark" ? "light" : "dark";
    localStorage.setItem("theme", next);
    applyTheme(next);
}

// Apply immediately to prevent flash
applyTheme(getEffectiveTheme());
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!localStorage.getItem("theme")) applyTheme(getEffectiveTheme());
});

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", init);

async function init() {
    // Initialize auth UI (navbar user info)
    if (window.azScoutAuth) await window.azScoutAuth.init();

    // Restore column visibility preferences
    _restoreColumnPrefs();

    // Restore chat state immediately (before any async work to avoid flash)
    _restoreChatHistory();

    // Tenant change
    document.getElementById("tenant-select").addEventListener("change", onTenantChange);

    // Init shared region combobox
    initRegionCombobox();

    // Hash-based tab routing (supports built-in + plugin tabs)
    const tabEl = document.querySelector('#mainTabs');
    if (tabEl) {
        tabEl.addEventListener('shown.bs.tab', (e) => {
            const target = e.target.getAttribute('data-bs-target');
            // Built-in tabs: #tab-topology → #topology, #tab-planner → #planner, etc.
            // Plugin tabs:   #tab-example  → #example
            const hash = target ? '#' + target.replace(/^#tab-/, '') : '#topology';
            window.history.replaceState(null, '', hash);
        });
    }
    // Activate tab from hash (built-in or plugin)
    const hash = window.location.hash.replace(/^#/, '');
    if (hash && hash !== 'topology') {
        const tabBtn = document.getElementById(hash + '-tab');
        if (tabBtn) new bootstrap.Tab(tabBtn).show();
    }

    // Load tenants
    await fetchTenants();

    // Load regions + subscriptions in parallel
    await Promise.all([fetchRegions(), fetchSubscriptions()]);

    updateTopoLoadButton();
    if (typeof updatePlannerLoadButton === "function") updatePlannerLoadButton();
}

// ---------------------------------------------------------------------------
// OBO sign-in screen (shown when OBO is enabled but user is not signed in)
// ---------------------------------------------------------------------------
function showSignInScreen() {
    // Hide main content
    const main = document.getElementById("main-content");
    if (main) main.style.display = "none";

    // Create sign-in screen
    let screen = document.getElementById("obo-signin-screen");
    if (!screen) {
        screen = document.createElement("div");
        screen.id = "obo-signin-screen";
        screen.className = "d-flex flex-column align-items-center justify-content-center";
        screen.style.cssText = "min-height: 60vh; text-align: center;";
        screen.innerHTML = `
            <div style="max-width: 420px;">
                <img src="/static/img/favicon.svg" width="64" height="64" alt="" class="mb-3 opacity-75">
                <h3 class="mb-2">Welcome to Azure Scout</h3>
                <p class="text-body-secondary mb-4">
                    Sign in with your Microsoft account to explore Azure resources
                    using your own permissions.
                </p>
                <a href="/auth/login" class="btn btn-primary btn-lg">
                    <i class="bi bi-microsoft"></i> Sign in with Microsoft
                </a>
                <p class="text-body-secondary small mt-3">
                    Your Azure RBAC permissions determine what you can access.
                </p>
            </div>`;
        document.querySelector(".container-fluid.mt-3")?.prepend(screen);
    }
}

// ---------------------------------------------------------------------------
// URL hash helper  (only stores active tab, no query params)
// ---------------------------------------------------------------------------
function getActiveTabFromHash() {
    const h = window.location.hash.replace(/^#/, '');
    return h || "topology";
}

// ---------------------------------------------------------------------------
// API helpers
// Cookies are sent automatically — no client-side token injection needed.
// ---------------------------------------------------------------------------

async function apiFetch(url) {
    const resp = await fetch(url);
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        // Session expired or not authenticated — redirect to login
        if (resp.status === 401 && body.error === "Authentication required") {
            window.location.href = "/auth/login";
            return;
        }
        throw new Error(body.error || body.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
}

async function apiPost(url, body) {
    const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        if (resp.status === 401 && data.error === "Authentication required") {
            window.location.href = "/auth/login";
            return;
        }
        throw new Error(data.error || data.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
}

function showError(targetId, msg, { html = false } = {}) {
    const el = document.getElementById(targetId);
    if (!el) return;
    if (html) el.innerHTML = msg;
    else el.textContent = msg;
    el.classList.remove("d-none");
}

function hideError(targetId) {
    const el = document.getElementById(targetId);
    if (el) el.classList.add("d-none");
}

function tenantQS(prefix) {
    const tid = document.getElementById("tenant-select").value;
    if (!tid) return "";
    return (prefix || "&") + "tenantId=" + encodeURIComponent(tid);
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

function truncate(str, max) {
    return str.length > max ? str.substring(0, max - 1) + "\u2026" : str;
}

/** Format a number with narrow no-break space thousands separator. */
function formatNum(value, decimals) {
    if (value == null) return "\u2014";
    const fixed = Number(value).toFixed(decimals);
    const [intPart, decPart] = fixed.split(".");
    const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, "\u00A0");
    return decPart != null ? grouped + "." + decPart : grouped;
}

function getSubName(id) {
    const s = subscriptions.find(s => s.id === id);
    return s ? s.name : id.substring(0, 8) + "\u2026";
}

// ---------------------------------------------------------------------------
// Tenants
// ---------------------------------------------------------------------------
async function fetchTenants() {
    const select = document.getElementById("tenant-select");
    select.innerHTML = '<option value="">Loading tenants\u2026</option>';
    try {
        const result = await apiFetch("/api/tenants");
        tenants = result.tenants || [];
        const defaultTid = result.defaultTenantId || "";
        const authTenants = tenants.filter(t => t.authenticated);
        const hiddenCount = tenants.length - authTenants.length;
        const hiddenOpt = hiddenCount > 0
            ? `<option disabled>+${hiddenCount} tenant${hiddenCount > 1 ? "s" : ""} hidden (no valid auth)</option>`
            : "";
        if (authTenants.length <= 1) {
            if (authTenants.length === 1) {
                select.innerHTML = `<option value="${authTenants[0].id}">${escapeHtml(authTenants[0].name)}</option>${hiddenOpt}`;
                select.value = authTenants[0].id;
                select.disabled = true;
                select.classList.add("no-arrow");
            } else {
                document.getElementById("tenant-section").classList.add("d-none");
            }
            emitContextEvent("azscout:tenants-loaded", {
                tenants,
                defaultTenantId: defaultTid,
                tenantId: select.value || "",
            });
            return;
        }
        select.innerHTML = authTenants.map(t => {
            const label = `${escapeHtml(t.name)} (${t.id.slice(0, 8)}\u2026)`;
            return `<option value="${t.id}">${label}</option>`;
        }).join("") + hiddenOpt;
        if (defaultTid && authTenants.some(t => t.id === defaultTid)) {
            select.value = defaultTid;
        }
        // Restore last-used tenant from localStorage
        const savedTid = localStorage.getItem("azscout_tenant");
        if (savedTid && authTenants.some(t => t.id === savedTid)) {
            select.value = savedTid;
        }
        emitContextEvent("azscout:tenants-loaded", {
            tenants,
            defaultTenantId: defaultTid,
            tenantId: select.value || "",
        });
    } catch {
        document.getElementById("tenant-section").classList.add("d-none");
        emitContextEvent("azscout:tenants-loaded", {
            tenants: [],
            defaultTenantId: "",
            tenantId: "",
        });
    }
}

async function onTenantChange() {
    // Save selected tenant for next reload
    const selectedTid = document.getElementById("tenant-select")?.value || "";
    if (selectedTid) {
        localStorage.setItem("azscout_tenant", selectedTid);
    } else {
        localStorage.removeItem("azscout_tenant");
    }

    emitContextEvent("azscout:tenant-changed", {
        tenantId: selectedTid,
    });

    // Reset all downstream state
    if (typeof topoSelectedSubs !== "undefined") topoSelectedSubs.clear();
    lastMappingData = null;
    if (typeof plannerSubscriptionId !== "undefined") plannerSubscriptionId = null;
    if (typeof plannerZoneMappings !== "undefined") plannerZoneMappings = null;
    if (typeof lastSkuData !== "undefined") lastSkuData = null;
    if (typeof lastSpotScores !== "undefined") lastSpotScores = null;

    // Remove MFA overlay if visible (tenant changed)
    const mfaOverlay = document.getElementById("mfa-overlay");
    if (mfaOverlay) mfaOverlay.remove();
    const tabs = document.getElementById("mainTabs");
    const tabContent = document.getElementById("mainTabContent");
    if (tabs) tabs.style.display = "";
    if (tabContent) tabContent.style.display = "";

    document.getElementById("region-select").value = "";
    document.getElementById("region-search").value = "";
    const topoFilter = document.getElementById("topo-sub-filter");
    if (topoFilter) topoFilter.value = "";
    const plannerSubSel = document.getElementById("planner-sub-select");
    if (plannerSubSel) plannerSubSel.value = "";
    const plannerSubSearch = document.getElementById("planner-sub-search");
    if (plannerSubSearch) plannerSubSearch.value = "";

    // Reset UI panels
    showPanel("topo", "empty");
    showPanel("planner", "empty");
    hideError("topo-error");
    hideError("planner-error");

    await Promise.all([fetchRegions(), fetchSubscriptions()]);
    updateTopoLoadButton();
    if (typeof updatePlannerLoadButton === "function") updatePlannerLoadButton();
}

// ---------------------------------------------------------------------------
// Regions  (single shared combobox)
// ---------------------------------------------------------------------------
async function fetchRegions() {
    const inp = document.getElementById("region-search");
    inp.placeholder = "Loading regions\u2026";
    inp.disabled = true;

    try {
        regions = await apiFetch("/api/regions" + tenantQS("?"));
        emitContextEvent("azscout:regions-loaded", {
            regions,
            tenantId: document.getElementById("tenant-select")?.value || "",
        });
        inp.placeholder = "Type to search regions\u2026";
        inp.disabled = false;
        renderRegionDropdown("");
    } catch (err) {
        if (!err) return; // redirect in progress
        inp.placeholder = "Error loading regions";
    }
}

// ---------------------------------------------------------------------------
// Shared region combobox
// ---------------------------------------------------------------------------
function initRegionCombobox() {
    const searchInput = document.getElementById("region-search");
    const dropdown = document.getElementById("region-dropdown");

    searchInput.addEventListener("focus", () => {
        searchInput.select();
        renderRegionDropdown(searchInput.value.includes("(") ? "" : searchInput.value);
        dropdown.classList.add("show");
    });
    searchInput.addEventListener("input", () => {
        document.getElementById("region-select").value = "";
        renderRegionDropdown(searchInput.value);
        dropdown.classList.add("show");
        onRegionChange();
    });
    searchInput.addEventListener("keydown", (e) => {
        const items = dropdown.querySelectorAll("li");
        const active = dropdown.querySelector("li.active");
        let idx = [...items].indexOf(active);
        if (e.key === "ArrowDown") {
            e.preventDefault();
            if (!dropdown.classList.contains("show")) dropdown.classList.add("show");
            if (active) active.classList.remove("active");
            idx = (idx + 1) % items.length;
            items[idx]?.classList.add("active");
            items[idx]?.scrollIntoView({ block: "nearest" });
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            if (active) active.classList.remove("active");
            idx = idx <= 0 ? items.length - 1 : idx - 1;
            items[idx]?.classList.add("active");
            items[idx]?.scrollIntoView({ block: "nearest" });
        } else if (e.key === "Enter") {
            e.preventDefault();
            if (active) selectRegion(active.dataset.value);
            else if (items.length === 1) selectRegion(items[0].dataset.value);
        } else if (e.key === "Escape") {
            dropdown.classList.remove("show");
            searchInput.blur();
        }
    });
    document.addEventListener("click", (e) => {
        if (!e.target.closest("#region-combobox")) dropdown.classList.remove("show");
    });
}

function renderRegionDropdown(filter) {
    const dropdown = document.getElementById("region-dropdown");
    const lc = (filter || "").toLowerCase();
    const matches = lc
        ? regions.filter(r => r.displayName.toLowerCase().includes(lc) || r.name.toLowerCase().includes(lc))
        : regions;
    dropdown.innerHTML = matches.map(r =>
        `<li class="dropdown-item" data-value="${r.name}">${escapeHtml(r.displayName)} <span class="region-name">(${r.name})</span></li>`
    ).join("");
    dropdown.querySelectorAll("li").forEach(li => {
        li.addEventListener("click", () => selectRegion(li.dataset.value));
    });
}

function selectRegion(name) {
    const r = regions.find(r => r.name === name);
    if (!r) return;
    document.getElementById("region-select").value = name;
    document.getElementById("region-search").value = `${r.displayName} (${r.name})`;
    document.getElementById("region-dropdown").classList.remove("show");
    onRegionChange();
}

function onRegionChange() {
    const regionName = document.getElementById("region-select")?.value || "";
    emitContextEvent("azscout:region-changed", {
        region: regionName,
        tenantId: document.getElementById("tenant-select")?.value || "",
    });

    // Region is shared – update both tabs
    updateTopoLoadButton();
    if (typeof resetPlannerResults === "function") resetPlannerResults();
    if (typeof updatePlannerLoadButton === "function") updatePlannerLoadButton();
}

// ---------------------------------------------------------------------------
// Panel visibility helper
// ---------------------------------------------------------------------------
function showPanel(prefix, state) {
    // state: "empty" | "loading" | "results"
    const ids = [`${prefix}-empty`, `${prefix}-loading`, `${prefix}-results`];
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        if (id.endsWith(state)) el.classList.remove("d-none");
        else el.classList.add("d-none");
    });
    // Toggle planner CSV export button visibility
    const csvBtn = document.getElementById(`${prefix}-csv-btn`);
    if (csvBtn) csvBtn.classList.toggle("d-none", state !== "results");
}

// ---------------------------------------------------------------------------
// Subscriptions  (shared – populates global `subscriptions` for both tabs)
// ---------------------------------------------------------------------------
async function fetchSubscriptions() {
    try {
        subscriptions = await apiFetch("/api/subscriptions" + tenantQS("?"));
        const tid = document.getElementById("tenant-select")?.value || "";
        emitContextEvent("azscout:subscriptions-loaded", {
            subscriptions,
            tenantId: tid,
        });
        if (typeof renderTopoSubList === "function") renderTopoSubList();
        if (typeof renderPlannerSubDropdown === "function") renderPlannerSubDropdown("");
    } catch (err) {
        if (!err) return; // redirect in progress
        const tenantId = document.getElementById("tenant-select")?.value || "";
        _showAuthError(tenantId, err.message);
    }
}

/**
 * Show an auth error message in place of the tab content.
 * Tenant/region selectors remain accessible.
 */
function _showAuthError(tenantId, message) {
    const tabs = document.getElementById("mainTabs");
    const tabContent = document.getElementById("mainTabContent");
    if (tabs) tabs.style.display = "none";
    if (tabContent) tabContent.style.display = "none";

    const tenantName = tenants.find(t => t.id === tenantId)?.name || tenantId;

    let overlay = document.getElementById("mfa-overlay");
    if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "mfa-overlay";
        overlay.className = "d-flex flex-column align-items-center justify-content-center";
        overlay.style.cssText = "min-height: 50vh; text-align: center;";
        const selectorBar = document.getElementById("selector-bar");
        if (selectorBar) {
            selectorBar.after(overlay);
        } else {
            document.querySelector(".container-fluid.mt-3")?.append(overlay);
        }
    }
    // Extract admin consent URL if present
    const consentMatch = message.match(/(https:\/\/login\.microsoftonline\.com\/[^\s]+\/adminconsent\?[^\s]+)/);
    const isConsent = consentMatch != null;
    const consentUrl = consentMatch ? consentMatch[1] : "";

    // Build a user-friendly message
    let bodyHtml;
    if (isConsent) {
        bodyHtml = `
            <p class="text-body-secondary mb-2">
                This tenant requires <strong>admin consent</strong> before
                az-scout can access Azure resources on your behalf.
            </p>
            <p class="text-body-secondary small mb-4">
                Ask a tenant administrator to grant consent by clicking the button below,
                or select a different tenant above.
            </p>
            <a href="${escapeHtml(consentUrl)}" target="_blank" rel="noopener"
               class="btn btn-warning btn-lg mb-2">
                <i class="bi bi-box-arrow-up-right me-1"></i> Grant Admin Consent
            </a>
            <br>
            <button class="btn btn-sm btn-outline-secondary mb-3" onclick="_copyConsentUrl(this, '${escapeHtml(consentUrl)}')">
                <i class="bi bi-clipboard me-1"></i> Copy link for admin
            </button>
            <p class="text-body-secondary small">
                After consent is granted, reload this page.
            </p>`;
    } else {
        bodyHtml = `
            <p class="text-body-secondary mb-4">
                ${escapeHtml(message)}
            </p>
            <p class="text-body-secondary small">
                Select a different tenant above or contact a tenant administrator.
            </p>`;
    }

    overlay.innerHTML = `
        <div style="max-width: 480px;">
            <i class="bi bi-${isConsent ? "shield-exclamation" : "exclamation-triangle"}" style="font-size:3rem;opacity:0.75"></i>
            <h3 class="mb-2 mt-3">${isConsent ? "Admin Consent Required" : "Authentication Error"}</h3>
            <p class="text-body-secondary mb-2">
                Tenant: <strong>${escapeHtml(tenantName)}</strong>
            </p>
            ${bodyHtml}
            <hr class="my-3 w-100">
            <a href="/auth/login" class="btn btn-sm btn-outline-secondary mb-4">
                <i class="bi bi-person-lines-fill me-1"></i> Sign in with a different account
            </a>
        </div>`;
    overlay.style.display = "flex";
}

function _copyConsentUrl(btn, url) {
    navigator.clipboard.writeText(url).then(() => {
        btn.innerHTML = '<i class="bi bi-check me-1"></i> Copied!';
        setTimeout(() => {
            btn.innerHTML = '<i class="bi bi-clipboard me-1"></i> Copy link for admin';
        }, 2000);
    });
}

// ---------------------------------------------------------------------------
// Shared utility
// ---------------------------------------------------------------------------
function downloadCSV(data, filename) {
    const csv = data.map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const a = document.createElement("a");
    a.download = filename;
    a.href = URL.createObjectURL(blob);
    a.click();
    URL.revokeObjectURL(a.href);
}
