/* ===================================================================
   Azure Scout – Frontend Logic  (Bootstrap 5 rewrite)
   =================================================================== */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let subscriptions = [];                     // [{id, name}] – all subs for current tenant
let regions = [];                           // [{name, displayName}]
let tenants = [];                           // [{id, name, authenticated}]

// --- Topology tab state ---
let topoSelectedSubs = new Set();           // selected subscription IDs for topology
let lastMappingData = null;                 // cached /api/mappings result

// --- Planner tab state ---
let plannerSubscriptionId = null;           // single selected subscription ID
let plannerZoneMappings = null;             // zone mappings fetched independently for planner
let lastSkuData = null;                     // cached SKU list
let lastSpotScores = null;                  // {scores: {sku: {zone: label}}, errors: []}
let _skuDataTable = null;                   // Simple-DataTables instance
let _skuFilterState = {};                   // {headerText: filterValue} – persists across re-renders
let _admissionCache = {};                   // {skuName: admissionData} – cached admission intelligence

// ---------------------------------------------------------------------------
// Deployment Confidence – scores are computed server-side only.
// The frontend NEVER recomputes confidence; it displays what the API returns.
// Use refreshDeploymentConfidence() to fetch updated scores from the backend.
// ---------------------------------------------------------------------------
const _REGION_SCORE_LABELS = [[80, "High"], [60, "Medium"], [40, "Low"], [0, "Very Low"]];

/**
 * Fetch canonical Deployment Confidence scores from the backend for the
 * given SKU names.  Updates ``lastSkuData[].confidence`` in place and
 * re-renders the table + region summary.
 */
async function refreshDeploymentConfidence(skuNames) {
    const region = document.getElementById("region-select").value;
    const subscriptionId = plannerSubscriptionId;
    if (!region || !subscriptionId || !skuNames || !skuNames.length) return;

    const currency = document.getElementById("planner-currency")?.value || "USD";
    const payload = {
        subscriptionId,
        region,
        currencyCode: currency,
        preferSpot: true,
        instanceCount: 1,
        skus: skuNames,
        includeSignals: false,
        includeProvenance: false,
    };
    const tenant = document.getElementById("tenant-select").value;
    if (tenant) payload.tenantId = tenant;

    try {
        const result = await apiPost("/api/deployment-confidence", payload);
        if (result.results) {
            for (const r of result.results) {
                const sku = (lastSkuData || []).find(s => s.name === r.sku);
                if (sku && r.deploymentConfidence) {
                    sku.confidence = r.deploymentConfidence;
                }
            }
        }
    } catch (err) {
        console.error("Failed to refresh deployment confidence:", err);
    }
}

/** Pick the best Spot Placement Score label from per-zone data (display helper). */
function _bestSpotLabel(zoneScores) {
    const order = { high: 3, medium: 2, low: 1 };
    let best = null;
    for (const s of Object.values(zoneScores)) {
        const rank = order[s.toLowerCase()] || 0;
        if (rank > (order[(best || "").toLowerCase()] || 0)) best = s;
    }
    return best || null;
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

    // Topology subscription filter
    document.getElementById("topo-sub-filter").addEventListener("input", e => renderTopoSubList(e.target.value));

    // Tenant change
    document.getElementById("tenant-select").addEventListener("change", onTenantChange);

    // Event delegation for SKU table interactive cells
    document.getElementById("sku-table-container").addEventListener("click", (e) => {
        const btn = e.target.closest("[data-action]");
        if (!btn) return;
        const sku = btn.dataset.sku;
        if (btn.dataset.action === "pricing") openPricingModal(sku);
        else if (btn.dataset.action === "spot") openSpotModal(sku);
    });

    // Init shared region combobox
    initRegionCombobox();

    // Init planner subscription combobox
    initPlannerSubCombobox();

    // Hash-based tab routing
    const tabEl = document.querySelector('#mainTabs');
    if (tabEl) {
        tabEl.addEventListener('shown.bs.tab', (e) => {
            const target = e.target.getAttribute('data-bs-target');
            const hashMap = { '#tab-planner': '#planner', '#tab-strategy': '#strategy' };
            window.history.replaceState(null, '', hashMap[target] || '#topology');
        });
    }
    // Activate tab from hash
    const hash = window.location.hash;
    if (hash === '#planner') {
        const plannerTab = document.getElementById('planner-tab');
        if (plannerTab) new bootstrap.Tab(plannerTab).show();
    } else if (hash === '#strategy') {
        const stratTab = document.getElementById('strategy-tab');
        if (stratTab) new bootstrap.Tab(stratTab).show();
    }

    // Init strategy subscription combobox
    initStratSubCombobox();

    // Load tenants
    await fetchTenants();

    // Load regions + subscriptions in parallel
    await Promise.all([fetchRegions(), fetchSubscriptions()]);

    updateTopoLoadButton();
    updatePlannerLoadButton();
}

