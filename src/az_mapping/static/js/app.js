/* ===================================================================
   Azure AZ Mapping Viewer – Frontend Logic  (Bootstrap 5 rewrite)
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

// ---------------------------------------------------------------------------
// Deployment Confidence Score – client-side recomputation
// ---------------------------------------------------------------------------
const _CONF_WEIGHTS = { quota: 0.25, spot: 0.35, zones: 0.15, restrictions: 0.15, pricePressure: 0.10 };
const _CONF_LABELS = [[80, "High"], [60, "Medium"], [40, "Low"], [0, "Very Low"]];

function _bestSpotLabel(zoneScores) {
    const order = { high: 3, medium: 2, low: 1 };
    let best = null;
    for (const s of Object.values(zoneScores)) {
        const rank = order[s.toLowerCase()] || 0;
        if (rank > (order[(best || "").toLowerCase()] || 0)) best = s;
    }
    return best || null;
}

function recomputeConfidence(sku) {
    const caps = sku.capabilities || {};
    const quota = sku.quota || {};
    const pricing = sku.pricing || {};
    const vcpus = parseInt(caps.vCPUs, 10) || 0;
    const remaining = quota.remaining;
    const zones = sku.zones || [];
    const restrictions = sku.restrictions || [];
    const zoneScores = (lastSpotScores?.scores || {})[sku.name] || {};
    const spotLabel = _bestSpotLabel(zoneScores);

    const signals = {};
    if (remaining != null) {
        if (remaining <= 0) signals.quota = 0;
        else signals.quota = Math.min((remaining / Math.max(vcpus, 1)) / 10, 1) * 100;
    }
    const spotMap = { high: 100, medium: 60, low: 25 };
    if (spotLabel && spotMap[spotLabel.toLowerCase()] != null) {
        signals.spot = spotMap[spotLabel.toLowerCase()];
    }
    signals.zones = Math.min(zones.length / 3, 1) * 100;
    signals.restrictions = restrictions.length > 0 ? 0 : 100;
    if (pricing.paygo != null && pricing.spot != null && pricing.paygo > 0) {
        const ratio = pricing.spot / pricing.paygo;
        signals.pricePressure = Math.max(0, Math.min(1, (0.8 - ratio) / 0.6)) * 100;
    }

    const breakdown = [];
    const missing = [];
    let totalWeight = 0;
    for (const [k, w] of Object.entries(_CONF_WEIGHTS)) {
        if (signals[k] != null) totalWeight += w;
        else missing.push(k);
    }
    let weightedSum = 0;
    for (const [k, w] of Object.entries(_CONF_WEIGHTS)) {
        if (signals[k] == null) continue;
        const ew = totalWeight > 0 ? w / totalWeight : 0;
        const contrib = signals[k] * ew;
        weightedSum += contrib;
        breakdown.push({ signal: k, score: Math.round(signals[k] * 10) / 10, weight: Math.round(ew * 1000) / 1000, contribution: Math.round(contrib * 10) / 10 });
    }
    const score = totalWeight > 0 ? Math.round(weightedSum) : 0;
    let label = "Very Low";
    for (const [th, lbl] of _CONF_LABELS) {
        if (score >= th) { label = lbl; break; }
    }
    sku.confidence = { score, label, breakdown, missing };
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
            window.history.replaceState(null, '', target === '#tab-planner' ? '#planner' : '#topology');
        });
    }
    // Activate tab from hash
    const hash = window.location.hash;
    if (hash === '#planner') {
        const plannerTab = document.getElementById('planner-tab');
        if (plannerTab) new bootstrap.Tab(plannerTab).show();
    }

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
    return window.location.hash === "#planner" ? "planner" : "topology";
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
        if (authTenants.length <= 1) {
            document.getElementById("tenant-section").classList.add("d-none");
            if (authTenants.length === 1) {
                select.innerHTML = `<option value="${authTenants[0].id}">${escapeHtml(authTenants[0].name)}</option>`;
                select.value = authTenants[0].id;
            }
            return;
        }
        select.innerHTML = tenants.map(t => {
            const disabled = t.authenticated ? "" : "disabled";
            const label = t.authenticated
                ? `${escapeHtml(t.name)} (${t.id.slice(0, 8)}\u2026)`
                : `${escapeHtml(t.name)} \u2014 no valid auth`;
            return `<option value="${t.id}" ${disabled}>${label}</option>`;
        }).join("");
        if (defaultTid && tenants.some(t => t.id === defaultTid && t.authenticated)) {
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
        const region = document.getElementById("region-select").value || "az-mapping";
        const a = document.createElement("a");
        a.download = `az-mapping-${region}.png`;
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
    downloadCSV([headers, ...rows], `az-mapping-${document.getElementById("region-select").value || "export"}.csv`);
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

        // Fetch SKUs
        const params = new URLSearchParams({ region, subscriptionId });
        if (tenant) params.append("tenantId", tenant);
        const includePrices = document.getElementById("planner-include-prices")?.checked;
        if (includePrices) {
            params.append("includePrices", "true");
            const currency = document.getElementById("planner-currency")?.value || "USD";
            params.append("currencyCode", currency);
        }
        const skuPromise = apiFetch(`/api/skus?${params}`);

        // Run in parallel
        const [mappingsResult, skuResult] = await Promise.all([mappingsPromise, skuPromise]);

        // Store zone mappings for this planner session
        plannerZoneMappings = mappingsResult;

        if (skuResult.error) throw new Error(skuResult.error);

        lastSkuData = skuResult;
        // Compute confidence scores
        for (const sku of lastSkuData) recomputeConfidence(sku);

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

        // Recompute confidence and re-render
        if (lastSkuData) {
            for (const sku of lastSkuData) recomputeConfidence(sku);
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
// SKU Pricing Detail Modal
// ---------------------------------------------------------------------------
let _pricingModalSku = null;
let _pricingModal = null;

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
    if (_pricingModalSku) fetchPricingDetail();
}

async function fetchPricingDetail() {
    const skuName = _pricingModalSku;
    if (!skuName) return;
    const region = document.getElementById("region-select").value;
    const currency = document.getElementById("pricing-modal-currency-select").value;
    if (!region) return;

    document.getElementById("pricing-modal-loading").classList.remove("d-none");
    document.getElementById("pricing-modal-content").classList.add("d-none");

    try {
        const params = new URLSearchParams({ region, skuName, currencyCode: currency });
        if (plannerSubscriptionId) params.set("subscriptionId", plannerSubscriptionId);
        const tqs = tenantQS("&");
        const data = await apiFetch(`/api/sku-pricing?${params}${tqs}`);
        renderPricingDetail(data);
    } catch (err) {
        const content = document.getElementById("pricing-modal-content");
        content.innerHTML = `<p class="text-danger small">Failed to load pricing: ${escapeHtml(err.message)}</p>`;
        content.classList.remove("d-none");
    } finally {
        document.getElementById("pricing-modal-loading").classList.add("d-none");
    }
}

function renderPricingDetail(data) {
    const content = document.getElementById("pricing-modal-content");
    const currency = data.currency || "USD";
    const HOURS_PER_MONTH = 730;

    const rows = [
        { label: "Pay-As-You-Go", hourly: data.paygo },
        { label: "Spot", hourly: data.spot },
        { label: "Reserved Instance 1Y", hourly: data.ri_1y },
        { label: "Reserved Instance 3Y", hourly: data.ri_3y },
        { label: "Savings Plan 1Y", hourly: data.sp_1y },
        { label: "Savings Plan 3Y", hourly: data.sp_3y },
    ];

    let html = '<table class="table table-sm pricing-detail-table">';
    html += `<thead><tr><th>Type</th><th>${escapeHtml(currency)}/hour</th><th>${escapeHtml(currency)}/month</th></tr></thead><tbody>`;
    rows.forEach(r => {
        const hourStr = r.hourly != null ? r.hourly.toFixed(4) : "\u2014";
        const monthStr = r.hourly != null ? (r.hourly * HOURS_PER_MONTH).toFixed(2) : "\u2014";
        html += `<tr><td>${escapeHtml(r.label)}</td><td class="price-cell">${hourStr}</td><td class="price-cell">${monthStr}</td></tr>`;
    });
    html += "</tbody></table>";

    if (data.profile) html += renderVmProfile(data.profile);

    const confSku = (lastSkuData || []).find(s => s.name === _pricingModalSku);
    if (confSku?.confidence) html += renderConfidenceBreakdown(confSku.confidence);

    content.innerHTML = html;
    content.classList.remove("d-none");

    // Init Bootstrap tooltips for confidence info icons
    content.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
        new bootstrap.Tooltip(el, { delay: { show: 0, hide: 100 }, placement: "top" });
    });
}

function renderVmProfile(profile) {
    const caps = profile.capabilities || {};
    const zones = profile.zones || [];
    const restrictions = profile.restrictions || [];

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

    let restrictionSummary = '<span class="vm-badge vm-badge-yes">None</span>';
    if (restrictions.length > 0) {
        const parts = restrictions.map(r => {
            let desc = r.type || "Unknown";
            if (r.reasonCode) desc += ` (${r.reasonCode})`;
            if (r.zones?.length) desc += ` zones: ${r.zones.join(", ")}`;
            return `<div class="restriction-item">${escapeHtml(desc)}</div>`;
        });
        restrictionSummary = `<span class="vm-badge vm-badge-limited vm-badge-block">${parts.join("")}</span>`;
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
    html += '</div>';

    html += '<div class="vm-profile-card">';
    html += '<div class="vm-profile-card-title">Deployment</div>';
    html += row("Zones", val(zones.length > 0 ? zones.join(", ") : "None"));
    html += row("HyperV Gen.", val(caps.HyperVGenerations));
    html += row("Encryption at Host", badge(caps.EncryptionAtHostSupported));
    html += row("Confidential", val(caps.ConfidentialComputingType || null));
    if (restrictions.length > 0) {
        html += `<div class="vm-profile-row vm-profile-row-stacked"><span class="vm-profile-label">Restrictions</span>${restrictionSummary}</div>`;
    } else {
        html += row("Restrictions", restrictionSummary);
    }
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

function renderConfidenceBreakdown(conf) {
    const lbl = (conf.label || "").toLowerCase().replace(/\s+/g, "-");
    let html = '<div class="confidence-section">';
    html += `<h4 class="confidence-title">Deployment Confidence <span class="confidence-badge confidence-${lbl}">${conf.score} ${escapeHtml(conf.label || "")}</span> <i class="bi bi-info-circle text-body-secondary confidence-info-icon" data-bs-toggle="tooltip" data-bs-title="Composite score (0\u2013100) predicting deployment success based on weighted signals. Higher is better."></i></h4>`;
    if (conf.breakdown?.length) {
        html += '<table class="table table-sm confidence-breakdown-table"><thead><tr><th>Signal</th><th>Score</th><th>Weight</th><th>Contribution</th></tr></thead><tbody>';
        const signalLabels = { quota: "Quota Headroom", spot: "Spot Placement", zones: "Zone Breadth", restrictions: "Restrictions", pricePressure: "Price Pressure" };
        const signalDescriptions = {
            quota: "Remaining quota relative to vCPU count. Low quota means deployments may be blocked.",
            spot: "Best spot eviction score across zones. High means lower eviction risk.",
            zones: "Number of availability zones where the SKU is offered (out of 3).",
            restrictions: "Whether any subscription or zone-level restrictions apply to this SKU.",
            pricePressure: "Spot-to-PAYGO price ratio. A lower ratio indicates better spot savings."
        };
        conf.breakdown.forEach(b => {
            const desc = signalDescriptions[b.signal] || "";
            html += `<tr><td>${escapeHtml(signalLabels[b.signal] || b.signal)} <i class="bi bi-info-circle text-body-secondary" data-bs-toggle="tooltip" data-bs-title="${escapeHtml(desc)}"></i></td><td>${b.score}</td><td>${(b.weight * 100).toFixed(1)}%</td><td>${b.contribution.toFixed(1)}</td></tr>`;
        });
        html += '</tbody></table>';
    }
    if (conf.missing?.length) {
        const signalLabels = { quota: "Quota Headroom", spot: "Spot Placement", zones: "Zone Breadth", restrictions: "Restrictions", pricePressure: "Price Pressure" };
        const names = conf.missing.map(m => signalLabels[m] || m).join(", ");
        html += `<p class="confidence-missing"><i class="bi bi-exclamation-circle"></i> Missing signals (excluded from score): ${escapeHtml(names)}</p>`;
    }
    html += '</div>';
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
    for (const [th, lbl] of _CONF_LABELS) {
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

    // Build table HTML
    let html = '<table id="sku-datatable" class="table table-sm table-hover sku-table">';
    html += "<thead><tr>";

    const headers = ["SKU Name", "Family", "vCPUs", "Memory (GB)",
        "Quota Limit", "Quota Used", "Quota Remaining",
        "Spot Score", "Confidence"];
    if (hasPricing) {
        const currency = skus.find(s => s.pricing)?.pricing?.currency || "USD";
        headers.push(`PAYGO ${currency}/h`, `Spot ${currency}/h`);
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
        const spotZoneScores = (lastSpotScores?.scores || {})[sku.name] || {};
        const spotZones = Object.keys(spotZoneScores).sort();
        if (spotZones.length > 0) {
            const badges = spotZones.map(z => {
                const s = spotZoneScores[z] || "Unknown";
                return `<span class="spot-zone-label">Z${escapeHtml(z)}</span><span class="spot-badge spot-${s.toLowerCase()}">${escapeHtml(s)}</span>`;
            }).join(" ");
            html += `<td><button type="button" class="spot-cell-btn has-score" data-action="spot" data-sku="${escapeHtml(sku.name)}" title="Click to refresh">${badges}</button></td>`;
        } else {
            html += `<td><button type="button" class="spot-cell-btn" data-action="spot" data-sku="${escapeHtml(sku.name)}" title="Get Spot Placement Score">Score?</button></td>`;
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

        // Prices
        if (hasPricing) {
            const pricing = sku.pricing || {};
            html += `<td class="price-cell">${pricing.paygo != null ? pricing.paygo.toFixed(4) : '\u2014'}</td>`;
            html += `<td class="price-cell">${pricing.spot != null ? pricing.spot.toFixed(4) : '\u2014'}</td>`;
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
    const colConfig = [
        { select: [2, 3, 4, 5, 6], type: "number" },   // vCPUs, Memory, Quota
        { select: 8, type: "number" },                   // Confidence (uses data-sort attr)
    ];
    let nextCol = 9;
    if (hasPricing) {
        colConfig.push({ select: [nextCol, nextCol + 1], type: "number" });
        nextCol += 2;
    }

    // Init Simple-DataTables
    const tableEl = document.getElementById("sku-datatable");

    // Build per-column header filter config
    // Only text-filterable columns get an input; Zone columns are excluded
    const filterableCols = [0, 1, 2, 3, 4, 5, 6, 7, 8];
    if (hasPricing) { filterableCols.push(nextCol - 2, nextCol - 1); }
    const headerConfig = {};
    filterableCols.forEach(idx => {
        headerConfig[idx] = { type: "input", attr: { placeholder: "Filter\u2026", class: "datatable-column-filter" } };
    });

    _skuDataTable = new simpleDatatables.DataTable(tableEl, {
        searchable: false,
        paging: false,
        labels: {
            noRows: "No SKUs match",
            info: "{rows} SKUs",
        },
        columns: colConfig,
    });

    // Build per-column filter row in thead
    _buildColumnFilters(tableEl, filterableCols);

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

/**
 * Inject a second <tr> into thead with <input> filters for specified columns.
 * Filtering uses Simple-DataTables columns().search() when available,
 * otherwise falls back to manual row-level filtering.
 */
function _buildColumnFilters(tableEl, filterableCols) {
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
            input.placeholder = "Filter\u2026";
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
        const val = inp.value.trim().toLowerCase();
        if (val) filters.push({ col: parseInt(inp.dataset.col, 10), text: val });
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
            return cell.textContent.toLowerCase().includes(f.text);
        });
        row.style.display = match ? "" : "none";
    });
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
        "Confidence Score", "Confidence Label", ...priceHeaders, ...zoneHeaders];
    const rows = lastSkuData.map(sku => {
        const quota = sku.quota || {};
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
            ...(hasPricing ? [sku.pricing?.paygo ?? "", sku.pricing?.spot ?? ""] : []),
            ...zoneCols
        ];
    });
    downloadCSV([headers, ...rows], `az-skus-${document.getElementById("region-select").value || "export"}.csv`);
}
