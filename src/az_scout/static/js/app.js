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
// URL hash helper  (only stores active tab, no query params)
// ---------------------------------------------------------------------------
function getActiveTabFromHash() {
    const h = window.location.hash.replace(/^#/, '');
    return h || "topology";
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------
async function apiFetch(url) {
    const resp = await fetch(url);
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${resp.status}`);
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
        throw new Error(data.error || `HTTP ${resp.status}`);
    }
    return resp.json();
}

function showError(targetId, msg) {
    const el = document.getElementById(targetId);
    if (!el) return;
    el.textContent = msg;
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
            return;
        }
        select.innerHTML = authTenants.map(t => {
            const label = `${escapeHtml(t.name)} (${t.id.slice(0, 8)}\u2026)`;
            return `<option value="${t.id}">${label}</option>`;
        }).join("") + hiddenOpt;
        if (defaultTid && authTenants.some(t => t.id === defaultTid)) {
            select.value = defaultTid;
        }
    } catch {
        document.getElementById("tenant-section").classList.add("d-none");
    }
}

async function onTenantChange() {
    // Reset all downstream state
    if (typeof topoSelectedSubs !== "undefined") topoSelectedSubs.clear();
    lastMappingData = null;
    if (typeof plannerSubscriptionId !== "undefined") plannerSubscriptionId = null;
    if (typeof plannerZoneMappings !== "undefined") plannerZoneMappings = null;
    if (typeof lastSkuData !== "undefined") lastSkuData = null;
    if (typeof lastSpotScores !== "undefined") lastSpotScores = null;

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
        inp.placeholder = "Type to search regions\u2026";
        inp.disabled = false;
        renderRegionDropdown("");
    } catch (err) {
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
        if (typeof renderTopoSubList === "function") renderTopoSubList();
        if (typeof renderPlannerSubDropdown === "function") renderPlannerSubDropdown("");
    } catch (err) {
        showError("topo-error", "Failed to load subscriptions: " + err.message);
    }
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