// ---------------------------------------------------------------------------
// URL hash helper  (only stores active tab, no query params)
// ---------------------------------------------------------------------------
function getActiveTabFromHash() {
    const h = window.location.hash;
    if (h === "#planner") return "planner";
    if (h === "#strategy") return "strategy";
    return "topology";
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
    topoSelectedSubs.clear();
    lastMappingData = null;
    plannerSubscriptionId = null;
    plannerZoneMappings = null;
    lastSkuData = null;
    lastSpotScores = null;

    document.getElementById("region-select").value = "";
    document.getElementById("region-search").value = "";
    document.getElementById("topo-sub-filter").value = "";
    document.getElementById("planner-sub-select").value = "";
    document.getElementById("planner-sub-search").value = "";

    // Reset UI panels
    showPanel("topo", "empty");
    showPanel("planner", "empty");
    hideError("topo-error");
    hideError("planner-error");

    await Promise.all([fetchRegions(), fetchSubscriptions()]);
    updateTopoLoadButton();
    updatePlannerLoadButton();
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
    resetPlannerResults();
    updatePlannerLoadButton();
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

// =========================================================================
// TOPOLOGY TAB
// =========================================================================

// ---------------------------------------------------------------------------
// Topology subscriptions (multi-select checklist)
// ---------------------------------------------------------------------------
async function fetchSubscriptions() {
    try {
        subscriptions = await apiFetch("/api/subscriptions" + tenantQS("?"));
        renderTopoSubList();
        renderPlannerSubDropdown("");
    } catch (err) {
        showError("topo-error", "Failed to load subscriptions: " + err.message);
    }
}

function renderTopoSubList(filter) {
    const container = document.getElementById("topo-sub-list");
    const list = filter
        ? subscriptions.filter(s => s.name.toLowerCase().includes(filter.toLowerCase()))
        : subscriptions;

    if (!list.length && !filter) {
        container.innerHTML = '<span class="text-body-secondary small">No subscriptions found</span>';
        return;
    }
    container.innerHTML = list.map(s => {
        const checked = topoSelectedSubs.has(s.id) ? "checked" : "";
        return `<label title="${escapeHtml(s.name)}">
            <input type="checkbox" class="form-check-input me-1" value="${s.id}" ${checked}
                   onchange="topoToggleSub('${s.id}')">
            ${escapeHtml(s.name)}
        </label>`;
    }).join("");
    updateTopoSubCount();
}

function topoToggleSub(id) {
    if (topoSelectedSubs.has(id)) topoSelectedSubs.delete(id);
    else topoSelectedSubs.add(id);
    updateTopoSubCount();
    updateTopoLoadButton();
}

function topoSelectAllVisible() {
    document.querySelectorAll("#topo-sub-list input[type=checkbox]").forEach(cb => {
        cb.checked = true;
        topoSelectedSubs.add(cb.value);
    });
    updateTopoSubCount();
    updateTopoLoadButton();
}

function topoDeselectAll() {
    topoSelectedSubs.clear();
    document.querySelectorAll("#topo-sub-list input[type=checkbox]").forEach(cb => { cb.checked = false; });
    updateTopoSubCount();
    updateTopoLoadButton();
}

function updateTopoSubCount() {
    document.getElementById("topo-sub-count").textContent = `${topoSelectedSubs.size} selected`;
}

function updateTopoLoadButton() {
    const btn = document.getElementById("topo-load-btn");
    const region = document.getElementById("region-select").value;
    btn.disabled = !(topoSelectedSubs.size > 0 && region);
}

// ---------------------------------------------------------------------------
// Load topology mappings
// ---------------------------------------------------------------------------
async function loadMappings() {
    const region = document.getElementById("region-select").value;
    if (!region || topoSelectedSubs.size === 0) return;

    hideError("topo-error");
    showPanel("topo", "loading");

    try {
        const subs = [...topoSelectedSubs].join(",");
        lastMappingData = await apiFetch(`/api/mappings?region=${region}&subscriptions=${subs}${tenantQS()}`);
        showPanel("topo", "results");
        renderGraph(lastMappingData);
        renderTable(lastMappingData);
    } catch (err) {
        showPanel("topo", "empty");
        showError("topo-error", "Failed to load mappings: " + err.message);
    }
}

// ---------------------------------------------------------------------------
// Physical-zone colour helpers
// ---------------------------------------------------------------------------
function pzIndex(physicalZone) {
    const m = physicalZone.match(/(\d+)$/);
    return m ? parseInt(m[1], 10) : 0;
}

function pzClass(physicalZone) {
    const idx = pzIndex(physicalZone);
    return idx >= 1 && idx <= 6 ? `pz-${idx}` : "pz-1";
}

// ---------------------------------------------------------------------------
// GRAPH RENDERING  (D3.js)
// ---------------------------------------------------------------------------
function renderGraph(data) {
    const container = document.getElementById("graph-container");
    const legendContainer = document.getElementById("graph-legend");
    container.innerHTML = "";
    legendContainer.innerHTML = "";

    const validData = data.filter(d => d.mappings && d.mappings.length > 0);
    if (!validData.length) {
        container.innerHTML = '<p class="text-body-secondary text-center py-3">No zone mappings available.</p>';
        return;
    }

    const logicalZones = [...new Set(validData.flatMap(d => d.mappings.map(m => m.logicalZone)))].sort();
    const physicalZones = [...new Set(validData.flatMap(d => d.mappings.map(m => m.physicalZone)))].sort();

    // Measure text widths
    const measurer = d3.select(container).append("svg").attr("class", "measurer").style("position", "absolute").style("visibility", "hidden");
    const measureText = (txt, fontSize) => {
        const t = measurer.append("text").attr("font-size", fontSize).attr("font-weight", 500)
            .attr("font-family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif").text(txt);
        const w = t.node().getComputedTextLength();
        t.remove();
        return w;
    };
    const lzLabels = logicalZones.map(z => `Zone ${z}`);
    const pzLabels = physicalZones;
    const subLabels = validData.map(d => truncate(getSubName(d.subscriptionId), 22));
    const maxLZText = Math.max(100, ...lzLabels.map(l => measureText(l, 13)));
    const maxPZText = Math.max(100, ...pzLabels.map(l => measureText(l, 13)));
    const maxSubText = Math.max(100, ...subLabels.map(l => measureText(l, 12)));
    measurer.remove();

    const nodePadX = 24;
    const leftNodeW = Math.ceil(Math.max(maxLZText, maxSubText) + nodePadX);
    const rightNodeW = Math.ceil(maxPZText + nodePadX);
    const nodeH = 36;
    const nodeGapY = 12;
    const groupGapY = 28;
    const groupPadTop = 36;
    const groupPadX = 12;
    const groupPadBot = 12;
    const linkGap = 180;
    const margin = { top: 20, right: 20, bottom: 20, left: 20 };

    const colorScale = d3.scaleOrdinal(d3.schemeTableau10).domain(validData.map(d => d.subscriptionId));

    const pzColors = ["#0078d4", "#107c10", "#d83b01", "#8764b8", "#008272", "#b4009e"];
    function pzColor(pz) {
        const m = pz.match(/(\d+)$/);
        const idx = m ? parseInt(m[1], 10) : 1;
        return pzColors[(idx - 1) % pzColors.length];
    }

    // Left-side layout: subscription groups
    const groups = [];
    let cursorY = 0;
    validData.forEach((sub, subIdx) => {
        const zones = sub.mappings.map(m => m.logicalZone).filter((v, i, a) => a.indexOf(v) === i).sort();
        const contentH = zones.length * nodeH + (zones.length - 1) * nodeGapY;
        const boxH = groupPadTop + contentH + groupPadBot;
        const nodes = zones.map((z, zi) => ({
            zone: z,
            y: cursorY + groupPadTop + zi * (nodeH + nodeGapY) + nodeH / 2,
        }));
        groups.push({ subIdx, subId: sub.subscriptionId, subName: getSubName(sub.subscriptionId), y: cursorY, h: boxH, nodes });
        cursorY += boxH + groupGapY;
    });
    const leftTotalH = cursorY - groupGapY;

    // Right-side layout: physical zone nodes
    const rightContentH = physicalZones.length * nodeH + (physicalZones.length - 1) * nodeGapY;
    const rightBoxPadTop = 36;
    const rightBoxPadBot = 14;
    const rightBoxH = rightBoxPadTop + rightContentH + rightBoxPadBot;
    const rightBoxY = Math.max(0, (leftTotalH - rightBoxH) / 2);
    const physicalNodes = physicalZones.map((pz, i) => ({
        zone: pz,
        y: rightBoxY + rightBoxPadTop + i * (nodeH + nodeGapY) + nodeH / 2,
    }));

    // SVG dimensions
    const groupBoxW = leftNodeW + groupPadX * 2;
    const rightBoxW = rightNodeW + groupPadX * 2;
    const totalW = margin.left + groupBoxW + linkGap + rightBoxW + margin.right;
    const totalH = Math.max(leftTotalH, rightBoxY + rightBoxH) + margin.top + margin.bottom;
    const leftX = margin.left;
    const rightX = margin.left + groupBoxW + linkGap;

    const svg = d3.select(container).append("svg")
        .attr("viewBox", `0 0 ${totalW} ${totalH}`)
        .attr("preserveAspectRatio", "xMidYMid meet");
    const g = svg.append("g").attr("transform", `translate(0, ${margin.top})`);

    // Right-side group box
    const rightGroup = g.append("g").attr("transform", `translate(${rightX}, ${rightBoxY})`);
    rightGroup.append("rect").attr("width", rightBoxW).attr("height", rightBoxH)
        .attr("rx", 10).attr("class", "group-box-right");
    rightGroup.append("text").attr("x", rightBoxW / 2).attr("y", 22)
        .attr("text-anchor", "middle").attr("class", "group-label-right").text("Physical Zones");

    // Physical zone nodes
    physicalNodes.forEach(pn => {
        const ng = g.append("g").attr("transform", `translate(${rightX + groupPadX}, ${pn.y})`)
            .attr("class", "pz-node-group").attr("data-pz", pn.zone).style("cursor", "pointer");
        ng.append("rect").attr("x", 0).attr("y", -nodeH / 2)
            .attr("width", rightNodeW).attr("height", nodeH).attr("rx", 6)
            .attr("class", "node-rect-right")
            .attr("style", `fill: ${pzColor(pn.zone)}20; stroke: ${pzColor(pn.zone)};`);
        ng.append("text").attr("x", rightNodeW / 2).attr("y", 5)
            .attr("text-anchor", "middle").attr("class", "node-label").text(pn.zone);
        ng.on("mouseenter", () => highlightByPZ(pn.zone));
        ng.on("mouseleave", clearHighlight);
    });

    // Left-side subscription groups
    groups.forEach(grp => {
        const gg = g.append("g").attr("transform", `translate(${leftX}, ${grp.y})`)
            .attr("class", "sub-group").attr("data-sub", grp.subIdx).style("cursor", "pointer");
        gg.append("rect").attr("width", groupBoxW).attr("height", grp.h)
            .attr("rx", 10).attr("class", "group-box-left")
            .attr("style", `stroke: ${colorScale(grp.subId)};`);
        gg.append("text").attr("x", groupBoxW / 2).attr("y", 22)
            .attr("text-anchor", "middle").attr("class", "group-label-left")
            .attr("fill", colorScale(grp.subId)).text(truncate(grp.subName, 22));
        gg.append("title").text(grp.subName);
        gg.on("mouseenter", () => highlightSub(grp.subIdx, validData));
        gg.on("mouseleave", clearHighlight);
    });

    // Logical zone nodes
    groups.forEach(grp => {
        grp.nodes.forEach(ln => {
            const ng = g.append("g").attr("transform", `translate(${leftX + groupPadX}, ${ln.y})`)
                .attr("class", "lz-node-group").attr("data-sub", grp.subIdx).attr("data-lz", ln.zone)
                .style("cursor", "pointer");
            ng.append("rect").attr("x", 0).attr("y", -nodeH / 2)
                .attr("width", leftNodeW).attr("height", nodeH).attr("rx", 6)
                .attr("class", "node-rect-left")
                .attr("style", `stroke: ${colorScale(grp.subId)};`);
            ng.append("text").attr("x", leftNodeW / 2).attr("y", 5)
                .attr("text-anchor", "middle").attr("class", "node-label").text(`Zone ${ln.zone}`);
            ng.on("mouseenter", () => highlightByLZ(grp.subIdx, ln.zone, validData));
            ng.on("mouseleave", clearHighlight);
        });
    });

    // Links
    const linksGroup = g.append("g").attr("class", "links-group");
    groups.forEach(grp => {
        const sub = validData[grp.subIdx];
        sub.mappings.forEach(m => {
            const srcNode = grp.nodes.find(n => n.zone === m.logicalZone);
            const tgtNode = physicalNodes.find(n => n.zone === m.physicalZone);
            if (!srcNode || !tgtNode) return;
            linksGroup.append("path")
                .attr("d", d3.linkHorizontal()({ source: [leftX + groupPadX + leftNodeW, srcNode.y], target: [rightX + groupPadX, tgtNode.y] }))
                .attr("stroke", colorScale(sub.subscriptionId))
                .attr("stroke-width", 2.5).attr("fill", "none").attr("opacity", 0.55)
                .attr("class", `link link-sub-${grp.subIdx}`)
                .attr("data-sub", grp.subIdx).attr("data-lz", m.logicalZone).attr("data-pz", m.physicalZone)
                .append("title").text(`${grp.subName}: Zone ${m.logicalZone} \u2192 ${m.physicalZone}`);
        });
    });

    // Legend
    validData.forEach((sub, i) => {
        const item = document.createElement("div");
        item.className = "legend-item";
        item.innerHTML = `<span class="legend-swatch" style="background:${colorScale(sub.subscriptionId)}"></span>
            <span>${escapeHtml(getSubName(sub.subscriptionId))}</span>`;
        item.addEventListener("mouseenter", () => highlightSub(i, validData));
        item.addEventListener("mouseleave", clearHighlight);
        legendContainer.appendChild(item);
    });
}

// ---------------------------------------------------------------------------
// Highlight helpers
// ---------------------------------------------------------------------------
function highlightSub(subIdx, validData) {
    d3.selectAll(".link").classed("dimmed", true).classed("highlighted", false);
    d3.selectAll(`.link-sub-${subIdx}`).classed("dimmed", false).classed("highlighted", true);
    d3.selectAll(".sub-group").style("opacity", function () { return +d3.select(this).attr("data-sub") === subIdx ? 1 : 0.25; });
    d3.selectAll(".lz-node-group").style("opacity", function () { return +d3.select(this).attr("data-sub") === subIdx ? 1 : 0.25; });
    const targetPZs = new Set();
    if (validData?.[subIdx]) validData[subIdx].mappings.forEach(m => targetPZs.add(m.physicalZone));
    d3.selectAll(".pz-node-group").style("opacity", function () { return targetPZs.has(d3.select(this).attr("data-pz")) ? 1 : 0.25; });
}

function highlightByLZ(subIdx, lz, validData) {
    d3.selectAll(".link").classed("dimmed", true).classed("highlighted", false);
    d3.selectAll(".sub-group").style("opacity", 0.25);
    d3.selectAll(".lz-node-group").style("opacity", 0.25);
    d3.selectAll(".pz-node-group").style("opacity", 0.25);
    d3.selectAll(".link").each(function () {
        const el = d3.select(this);
        if (+el.attr("data-sub") === subIdx && el.attr("data-lz") === lz) el.classed("dimmed", false).classed("highlighted", true);
    });
    d3.selectAll(".sub-group").filter(function () { return +d3.select(this).attr("data-sub") === subIdx; }).style("opacity", 1);
    d3.selectAll(".lz-node-group").filter(function () { return +d3.select(this).attr("data-sub") === subIdx && d3.select(this).attr("data-lz") === lz; }).style("opacity", 1);
    const targetPZ = validData[subIdx]?.mappings.find(m => m.logicalZone === lz)?.physicalZone;
    if (targetPZ) d3.selectAll(".pz-node-group").filter(function () { return d3.select(this).attr("data-pz") === targetPZ; }).style("opacity", 1);
}

function highlightByPZ(pz) {
    d3.selectAll(".link").classed("dimmed", true).classed("highlighted", false);
    d3.selectAll(".sub-group").style("opacity", 0.25);
    d3.selectAll(".lz-node-group").style("opacity", 0.25);
    d3.selectAll(".pz-node-group").style("opacity", 0.25);
    d3.selectAll(".pz-node-group").filter(function () { return d3.select(this).attr("data-pz") === pz; }).style("opacity", 1);
    const matchedSubs = new Set();
    const matchedLZKeys = new Set();
    d3.selectAll(".link").each(function () {
        const el = d3.select(this);
        if (el.attr("data-pz") === pz) {
            el.classed("dimmed", false).classed("highlighted", true);
            matchedSubs.add(+el.attr("data-sub"));
            matchedLZKeys.add(el.attr("data-sub") + "::" + el.attr("data-lz"));
        }
    });
    d3.selectAll(".sub-group").filter(function () { return matchedSubs.has(+d3.select(this).attr("data-sub")); }).style("opacity", 1);
    d3.selectAll(".lz-node-group").filter(function () { return matchedLZKeys.has(d3.select(this).attr("data-sub") + "::" + d3.select(this).attr("data-lz")); }).style("opacity", 1);
}

function clearHighlight() {
    d3.selectAll(".link").classed("dimmed", false).classed("highlighted", false);
    d3.selectAll(".sub-group").style("opacity", 1);
    d3.selectAll(".lz-node-group").style("opacity", 1);
    d3.selectAll(".pz-node-group").style("opacity", 1);
}

// ---------------------------------------------------------------------------
// TABLE RENDERING
// ---------------------------------------------------------------------------
function renderTable(data) {
    const container = document.getElementById("table-container");
    container.innerHTML = "";
    const validData = data.filter(d => d.mappings && d.mappings.length > 0);
    if (!validData.length) {
        container.innerHTML = '<p class="text-body-secondary">No zone mappings available.</p>';
        return;
    }
    const logicalZones = [...new Set(validData.flatMap(d => d.mappings.map(m => m.logicalZone)))].sort();

    const table = document.createElement("table");
    table.className = "table table-sm table-hover mapping-table";

    const thead = table.createTHead();
    const headerRow = thead.insertRow();
    ["Subscription", "Subscription ID", ...logicalZones.map(z => `Logical Zone ${z}`)].forEach(txt => {
        const th = document.createElement("th");
        th.textContent = txt;
        headerRow.appendChild(th);
    });

    const tbody = table.createTBody();
    validData.forEach(sub => {
        const row = tbody.insertRow();
        const nameCell = row.insertCell();
        nameCell.textContent = getSubName(sub.subscriptionId);
        nameCell.className = "sub-name-cell";
        nameCell.title = getSubName(sub.subscriptionId);
        const idCell = row.insertCell();
        idCell.textContent = sub.subscriptionId;
        idCell.style.cssText = "font-size:0.78rem;opacity:0.6;font-family:monospace;";
        logicalZones.forEach(z => {
            const cell = row.insertCell();
            const mapping = sub.mappings.find(m => m.logicalZone === z);
            if (mapping) {
                const badge = document.createElement("span");
                badge.className = `zone-badge ${pzClass(mapping.physicalZone)}`;
                badge.textContent = mapping.physicalZone;
                cell.appendChild(badge);
            } else {
                cell.textContent = "\u2014";
                cell.style.opacity = "0.5";
            }
        });
    });

    // Consistency footer
    if (validData.length > 1) {
        const tfoot = table.createTFoot();
        const footRow = tfoot.insertRow();
        footRow.className = "consistency-row";
        const label = footRow.insertCell();
        label.colSpan = 2;
        label.textContent = "Consistency";
        logicalZones.forEach(z => {
            const cell = footRow.insertCell();
            const physicals = validData.map(sub => sub.mappings.find(m => m.logicalZone === z)).filter(Boolean).map(m => m.physicalZone);
            const unique = [...new Set(physicals)];
            if (unique.length <= 1) {
                cell.textContent = "\u2713 Same";
                cell.className = "same";
            } else {
                cell.textContent = `\u26A0 ${unique.length} different`;
                cell.className = "different";
            }
        });
    }
    container.appendChild(table);
}

// ---------------------------------------------------------------------------
// EXPORT: Graph → PNG
// ---------------------------------------------------------------------------
function exportGraphPNG() {
    const svgEl = document.querySelector("#graph-container svg");
    if (!svgEl) return;
    const clone = svgEl.cloneNode(true);
    inlineStyles(svgEl, clone);
    const box = svgEl.viewBox.baseVal;
    const scale = 2;
    const w = box.width * scale;
    const h = box.height * scale;
    clone.setAttribute("width", w);
    clone.setAttribute("height", h);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    const svgData = new XMLSerializer().serializeToString(clone);
    const blob = new Blob([svgData], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = getEffectiveTheme() === "dark" ? "#1e1e1e" : "#ffffff";
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(img, 0, 0, w, h);
        URL.revokeObjectURL(url);
        const region = document.getElementById("region-select").value || "az-scout";
        const a = document.createElement("a");
        a.download = `az-scout-${region}.png`;
        a.href = canvas.toDataURL("image/png");
        a.click();
    };
    img.src = url;
}

function inlineStyles(src, dst) {
    const computed = window.getComputedStyle(src);
    ["fill", "stroke", "stroke-width", "stroke-dasharray", "opacity", "font-size", "font-weight", "font-family", "text-anchor", "dominant-baseline", "letter-spacing"]
        .forEach(prop => { const v = computed.getPropertyValue(prop); if (v) dst.style.setProperty(prop, v); });
    for (let i = 0; i < src.children.length; i++) {
        if (dst.children[i]) inlineStyles(src.children[i], dst.children[i]);
    }
}

// ---------------------------------------------------------------------------
// EXPORT: Table → CSV
// ---------------------------------------------------------------------------
function exportTableCSV() {
    if (!lastMappingData) return;
    const validData = lastMappingData.filter(d => d.mappings && d.mappings.length > 0);
    if (!validData.length) return;
    const logicalZones = [...new Set(validData.flatMap(d => d.mappings.map(m => m.logicalZone)))].sort();
    const headers = ["Subscription", "Subscription ID", ...logicalZones.map(z => `Logical Zone ${z}`)];
    const rows = validData.map(sub => {
        const cols = logicalZones.map(z => { const m = sub.mappings.find(m => m.logicalZone === z); return m ? m.physicalZone : ""; });
        return [getSubName(sub.subscriptionId), sub.subscriptionId, ...cols];
    });
    downloadCSV([headers, ...rows], `az-scout-${document.getElementById("region-select").value || "export"}.csv`);
}

function downloadCSV(data, filename) {
    const csv = data.map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const a = document.createElement("a");
    a.download = filename;
    a.href = URL.createObjectURL(blob);
    a.click();
    URL.revokeObjectURL(a.href);
}

// =========================================================================
// PLANNER TAB
// =========================================================================

// ---------------------------------------------------------------------------
// Planner subscription combobox (single-select)
// ---------------------------------------------------------------------------
function initPlannerSubCombobox() {
    const searchInput = document.getElementById("planner-sub-search");
    const dropdown = document.getElementById("planner-sub-dropdown");

    searchInput.addEventListener("focus", () => {
        searchInput.select();
        renderPlannerSubDropdown(searchInput.value.includes("(") ? "" : searchInput.value);
        dropdown.classList.add("show");
    });
    searchInput.addEventListener("input", () => {
        document.getElementById("planner-sub-select").value = "";
        plannerSubscriptionId = null;
        renderPlannerSubDropdown(searchInput.value);
        dropdown.classList.add("show");
        updatePlannerLoadButton();
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
            if (active) selectPlannerSub(active.dataset.value);
            else if (items.length === 1) selectPlannerSub(items[0].dataset.value);
        } else if (e.key === "Escape") {
            dropdown.classList.remove("show");
            searchInput.blur();
        }
    });
    document.addEventListener("click", (e) => {
        if (!e.target.closest("#planner-sub-combobox")) dropdown.classList.remove("show");
    });
}

function renderPlannerSubDropdown(filter) {
    const dropdown = document.getElementById("planner-sub-dropdown");
    const lc = (filter || "").toLowerCase();
    const matches = lc
        ? subscriptions.filter(s => s.name.toLowerCase().includes(lc) || s.id.toLowerCase().includes(lc))
        : subscriptions;
    dropdown.innerHTML = matches.map(s =>
        `<li class="dropdown-item" data-value="${s.id}">${escapeHtml(s.name)} <span class="region-name">(${s.id.slice(0, 8)}\u2026)</span></li>`
    ).join("");
    dropdown.querySelectorAll("li").forEach(li => {
        li.addEventListener("click", () => selectPlannerSub(li.dataset.value));
    });
    // Enable search input once subscriptions are loaded
    const searchInput = document.getElementById("planner-sub-search");
    if (subscriptions.length > 0) {
        searchInput.placeholder = "Type to search subscriptions\u2026";
        searchInput.disabled = false;
    }
}

function selectPlannerSub(id) {
    const s = subscriptions.find(s => s.id === id);
    if (!s) return;
    plannerSubscriptionId = id;
    document.getElementById("planner-sub-select").value = id;
    document.getElementById("planner-sub-search").value = s.name;
    document.getElementById("planner-sub-dropdown").classList.remove("show");
    resetPlannerResults();
    updatePlannerLoadButton();
}

function updatePlannerLoadButton() {
    const btn = document.getElementById("planner-load-btn");
    const region = document.getElementById("region-select").value;
    btn.disabled = !(plannerSubscriptionId && region);
}

function resetPlannerResults() {
    lastSkuData = null;
    lastSpotScores = null;
    plannerZoneMappings = null;
    _skuFilterState = {};
    if (_skuDataTable) {
        try { _skuDataTable.destroy(); } catch {}
        _skuDataTable = null;
    }
    showPanel("planner", "empty");
}

// ---------------------------------------------------------------------------
// Load SKUs  (independently fetches zone mappings for headers)
// ---------------------------------------------------------------------------
async function loadSkus() {
    const region = document.getElementById("region-select").value;
    const tenant = document.getElementById("tenant-select").value;
    const subscriptionId = plannerSubscriptionId;

    if (!region || !subscriptionId) return;

    hideError("planner-error");
    showPanel("planner", "loading");

    try {
        // Fetch zone mappings for this sub to get physical zone headers
        const mappingsPromise = apiFetch(`/api/mappings?region=${region}&subscriptions=${subscriptionId}${tenantQS()}`);

        // Fetch SKUs (always include prices)
        const params = new URLSearchParams({ region, subscriptionId });
        if (tenant) params.append("tenantId", tenant);
        params.append("includePrices", "true");
        const currency = document.getElementById("planner-currency")?.value || "USD";
        params.append("currencyCode", currency);
        const skuPromise = apiFetch(`/api/skus?${params}`);

        // Run in parallel
        const [mappingsResult, skuResult] = await Promise.all([mappingsPromise, skuPromise]);

        // Store zone mappings for this planner session
        plannerZoneMappings = mappingsResult;

        if (skuResult.error) throw new Error(skuResult.error);

        lastSkuData = skuResult;
        // Confidence scores are already computed server-side in GET /api/skus

        showPanel("planner", "results");
        try { renderRegionSummary(lastSkuData); } catch (e) { console.error("renderRegionSummary failed:", e); }
        try { renderSkuTable(lastSkuData); } catch (e) { console.error("renderSkuTable failed:", e); }
    } catch (err) {
        showPanel("planner", "empty");
        showError("planner-error", `Failed to fetch SKUs: ${err.message}`);
    }
}

// ---------------------------------------------------------------------------
// Physical zone map for planner (uses plannerZoneMappings)
// ---------------------------------------------------------------------------
function getPlannerPhysicalZoneMap() {
    const map = {};
    if (!plannerZoneMappings || !plannerZoneMappings.length) return map;
    const subMapping = plannerZoneMappings.find(d => d.subscriptionId === plannerSubscriptionId);
    if (subMapping?.mappings) {
        subMapping.mappings.forEach(m => { map[m.logicalZone] = m.physicalZone; });
    }
    return map;
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Fetch spot score from the Zone Availability panel button
// ---------------------------------------------------------------------------
async function fetchSpotFromPanel() {
    const skuName = _pricingModalSku;
    if (!skuName) return;
    const region = document.getElementById("region-select").value;
    const tenant = document.getElementById("tenant-select").value;
    const subscriptionId = plannerSubscriptionId;
    if (!subscriptionId || !region) return;

    // Show spinner on button
    const btn = document.querySelector('#zoneCollapsePanel .btn-outline-primary');
    const origHtml = btn?.innerHTML;
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>Fetching…';
    }
    const instanceCount = parseInt(document.getElementById("spot-panel-instances")?.value, 10) || 1;

    try {
        const payload = { region, subscriptionId, skus: [skuName], instanceCount };
        if (tenant) payload.tenantId = tenant;
        const result = await apiPost("/api/spot-scores", payload);
        if (!lastSpotScores) lastSpotScores = { scores: {}, errors: [] };
        if (result.scores) {
            for (const [sku, zoneScores] of Object.entries(result.scores)) {
                lastSpotScores.scores[sku] = { ...(lastSpotScores.scores[sku] || {}), ...zoneScores };
            }
        }
        if (result.errors?.length) lastSpotScores.errors.push(...result.errors);

        // Refresh confidence from the backend (canonical source of truth)
        if (lastSkuData) {
            await refreshDeploymentConfidence([skuName]);
            renderRegionSummary(lastSkuData);
            renderSkuTable(lastSkuData);
        }

        // Re-render modal in place with Zone Availability kept open
        if (_lastPricingData) {
            // Collect currently open accordion panels
            const content = document.getElementById("pricing-modal-content");
            const openIds = [...content.querySelectorAll('.accordion-collapse.show')].map(el => el.id).filter(Boolean);
            // Ensure zone accordion stays open
            if (!openIds.includes('zoneCollapsePanel')) openIds.push('zoneCollapsePanel');
            renderPricingDetail(_lastPricingData, openIds);
        }

        if (result.errors?.length) showError("planner-error", "Spot score error: " + result.errors.join("; "));
    } catch (err) {
        showError("planner-error", "Failed to fetch Spot Score: " + err.message);
        if (btn) { btn.disabled = false; btn.innerHTML = origHtml; }
    }
}

// Spot Score Modal
// ---------------------------------------------------------------------------
let _spotModalSku = null;
let _spotModal = null;

function openSpotModal(skuName) {
    _spotModalSku = skuName;
    document.getElementById("spot-modal-sku").textContent = skuName;
    document.getElementById("spot-modal-instances").value = "1";
    document.getElementById("spot-modal-loading").classList.add("d-none");
    document.getElementById("spot-modal-result").classList.add("d-none");
    if (!_spotModal) _spotModal = new bootstrap.Modal(document.getElementById("spotModal"));
    _spotModal.show();
    setTimeout(() => {
        const input = document.getElementById("spot-modal-instances");
        input.focus();
        input.select();
    }, 300);
}

async function confirmSpotScore() {
    const skuName = _spotModalSku;
    if (!skuName) return;

    const region = document.getElementById("region-select").value;
    const tenant = document.getElementById("tenant-select").value;
    const subscriptionId = plannerSubscriptionId;
    if (!subscriptionId || !region) return;

    const instanceCount = parseInt(document.getElementById("spot-modal-instances").value, 10) || 1;
    document.getElementById("spot-modal-loading").classList.remove("d-none");
    document.getElementById("spot-modal-result").classList.add("d-none");

    try {
        const payload = { region, subscriptionId, skus: [skuName], instanceCount };
        if (tenant) payload.tenantId = tenant;
        const result = await apiPost("/api/spot-scores", payload);

        // Accumulate into cache
        if (!lastSpotScores) lastSpotScores = { scores: {}, errors: [] };
        if (result.scores) {
            for (const [sku, zoneScores] of Object.entries(result.scores)) {
                lastSpotScores.scores[sku] = { ...(lastSpotScores.scores[sku] || {}), ...zoneScores };
            }
        }
        if (result.errors?.length) lastSpotScores.errors.push(...result.errors);

        // Show result in modal
        const zoneScores = result.scores?.[skuName] || {};
        const resultEl = document.getElementById("spot-modal-result");
        const zones = Object.keys(zoneScores).sort();
        if (zones.length > 0) {
            resultEl.innerHTML = '<div class="spot-modal-grid">' + zones.map(z => {
                const s = zoneScores[z] || "Unknown";
                return `<span class="spot-zone-label">Z${escapeHtml(z)}</span><span class="spot-badge spot-${s.toLowerCase()}">${escapeHtml(s)}</span>`;
            }).join("") + '</div>';
        } else {
            resultEl.innerHTML = '<span class="spot-badge spot-unknown">Unknown</span>';
        }
        resultEl.classList.remove("d-none");

        // Refresh confidence from the backend (canonical source of truth)
        if (lastSkuData) {
            await refreshDeploymentConfidence([skuName]);
            renderRegionSummary(lastSkuData);
            renderSkuTable(lastSkuData);
        }

        if (result.errors?.length) showError("planner-error", "Spot score error: " + result.errors.join("; "));
    } catch (err) {
        showError("planner-error", "Failed to fetch Spot Score: " + err.message);
    } finally {
        document.getElementById("spot-modal-loading").classList.add("d-none");
    }
}

// ---------------------------------------------------------------------------
// SKU Detail Modal
// ---------------------------------------------------------------------------
let _pricingModalSku = null;
let _pricingModal = null;
let _pricingModalCurrency = "USD";
let _lastPricingData = null;

function openPricingModal(skuName) {
    _pricingModalSku = skuName;
    document.getElementById("pricing-modal-sku").textContent = skuName;
    document.getElementById("pricing-modal-loading").classList.add("d-none");
    document.getElementById("pricing-modal-content").classList.add("d-none");
    if (!_pricingModal) _pricingModal = new bootstrap.Modal(document.getElementById("pricingModal"));
    _pricingModal.show();
    fetchPricingDetail();
}

function refreshPricingModal() {
    const sel = document.getElementById("pricing-modal-currency-select");
    if (sel) _pricingModalCurrency = sel.value;
    if (_pricingModalSku) fetchPricingDetail();
}

async function fetchAdmissionIntelligence(skuName) {
    const region = document.getElementById("region-select").value;
    if (!region || !plannerSubscriptionId) return null;
    try {
        const params = new URLSearchParams({ region, sku: skuName, subscriptionId: plannerSubscriptionId });
        const tqs = tenantQS("&");
        const data = await apiFetch(`/api/sku-admission?${params}${tqs}`);
        if (data && !data.error) {
            _admissionCache[skuName] = data;
            return data;
        }
    } catch (err) {
        console.warn("Admission intelligence fetch failed for", skuName, err);
    }
    return null;
}

async function fetchPricingDetail() {
    const skuName = _pricingModalSku;
    if (!skuName) return;
    const region = document.getElementById("region-select").value;
    const currency = _pricingModalCurrency;
    if (!region) return;

    document.getElementById("pricing-modal-loading").classList.remove("d-none");
    document.getElementById("pricing-modal-content").classList.add("d-none");

    try {
        const params = new URLSearchParams({ region, skuName, currencyCode: currency });
        if (plannerSubscriptionId) params.set("subscriptionId", plannerSubscriptionId);
        const tqs = tenantQS("&");
        const [data] = await Promise.all([
            apiFetch(`/api/sku-pricing?${params}${tqs}`),
            fetchAdmissionIntelligence(skuName),
        ]);
        renderPricingDetail(data);
        // Update admission column in table if data was cached
        if (_admissionCache[skuName] && lastSkuData) renderSkuTable(lastSkuData);
    } catch (err) {
        const content = document.getElementById("pricing-modal-content");
        content.innerHTML = `<p class="text-danger small">Failed to load pricing: ${escapeHtml(err.message)}</p>`;
        content.classList.remove("d-none");
    } finally {
        document.getElementById("pricing-modal-loading").classList.add("d-none");
    }
}

function renderPricingDetail(data, openAccordionIds) {
    _lastPricingData = data;
    const content = document.getElementById("pricing-modal-content");
    const currency = data.currency || "USD";
    const HOURS_PER_MONTH = 730;

    const confSku = (lastSkuData || []).find(s => s.name === _pricingModalSku);

    // Update local pricing data (for display) – confidence is NOT recomputed
    // here; it comes from the backend (canonical source of truth).
    if (confSku && (data.paygo != null || data.spot != null)) {
        if (!confSku.pricing) confSku.pricing = {};
        if (data.paygo != null) confSku.pricing.paygo = data.paygo;
        if (data.spot != null) confSku.pricing.spot = data.spot;
        confSku.pricing.currency = currency;
    }

    // Build sections in order: Confidence → Admission → VM Profile → Zone Availability → Quota → Pricing
    let html = "";
    if (confSku?.confidence) html += renderConfidenceBreakdown(confSku.confidence);
    const admData = _admissionCache[_pricingModalSku];
    html += renderAdmissionIntelligence(admData);
    if (data.profile) html += renderVmProfile(data.profile);
    if (data.profile) html += renderZoneAvailability(data.profile, confSku?.confidence);
    const vcpus = parseInt(confSku?.capabilities?.vCPUs, 10) || 0;
    if (confSku?.quota) html += renderQuotaPanel(confSku.quota, vcpus, confSku.confidence);

    // Build pricing table
    const spotDiscount = (data.paygo != null && data.spot != null && data.paygo > 0)
        ? Math.round((1 - data.spot / data.paygo) * 100)
        : null;
    const spotLabel = spotDiscount != null
        ? `Spot <span class="badge bg-success-subtle text-success-emphasis ms-1">\u2212${spotDiscount}%</span>`
        : "Spot";
    const rows = [
        { label: "Pay-As-You-Go", hourly: data.paygo },
        { label: spotLabel, raw: true, hourly: data.spot },
        { label: "Reserved Instance 1Y", hourly: data.ri_1y },
        { label: "Reserved Instance 3Y", hourly: data.ri_3y },
        { label: "Savings Plan 1Y", hourly: data.sp_1y },
        { label: "Savings Plan 3Y", hourly: data.sp_3y },
    ];

    let pricingHtml = '<table class="table table-sm pricing-detail-table mb-0">';
    pricingHtml += `<thead><tr><th>Type</th><th>${escapeHtml(currency)}/hour</th><th>${escapeHtml(currency)}/month</th></tr></thead><tbody>`;
    rows.forEach(r => {
        const hourStr = r.hourly != null ? formatNum(r.hourly, 4) : "\u2014";
        const monthStr = r.hourly != null ? formatNum(r.hourly * HOURS_PER_MONTH, 2) : "\u2014";
        const labelHtml = r.raw ? r.label : escapeHtml(r.label);
        pricingHtml += `<tr><td>${labelHtml}</td><td class="price-cell">${hourStr}</td><td class="price-cell">${monthStr}</td></tr>`;
    });
    pricingHtml += "</tbody></table>";

    // Currency selector options
    const currencies = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "SEK", "BRL", "INR"];
    const currencyOpts = currencies.map(c =>
        `<option value="${c}"${c === currency ? " selected" : ""}>${c}</option>`
    ).join("");

    // Wrap pricing + currency in a collapsible accordion
    html += '<div class="accordion mt-3" id="pricingAccordion">';
    html += '<div class="accordion-item">';
    html += '<h2 class="accordion-header">';
    html += '<button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#pricingCollapsePanel" aria-expanded="false" aria-controls="pricingCollapsePanel">';
    html += '<i class="bi bi-currency-exchange me-2"></i>Pricing';
    html += '</button></h2>';
    html += '<div id="pricingCollapsePanel" class="accordion-collapse collapse" data-bs-parent="#pricingAccordion">';
    html += '<div class="accordion-body p-2">';
    html += '<div class="d-flex align-items-center gap-2 mb-2">';
    html += '<label for="pricing-modal-currency-select" class="form-label small mb-0">Currency:</label>';
    html += `<select class="form-select form-select-sm" id="pricing-modal-currency-select" onchange="refreshPricingModal()" style="width:100px;">${currencyOpts}</select>`;
    html += '</div>';
    html += pricingHtml;
    html += '</div></div></div></div>';

    content.innerHTML = html;
    content.classList.remove("d-none");

    // Restore open accordion panels
    if (openAccordionIds?.length) {
        openAccordionIds.forEach(id => {
            const panel = content.querySelector(`#${id}`);
            if (panel) panel.classList.add("show");
            const btn = content.querySelector(`[data-bs-target="#${id}"]`);
            if (btn) { btn.classList.remove("collapsed"); btn.setAttribute("aria-expanded", "true"); }
        });
    }

    // Init Bootstrap tooltips for confidence info icons
    content.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
        new bootstrap.Tooltip(el, { delay: { show: 0, hide: 100 }, placement: "top" });
    });
}

function renderVmProfile(profile) {
    const caps = profile.capabilities || {};

    function badge(val, trueLabel, falseLabel) {
        if (val === true) return `<span class="vm-badge vm-badge-yes">${escapeHtml(trueLabel || "Yes")}</span>`;
        if (val === false) return `<span class="vm-badge vm-badge-no">${escapeHtml(falseLabel || "No")}</span>`;
        return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
    }
    function row(label, value) {
        return `<div class="vm-profile-row"><span class="vm-profile-label">${escapeHtml(label)}</span><span>${value}</span></div>`;
    }
    function val(v, suffix) {
        if (v == null) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        return escapeHtml(String(v) + (suffix || ""));
    }
    function bytesToMBs(v) {
        if (v == null) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        return escapeHtml((Number(v) / (1024 * 1024)).toFixed(0) + " MB/s");
    }
    function bytesToGB(v) {
        if (v == null) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        return escapeHtml((Number(v) / (1024 * 1024 * 1024)).toFixed(0) + " GB");
    }
    function mbpsToGbps(v) {
        if (v == null) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        const gbps = Number(v) / 1000;
        return escapeHtml(gbps >= 1 ? gbps.toFixed(1) + " Gbps" : v + " Mbps");
    }

    let html = '<div class="vm-profile-section">';
    html += '<h4 class="vm-profile-title">VM Profile</h4>';
    html += '<div class="vm-profile-grid">';

    html += '<div class="vm-profile-card">';
    html += '<div class="vm-profile-card-title">Compute</div>';
    html += row("vCPUs", val(caps.vCPUs));
    html += row("Memory", val(caps.MemoryGB, " GB"));
    html += row("Architecture", val(caps.CpuArchitectureType));
    html += row("GPUs", val(caps.GPUs ?? caps.GpuCount));
    html += row("HyperV Gen.", val(caps.HyperVGenerations));
    html += row("Encryption at Host", badge(caps.EncryptionAtHostSupported));
    html += row("Confidential", val(caps.ConfidentialComputingType || null));
    html += '</div>';

    html += '<div class="vm-profile-card">';
    html += '<div class="vm-profile-card-title">Storage</div>';
    html += row("Premium IO", badge(caps.PremiumIO));
    html += row("Ultra SSD", badge(caps.UltraSSDAvailable));
    html += row("Ephemeral OS Disk", badge(caps.EphemeralOSDiskSupported));
    html += row("Max Data Disks", val(caps.MaxDataDiskCount));
    html += row("Uncached Disk IOPS", val(caps.UncachedDiskIOPS));
    html += row("Uncached Disk BW", bytesToMBs(caps.UncachedDiskBytesPerSecond));
    html += row("Cached Disk Size", bytesToGB(caps.CachedDiskBytes));
    html += row("Write Accelerator", val(caps.MaxWriteAcceleratorDisksAllowed));
    html += row("Temp Disk", val(caps.TempDiskSizeInGiB, " GiB"));
    html += '</div>';

    html += '<div class="vm-profile-card">';
    html += '<div class="vm-profile-card-title">Network</div>';
    html += row("Accelerated Net.", badge(caps.AcceleratedNetworkingEnabled));
    html += row("Max NICs", val(caps.MaxNetworkInterfaces ?? caps.MaximumNetworkInterfaces));
    html += row("Max Bandwidth", mbpsToGbps(caps.MaxBandwidthMbps));
    html += row("RDMA", badge(caps.RdmaEnabled));
    html += '</div>';

    html += '</div></div>';
    return html;
}

function renderZoneAvailability(profile, confidence) {
    const zones = profile.zones || [];
    const restrictions = profile.restrictions || [];
    const _components = confidence?.breakdown?.components || [];
    const zoneSignal = _components.find(b => b.name === "zones");
    const zoneScore = zoneSignal?.score100;
    const spotSignal = _components.find(b => b.name === "spot");
    const spotScore = spotSignal?.score100;
    const physicalZoneMap = getPlannerPhysicalZoneMap();
    const spotZoneScores = (lastSpotScores?.scores || {})[_pricingModalSku] || {};

    function row(label, value) {
        return `<div class="vm-profile-row"><span class="vm-profile-label">${escapeHtml(label)}</span><span>${value}</span></div>`;
    }
    function val(v) {
        if (v == null) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        return escapeHtml(String(v));
    }

    const reasonLabels = {
        NotAvailableForSubscription: "Not available for this subscription",
        QuotaId: "Subscription offer type not eligible",
    };

    const hasLocationRestriction = restrictions.some(r => r.type === "Location");
    const zoneRestrictionZones = new Set(restrictions.filter(r => r.type === "Zone").flatMap(r => r.zones || []));
    const allZoneIds = [...new Set([...["1", "2", "3"], ...zones])].sort();
    const availableCount = zones.filter(z => !zoneRestrictionZones.has(z) && !hasLocationRestriction).length;

    let bodyHtml = '<div class="vm-profile-grid">';

    // Zone status card
    bodyHtml += '<div class="vm-profile-card">';
    bodyHtml += '<div class="vm-profile-card-title">Zones</div>';
    if (hasLocationRestriction) {
        bodyHtml += row("Region", '<span class="vm-badge vm-badge-no">Restricted</span>');
    }
    bodyHtml += row("Available", val(availableCount + " / 3"));
    allZoneIds.forEach(z => {
        const pz = physicalZoneMap[z];
        const zLabel = `Zone ${z}`;
        const pzTip = pz ? ` data-bs-toggle="tooltip" data-bs-title="${escapeHtml(pz)}"` : "";
        const offered = zones.includes(z);
        const restricted = zoneRestrictionZones.has(z) || hasLocationRestriction;
        if (!offered && !restricted) {
            bodyHtml += `<div class="vm-profile-row"><span class="vm-profile-label"${pzTip}>${escapeHtml(zLabel)}</span><span class="vm-badge vm-badge-unknown">Not offered</span></div>`;
        } else if (restricted) {
            bodyHtml += `<div class="vm-profile-row"><span class="vm-profile-label"${pzTip}>${escapeHtml(zLabel)}</span><span class="vm-badge vm-badge-no">Restricted</span></div>`;
        } else {
            bodyHtml += `<div class="vm-profile-row"><span class="vm-profile-label"${pzTip}>${escapeHtml(zLabel)}</span><span class="vm-badge vm-badge-yes">Available</span></div>`;
        }
    });
    if (zoneScore != null) {
        const lbl = _scoreLabel(zoneScore).toLowerCase().replace(/\s+/g, "-");
        bodyHtml += `<div class="vm-profile-row"><span class="vm-profile-label">Breadth Score</span><span class="confidence-badge confidence-${lbl}" data-bs-toggle="tooltip" data-bs-title="Zone Breadth signal: ${availableCount}/3 zones available without restrictions. Score of 100 means all 3 zones are offered and unrestricted.">${zoneScore}/100</span></div>`;
    }
    bodyHtml += '</div>';

    // Spot Placement card
    const hasSpotData = Object.keys(spotZoneScores).length > 0;
    const spotLabels = {
        high: "High", medium: "Medium", low: "Low",
        restrictedskunotavailable: "Restricted", unknown: "Unknown",
        datanotfoundorstale: "No data",
    };
    function spotBadgeHtml(raw) {
        const key = (raw || "").toLowerCase();
        const friendly = spotLabels[key] || raw;
        const knownClass = ["high", "medium", "low"].includes(key) ? `spot-badge spot-${key}` : "vm-badge vm-badge-unknown";
        return `<span class="${knownClass}">${escapeHtml(friendly)}</span>`;
    }
    bodyHtml += '<div class="vm-profile-card">';
    bodyHtml += '<div class="vm-profile-card-title">Spot Placement</div>';
    const modalSku = (lastSkuData || []).find(s => s.name === _pricingModalSku);
    const hasSpotPrice = modalSku?.pricing?.spot != null || (_lastPricingData?.spot != null);
    if (!hasSpotData && !hasSpotPrice) {
        bodyHtml += row("Status", '<span class="vm-badge vm-badge-unknown">No spot pricing</span>');
    } else if (!hasSpotData) {
        bodyHtml += row("Status", '<span class="vm-badge vm-badge-unknown">No data</span>');
        bodyHtml += '<div class="vm-profile-row"><div class="d-flex align-items-center gap-2 w-100">';
        bodyHtml += '<input type="number" id="spot-panel-instances" class="form-control form-control-sm" value="1" min="1" max="1000" style="width:70px;" title="Instance count">';
        bodyHtml += '<button class="btn btn-sm btn-outline-primary flex-grow-1" onclick="fetchSpotFromPanel()"><i class="bi bi-lightning-charge me-1"></i>Fetch Spot Scores</button>';
        bodyHtml += '</div></div>';
    } else {
        const bestLabel = _bestSpotLabel(spotZoneScores);
        allZoneIds.forEach(z => {
            const pz = physicalZoneMap[z];
            const zLabel = `Zone ${z}`;
            const pzTip = pz ? ` data-bs-toggle="tooltip" data-bs-title="${escapeHtml(pz)}"` : "";
            const s = spotZoneScores[z];
            if (s) {
                const key = s.toLowerCase();
                const isBest = key === (bestLabel || "").toLowerCase() && ["high", "medium", "low"].includes(key);
                const star = isBest ? ' <i class="bi bi-star-fill text-warning" data-bs-toggle="tooltip" data-bs-title="Best eviction rate \u2014 used for confidence score"></i>' : "";
                bodyHtml += `<div class="vm-profile-row"><span class="vm-profile-label"${pzTip}>${escapeHtml(zLabel)}</span><span>${spotBadgeHtml(s)}${star}</span></div>`;
            } else {
                bodyHtml += `<div class="vm-profile-row"><span class="vm-profile-label"${pzTip}>${escapeHtml(zLabel)}</span><span class="vm-badge vm-badge-unknown">\u2014</span></div>`;
            }
        });
        if (spotScore != null) {
            const lbl = _scoreLabel(spotScore).toLowerCase().replace(/\s+/g, "-");
            bodyHtml += `<div class="vm-profile-row"><span class="vm-profile-label">Spot Score</span><span class="confidence-badge confidence-${lbl}" data-bs-toggle="tooltip" data-bs-title="Average per-zone score across 3 zones (High\u2192100, Medium\u219260, Low\u219225, Restricted/Unknown\u21920).">${spotScore}/100</span></div>`;
        }
    }
    bodyHtml += '</div>';

    // Restrictions card
    bodyHtml += '<div class="vm-profile-card">';
    bodyHtml += '<div class="vm-profile-card-title">Restrictions</div>';
    if (restrictions.length === 0) {
        bodyHtml += row("Status", '<span class="vm-badge vm-badge-yes">None</span>');
    } else {
        restrictions.forEach(r => {
            const reason = reasonLabels[r.reasonCode] || r.reasonCode || "Unknown reason";
            let scope;
            if (r.type === "Location") {
                scope = "Entire region";
            } else if (r.type === "Zone" && r.zones?.length) {
                scope = `Zone${r.zones.length > 1 ? "s" : ""} ${r.zones.join(", ")}`;
            } else {
                scope = r.type || "Unknown";
            }
            bodyHtml += `<div class="vm-profile-row vm-profile-row-stacked"><span class="vm-profile-label">${escapeHtml(scope)}</span><span class="vm-badge vm-badge-limited vm-badge-block">${escapeHtml(reason)}</span></div>`;
        });
    }
    bodyHtml += '</div>';

    bodyHtml += '</div>';

    // Wrap in accordion
    let html = '<div class="accordion mt-3" id="zoneAccordion">';
    html += '<div class="accordion-item">';
    html += '<h2 class="accordion-header">';
    html += '<button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#zoneCollapsePanel" aria-expanded="false" aria-controls="zoneCollapsePanel">';
    html += '<i class="bi bi-pin-map me-2"></i>Zone Availability';
    html += '</button></h2>';
    html += '<div id="zoneCollapsePanel" class="accordion-collapse collapse" data-bs-parent="#zoneAccordion">';
    html += '<div class="accordion-body p-2">';
    html += bodyHtml;
    html += '</div></div></div></div>';
    return html;
}

function renderQuotaPanel(quota, vcpus, confidence) {
    const limit = quota.limit;
    const used = quota.used;
    const remaining = quota.remaining;
    const pct = (limit != null && limit > 0) ? Math.round((used / limit) * 100) : null;
    const deployable = (remaining != null && vcpus > 0) ? Math.floor(remaining / vcpus) : null;

    // Extract the quota signal from the confidence breakdown
    const _qComponents = confidence?.breakdown?.components || [];
    const quotaSignal = _qComponents.find(b => b.name === "quota");
    const quotaScore = quotaSignal?.score100;

    function row(label, value) {
        return `<div class="vm-profile-row"><span class="vm-profile-label">${escapeHtml(label)}</span><span>${value}</span></div>`;
    }
    function val(v) {
        if (v == null) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        return escapeHtml(formatNum(v, 0));
    }

    let barClass = "bg-success";
    if (pct != null) {
        if (pct >= 90) barClass = "bg-danger";
        else if (pct >= 70) barClass = "bg-warning";
    }

    let bodyHtml = '<div class="vm-profile-grid">';

    bodyHtml += '<div class="vm-profile-card">';
    bodyHtml += '<div class="vm-profile-card-title">vCPU Family Quota</div>';
    bodyHtml += row("Limit", val(limit));
    bodyHtml += row("Used", val(used));
    bodyHtml += row("Remaining", val(remaining));
    if (pct != null) {
        bodyHtml += `<div class="vm-profile-row"><span class="vm-profile-label">Usage</span><span>${pct}%</span></div>`;
        bodyHtml += `<div class="progress mt-1" style="height: 6px;" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100">`;
        bodyHtml += `<div class="progress-bar ${barClass}" style="width: ${pct}%"></div></div>`;
    }
    bodyHtml += '</div>';

    bodyHtml += '<div class="vm-profile-card">';
    bodyHtml += '<div class="vm-profile-card-title">Deployment Headroom</div>';
    bodyHtml += row("vCPUs per Instance", vcpus > 0 ? escapeHtml(String(vcpus)) : '\u2014');
    if (deployable != null) {
        const badge = deployable === 0
            ? '<span class="vm-badge vm-badge-no">' + formatNum(deployable, 0) + '</span>'
            : deployable <= 5
                ? '<span class="vm-badge vm-badge-limited">' + formatNum(deployable, 0) + '</span>'
                : '<span class="vm-badge vm-badge-yes">' + formatNum(deployable, 0) + '</span>';
        bodyHtml += `<div class="vm-profile-row"><span class="vm-profile-label">Deployable Instances</span>${badge}</div>`;
    } else {
        bodyHtml += row("Deployable Instances", '\u2014');
    }
    if (quotaScore != null) {
        const lbl = _scoreLabel(quotaScore).toLowerCase().replace(/\s+/g, "-");
        bodyHtml += `<div class="vm-profile-row"><span class="vm-profile-label">Headroom Score</span><span class="confidence-badge confidence-${lbl}" data-bs-toggle="tooltip" data-bs-title="Quota signal score: remaining vCPUs relative to SKU size. Score of 100 means \u226510 instances can be deployed.">${quotaScore}/100</span></div>`;
    }
    bodyHtml += '</div>';

    bodyHtml += '</div>';

    // Wrap in accordion
    let html = '<div class="accordion mt-3" id="quotaAccordion">';
    html += '<div class="accordion-item">';
    html += '<h2 class="accordion-header">';
    html += '<button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#quotaCollapsePanel" aria-expanded="false" aria-controls="quotaCollapsePanel">';
    html += '<i class="bi bi-speedometer me-2"></i>Quota';
    html += '</button></h2>';
    html += '<div id="quotaCollapsePanel" class="accordion-collapse collapse" data-bs-parent="#quotaAccordion">';
    html += '<div class="accordion-body p-2">';
    html += bodyHtml;
    html += '</div></div></div></div>';
    return html;
}

function renderConfidenceBreakdown(conf) {
    const lbl = (conf.label || "").toLowerCase().replace(/\s+/g, "-");
    let html = '<div class="confidence-section">';
    html += `<h4 class="confidence-title">Deployment Confidence <span class="confidence-badge confidence-${lbl}">${conf.score} ${escapeHtml(conf.label || "")}</span> <i class="bi bi-info-circle text-body-secondary confidence-info-icon" data-bs-toggle="tooltip" data-bs-title="Composite score (0\u2013100) predicting deployment success based on weighted signals. Higher is better. Scoring version: ${escapeHtml(conf.scoringVersion || '')}"></i></h4>`;
    const components = conf.breakdown?.components || conf.breakdown || [];
    const usedComponents = components.filter(c => c.status === "used");
    if (usedComponents.length) {
        html += '<table class="table table-sm confidence-breakdown-table"><thead><tr><th>Signal</th><th>Score</th><th>Weight</th><th>Contribution</th></tr></thead><tbody>';
        const signalLabels = { quota: "Quota Headroom", spot: "Spot Placement", zones: "Zone Breadth", restrictions: "Restrictions", pricePressure: "Price Pressure" };
        const signalDescriptions = {
            quota: "Remaining quota relative to vCPU count. Low quota means deployments may be blocked.",
            spot: "Best per-zone Spot Placement Score (Azure API). Higher means better spot allocation likelihood.",
            zones: "Number of available (non-restricted) availability zones where the SKU is offered (out of 3).",
            restrictions: "Whether any subscription or zone-level restrictions apply to this SKU.",
            pricePressure: "Spot-to-PAYGO price ratio. A lower ratio indicates better spot savings."
        };
        usedComponents.forEach(b => {
            const name = b.name || b.signal || "";
            const desc = signalDescriptions[name] || "";
            const score = b.score100 != null ? b.score100 : b.score;
            const contribution = b.contribution != null ? (b.contribution * 100).toFixed(1) : "0.0";
            html += `<tr><td>${escapeHtml(signalLabels[name] || name)} <i class="bi bi-info-circle text-body-secondary" data-bs-toggle="tooltip" data-bs-title="${escapeHtml(desc)}"></i></td><td>${score}</td><td>${(b.weight * 100).toFixed(1)}%</td><td>${contribution}</td></tr>`;
        });
        html += '</tbody></table>';
    }
    const missingSignals = conf.missingSignals || conf.missing || [];
    if (missingSignals.length) {
        const signalLabels = { quota: "Quota Headroom", spot: "Spot Placement", zones: "Zone Breadth", restrictions: "Restrictions", pricePressure: "Price Pressure" };
        const names = missingSignals.map(m => signalLabels[m] || m).join(", ");
        html += `<p class="confidence-missing"><i class="bi bi-exclamation-circle"></i> Missing signals (excluded from score): ${escapeHtml(names)}</p>`;
    }
    if (conf.disclaimers?.length) {
        html += '<p class="confidence-disclaimer text-body-secondary small fst-italic mt-1 mb-0">' + escapeHtml(conf.disclaimers[0]) + '</p>';
    }
    html += '</div>';
    return html;
}

function renderAdmissionIntelligence(admData) {
    if (!admData) return "";
    const ac = admData.admissionConfidence || {};
    const frag = admData.fragmentationRisk || {};
    const vol24 = admData.volatility24h || {};
    const vol7d = admData.volatility7d || {};
    const evict = admData.evictionRate || {};

    function admBadge(score, label) {
        if (score == null) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        const cls = score >= 80 ? "admission-high" : score >= 60 ? "admission-medium" : score >= 40 ? "admission-low" : "admission-very-low";
        return `<span class="admission-badge ${cls}">${score} ${escapeHtml(label || "")}</span>`;
    }
    function admRow(label, value) {
        return `<div class="vm-profile-row"><span class="vm-profile-label">${escapeHtml(label)}</span><span>${value}</span></div>`;
    }
    function signalBadge(label) {
        if (!label) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        const lower = label.toLowerCase();
        const cls = lower === "low" ? "admission-high" : lower === "moderate" || lower === "medium" ? "admission-medium" : lower === "high" ? "admission-low" : lower === "critical" || lower === "very high" ? "admission-very-low" : "";
        return cls ? `<span class="admission-badge ${cls}">${escapeHtml(label)}</span>` : escapeHtml(label);
    }

    let html = '<div class="admission-section">';
    html += '<h4 class="vm-profile-title">Admission Intelligence</h4>';
    html += '<div class="admission-grid">';

    // Left column: Admission Confidence card
    html += '<div class="vm-profile-card">';
    html += '<div class="vm-profile-card-title">Admission Confidence</div>';
    html += admRow("Score", admBadge(ac.score, ac.label));
    html += admRow("Signals Available", ac.signalsAvailable != null ? String(ac.signalsAvailable) : "\u2014");

    if (ac.breakdown?.length) {
        html += '<div class="confidence-breakdown-table mt-2"><table class="table table-sm mb-0"><thead><tr><th>Signal</th><th>Raw</th><th>Norm</th><th>Weight</th><th>Contrib</th></tr></thead><tbody>';
        ac.breakdown.forEach(b => {
            const rawStr = b.rawValue != null ? escapeHtml(String(b.rawValue)) : "\u2014";
            const normStr = b.normalizedScore != null ? b.normalizedScore.toFixed(2) : "\u2014";
            const weightStr = b.weight != null ? b.weight.toFixed(2) : "\u2014";
            const contribStr = b.contribution != null ? b.contribution.toFixed(1) : "\u2014";
            html += `<tr><td>${escapeHtml(b.signal || "")}</td><td>${rawStr}</td><td>${normStr}</td><td>${weightStr}</td><td>${contribStr}</td></tr>`;
        });
        html += '</tbody></table></div>';
    }

    if (ac.missingInputs?.length) {
        html += `<div class="confidence-missing mt-2"><i class="bi bi-exclamation-triangle me-1"></i>Missing: ${ac.missingInputs.map(m => escapeHtml(m)).join(", ")}</div>`;
    }
    html += '</div>';

    // Right column: Signals card
    html += '<div class="vm-profile-card">';
    html += '<div class="vm-profile-card-title">Signals</div>';
    html += admRow("Fragmentation Risk", signalBadge(frag.label));
    if (frag.factors?.length) {
        html += `<div class="small text-body-secondary ms-2 mb-2">${frag.factors.map(f => escapeHtml(f)).join("; ")}</div>`;
    }
    html += admRow("Volatility (24h)", signalBadge(vol24.label));
    if (vol24.sampleCount != null) {
        html += `<div class="small text-body-secondary ms-2 mb-1">Samples: ${vol24.sampleCount}`;
        if (vol24.timeInLowPercent != null) html += ` | Time in Low: ${vol24.timeInLowPercent.toFixed(0)}%`;
        html += '</div>';
    }
    html += admRow("Volatility (7d)", signalBadge(vol7d.label));
    if (vol7d.sampleCount != null) {
        html += `<div class="small text-body-secondary ms-2 mb-1">Samples: ${vol7d.sampleCount}`;
        if (vol7d.timeInLowPercent != null) html += ` | Time in Low: ${vol7d.timeInLowPercent.toFixed(0)}%`;
        html += '</div>';
    }
    html += admRow("Eviction Rate", evict.evictionRate ? escapeHtml(evict.evictionRate) : "\u2014");
    if (evict.status) html += `<div class="small text-body-secondary ms-2 mb-1">${escapeHtml(evict.status)}</div>`;
    html += '</div>';

    html += '</div>'; // .admission-grid

    html += '<div class="admission-disclaimer mt-2"><i class="bi bi-info-circle me-1"></i>Estimated signals derived from public APIs and collected history. Not a deployment guarantee.</div>';

    html += '<div class="accordion mt-2" id="admissionAccordion">';
    html += '<div class="accordion-item">';
    html += '<h2 class="accordion-header">';
    html += '<button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#admissionJsonPanel" aria-expanded="false" aria-controls="admissionJsonPanel">';
    html += '<i class="bi bi-code-slash me-2"></i>Breakdown JSON';
    html += '</button></h2>';
    html += '<div id="admissionJsonPanel" class="accordion-collapse collapse" data-bs-parent="#admissionAccordion">';
    html += `<div class="accordion-body p-2"><pre class="mb-0 small" style="max-height:300px;overflow:auto">${escapeHtml(JSON.stringify(admData, null, 2))}</pre></div>`;
    html += '</div></div></div>';

    html += '</div>'; // .admission-section
    return html;
}

// ---------------------------------------------------------------------------
// Render SKU table  (powered by Simple-DataTables)
// ---------------------------------------------------------------------------
function _computeRegionScores(skus) {
    // Region Readiness: average confidence score
    const confScores = skus.map(s => s.confidence?.score).filter(s => s != null);
    const readiness = confScores.length > 0
        ? Math.round(confScores.reduce((a, b) => a + b, 0) / confScores.length)
        : null;

    // Zone Consistency: how uniformly SKUs are distributed across zones
    const allLogicalZones = [...new Set(skus.flatMap(s => s.zones || []))].sort();
    let consistency = null;
    if (allLogicalZones.length > 1) {
        const zoneCounts = allLogicalZones.map(lz =>
            skus.filter(s => (s.zones || []).includes(lz) && !(s.restrictions || []).includes(lz)).length
        );
        const minCount = Math.min(...zoneCounts);
        const maxCount = Math.max(...zoneCounts);
        consistency = minCount === maxCount ? 100 : Math.round((minCount / maxCount) * 100);
    } else if (allLogicalZones.length === 1) {
        consistency = 100;
    }

    // Zone breakdown for detail
    const zoneBreakdown = allLogicalZones.map(lz => {
        const available = skus.filter(s => (s.zones || []).includes(lz) && !(s.restrictions || []).includes(lz)).length;
        const restricted = skus.filter(s => (s.restrictions || []).includes(lz)).length;
        return { zone: lz, available, restricted };
    });

    return { readiness, consistency, total: skus.length, zones: allLogicalZones.length, zoneBreakdown };
}

function _scoreLabel(score) {
    for (const [th, lbl] of _REGION_SCORE_LABELS) {
        if (score >= th) return lbl;
    }
    return "Very Low";
}

function renderRegionSummary(skus) {
    const el = document.getElementById("region-summary");
    if (!el) return;
    if (!skus || skus.length === 0) { el.classList.add("d-none"); return; }

    const scores = _computeRegionScores(skus);
    const regionSelect = document.getElementById("region-select");
    let regionName = "Region";
    if (regionSelect) {
        const idx = regionSelect.selectedIndex;
        if (idx >= 0 && regionSelect.options[idx]) {
            regionName = regionSelect.options[idx].text || regionSelect.value || "Region";
        } else {
            regionName = regionSelect.value || "Region";
        }
    }

    const readinessLbl = scores.readiness != null ? _scoreLabel(scores.readiness).toLowerCase().replace(/\s+/g, "-") : null;
    const consistencyLbl = scores.consistency != null ? _scoreLabel(scores.consistency).toLowerCase().replace(/\s+/g, "-") : null;

    const icons = { high: "bi-shield-fill-check", medium: "bi-shield-fill-exclamation", low: "bi-shield-fill-x", "very-low": "bi-shield-fill-x" };
    const consistencyIcons = { high: "bi-symmetry-vertical", medium: "bi-distribute-horizontal", low: "bi-exclude", "very-low": "bi-exclude" };

    let html = '<div class="region-summary-bar">';
    html += `<div class="region-summary-title"><i class="bi bi-geo-alt-fill"></i> ${escapeHtml(regionName)}</div>`;
    html += '<div class="region-summary-scores">';

    // Region Readiness card
    if (scores.readiness != null) {
        html += `<div class="region-score-card">`;
        html += `<div class="region-score-label">Region Readiness</div>`;
        html += `<div class="region-score-value"><span class="confidence-badge confidence-${readinessLbl}" data-bs-toggle="tooltip" data-bs-title="Average deployment confidence across ${scores.total} SKUs. Reflects quota, spot availability, zone coverage, restrictions and pricing."><i class="bi ${icons[readinessLbl] || 'bi-shield'}"></i> ${scores.readiness}</span></div>`;
        html += `</div>`;
    }

    // Zone Consistency card
    if (scores.consistency != null) {
        const detail = scores.zoneBreakdown.map(z => `Zone ${z.zone}: ${z.available} avail${z.restricted ? ', ' + z.restricted + ' restricted' : ''}`).join(' | ');
        html += `<div class="region-score-card">`;
        html += `<div class="region-score-label">Zone Consistency</div>`;
        html += `<div class="region-score-value"><span class="confidence-badge confidence-${consistencyLbl}" data-bs-toggle="tooltip" data-bs-placement="bottom" data-bs-title="${escapeHtml(detail)}"><i class="bi ${consistencyIcons[consistencyLbl] || 'bi-symmetry-vertical'}"></i> ${scores.consistency}</span></div>`;
        html += `</div>`;
    }

    // SKU count & zone count
    html += `<div class="region-score-card">`;
    html += `<div class="region-score-label">SKUs</div>`;
    html += `<div class="region-score-value"><span class="region-stat">${scores.total}</span></div>`;
    html += `</div>`;

    html += `<div class="region-score-card">`;
    html += `<div class="region-score-label">Zones</div>`;
    html += `<div class="region-score-value"><span class="region-stat">${scores.zones}</span></div>`;
    html += `</div>`;

    html += '</div></div>';
    el.innerHTML = html;
    el.classList.remove("d-none");

    // Init tooltips
    el.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(t => {
        new bootstrap.Tooltip(t, { delay: { show: 0, hide: 100 }, placement: t.dataset.bsPlacement || "top", whiteSpace: "pre-line" });
    });
}

// ---------------------------------------------------------------------------
// SKU DataTable
// ---------------------------------------------------------------------------
function renderSkuTable(skus) {
    const container = document.getElementById("sku-table-container");

    // Save current filter values before destroying the table
    _saveSkuFilters();

    if (_skuDataTable) {
        try { _skuDataTable.destroy(); } catch {}
        _skuDataTable = null;
    }

    if (!skus || skus.length === 0) {
        container.innerHTML = '<p class="text-body-secondary text-center py-3">No SKUs found for this region.</p>';
        return;
    }

    const physicalZoneMap = getPlannerPhysicalZoneMap();
    const allLogicalZones = [...new Set(skus.flatMap(s => s.zones))].sort();
    const physicalZones = allLogicalZones.map(lz => physicalZoneMap[lz] || `Zone ${lz}`);
    const hasPricing = skus.some(s => s.pricing);
    const showPricing = hasPricing && (document.getElementById("planner-show-prices")?.checked !== false);
    const showSpot = document.getElementById("planner-show-spot")?.checked !== false;

    // Build table HTML
    let html = '<table id="sku-datatable" class="table table-sm table-hover sku-table">';
    html += "<thead><tr>";

    const priceCurrency = skus.find(s => s.pricing)?.pricing?.currency || "USD";
    const headers = ["SKU Name", "Family", "vCPUs", "Memory (GB)",
        "Quota Limit", "Quota Used", "Quota Remaining"];
    if (showSpot) headers.push("Spot Score");
    headers.push("Confidence");
    headers.push("Admission");
    if (showPricing) {
        headers.push(`PAYGO ${priceCurrency}/h`, `Spot ${priceCurrency}/h`);
    }
    allLogicalZones.forEach((lz, i) => {
        headers.push(`Zone ${escapeHtml(lz)}<br>${escapeHtml(physicalZones[i])}`);
    });
    headers.forEach(h => { html += `<th>${h}</th>`; });
    html += "</tr></thead><tbody>";

    skus.forEach(sku => {
        html += "<tr>";
        // SKU Name (clickable via event delegation)
        html += `<td><button type="button" class="sku-name-btn" data-action="pricing" data-sku="${escapeHtml(sku.name)}">${escapeHtml(sku.name)}</button></td>`;
        html += `<td>${escapeHtml(sku.family || "\u2014")}</td>`;
        html += `<td>${escapeHtml(sku.capabilities.vCPUs || "\u2014")}</td>`;
        html += `<td>${escapeHtml(sku.capabilities.MemoryGB || "\u2014")}</td>`;
        const quota = sku.quota || {};
        html += `<td>${quota.limit != null ? quota.limit : "\u2014"}</td>`;
        html += `<td>${quota.used != null ? quota.used : "\u2014"}</td>`;
        html += `<td>${quota.remaining != null ? quota.remaining : "\u2014"}</td>`;

        // Spot Score
        if (showSpot) {
            const spotZoneScores = (lastSpotScores?.scores || {})[sku.name] || {};
            const spotZones = Object.keys(spotZoneScores).sort();
            const hasSpotPrice = sku.pricing?.spot != null;
            if (spotZones.length > 0) {
                const badges = spotZones.map(z => {
                    const s = spotZoneScores[z] || "Unknown";
                    return `<span class="spot-zone-label">Z${escapeHtml(z)}</span><span class="spot-badge spot-${s.toLowerCase()}">${escapeHtml(s)}</span>`;
                }).join(" ");
                html += `<td><button type="button" class="spot-cell-btn has-score" data-action="spot" data-sku="${escapeHtml(sku.name)}" title="Click to refresh">${badges}</button></td>`;
            } else if (hasSpotPrice) {
                html += `<td><button type="button" class="spot-cell-btn" data-action="spot" data-sku="${escapeHtml(sku.name)}" title="Get Spot Placement Score">Spot Score?</button></td>`;
            } else {
                html += '<td class="text-body-secondary small">\u2014</td>';
            }
        }

        // Confidence
        const conf = sku.confidence || {};
        if (conf.score != null) {
            const lbl = (conf.label || "").toLowerCase().replace(/\s+/g, "-");
            const confIcons = { high: "bi-check-circle-fill", medium: "bi-dash-circle-fill", low: "bi-exclamation-triangle-fill", "very-low": "bi-x-circle-fill" };
            const icon = confIcons[lbl] || "bi-question-circle";
            html += `<td data-sort="${conf.score}"><span class="confidence-badge confidence-${lbl}" data-bs-toggle="tooltip" data-bs-title="Deployment confidence: ${conf.score}/100 (${escapeHtml(conf.label || '')})"><i class="bi ${icon}"></i> ${conf.score}</span></td>`;
        } else {
            html += '<td data-sort="-1">\u2014</td>';
        }

        // Admission
        const adm = _admissionCache[sku.name];
        if (adm?.admissionConfidence?.score != null) {
            const admScore = adm.admissionConfidence.score;
            const admLabel = adm.admissionConfidence.label || "";
            const admClass = admScore >= 80 ? "admission-high" : admScore >= 60 ? "admission-medium" : admScore >= 40 ? "admission-low" : "admission-very-low";
            html += `<td data-sort="${admScore}"><span class="admission-badge ${admClass}" data-bs-toggle="tooltip" data-bs-title="Heuristic admission confidence estimate (not a guarantee)">${admScore} ${escapeHtml(admLabel)}</span></td>`;
        } else {
            html += '<td data-sort="-1">\u2014</td>';
        }

        // Prices
        if (showPricing) {
            const pricing = sku.pricing || {};
            html += `<td class="price-cell">${pricing.paygo != null ? formatNum(pricing.paygo, 4) : '\u2014'}</td>`;
            html += `<td class="price-cell">${pricing.spot != null ? formatNum(pricing.spot, 4) : '\u2014'}</td>`;
        }

        // Zone availability
        allLogicalZones.forEach(lz => {
            const isRestricted = sku.restrictions.includes(lz);
            const isAvailable = sku.zones.includes(lz);
            if (isRestricted) html += '<td class="zone-restricted" data-bs-toggle="tooltip" data-bs-title="Restricted: this SKU has deployment restrictions in this zone"><i class="bi bi-exclamation-triangle-fill"></i></td>';
            else if (isAvailable) html += '<td class="zone-available" data-bs-toggle="tooltip" data-bs-title="Available: this SKU can be deployed in this zone"><i class="bi bi-check-circle-fill"></i></td>';
            else html += '<td class="zone-unavailable" data-bs-toggle="tooltip" data-bs-title="Not available: this SKU is not offered in this zone"><i class="bi bi-dash-circle"></i></td>';
        });
        html += "</tr>";
    });
    html += "</tbody></table>";
    container.innerHTML = html;

    // Column type configuration for proper numeric sorting
    // Columns: SKU(0) Family(1) vCPUs(2) Mem(3) QLimit(4) QUsed(5) QRem(6) [Spot(7)] Conf(7|8) Adm(8|9) [PAYGO Spot]
    const confCol = showSpot ? 8 : 7;
    const admCol = confCol + 1;
    const colConfig = [
        { select: [2, 3, 4, 5, 6], type: "number" },   // vCPUs, Memory, Quota
        { select: confCol, type: "number" },              // Confidence (uses data-sort attr)
        { select: admCol, type: "number" },               // Admission (uses data-sort attr)
    ];
    let nextCol = admCol + 1;
    if (showPricing) {
        colConfig.push({ select: [nextCol, nextCol + 1], type: "number" });
        nextCol += 2;
    }

    // Init Simple-DataTables
    const tableEl = document.getElementById("sku-datatable");

    // Build per-column header filter config
    // Only text-filterable columns get an input; Zone columns are excluded
    const filterableCols = [];
    for (let i = 0; i <= admCol; i++) filterableCols.push(i);
    if (showPricing) { filterableCols.push(nextCol - 2, nextCol - 1); }

    _skuDataTable = new simpleDatatables.DataTable(tableEl, {
        searchable: false,
        paging: false,
        labels: {
            noRows: "No SKUs match",
            info: "{rows} SKUs",
        },
        columns: colConfig,
    });

    // Numeric column indices (for operator-aware filtering: >5, <32, 4-16, etc.)
    const numericCols = new Set([2, 3, 4, 5, 6, confCol, admCol]);
    if (showPricing) { numericCols.add(nextCol - 2); numericCols.add(nextCol - 1); }

    // Build per-column filter row in thead
    _buildColumnFilters(tableEl, filterableCols, numericCols);

    // Restore saved filter values and re-apply
    _restoreSkuFilters(tableEl);

    // Init Bootstrap tooltips on zone & confidence cells
    function _initSkuTooltips() {
        tableEl.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
            if (!bootstrap.Tooltip.getInstance(el)) {
                new bootstrap.Tooltip(el, { delay: { show: 0, hide: 100 }, placement: "top" });
            }
        });
    }
    _initSkuTooltips();

    // Re-init tooltips after sort re-renders the table
    _skuDataTable.on("datatable.sort", () => _initSkuTooltips());
}

/** Save current filter input values keyed by column header text. */
function _saveSkuFilters() {
    const tableEl = document.getElementById("sku-datatable");
    if (!tableEl) return;
    const headers = tableEl.querySelectorAll("thead tr:first-child th");
    const inputs = tableEl.querySelectorAll(".datatable-filter-row input[data-col]");
    const state = {};
    inputs.forEach(inp => {
        const val = inp.value.trim();
        if (!val) return;
        const col = parseInt(inp.dataset.col, 10);
        const hdr = headers[col]?.textContent?.trim();
        if (hdr) state[hdr] = val;
    });
    _skuFilterState = state;
}

/** Restore saved filter values after a re-render, then re-apply filtering. */
function _restoreSkuFilters(tableEl) {
    if (!Object.keys(_skuFilterState).length) return;
    const headers = tableEl.querySelectorAll("thead tr:first-child th");
    const headerMap = {};
    headers.forEach((th, idx) => { headerMap[th.textContent.trim()] = idx; });
    const filterRow = tableEl.querySelector(".datatable-filter-row");
    if (!filterRow) return;
    let restored = false;
    for (const [hdr, val] of Object.entries(_skuFilterState)) {
        const col = headerMap[hdr];
        if (col == null) continue;
        const input = filterRow.querySelector(`input[data-col="${col}"]`);
        if (input) { input.value = val; restored = true; }
    }
    if (restored) _applyColumnFilters(tableEl, filterRow);
}

/**
 * Parse a numeric filter expression.
 * Supported syntax:  >5  >=5  <32  <=32  =8  5-16 (range)  or plain number (exact).
 * Returns null if the input is not a numeric filter.
 */
function _parseNumericFilter(val) {
    const s = val.trim();
    let m;
    // Range: 4-16, 4..16, 4–16
    m = s.match(/^(\d+(?:\.\d+)?)\s*(?:[-–]|\.\.)\s*(\d+(?:\.\d+)?)$/);
    if (m) return { op: "range", lo: parseFloat(m[1]), hi: parseFloat(m[2]) };
    // Operators: >=, <=, >, <, =
    m = s.match(/^(>=?|<=?|=)\s*(\d+(?:\.\d+)?)$/);
    if (m) return { op: m[1], val: parseFloat(m[2]) };
    // Plain number → exact match
    if (/^\d+(?:\.\d+)?$/.test(s)) return { op: "=", val: parseFloat(s) };
    return null;
}

/** Test a cell value against a parsed numeric filter. */
function _matchNumericFilter(cellVal, filter) {
    const n = parseFloat(cellVal);
    if (isNaN(n)) return false;
    switch (filter.op) {
        case ">": return n > filter.val;
        case ">=": return n >= filter.val;
        case "<": return n < filter.val;
        case "<=": return n <= filter.val;
        case "=": return n === filter.val;
        case "range": return n >= filter.lo && n <= filter.hi;
        default: return false;
    }
}

/**
 * Inject a second <tr> into thead with <input> filters for specified columns.
 * Numeric columns accept operator expressions (>5, <32, 4-16, etc.).
 * Text columns use substring matching.
 */
function _buildColumnFilters(tableEl, filterableCols, numericCols) {
    const thead = tableEl.querySelector("thead");
    if (!thead) return;

    const headerCells = thead.querySelectorAll("tr:first-child th");
    const filterRow = document.createElement("tr");
    filterRow.className = "datatable-filter-row";

    headerCells.forEach((_, idx) => {
        const td = document.createElement("td");
        if (filterableCols.includes(idx)) {
            const input = document.createElement("input");
            input.type = "search";
            input.className = "datatable-column-filter";
            const isNumeric = numericCols && numericCols.has(idx);
            input.placeholder = isNumeric ? ">5, <32, 4-16\u2026" : "Filter\u2026";
            if (isNumeric) input.dataset.numeric = "1";
            input.dataset.col = idx;
            td.appendChild(input);
        }
        filterRow.appendChild(td);
    });
    thead.appendChild(filterRow);

    // Debounced column filtering via row visibility
    let _colFilterTimeout;
    filterRow.addEventListener("input", () => {
        clearTimeout(_colFilterTimeout);
        _colFilterTimeout = setTimeout(() => _applyColumnFilters(tableEl, filterRow), 200);
    });
}

function _applyColumnFilters(tableEl, filterRow) {
    const inputs = filterRow.querySelectorAll("input[data-col]");
    const filters = [];
    inputs.forEach(inp => {
        const val = inp.value.trim();
        if (!val) return;
        const col = parseInt(inp.dataset.col, 10);
        const isNumeric = inp.dataset.numeric === "1";
        if (isNumeric) {
            const nf = _parseNumericFilter(val);
            if (nf) { filters.push({ col, numeric: nf }); return; }
        }
        // Fallback: text substring match
        filters.push({ col, text: val.toLowerCase() });
    });

    const rows = tableEl.querySelectorAll("tbody tr");
    rows.forEach(row => {
        if (filters.length === 0) {
            row.style.display = "";
            return;
        }
        const cells = row.querySelectorAll("td");
        const match = filters.every(f => {
            const cell = cells[f.col];
            if (!cell) return false;
            if (f.numeric) return _matchNumericFilter(cell.textContent, f.numeric);
            return cell.textContent.toLowerCase().includes(f.text);
        });
        row.style.display = match ? "" : "none";
    });
}

// ---------------------------------------------------------------------------
// Toggle table column visibility (persisted in localStorage)
// ---------------------------------------------------------------------------
function toggleTableColumns() {
    try {
        localStorage.setItem("azm-show-prices", document.getElementById("planner-show-prices")?.checked ? "1" : "0");
        localStorage.setItem("azm-show-spot", document.getElementById("planner-show-spot")?.checked ? "1" : "0");
    } catch {}
    if (lastSkuData) renderSkuTable(lastSkuData);
}

function _restoreColumnPrefs() {
    try {
        const prices = localStorage.getItem("azm-show-prices");
        const spot = localStorage.getItem("azm-show-spot");
        if (prices !== null) {
            const el = document.getElementById("planner-show-prices");
            if (el) el.checked = prices === "1";
        }
        if (spot !== null) {
            const el = document.getElementById("planner-show-spot");
            if (el) el.checked = spot === "1";
        }
    } catch {}
}

// ---------------------------------------------------------------------------
// EXPORT: SKU → CSV
// ---------------------------------------------------------------------------
function exportSkuCSV() {
    if (!lastSkuData || lastSkuData.length === 0) return;
    const physicalZoneMap = getPlannerPhysicalZoneMap();
    const allLogicalZones = [...new Set(lastSkuData.flatMap(s => s.zones))].sort();
    const physicalZones = allLogicalZones.map(lz => physicalZoneMap[lz] || `Zone ${lz}`);
    const hasPricing = lastSkuData.some(s => s.pricing);
    const priceCurrency = lastSkuData.find(s => s.pricing)?.pricing?.currency || "USD";
    const priceHeaders = hasPricing ? [`PAYGO ${priceCurrency}/h`, `Spot ${priceCurrency}/h`] : [];
    const zoneHeaders = allLogicalZones.map((lz, i) => `Zone ${lz}\n${physicalZones[i]}`);
    const headers = ["SKU Name", "Family", "vCPUs", "Memory (GB)",
        "Quota Limit", "Quota Used", "Quota Remaining", "Spot Score",
        "Confidence Score", "Confidence Label", "Admission Score", "Admission Label", ...priceHeaders, ...zoneHeaders];
    const rows = lastSkuData.map(sku => {
        const quota = sku.quota || {};
        const adm = _admissionCache[sku.name];
        const zoneCols = allLogicalZones.map(lz => {
            if (sku.restrictions.includes(lz)) return "Restricted";
            if (sku.zones.includes(lz)) return "Available";
            return "Unavailable";
        });
        return [
            sku.name, sku.family || "", sku.capabilities.vCPUs || "", sku.capabilities.MemoryGB || "",
            quota.limit ?? "", quota.used ?? "", quota.remaining ?? "",
            Object.entries((lastSpotScores?.scores || {})[sku.name] || {}).sort(([a], [b]) => a.localeCompare(b)).map(([z, s]) => `Z${z}:${s}`).join(" ") || "",
            sku.confidence?.score ?? "", sku.confidence?.label || "",
            adm?.admissionConfidence?.score ?? "", adm?.admissionConfidence?.label || "",
            ...(hasPricing ? [sku.pricing?.paygo ?? "", sku.pricing?.spot ?? ""] : []),
            ...zoneCols
        ];
    });
    downloadCSV([headers, ...rows], `az-skus-${document.getElementById("region-select").value || "export"}.csv`);
}

// ---------------------------------------------------------------------------
// AI Chat  (floating panel, SSE streaming, tool-call display)
// ---------------------------------------------------------------------------

let _chatMessages = [];   // [{role, content}] – conversation history
let _chatStreaming = false;
let _chatInputHistory = [];  // user-sent messages (strings)
let _chatHistoryIdx = -1;    // -1 = composing new message
let _chatDraft = "";         // saved draft while navigating history
let _chatPersist = false;    // whether to save chat history to localStorage
let _chatPinned = false;     // whether chat is pinned to right side
let _chatMode = "discussion"; // "discussion" | "planner"

const _CHAT_STORAGE_KEY = "azm-chat-history";
const _CHAT_PERSIST_KEY = "azm-chat-persist";
const _CHAT_INPUT_HIST_KEY = "azm-chat-input-history";
const _CHAT_MODE_KEY = "azm-chat-mode";

// Per-mode conversation state: { discussion: {messages, inputHistory}, planner: {messages, inputHistory} }
const _chatModeState = {
    discussion: { messages: [], inputHistory: [] },
    planner:   { messages: [], inputHistory: [] },
};


function toggleChatPanel() {
    const panel = document.getElementById("chat-panel");
    if (!panel) return;
    panel.classList.toggle("d-none");
    if (!panel.classList.contains("d-none")) {
        document.getElementById("chat-input")?.focus();
    }
    // If pinned and closing, unpin
    if (panel.classList.contains("d-none") && _chatPinned) {
        _setChatPinned(false);
    }
}

// ---------------------------------------------------------------------------
// Chat mode switching  (Discussion ↔ Planner)
// ---------------------------------------------------------------------------

const _CHAT_WELCOME = {
    discussion: `👋 Hi! I'm your Azure Scout assistant. Ask me about Azure regions, SKU availability, pricing, zone mappings, and more. I can query live Azure data for you.
- [[Show me available VM sizes in this region]]
- [[Compare zone mappings across my subscriptions]]
- [[What are the cheapest spot VMs with 4 vCPUs?]]
- [[List all regions with availability zones]]`,
    planner: `🗺️ Welcome to the **VM Deployment Planner**! I can help you with one of these:
- [[Find the best region for my VM workload]]
- [[Find the right VM size in a specific region]]
- [[Pick the best availability zone for a VM SKU]]`,
};

function switchChatMode(mode) {
    if (mode === _chatMode || _chatStreaming) return;

    // Save current mode's conversation state
    _chatModeState[_chatMode].messages = [..._chatMessages];
    _chatModeState[_chatMode].inputHistory = [..._chatInputHistory];

    // Switch
    _chatMode = mode;
    try { localStorage.setItem(_CHAT_MODE_KEY, mode); } catch {}

    // Update toggle UI
    document.querySelectorAll("#chat-mode-toggle button").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.mode === mode);
    });

    // Restore target mode's conversation (or start fresh)
    _chatMessages = [...(_chatModeState[mode].messages || [])];
    _chatInputHistory = [...(_chatModeState[mode].inputHistory || [])];
    _chatHistoryIdx = -1;
    _chatDraft = "";

    // Rebuild chat UI
    const container = document.getElementById("chat-messages");
    if (!container) return;
    container.innerHTML = "";

    // Show welcome message
    const welcome = document.createElement("div");
    welcome.className = "chat-message assistant";
    welcome.innerHTML = `<div class="chat-bubble">${_renderMarkdown(_CHAT_WELCOME[mode])}</div>`;
    container.appendChild(welcome);

    // Replay stored messages
    for (const msg of _chatMessages) {
        _appendChatBubble(msg.role, msg.content);
    }

    // Update input placeholder
    const input = document.getElementById("chat-input");
    if (input) {
        input.placeholder = mode === "planner"
            ? "Describe your deployment needs…"
            : "Ask about Azure SKUs, zones, pricing…";
        input.focus();
    }

    _saveChatHistory();
}

function toggleChatPin() {
    _setChatPinned(!_chatPinned);
}

function _setChatPinned(pinned) {
    _chatPinned = pinned;
    document.body.classList.toggle("chat-pinned", _chatPinned);
    const btn = document.getElementById("chat-pin-btn");
    if (btn) {
        btn.classList.toggle("active", _chatPinned);
        btn.title = _chatPinned ? "Unpin chat" : "Pin chat to side";
        btn.dataset.tooltip = _chatPinned ? "Unpin" : "Pin";
        const icon = btn.querySelector("i");
        if (icon) {
            icon.className = _chatPinned ? "bi bi-pin-fill" : "bi bi-pin-angle";
        }
    }
    // Adjust textarea rows for pinned mode
    const ta = document.getElementById("chat-input");
    if (ta) ta.rows = _chatPinned ? 6 : 1;
    // Show panel when pinning
    if (_chatPinned) {
        const panel = document.getElementById("chat-panel");
        if (panel) {
            panel.classList.remove("d-none");
            panel.style.animation = "none";
        }
        // Sync pinned width to content margin
        _syncPinnedWidth();
    }
}

function _syncPinnedWidth() {
    if (!_chatPinned) return;
    const panel = document.getElementById("chat-panel");
    if (!panel) return;
    const w = panel.getBoundingClientRect().width;
    document.documentElement.style.setProperty("--chat-pinned-width", w + "px");
}


function toggleChatPersist() {
    _chatPersist = !_chatPersist;
    const btn = document.getElementById("chat-persist-btn");
    if (btn) btn.classList.toggle("active", _chatPersist);
    try {
        if (_chatPersist) {
            localStorage.setItem(_CHAT_PERSIST_KEY, "1");
            _saveChatHistory();
        } else {
            localStorage.removeItem(_CHAT_PERSIST_KEY);
            localStorage.removeItem(_CHAT_STORAGE_KEY);
            localStorage.removeItem(_CHAT_INPUT_HIST_KEY);
        }
    } catch {}
}

function _saveChatHistory() {
    if (!_chatPersist) return;
    try {
        // Save per-mode state
        _chatModeState[_chatMode].messages = [..._chatMessages];
        _chatModeState[_chatMode].inputHistory = [..._chatInputHistory];
        localStorage.setItem(_CHAT_STORAGE_KEY, JSON.stringify(_chatModeState));
        localStorage.setItem(_CHAT_INPUT_HIST_KEY, JSON.stringify(_chatInputHistory));
        localStorage.setItem(_CHAT_MODE_KEY, _chatMode);
    } catch {}
}

function _restoreChatHistory() {
    try {
        _chatPersist = localStorage.getItem(_CHAT_PERSIST_KEY) === "1";
        const btn = document.getElementById("chat-persist-btn");
        if (btn) btn.classList.toggle("active", _chatPersist);

        // Restore saved mode
        const savedMode = localStorage.getItem(_CHAT_MODE_KEY);
        if (savedMode && (savedMode === "discussion" || savedMode === "planner")) {
            _chatMode = savedMode;
            document.querySelectorAll("#chat-mode-toggle button").forEach(b => {
                b.classList.toggle("active", b.dataset.mode === _chatMode);
            });
        }

        let hasHistory = false;

        if (_chatPersist) {
            const saved = localStorage.getItem(_CHAT_STORAGE_KEY);
            if (saved) {
                const state = JSON.parse(saved);
                // Support both old format (array) and new format (object with per-mode state)
                if (Array.isArray(state)) {
                    // Legacy: migrate old format into discussion mode
                    _chatModeState.discussion.messages = state;
                } else if (state && typeof state === "object") {
                    // Support old "assistant" key for backward compat
                    const disc = state.discussion || state.assistant;
                    if (disc?.messages) _chatModeState.discussion = disc;
                    if (state.planner?.messages) _chatModeState.planner = state.planner;
                }

                // Load current mode's state
                _chatMessages = [...(_chatModeState[_chatMode].messages || [])];
                _chatInputHistory = [...(_chatModeState[_chatMode].inputHistory || [])];
                hasHistory = _chatMessages.length > 0;
            }
        }

        // Build the chat UI in one pass (no flash)
        const container = document.getElementById("chat-messages");
        if (!container) return;

        const welcome = _renderMarkdown(_CHAT_WELCOME[_chatMode]);
        container.innerHTML = `<div class="chat-message assistant"><div class="chat-bubble">${welcome}</div></div>`;

        if (hasHistory) {
            // Add restored-session notice then replay messages
            const notice = document.createElement("div");
            notice.className = "chat-message assistant";
            notice.innerHTML = `<div class="chat-bubble"><em>Restored ${_chatMessages.filter(m => m.role === "user").length} message(s) from previous session.</em></div>`;
            container.appendChild(notice);
            for (const msg of _chatMessages) {
                _appendChatBubble(msg.role, msg.content);
            }
        }

        // Update placeholder
        const input = document.getElementById("chat-input");
        if (input) {
            input.placeholder = _chatMode === "planner"
                ? "Describe your deployment needs…"
                : "Ask about Azure SKUs, zones, pricing…";
        }
    } catch {}
}

function clearChat() {
    _chatMessages = [];
    _chatInputHistory = [];
    _chatHistoryIdx = -1;
    _chatDraft = "";
    _chatModeState[_chatMode].messages = [];
    _chatModeState[_chatMode].inputHistory = [];
    _saveChatHistory();
    const container = document.getElementById("chat-messages");
    if (!container) return;
    const welcome = _renderMarkdown(_CHAT_WELCOME[_chatMode]);
    container.innerHTML = `<div class="chat-message assistant"><div class="chat-bubble">${welcome}</div></div>`;
}

function handleChatKeydown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendChatMessage();
    } else if (e.key === "ArrowUp" && !e.shiftKey) {
        _navigateChatHistory(-1, e);
    } else if (e.key === "ArrowDown" && !e.shiftKey) {
        _navigateChatHistory(1, e);
    }
}

async function sendChatMessage() {
    if (_chatStreaming) return;
    const input = document.getElementById("chat-input");
    const text = input?.value?.trim();
    if (!text) return;
    input.value = "";
    _autoResizeChatInput();

    // Track input history
    _chatInputHistory.push(text);
    _chatHistoryIdx = -1;
    _chatDraft = "";

    // Add user message to UI and history
    _appendChatBubble("user", text);
    _chatMessages.push({ role: "user", content: text });
    _saveChatHistory();

    // Create assistant bubble with thinking indicator
    const assistantBubble = _appendChatBubble("assistant", "");
    assistantBubble.innerHTML = '<span class="chat-thinking"><span class="dot"></span><span class="dot"></span><span class="dot"></span></span>';
    _scrollChatBottom();
    const sendBtn = document.getElementById("chat-send-btn");
    _chatStreaming = true;
    if (sendBtn) sendBtn.disabled = true;

    try {
        const tenantId = document.getElementById("tenant-select")?.value || "";
        const regionId = document.getElementById("region-select")?.value || "";
        const resp = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                messages: _chatMessages,
                mode: _chatMode,
                tenant_id: tenantId || undefined,
                region: regionId || undefined,
                subscription_id: plannerSubscriptionId || undefined,
            }),
        });

        if (!resp.ok) {
            const err = await resp.text();
            assistantBubble.innerHTML = `<span class="text-danger">Error: ${escapeHtml(err)}</span>`
                + '<br><button class="chat-choice-chip mt-2" onclick="_retryChatMessage(this)">Retry</button>';
            _chatStreaming = false;
            if (sendBtn) sendBtn.disabled = false;
            return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let fullContent = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            // Process complete SSE lines
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                let payload;
                try { payload = JSON.parse(line.slice(6)); } catch { continue; }

                if (payload.type === "delta") {
                    fullContent += payload.content;
                    assistantBubble.innerHTML = _renderMarkdown(fullContent);
                    assistantBubble.closest(".chat-message")?.classList.remove("is-thinking");
                    _scrollChatBottom();
                } else if (payload.type === "tool_call") {
                    _appendToolStatus(assistantBubble, payload.name, "calling", payload.arguments);
                    _scrollChatBottom();
                } else if (payload.type === "tool_result") {
                    _updateToolStatus(assistantBubble, payload.name, "done");
                    _scrollChatBottom();
                } else if (payload.type === "ui_action") {
                    _handleChatUiAction(payload);
                } else if (payload.type === "status") {
                    // Transient status message (e.g. rate-limit retry)
                    assistantBubble.innerHTML = `<span class="text-muted"><em>${escapeHtml(payload.content)}</em></span>`;
                    assistantBubble.closest(".chat-message")?.classList.remove("is-thinking");
                    _scrollChatBottom();
                } else if (payload.type === "error") {
                    fullContent = ""; // Don't store error as assistant message
                    assistantBubble.innerHTML = `<span class="text-danger"><strong>Error:</strong> ${escapeHtml(payload.content)}</span>`
                        + '<br><button class="chat-choice-chip mt-2" onclick="_retryChatMessage(this)">Retry</button>';
                } else if (payload.type === "done") {
                    // Stream finished
                }
            }
        }

        if (fullContent) {
            _chatMessages.push({ role: "assistant", content: fullContent });
            _saveChatHistory();
        }
    } catch (err) {
        assistantBubble.innerHTML = `<span class="text-danger">Connection error: ${escapeHtml(err.message)}</span>`
            + '<br><button class="chat-choice-chip mt-2" onclick="_retryChatMessage(this)">Retry</button>';
    } finally {
        _chatStreaming = false;
        if (sendBtn) sendBtn.disabled = false;
        _scrollChatBottom();
    }
}

function _appendChatBubble(role, content) {
    const container = document.getElementById("chat-messages");
    const msg = document.createElement("div");
    msg.className = `chat-message ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    if (content) bubble.innerHTML = role === "user" ? escapeHtml(content) : _renderMarkdown(content);
    msg.appendChild(bubble);
    container.appendChild(msg);
    _scrollChatBottom();
    return bubble;
}

/** Retry the last failed message — remove the error bubble and resend. */
function _retryChatMessage(btn) {
    if (_chatStreaming) return;
    // Remove the error assistant bubble
    const msgDiv = btn.closest(".chat-message");
    if (msgDiv) msgDiv.remove();
    // Pop the last assistant message if it was stored (shouldn't be on error, but be safe)
    if (_chatMessages.length && _chatMessages[_chatMessages.length - 1].role === "assistant") {
        _chatMessages.pop();
    }
    // Re-send: the last user message is still in _chatMessages
    if (!_chatMessages.length || _chatMessages[_chatMessages.length - 1].role !== "user") return;
    const lastUserMsg = _chatMessages[_chatMessages.length - 1].content;
    // Remove it so sendChatMessage re-adds it
    _chatMessages.pop();
    const input = document.getElementById("chat-input");
    if (input) input.value = lastUserMsg;
    sendChatMessage();
}

/** Handle click on a [[choice]] chip — send the choice text as a user message. */
function _onChatChoiceClick(btn) {
    if (_chatStreaming) return;
    const text = btn.textContent.trim();
    if (!text) return;
    // Dim all choice chips in the same bubble to show selection was made
    const bubble = btn.closest(".chat-bubble");
    if (bubble) {
        bubble.querySelectorAll(".chat-choice-chip").forEach(c => c.classList.add("used"));
    }
    // Populate input and send
    const input = document.getElementById("chat-input");
    if (input) input.value = text;
    sendChatMessage();
}

/** Navigate chat input history with Up/Down arrows (like a terminal). */
function _navigateChatHistory(direction, e) {
    if (!_chatInputHistory.length) return;
    const input = document.getElementById("chat-input");
    if (!input) return;

    // Only navigate when cursor is at the very start (Up) or very end (Down)
    if (direction === -1 && input.selectionStart !== 0) return;
    if (direction === 1 && input.selectionStart !== input.value.length) return;

    e.preventDefault();

    if (direction === -1) {
        // Going backwards (older)
        if (_chatHistoryIdx === -1) {
            // Entering history — save current draft
            _chatDraft = input.value;
            _chatHistoryIdx = _chatInputHistory.length - 1;
        } else if (_chatHistoryIdx > 0) {
            _chatHistoryIdx--;
        } else {
            return; // already at oldest
        }
        input.value = _chatInputHistory[_chatHistoryIdx];
    } else {
        // Going forwards (newer)
        if (_chatHistoryIdx === -1) return; // not in history
        if (_chatHistoryIdx >= _chatInputHistory.length - 1) {
            // Back to draft
            _chatHistoryIdx = -1;
            input.value = _chatDraft;
        } else {
            _chatHistoryIdx++;
            input.value = _chatInputHistory[_chatHistoryIdx];
        }
    }
    _autoResizeChatInput();
    // Move cursor to end
    input.setSelectionRange(input.value.length, input.value.length);
}

function _appendToolStatus(bubble, toolName, status, argsJson) {
    let toolsDiv = bubble.querySelector(".chat-tool-calls");
    if (!toolsDiv) {
        toolsDiv = document.createElement("div");
        toolsDiv.className = "chat-tool-calls";
        bubble.insertBefore(toolsDiv, bubble.firstChild);
    }
    const badge = document.createElement("span");
    badge.className = "chat-tool-badge calling";
    badge.dataset.tool = toolName;
    const friendlyName = toolName.replace(/_/g, " ");
    badge.innerHTML = `<i class="bi bi-gear-fill spin"></i> ${escapeHtml(friendlyName)}`;
    // Store arguments for tooltip
    if (argsJson) {
        try {
            const parsed = typeof argsJson === "string" ? JSON.parse(argsJson) : argsJson;
            const lines = Object.entries(parsed)
                .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
                .join("\n");
            badge.title = lines;
            badge.style.cursor = "help";
        } catch { /* ignore parse errors */ }
    }
    toolsDiv.appendChild(badge);
}

function _updateToolStatus(bubble, toolName, status) {
    const badge = bubble.querySelector(`.chat-tool-badge[data-tool="${toolName}"]`);
    if (badge) {
        badge.className = `chat-tool-badge ${status}`;
        const friendlyName = toolName.replace(/_/g, " ");
        badge.innerHTML = `<i class="bi bi-check-circle-fill"></i> ${escapeHtml(friendlyName)}`;
    }
}

function _scrollChatBottom() {
    const container = document.getElementById("chat-messages");
    if (container) container.scrollTop = container.scrollHeight;
}

/** Handle UI actions emitted by the chat backend (e.g. tenant/region switching). */
function _handleChatUiAction(payload) {
    if (payload.action === "switch_tenant") {
        const select = document.getElementById("tenant-select");
        if (!select) return;
        const targetId = payload.tenant_id;
        const option = Array.from(select.options).find(o => o.value === targetId);
        if (option && !option.disabled) {
            select.value = targetId;
            onTenantChange();
        }
    } else if (payload.action === "switch_region") {
        const regionName = payload.region;
        // Check the region exists in the loaded regions list
        const r = regions.find(r => r.name === regionName);
        if (r) {
            selectRegion(regionName);
        }
    }
}

/** Auto-resize textarea to fit content (up to 4 lines). */
function _autoResizeChatInput() {
    const el = document.getElementById("chat-input");
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 96) + "px";
}

// Auto-resize on input
document.addEventListener("input", (e) => {
    if (e.target.id === "chat-input") _autoResizeChatInput();
});

/** Minimal Markdown → HTML renderer for chat bubbles. */
function _renderMarkdown(md) {
    let html = escapeHtml(md);

    // Code blocks: ```lang\n...\n```
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) =>
        `<pre><code>${code}</code></pre>`
    );
    // Inline code
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    // Clickable choice chips: [[choice text]]
    html = html.replace(/\[\[(.+?)\]\]/g,
        '<button class="chat-choice-chip" onclick="_onChatChoiceClick(this)">$1</button>'
    );
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // Italic
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    // Headers (## and ###)
    html = html.replace(/^### (.+)$/gm, '<h6 class="mt-2 mb-1">$1</h6>');
    html = html.replace(/^## (.+)$/gm, '<h5 class="mt-2 mb-1">$1</h5>');
    // Horizontal rule
    html = html.replace(/^---$/gm, "<hr>");

    // Tables: detect lines with |
    html = _renderMarkdownTables(html);

    // Unordered lists
    html = html.replace(/^[-*] (.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>");
    // Collapse nested <ul>
    html = html.replace(/<\/ul>\s*<ul>/g, "");
    // Convert lists whose items are ALL choice chips into compact chip groups
    html = html.replace(/<ul>([\s\S]*?)<\/ul>/g, (_m, inner) => {
        const items = inner.match(/<li>([\s\S]*?)<\/li>/g);
        if (!items) return `<ul>${inner}</ul>`;
        const allChips = items.every(li => {
            const content = li.replace(/<\/?li>/g, "").trim();
            return /^(<button class="chat-choice-chip"[^>]*>.*?<\/button>\s*)+$/.test(content);
        });
        if (allChips) {
            const chips = items.map(li => li.replace(/<\/?li>/g, "").trim()).join("");
            if (items.length > 10) {
                return `<div class="chat-suggestions">${chips}</div>`;
            }
            return `<ul class="chat-choices-list">${inner}</ul>`;
        }
        return `<ul>${inner}</ul>`;
    });

    // Line breaks (but not inside pre/code)
    html = html.replace(/\n/g, "<br>");
    // Clean up excessive <br> around block elements
    html = html.replace(/<br>\s*(<h[56]|<pre|<ul|<hr|<table)/g, "$1");
    html = html.replace(/(<\/h[56]>|<\/pre>|<\/ul>|<hr>|<\/table>)\s*<br>/g, "$1");

    return html;
}

function _renderMarkdownTables(html) {
    const lines = html.split("\n");
    let result = [];
    let inTable = false;
    let tableRows = [];

    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
            // Skip separator rows (|---|---|, |:---:|, | --- | --- |, etc.)
            const inner = trimmed.slice(1, -1);
            if (inner.split("|").every(c => /^[\s\-:]+$/.test(c))) {
                if (!inTable) inTable = true; // still mark table as started
                continue;
            }
            const cells = trimmed.slice(1, -1).split("|").map(c => c.trim());
            if (!inTable) {
                inTable = true;
                tableRows = [];
                tableRows.push(`<tr>${cells.map(c => `<th>${c}</th>`).join("")}</tr>`);
            } else {
                tableRows.push(`<tr>${cells.map(c => `<td>${c}</td>`).join("")}</tr>`);
            }
        } else {
            if (inTable) {
                result.push(`<table class="table table-sm table-bordered chat-table"><thead>${tableRows[0]}</thead><tbody>${tableRows.slice(1).join("")}</tbody></table>`);
                inTable = false;
                tableRows = [];
            }
            result.push(line);
        }
    }
    if (inTable) {
        result.push(`<table class="table table-sm table-bordered chat-table"><thead>${tableRows[0]}</thead><tbody>${tableRows.slice(1).join("")}</tbody></table>`);
    }
    return result.join("\n");
}

// =========================================================================
// STRATEGY ADVISOR TAB
// =========================================================================
let _stratSubscriptionId = null;

function initStratSubCombobox() {
    const searchInput = document.getElementById("strat-sub-search");
    const dropdown = document.getElementById("strat-sub-dropdown");
    if (!searchInput || !dropdown) return;

    searchInput.addEventListener("focus", () => {
        searchInput.select();
        renderStratSubDropdown(searchInput.value.includes("(") ? "" : searchInput.value);
        dropdown.classList.add("show");
    });
    searchInput.addEventListener("input", () => {
        document.getElementById("strat-sub-select").value = "";
        _stratSubscriptionId = null;
        renderStratSubDropdown(searchInput.value);
        dropdown.classList.add("show");
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
            if (active) _selectStratSub(active.dataset.value);
            else if (items.length === 1) _selectStratSub(items[0].dataset.value);
        } else if (e.key === "Escape") {
            dropdown.classList.remove("show");
            searchInput.blur();
        }
    });
    document.addEventListener("click", (e) => {
        if (!e.target.closest("#strat-sub-combobox")) dropdown.classList.remove("show");
    });
}

function renderStratSubDropdown(filter) {
    const dropdown = document.getElementById("strat-sub-dropdown");
    if (!dropdown) return;
    const lc = (filter || "").toLowerCase();
    const matches = lc
        ? subscriptions.filter(s => s.name.toLowerCase().includes(lc) || s.id.toLowerCase().includes(lc))
        : subscriptions;
    dropdown.innerHTML = matches.map(s =>
        `<li class="dropdown-item" data-value="${s.id}">${escapeHtml(s.name)} <span class="region-name">(${s.id.slice(0, 8)}\u2026)</span></li>`
    ).join("");
    dropdown.querySelectorAll("li").forEach(li => {
        li.addEventListener("click", () => _selectStratSub(li.dataset.value));
    });
    const searchInput = document.getElementById("strat-sub-search");
    if (subscriptions.length > 0 && searchInput) {
        searchInput.placeholder = "Type to search subscriptions\u2026";
        searchInput.disabled = false;
    }
}

function _selectStratSub(id) {
    const s = subscriptions.find(s => s.id === id);
    if (!s) return;
    _stratSubscriptionId = id;
    document.getElementById("strat-sub-select").value = id;
    document.getElementById("strat-sub-search").value = s.name;
    document.getElementById("strat-sub-dropdown").classList.remove("show");
}

async function submitStrategy(e) {
    e.preventDefault();

    const subId = _stratSubscriptionId;
    if (!subId) { showError("strategy-error", "Please select a subscription."); return; }

    hideError("strategy-error");
    document.getElementById("strategy-results").classList.add("d-none");
    document.getElementById("strategy-loading").classList.remove("d-none");
    document.getElementById("strat-submit-btn").disabled = true;

    const body = {
        workloadName: document.getElementById("strat-workload-name").value.trim(),
        subscriptionId: subId,
        tenantId: document.getElementById("tenant-select")?.value || undefined,
        scale: {
            sku: document.getElementById("strat-sku").value.trim() || undefined,
            instanceCount: parseInt(document.getElementById("strat-instances").value, 10) || 1,
            gpuCountTotal: parseInt(document.getElementById("strat-gpu").value, 10) || undefined,
        },
        constraints: {
            dataResidency: document.getElementById("strat-residency").value || undefined,
            requireZonal: document.getElementById("strat-require-zonal").checked,
            maxInterRegionRttMs: parseInt(document.getElementById("strat-max-rtt").value, 10) || undefined,
        },
        usage: {
            statefulness: document.getElementById("strat-statefulness").value,
            crossRegionTraffic: document.getElementById("strat-cross-traffic").value,
            latencySensitivity: document.getElementById("strat-latency-sens").value,
        },
        data: {},
        timing: {
            deploymentUrgency: document.getElementById("strat-urgency").value,
        },
        pricing: {
            currencyCode: document.getElementById("strat-currency").value,
            preferSpot: document.getElementById("strat-prefer-spot").checked,
            maxHourlyBudget: parseFloat(document.getElementById("strat-budget").value) || undefined,
        },
    };

    try {
        const result = await apiPost("/api/capacity-strategy", body);
        renderStrategyResults(result);
    } catch (err) {
        showError("strategy-error", "Strategy computation failed: " + err.message);
    } finally {
        document.getElementById("strategy-loading").classList.add("d-none");
        document.getElementById("strat-submit-btn").disabled = false;
    }
}

function renderStrategyResults(data) {
    const container = document.getElementById("strategy-results");
    container.classList.remove("d-none");

    // Summary cards
    const summary = data.summary || {};
    const cards = document.getElementById("strategy-summary-cards");
    const confLbl = (summary.overallConfidenceLabel || "unknown").toLowerCase().replace(/\s+/g, "-");
    const stratLabel = (summary.strategy || "").replace(/_/g, " ");
    const costStr = summary.estimatedHourlyCost != null
        ? `${formatNum(summary.estimatedHourlyCost, 2)} ${escapeHtml(summary.currency || "USD")}/h`
        : "\u2014";
    cards.innerHTML = `
        <div class="col-md-3"><div class="card text-center p-3">
            <div class="text-body-secondary small">Strategy</div>
            <div class="fw-bold text-capitalize">${escapeHtml(stratLabel)}</div>
        </div></div>
        <div class="col-md-3"><div class="card text-center p-3">
            <div class="text-body-secondary small">Regions</div>
            <div class="fw-bold">${summary.regionCount ?? "\u2014"}</div>
        </div></div>
        <div class="col-md-3"><div class="card text-center p-3">
            <div class="text-body-secondary small">Instances</div>
            <div class="fw-bold">${summary.totalInstances ?? "\u2014"}</div>
        </div></div>
        <div class="col-md-3"><div class="card text-center p-3">
            <div class="text-body-secondary small">Confidence</div>
            <div><span class="confidence-badge confidence-${confLbl}">${summary.overallConfidence ?? "\u2014"} ${escapeHtml(summary.overallConfidenceLabel || "")}</span></div>
        </div></div>
    `;

    // Business view
    const biz = data.businessView || {};
    const bizEl = document.getElementById("strategy-business");
    let bizHtml = `<p class="fw-bold">${escapeHtml(biz.keyMessage || "")}</p>`;
    if (biz.justification?.length) {
        bizHtml += "<h6>Justification</h6><ul>" + biz.justification.map(j => `<li>${escapeHtml(j)}</li>`).join("") + "</ul>";
    }
    if (biz.risks?.length) {
        bizHtml += '<h6>Risks</h6><ul class="text-warning">' + biz.risks.map(r => `<li>${escapeHtml(r)}</li>`).join("") + "</ul>";
    }
    if (biz.mitigations?.length) {
        bizHtml += '<h6>Mitigations</h6><ul class="text-success">' + biz.mitigations.map(m => `<li>${escapeHtml(m)}</li>`).join("") + "</ul>";
    }
    bizHtml += `<p class="text-body-secondary small mt-2">Estimated cost: ${costStr}</p>`;
    bizEl.innerHTML = bizHtml;

    // Technical view
    const tech = data.technicalView || {};
    const techEl = document.getElementById("strategy-technical");
    let techHtml = "";

    // Allocations table
    if (tech.allocations?.length) {
        techHtml += '<h6>Region Allocations</h6><div class="table-responsive"><table class="table table-sm table-hover"><thead><tr>';
        techHtml += "<th>Region</th><th>Role</th><th>SKU</th><th>Instances</th><th>Zones</th><th>Quota Rem.</th><th>Spot</th><th>Confidence</th><th>RTT (ms)</th><th>PAYGO/h</th><th>Spot/h</th>";
        techHtml += "</tr></thead><tbody>";
        tech.allocations.forEach(a => {
            const aConfLbl = (a.confidenceLabel || "").toLowerCase().replace(/\s+/g, "-");
            techHtml += "<tr>";
            techHtml += `<td>${escapeHtml(a.region)}</td>`;
            techHtml += `<td><span class="badge bg-secondary">${escapeHtml(a.role)}</span></td>`;
            techHtml += `<td>${escapeHtml(a.sku)}</td>`;
            techHtml += `<td>${a.instanceCount}</td>`;
            techHtml += `<td>${a.zones?.length ? a.zones.join(", ") : "\u2014"}</td>`;
            techHtml += `<td>${a.quotaRemaining ?? "\u2014"}</td>`;
            techHtml += `<td>${a.spotScore ? `<span class="spot-badge spot-${a.spotScore.toLowerCase()}">${escapeHtml(a.spotScore)}</span>` : "\u2014"}</td>`;
            techHtml += `<td>${a.confidenceScore != null ? `<span class="confidence-badge confidence-${aConfLbl}">${a.confidenceScore}</span>` : "\u2014"}</td>`;
            techHtml += `<td>${a.rttFromPrimaryMs ?? "\u2014"}</td>`;
            techHtml += `<td class="price-cell">${a.paygoPerHour != null ? formatNum(a.paygoPerHour, 4) : "\u2014"}</td>`;
            techHtml += `<td class="price-cell">${a.spotPerHour != null ? formatNum(a.spotPerHour, 4) : "\u2014"}</td>`;
            techHtml += "</tr>";
        });
        techHtml += "</tbody></table></div>";
    }

    // Latency matrix
    if (tech.latencyMatrix && Object.keys(tech.latencyMatrix).length > 1) {
        const regions = Object.keys(tech.latencyMatrix).sort();
        techHtml += '<h6 class="mt-3">Inter-region Latency (ms)</h6><div class="table-responsive"><table class="table table-sm table-bordered"><thead><tr><th></th>';
        regions.forEach(r => { techHtml += `<th>${escapeHtml(r)}</th>`; });
        techHtml += "</tr></thead><tbody>";
        regions.forEach(src => {
            techHtml += `<tr><td class="fw-bold">${escapeHtml(src)}</td>`;
            regions.forEach(dst => {
                const v = tech.latencyMatrix[src]?.[dst];
                techHtml += `<td class="text-center">${v != null ? v : "\u2014"}</td>`;
            });
            techHtml += "</tr>";
        });
        techHtml += "</tbody></table></div>";
    }

    if (tech.evaluatedAt) {
        techHtml += `<p class="text-body-secondary small">Evaluated at: ${escapeHtml(tech.evaluatedAt)}</p>`;
    }
    techEl.innerHTML = techHtml || '<p class="text-body-secondary">No technical details available.</p>';

    // Warnings
    const warnEl = document.getElementById("strategy-warnings");
    const allWarnings = [...(data.warnings || []), ...(data.missingInputs || [])];
    if (allWarnings.length) {
        warnEl.innerHTML = allWarnings.map(w =>
            `<div class="alert alert-warning alert-sm py-1 px-2 mb-1"><i class="bi bi-exclamation-triangle"></i> ${escapeHtml(w)}</div>`
        ).join("");
    } else {
        warnEl.innerHTML = "";
    }

    // Errors
    if (data.errors?.length) {
        showError("strategy-error", data.errors.join("; "));
    }
}

// ---------------------------------------------------------------------------
// Chat panel resize  (drag top-left corner handle)
// ---------------------------------------------------------------------------
(function initChatResize() {
    const handle = document.getElementById("chat-resize-handle");
    if (!handle) return;

    let startX, startY, startW, startH;

    handle.addEventListener("mousedown", onStart);
    handle.addEventListener("touchstart", onStart, { passive: false });

    function onStart(e) {
        e.preventDefault();
        const panel = document.getElementById("chat-panel");
        if (!panel) return;
        const rect = panel.getBoundingClientRect();
        startW = rect.width;
        startH = rect.height;
        const pt = e.touches ? e.touches[0] : e;
        startX = pt.clientX;
        startY = pt.clientY;

        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onEnd);
        document.addEventListener("touchmove", onMove, { passive: false });
        document.addEventListener("touchend", onEnd);
        document.body.style.userSelect = "none";
        panel.style.animation = "none";
    }

    function onMove(e) {
        e.preventDefault();
        const panel = document.getElementById("chat-panel");
        if (!panel) return;
        const pt = e.touches ? e.touches[0] : e;

        if (_chatPinned) {
            // Pinned: resize handle is on the left edge, drag horizontally only
            const dx = startX - pt.clientX; // dragging left → wider
            const newW = Math.max(300, Math.min(startW + dx, window.innerWidth * 0.5));
            panel.style.width = newW + "px";
            _syncPinnedWidth();
        } else {
            // Floating: resize from top-left corner
            const dx = startX - pt.clientX; // dragging left → wider
            const dy = startY - pt.clientY; // dragging up → taller
            const newW = Math.max(300, Math.min(startW + dx, window.innerWidth - 32));
            const newH = Math.max(280, Math.min(startH + dy, window.innerHeight - 112));
            panel.style.width = newW + "px";
            panel.style.height = newH + "px";
        }
    }

    function onEnd() {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onEnd);
        document.removeEventListener("touchmove", onMove);
        document.removeEventListener("touchend", onEnd);
        document.body.style.userSelect = "";
        // Persist size
        const panel = document.getElementById("chat-panel");
        if (panel) {
            try {
                localStorage.setItem("azm-chat-w", panel.style.width);
                localStorage.setItem("azm-chat-h", panel.style.height);
                if (_chatPinned) _syncPinnedWidth();
            } catch {}
        }
    }

    // Restore saved size on load
    try {
        const w = localStorage.getItem("azm-chat-w");
        const h = localStorage.getItem("azm-chat-h");
        const panel = document.getElementById("chat-panel");
        if (panel) {
            if (w) panel.style.width = w;
            if (h) panel.style.height = h;
        }
    } catch {}
})();
