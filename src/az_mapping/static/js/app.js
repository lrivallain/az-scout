/* ===================================================================
   Azure AZ Mapping Viewer – Frontend Logic
   =================================================================== */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let subscriptions = [];          // [{id, name}]
let selectedSubscriptions = new Set();
let regions = [];                // [{name, displayName}]
let tenants = [];                // [{id, name}]
let lastMappingData = null;      // cached result from /api/mappings
let selectedSkuSubscription = null; // subscription selected for SKU loading
let lastSkuData = null;          // cached SKU data
let skuSortColumn = null;        // current SKU table sort column
let skuSortAsc = true;           // sort direction
let lastSpotScores = null;       // cached spot placement scores {scores: {sku: score}, errors: []}

// ---------------------------------------------------------------------------
// Theme management
// ---------------------------------------------------------------------------
function getEffectiveTheme() {
    const stored = localStorage.getItem("theme");
    if (stored === "dark" || stored === "light") return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    // Toggle icon visibility
    const sun = document.querySelector(".icon-sun");
    const moon = document.querySelector(".icon-moon");
    if (sun && moon) {
        sun.style.display = theme === "dark" ? "block" : "none";
        moon.style.display = theme === "dark" ? "none" : "block";
    }
}

function toggleTheme() {
    const current = getEffectiveTheme();
    const next = current === "dark" ? "light" : "dark";
    localStorage.setItem("theme", next);
    applyTheme(next);
}

// Apply theme immediately (before DOMContentLoaded) to prevent flash
applyTheme(getEffectiveTheme());

// Listen for system preference changes
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!localStorage.getItem("theme")) {
        applyTheme(getEffectiveTheme());
    }
});

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", init);

async function init() {
    document.getElementById("sub-filter").addEventListener("input", onFilterSubs);
    document.getElementById("tenant-select").addEventListener("change", onTenantChange);
    document.getElementById("sku-subscription-select").addEventListener("change", onSkuSubscriptionChange);
    
    // SKU filter with debounce (250ms)
    let skuFilterTimeout;
    const skuFilterInput = document.getElementById("sku-filter");
    if (skuFilterInput) {
        skuFilterInput.addEventListener("input", () => {
            clearTimeout(skuFilterTimeout);
            skuFilterTimeout = setTimeout(() => {
                if (lastSkuData) {
                    const subscriptionId = selectedSkuSubscription || [...selectedSubscriptions][0];
                    const subscriptionName = subscriptionId ? getSubName(subscriptionId) : "Unknown";
                    renderSkuTable(lastSkuData, subscriptionName);
                }
            }, 250);
        });
    }
    
    initRegionCombobox();

    // Load tenants first
    await fetchTenants();

    // Restore tenant from URL before loading regions/subs
    const urlState = getUrlParams();
    if (urlState.tenant) {
        const select = document.getElementById("tenant-select");
        if ([...select.options].some(o => o.value === urlState.tenant)) {
            select.value = urlState.tenant;
        }
    }

    // Load regions and subscriptions in parallel – independent of each other
    await Promise.all([fetchRegions(), fetchSubscriptions()]);

    // Restore state from URL parameters
    if (urlState.region) {
        if (regions.some(r => r.name === urlState.region)) {
            selectRegion(urlState.region);
        }
    }
    if (urlState.subscriptions.length) {
        urlState.subscriptions.forEach(id => {
            if (subscriptions.some(s => s.id === id)) {
                selectedSubscriptions.add(id);
            }
        });
        renderSubscriptionList();
        updateSkuSubscriptionSelector();
    }
    updateLoadButton();

    // Auto-load mappings if both region and subscriptions are set
    if (urlState.region && urlState.subscriptions.length && !document.getElementById("load-btn").disabled) {
        await loadMappings();
    }
}

// ---------------------------------------------------------------------------
// Sidebar collapse / expand
// ---------------------------------------------------------------------------
function toggleSidebar() {
    const panel = document.getElementById("filters-panel");
    const btn = document.getElementById("sidebar-toggle");
    const isCollapsed = panel.classList.toggle("collapsed");
    btn.title = isCollapsed ? "Expand filters" : "Collapse filters";
}

// ---------------------------------------------------------------------------
// URL parameter helpers
// ---------------------------------------------------------------------------
function getUrlParams() {
    const params = new URLSearchParams(window.location.search);
    return {
        tenant: params.get("tenant") || "",
        region: params.get("region") || "",
        subscriptions: params.get("subscriptions") ? params.get("subscriptions").split(",") : []
    };
}

function syncUrlParams() {
    const tenant = document.getElementById("tenant-select").value;
    const region = document.getElementById("region-select").value;
    const subs = [...selectedSubscriptions];
    const params = new URLSearchParams();
    if (tenant) params.set("tenant", tenant);
    if (region) params.set("region", region);
    if (subs.length) params.set("subscriptions", subs.join(","));
    const qs = params.toString();
    const newUrl = window.location.pathname + (qs ? "?" + qs : "");
    window.history.replaceState(null, "", newUrl);
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

function showError(msg) {
    const panel = document.getElementById("error-panel");
    panel.textContent = msg;
    panel.style.display = "block";
}
function hideError() {
    document.getElementById("error-panel").style.display = "none";
}

/** Return '&tenantId=xxx' or '' depending on current selection. */
function tenantQS(prefix) {
    const tid = document.getElementById("tenant-select").value;
    if (!tid) return "";
    return (prefix || "&") + "tenantId=" + encodeURIComponent(tid);
}

// ---------------------------------------------------------------------------
// Subscriptions
// ---------------------------------------------------------------------------
async function fetchSubscriptions() {
    try {
        subscriptions = await apiFetch("/api/subscriptions" + tenantQS("?"));
        renderSubscriptionList();
    } catch (err) {
        showError("Failed to load subscriptions: " + err.message);
        document.getElementById("sub-loading").innerHTML =
            '<span style="color:var(--danger)">Error loading subscriptions</span>';
    }
}

function renderSubscriptionList(filter) {
    const container = document.getElementById("sub-list");
    const list = filter
        ? subscriptions.filter(s => s.name.toLowerCase().includes(filter.toLowerCase()))
        : subscriptions;

    if (!list.length && !filter) {
        container.innerHTML = '<div class="loading-indicator"><span>No subscriptions found</span></div>';
        return;
    }

    container.innerHTML = list.map(s => {
        const checked = selectedSubscriptions.has(s.id) ? "checked" : "";
        const escapedName = escapeHtml(s.name);
        return `<label class="checkbox-item" title="${escapedName}">
            <input type="checkbox" value="${s.id}" ${checked}
                   onchange="toggleSubscription('${s.id}')">
            <span class="sub-name">${escapedName}</span>
        </label>`;
    }).join("");

    updateSubCount();
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

function onFilterSubs(e) {
    renderSubscriptionList(e.target.value);
}

function toggleSubscription(id) {
    if (selectedSubscriptions.has(id)) {
        selectedSubscriptions.delete(id);
    } else {
        selectedSubscriptions.add(id);
    }
    updateSubCount();
    updateLoadButton();
    syncUrlParams();
    updateSkuSubscriptionSelector();
}

function selectAllVisible() {
    document.querySelectorAll("#sub-list input[type=checkbox]").forEach(cb => {
        cb.checked = true;
        selectedSubscriptions.add(cb.value);
    });
    updateSubCount();
    updateLoadButton();
    syncUrlParams();
    updateSkuSubscriptionSelector();
}

function deselectAll() {
    selectedSubscriptions.clear();
    document.querySelectorAll("#sub-list input[type=checkbox]").forEach(cb => {
        cb.checked = false;
    });
    updateSubCount();
    updateLoadButton();
    syncUrlParams();
    updateSkuSubscriptionSelector();
}

function updateSubCount() {
    document.getElementById("sub-count").textContent =
        `${selectedSubscriptions.size} selected`;
}

function updateSkuSubscriptionSelector() {
    const selector = document.getElementById("sku-subscription-select");
    const selectedSubs = [...selectedSubscriptions];
    
    // Clear existing options
    selector.innerHTML = '<option value="">Select subscription…</option>';
    
    // Show selector only if multiple subscriptions are selected
    if (selectedSubs.length > 1) {
        selector.style.display = "block";
        
        // Populate options with selected subscriptions
        selectedSubs.forEach(subId => {
            const sub = subscriptions.find(s => s.id === subId);
            if (sub) {
                const option = document.createElement("option");
                option.value = subId;
                option.textContent = sub.name;
                selector.appendChild(option);
            }
        });
        
        // Set selected value if exists and is still in selection
        if (selectedSkuSubscription && selectedSubs.includes(selectedSkuSubscription)) {
            selector.value = selectedSkuSubscription;
        } else {
            // Default to first subscription
            selectedSkuSubscription = selectedSubs[0];
            selector.value = selectedSkuSubscription;
        }
    } else {
        selector.style.display = "none";
        // Set to single selected subscription or null
        selectedSkuSubscription = selectedSubs.length === 1 ? selectedSubs[0] : null;
    }
}

function onSkuSubscriptionChange() {
    const selector = document.getElementById("sku-subscription-select");
    selectedSkuSubscription = selector.value;
    // Reset SKU data when subscription changes
    resetSkuSection();
}

// ---------------------------------------------------------------------------
// Tenants
// ---------------------------------------------------------------------------
async function fetchTenants() {
    const select = document.getElementById("tenant-select");
    select.innerHTML = '<option value="">Loading tenants…</option>';
    try {
        const result = await apiFetch("/api/tenants");
        tenants = result.tenants || [];
        const defaultTid = result.defaultTenantId || "";

        // Filter to authenticated tenants for auto-hide logic
        const authTenants = tenants.filter(t => t.authenticated);
        if (authTenants.length <= 1) {
            // Single reachable tenant – auto-select and hide the selector
            select.closest(".filter-section").style.display = "none";
            if (authTenants.length === 1) {
                select.innerHTML = `<option value="${authTenants[0].id}">${escapeHtml(authTenants[0].name)}</option>`;
                select.value = authTenants[0].id;
            }
            return;
        }
        select.innerHTML = tenants.map(t => {
            const disabled = t.authenticated ? "" : "disabled";
            const label = t.authenticated
                ? `${escapeHtml(t.name)} (${t.id.slice(0, 8)}…)`
                : `${escapeHtml(t.name)} — no valid auth`;
            return `<option value="${t.id}" ${disabled}>${label}</option>`;
        }).join("");
        // Auto-select the tenant matching the current credential
        if (defaultTid && tenants.some(t => t.id === defaultTid && t.authenticated)) {
            select.value = defaultTid;
        }
    } catch (err) {
        // Non-blocking: if tenants fail, just hide the selector
        select.closest(".filter-section").style.display = "none";
    }
}

async function onTenantChange() {
    // Reset downstream state
    selectedSubscriptions.clear();
    document.getElementById("region-select").value = "";
    document.getElementById("region-search").value = "";
    document.getElementById("sub-filter").value = "";
    document.getElementById("results-content").style.display = "none";
    document.getElementById("empty-state").style.display = "flex";
    hideError();
    syncUrlParams();

    // Reload regions and subscriptions for the new tenant
    await Promise.all([fetchRegions(), fetchSubscriptions()]);
    updateLoadButton();
}

// ---------------------------------------------------------------------------
// Regions  (loaded once at startup, never reloaded)
// ---------------------------------------------------------------------------
async function fetchRegions() {
    const searchInput = document.getElementById("region-search");
    searchInput.placeholder = "Loading regions…";
    searchInput.disabled = true;

    try {
        regions = await apiFetch("/api/regions" + tenantQS("?"));
        searchInput.placeholder = "Type to search regions…";
        searchInput.disabled = false;
        renderRegionDropdown("");
    } catch (err) {
        showError("Failed to load regions: " + err.message);
        searchInput.placeholder = "Error loading regions";
    }
}

function renderRegionDropdown(filter) {
    const dropdown = document.getElementById("region-dropdown");
    const lc = filter.toLowerCase();
    const matches = lc
        ? regions.filter(r => r.displayName.toLowerCase().includes(lc) || r.name.toLowerCase().includes(lc))
        : regions;
    dropdown.innerHTML = matches.map(r =>
        `<li data-value="${r.name}">${escapeHtml(r.displayName)} <span class="region-name">(${r.name})</span></li>`
    ).join("");
    // Attach click handlers
    dropdown.querySelectorAll("li").forEach(li => {
        li.addEventListener("click", () => selectRegion(li.dataset.value));
    });
}

function selectRegion(name) {
    const r = regions.find(r => r.name === name);
    if (!r) return;
    document.getElementById("region-select").value = name;
    document.getElementById("region-search").value = `${r.displayName} (${r.name})`;
    closeRegionDropdown();
    onRegionChange();
}

function openRegionDropdown() {
    document.getElementById("region-dropdown").classList.add("open");
}
function closeRegionDropdown() {
    document.getElementById("region-dropdown").classList.remove("open");
}

function initRegionCombobox() {
    const searchInput = document.getElementById("region-search");
    const dropdown = document.getElementById("region-dropdown");

    searchInput.addEventListener("focus", () => {
        // If a region was selected, select the text so the user can type over it
        searchInput.select();
        renderRegionDropdown(searchInput.value.includes("(") ? "" : searchInput.value);
        openRegionDropdown();
    });
    searchInput.addEventListener("input", () => {
        // Clear the hidden select when user types
        document.getElementById("region-select").value = "";
        renderRegionDropdown(searchInput.value);
        openRegionDropdown();
        onRegionChange();
    });
    // Keyboard navigation
    searchInput.addEventListener("keydown", (e) => {
        const items = dropdown.querySelectorAll("li");
        const active = dropdown.querySelector("li.active");
        let idx = [...items].indexOf(active);
        if (e.key === "ArrowDown") {
            e.preventDefault();
            if (!dropdown.classList.contains("open")) openRegionDropdown();
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
            if (active) {
                selectRegion(active.dataset.value);
            } else if (items.length === 1) {
                selectRegion(items[0].dataset.value);
            }
        } else if (e.key === "Escape") {
            closeRegionDropdown();
            searchInput.blur();
        }
    });
    // Close dropdown when clicking outside
    document.addEventListener("click", (e) => {
        if (!e.target.closest("#region-combobox")) {
            closeRegionDropdown();
        }
    });
}

function onRegionChange() {
    updateLoadButton();
    syncUrlParams();
    // Reset SKU data when region changes to avoid confusion
    resetSkuSection();
}

function resetSkuSection() {
    lastSkuData = null;
    lastSpotScores = null;
    skuSortColumn = null;
    skuSortAsc = true;
    document.getElementById("sku-empty").style.display = "block";
    document.getElementById("sku-table-container").style.display = "none";
    document.getElementById("sku-loading").style.display = "none";
}

function updateLoadButton() {
    const btn = document.getElementById("load-btn");
    const region = document.getElementById("region-select").value;
    btn.disabled = !(selectedSubscriptions.size > 0 && region);
}

// ---------------------------------------------------------------------------
// Load mappings
// ---------------------------------------------------------------------------
async function loadMappings() {
    const region = document.getElementById("region-select").value;
    if (!region || selectedSubscriptions.size === 0) return;

    hideError();
    document.getElementById("empty-state").style.display = "none";
    document.getElementById("results-content").style.display = "none";
    document.getElementById("results-loading").style.display = "flex";
    
    // Reset SKU section when mappings are reloaded
    resetSkuSection();

    try {
        const subs = [...selectedSubscriptions].join(",");
        lastMappingData = await apiFetch(`/api/mappings?region=${region}&subscriptions=${subs}${tenantQS()}`);

        document.getElementById("results-loading").style.display = "none";
        document.getElementById("results-content").style.display = "block";

        renderGraph(lastMappingData);
        renderTable(lastMappingData);
    } catch (err) {
        document.getElementById("results-loading").style.display = "none";
        document.getElementById("empty-state").style.display = "flex";
        showError("Failed to load mappings: " + err.message);
    }
}

// ---------------------------------------------------------------------------
// Utility: subscription name lookup
// ---------------------------------------------------------------------------
function getSubName(id) {
    const s = subscriptions.find(s => s.id === id);
    return s ? s.name : id.substring(0, 8) + "…";
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

    // Filter out entries with no mappings
    const validData = data.filter(d => d.mappings && d.mappings.length > 0);
    if (!validData.length) {
        container.innerHTML = '<div class="empty-state"><p>No zone mappings available.</p></div>';
        return;
    }

    // Collect unique zones
    const logicalZones = [...new Set(validData.flatMap(d => d.mappings.map(m => m.logicalZone)))].sort();
    const physicalZones = [...new Set(validData.flatMap(d => d.mappings.map(m => m.physicalZone)))].sort();

    // Measure text widths to size nodes dynamically
    const measurer = d3.select(container).append("svg").attr("class", "measurer").style("position", "absolute").style("visibility", "hidden");
    const measureText = (txt, fontSize) => {
        const t = measurer.append("text").attr("font-size", fontSize).attr("font-weight", 500).attr("font-family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif").text(txt);
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

    // Layout constants – widths adapt to content
    const nodePadX = 24;           // horizontal padding inside a node
    const leftNodeW = Math.ceil(Math.max(maxLZText, maxSubText) + nodePadX);
    const rightNodeW = Math.ceil(maxPZText + nodePadX);
    const nodeH = 36;
    const nodeGapY = 12;           // vertical gap between zone nodes inside a group
    const groupGapY = 28;          // vertical gap between subscription groups
    const groupPadTop = 36;        // space for subscription label inside group box
    const groupPadX = 12;
    const groupPadBot = 12;
    const linkGap = 180;           // horizontal gap for links between left and right columns
    const margin = { top: 20, right: 20, bottom: 20, left: 20 };

    const colorScale = d3.scaleOrdinal(d3.schemeTableau10).domain(validData.map(d => d.subscriptionId));

    // Physical-zone palette for right-side nodes
    const pzColors = ["#0078d4", "#107c10", "#d83b01", "#8764b8", "#008272", "#b4009e"];
    function pzColor(pz) {
        const m = pz.match(/(\d+)$/);
        const idx = m ? parseInt(m[1], 10) : 1;
        return pzColors[(idx - 1) % pzColors.length];
    }

    // ---- Compute left-side layout: subscription groups ----
    const groups = [];   // { subIdx, subId, subName, y, h, nodes: [{zone, y}] }
    let cursorY = 0;
    validData.forEach((sub, subIdx) => {
        const zones = sub.mappings.map(m => m.logicalZone)
            .filter((v, i, a) => a.indexOf(v) === i).sort();
        const contentH = zones.length * nodeH + (zones.length - 1) * nodeGapY;
        const boxH = groupPadTop + contentH + groupPadBot;
        const nodes = zones.map((z, zi) => ({
            zone: z,
            y: cursorY + groupPadTop + zi * (nodeH + nodeGapY) + nodeH / 2,
        }));
        groups.push({
            subIdx,
            subId: sub.subscriptionId,
            subName: getSubName(sub.subscriptionId),
            y: cursorY,
            h: boxH,
            nodes,
        });
        cursorY += boxH + groupGapY;
    });
    const leftTotalH = cursorY - groupGapY;

    // ---- Compute right-side layout: physical zone nodes ----
    const rightContentH = physicalZones.length * nodeH + (physicalZones.length - 1) * nodeGapY;
    const rightBoxPadTop = 36;
    const rightBoxPadBot = 14;
    const rightBoxH = rightBoxPadTop + rightContentH + rightBoxPadBot;
    // Vertically centre right column relative to left column
    const rightBoxY = Math.max(0, (leftTotalH - rightBoxH) / 2);
    const physicalNodes = physicalZones.map((pz, i) => ({
        zone: pz,
        y: rightBoxY + rightBoxPadTop + i * (nodeH + nodeGapY) + nodeH / 2,
    }));

    // ---- SVG dimensions ----
    const groupBoxW = leftNodeW + groupPadX * 2;
    const rightBoxW = rightNodeW + groupPadX * 2;
    const totalW = margin.left + groupBoxW + linkGap + rightBoxW + margin.right;
    const totalH = Math.max(leftTotalH, rightBoxY + rightBoxH) + margin.top + margin.bottom;

    const leftX = margin.left;
    const rightX = margin.left + groupBoxW + linkGap;

    const svg = d3.select(container)
        .append("svg")
        .attr("viewBox", `0 0 ${totalW} ${totalH}`)
        .attr("preserveAspectRatio", "xMidYMid meet");

    const g = svg.append("g")
        .attr("transform", `translate(0, ${margin.top})`);

    // ---- Draw right-side group box (Physical Zones) ----
    const rightGroup = g.append("g").attr("transform", `translate(${rightX}, ${rightBoxY})`);
    rightGroup.append("rect")
        .attr("x", 0).attr("y", 0)
        .attr("width", rightBoxW).attr("height", rightBoxH)
        .attr("rx", 10)
        .attr("class", "group-box-right");
    rightGroup.append("text")
        .attr("x", rightBoxW / 2).attr("y", 22)
        .attr("text-anchor", "middle")
        .attr("class", "group-label-right")
        .text("Physical Zones");

    // ---- Draw physical zone nodes ----
    physicalNodes.forEach(pn => {
        const ng = g.append("g")
            .attr("transform", `translate(${rightX + groupPadX}, ${pn.y})`)
            .attr("class", "pz-node-group")
            .attr("data-pz", pn.zone)
            .style("cursor", "pointer");
        ng.append("rect")
            .attr("x", 0).attr("y", -nodeH / 2)
            .attr("width", rightNodeW).attr("height", nodeH)
            .attr("rx", 6)
            .attr("class", "node-rect-right")
            .attr("style", `fill: ${pzColor(pn.zone)}20; stroke: ${pzColor(pn.zone)};`);
        ng.append("text")
            .attr("x", rightNodeW / 2).attr("y", 5)
            .attr("text-anchor", "middle")
            .attr("class", "node-label")
            .text(pn.zone);
        ng.on("mouseenter", () => highlightByPZ(pn.zone));
        ng.on("mouseleave", clearHighlight);
    });

    // ---- Draw left-side subscription groups ----
    groups.forEach(grp => {
        const gg = g.append("g")
            .attr("transform", `translate(${leftX}, ${grp.y})`)
            .attr("class", "sub-group")
            .attr("data-sub", grp.subIdx)
            .style("cursor", "pointer");
        // Group box
        gg.append("rect")
            .attr("x", 0).attr("y", 0)
            .attr("width", groupBoxW).attr("height", grp.h)
            .attr("rx", 10)
            .attr("class", "group-box-left")
            .attr("style", `stroke: ${colorScale(grp.subId)};`);
        // Subscription label
        gg.append("text")
            .attr("x", groupBoxW / 2).attr("y", 22)
            .attr("text-anchor", "middle")
            .attr("class", "group-label-left")
            .attr("fill", colorScale(grp.subId))
            .text(truncate(grp.subName, 22));
        gg.append("title").text(grp.subName);
        gg.on("mouseenter", () => highlightSub(grp.subIdx, validData.length, validData));
        gg.on("mouseleave", clearHighlight);
    });

    // ---- Draw logical zone nodes inside each group ----
    groups.forEach(grp => {
        grp.nodes.forEach(ln => {
            const ng = g.append("g")
                .attr("transform", `translate(${leftX + groupPadX}, ${ln.y})`)
                .attr("class", "lz-node-group")
                .attr("data-sub", grp.subIdx)
                .attr("data-lz", ln.zone)
                .style("cursor", "pointer");
            ng.append("rect")
                .attr("x", 0).attr("y", -nodeH / 2)
                .attr("width", leftNodeW).attr("height", nodeH)
                .attr("rx", 6)
                .attr("class", "node-rect-left")
                .attr("style", `stroke: ${colorScale(grp.subId)};`);
            ng.append("text")
                .attr("x", leftNodeW / 2).attr("y", 5)
                .attr("text-anchor", "middle")
                .attr("class", "node-label")
                .text(`Zone ${ln.zone}`);
            ng.on("mouseenter", () => highlightByLZ(grp.subIdx, ln.zone, validData));
            ng.on("mouseleave", clearHighlight);
        });
    });

    // ---- Draw links ----
    const linksGroup = g.append("g").attr("class", "links-group");
    groups.forEach(grp => {
        const sub = validData[grp.subIdx];
        sub.mappings.forEach(m => {
            const srcNode = grp.nodes.find(n => n.zone === m.logicalZone);
            const tgtNode = physicalNodes.find(n => n.zone === m.physicalZone);
            if (!srcNode || !tgtNode) return;

            const sourceX = leftX + groupPadX + leftNodeW;
            const sourceY = srcNode.y;
            const targetX = rightX + groupPadX;
            const targetY = tgtNode.y;

            linksGroup.append("path")
                .attr("d", d3.linkHorizontal()({ source: [sourceX, sourceY], target: [targetX, targetY] }))
                .attr("stroke", colorScale(sub.subscriptionId))
                .attr("stroke-width", 2.5)
                .attr("fill", "none")
                .attr("opacity", 0.55)
                .attr("class", `link link-sub-${grp.subIdx}`)
                .attr("data-sub", grp.subIdx)
                .attr("data-lz", m.logicalZone)
                .attr("data-pz", m.physicalZone)
                .append("title")
                .text(`${grp.subName}: Zone ${m.logicalZone} → ${m.physicalZone}`);
        });
    });

    // ---- Legend (HTML) ----
    validData.forEach((sub, i) => {
        const item = document.createElement("div");
        item.className = "legend-item";
        item.dataset.subIdx = i;
        item.innerHTML = `<span class="legend-swatch" style="background:${colorScale(sub.subscriptionId)}"></span>
            <span>${escapeHtml(getSubName(sub.subscriptionId))}</span>`;
        item.addEventListener("mouseenter", () => highlightSub(i, validData.length, validData));
        item.addEventListener("mouseleave", () => clearHighlight());
        legendContainer.appendChild(item);
    });
}

function truncate(str, max) {
    return str.length > max ? str.substring(0, max - 1) + "…" : str;
}

// ---------------------------------------------------------------------------
// Highlight helpers
// ---------------------------------------------------------------------------

/** Highlight all links for a given subscription index. */
function highlightSub(subIdx, total, validData) {
    // Dim all links, highlight only this sub's links
    d3.selectAll(".link").classed("dimmed", true).classed("highlighted", false);
    d3.selectAll(`.link-sub-${subIdx}`).classed("dimmed", false).classed("highlighted", true);

    // Dim other subscription groups
    d3.selectAll(".sub-group").style("opacity", function() {
        return +d3.select(this).attr("data-sub") === subIdx ? 1 : 0.25;
    });
    d3.selectAll(".lz-node-group").style("opacity", function() {
        return +d3.select(this).attr("data-sub") === subIdx ? 1 : 0.25;
    });

    // Highlight the PZ nodes that this sub connects to
    const targetPZs = new Set();
    if (validData && validData[subIdx]) {
        validData[subIdx].mappings.forEach(m => targetPZs.add(m.physicalZone));
    }
    d3.selectAll(".pz-node-group").style("opacity", function() {
        return targetPZs.has(d3.select(this).attr("data-pz")) ? 1 : 0.25;
    });
}

/** Highlight the link from a specific LZ in a specific sub, and the target PZ. */
function highlightByLZ(subIdx, lz, validData) {
    // Dim everything first
    d3.selectAll(".link").classed("dimmed", true).classed("highlighted", false);
    d3.selectAll(".sub-group").style("opacity", 0.25);
    d3.selectAll(".lz-node-group").style("opacity", 0.25);
    d3.selectAll(".pz-node-group").style("opacity", 0.25);

    // Find the matching link(s) and highlight
    d3.selectAll(".link").each(function() {
        const el = d3.select(this);
        if (+el.attr("data-sub") === subIdx && el.attr("data-lz") === lz) {
            el.classed("dimmed", false).classed("highlighted", true);
        }
    });

    // Highlight the source subscription group and LZ node
    d3.selectAll(".sub-group").filter(function() {
        return +d3.select(this).attr("data-sub") === subIdx;
    }).style("opacity", 1);
    d3.selectAll(".lz-node-group").filter(function() {
        return +d3.select(this).attr("data-sub") === subIdx && d3.select(this).attr("data-lz") === lz;
    }).style("opacity", 1);

    // Highlight the target PZ node
    const targetPZ = validData[subIdx]?.mappings.find(m => m.logicalZone === lz)?.physicalZone;
    if (targetPZ) {
        d3.selectAll(".pz-node-group").filter(function() {
            return d3.select(this).attr("data-pz") === targetPZ;
        }).style("opacity", 1);
    }
}

/** Highlight all links pointing to a given physical zone, and their source LZ nodes. */
function highlightByPZ(pz) {
    // Dim everything first
    d3.selectAll(".link").classed("dimmed", true).classed("highlighted", false);
    d3.selectAll(".sub-group").style("opacity", 0.25);
    d3.selectAll(".lz-node-group").style("opacity", 0.25);
    d3.selectAll(".pz-node-group").style("opacity", 0.25);

    // Highlight the hovered PZ node
    d3.selectAll(".pz-node-group").filter(function() {
        return d3.select(this).attr("data-pz") === pz;
    }).style("opacity", 1);

    // Find matching links and collect source sub+lz pairs
    const matchedSubs = new Set();
    const matchedLZKeys = new Set();
    d3.selectAll(".link").each(function() {
        const el = d3.select(this);
        if (el.attr("data-pz") === pz) {
            el.classed("dimmed", false).classed("highlighted", true);
            matchedSubs.add(+el.attr("data-sub"));
            matchedLZKeys.add(el.attr("data-sub") + "::" + el.attr("data-lz"));
        }
    });

    // Highlight matching sub groups
    d3.selectAll(".sub-group").filter(function() {
        return matchedSubs.has(+d3.select(this).attr("data-sub"));
    }).style("opacity", 1);

    // Highlight matching LZ nodes
    d3.selectAll(".lz-node-group").filter(function() {
        const key = d3.select(this).attr("data-sub") + "::" + d3.select(this).attr("data-lz");
        return matchedLZKeys.has(key);
    }).style("opacity", 1);
}

/** Reset all highlight states. */
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
        container.innerHTML = '<p class="empty-state">No zone mappings available.</p>';
        return;
    }

    const logicalZones = [...new Set(validData.flatMap(d => d.mappings.map(m => m.logicalZone)))].sort();

    // Build table
    const table = document.createElement("table");
    table.className = "mapping-table";

    // Header
    const thead = table.createTHead();
    const headerRow = thead.insertRow();
    headerRow.insertCell().textContent = "Subscription";
    headerRow.insertCell().textContent = "Subscription ID";
    logicalZones.forEach(z => {
        const th = document.createElement("th");
        th.textContent = `Logical Zone ${z}`;
        headerRow.appendChild(th);
    });
    // Replace td with th in header
    headerRow.querySelectorAll("td").forEach(td => {
        const th = document.createElement("th");
        th.textContent = td.textContent;
        td.replaceWith(th);
    });

    // Body
    const tbody = table.createTBody();
    validData.forEach(sub => {
        const row = tbody.insertRow();
        const nameCell = row.insertCell();
        nameCell.textContent = getSubName(sub.subscriptionId);
        nameCell.className = "sub-name-cell";
        nameCell.title = getSubName(sub.subscriptionId);

        const idCell = row.insertCell();
        idCell.textContent = sub.subscriptionId;
        idCell.style.fontSize = "0.78rem";
        idCell.style.color = "var(--text-muted)";
        idCell.style.fontFamily = "monospace";

        logicalZones.forEach(z => {
            const cell = row.insertCell();
            const mapping = sub.mappings.find(m => m.logicalZone === z);
            if (mapping) {
                const badge = document.createElement("span");
                badge.className = `zone-badge ${pzClass(mapping.physicalZone)}`;
                badge.textContent = mapping.physicalZone;
                cell.appendChild(badge);
            } else {
                cell.textContent = "—";
                cell.style.color = "var(--text-muted)";
            }
        });
    });

    // Consistency footer row (only if >1 subscription)
    if (validData.length > 1) {
        const tfoot = table.createTFoot();
        const footRow = tfoot.insertRow();
        footRow.className = "consistency-row";
        const label = footRow.insertCell();
        label.colSpan = 2;
        label.textContent = "Consistency";

        logicalZones.forEach(z => {
            const cell = footRow.insertCell();
            const physicals = validData
                .map(sub => sub.mappings.find(m => m.logicalZone === z))
                .filter(Boolean)
                .map(m => m.physicalZone);
            const unique = [...new Set(physicals)];
            if (unique.length <= 1) {
                cell.textContent = "✓ Same";
                cell.className = "same";
            } else {
                cell.textContent = `⚠ ${unique.length} different`;
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

    // Serialize SVG with inline styles
    const clone = svgEl.cloneNode(true);

    // Collect all computed styles from the original and inline them on the clone
    inlineStyles(svgEl, clone);

    // Set explicit dimensions for the canvas
    const box = svgEl.viewBox.baseVal;
    const scale = 2; // retina-quality
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

/** Recursively copy computed styles from src elements onto dst (clone) elements. */
function inlineStyles(src, dst) {
    const computed = window.getComputedStyle(src);
    const dominated = ["fill", "stroke", "stroke-width", "stroke-dasharray",
        "opacity", "font-size", "font-weight", "font-family", "text-anchor",
        "dominant-baseline", "letter-spacing"];
    dominated.forEach(prop => {
        const val = computed.getPropertyValue(prop);
        if (val) dst.style.setProperty(prop, val);
    });
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

    // Header row
    const headers = ["Subscription", "Subscription ID",
        ...logicalZones.map(z => `Logical Zone ${z}`)];

    // Data rows
    const rows = validData.map(sub => {
        const name = getSubName(sub.subscriptionId);
        const cols = logicalZones.map(z => {
            const m = sub.mappings.find(m => m.logicalZone === z);
            return m ? m.physicalZone : "";
        });
        return [name, sub.subscriptionId, ...cols];
    });

    // Build CSV string
    const csvContent = [headers, ...rows]
        .map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(","))
        .join("\n");

    const region = document.getElementById("region-select").value || "az-mapping";
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const a = document.createElement("a");
    a.download = `az-mapping-${region}.csv`;
    a.href = URL.createObjectURL(blob);
    a.click();
    URL.revokeObjectURL(a.href);
}

// ---------------------------------------------------------------------------
// SKU Section
// ---------------------------------------------------------------------------

function toggleSkuSection() {
    const section = document.querySelector(".sku-section");
    section.classList.toggle("collapsed");
}

function getPhysicalZoneMap(subscriptionId) {
    const map = {};
    if (!lastMappingData) return map;
    const subMapping = lastMappingData.find(d => d.subscriptionId === subscriptionId);
    if (subMapping && subMapping.mappings) {
        subMapping.mappings.forEach(m => { map[m.logicalZone] = m.physicalZone; });
    }
    return map;
}

async function loadSkus() {
    const region = document.getElementById("region-select").value;
    const tenant = document.getElementById("tenant-select").value;
    const loadBtn = document.querySelector('.sku-controls .secondary-btn');
    
    if (!region) {
        showError("Please select a region first.");
        return;
    }
    
    // Check if subscriptions are selected
    if (selectedSubscriptions.size === 0) {
        showError("Please select at least one subscription.");
        return;
    }
    
    // Ensure zone mappings are loaded first to get physical zone names
    if (!lastMappingData) {
        showError("Please load zone mappings first by clicking 'Load Mappings' button.");
        return;
    }
    
    // Use selected SKU subscription (or first if only one selected)
    const subscriptionId = selectedSkuSubscription || [...selectedSubscriptions][0];
    if (!subscriptionId) {
        showError("Please select a subscription for SKU loading.");
        return;
    }
    
    const subscriptionName = getSubName(subscriptionId);
    
    // Disable button while loading
    if (loadBtn) loadBtn.disabled = true;
    
    document.getElementById("sku-loading").style.display = "flex";
    document.getElementById("sku-empty").style.display = "none";
    document.getElementById("sku-table-container").style.display = "none";
    
    try {
        const params = new URLSearchParams({ region, subscriptionId });
        if (tenant) params.append("tenantId", tenant);
        const includePrices = document.getElementById("sku-include-prices")?.checked;
        if (includePrices) {
            params.append("includePrices", "true");
            const currency = document.getElementById("sku-currency")?.value || "USD";
            params.append("currencyCode", currency);
        }
        
        const data = await apiFetch(`/api/skus?${params}`);
        
        if (data.error) {
            throw new Error(data.error);
        }
        
        lastSkuData = data;
        renderSkuTable(data, subscriptionName);
        
    } catch (err) {
        showError(`Failed to fetch SKUs: ${err.message}`);
        document.getElementById("sku-empty").style.display = "block";
    } finally {
        document.getElementById("sku-loading").style.display = "none";
        if (loadBtn) loadBtn.disabled = false;
    }
}

// ---------------------------------------------------------------------------
// Spot Score Modal – per-SKU on-demand lookup with caching
// ---------------------------------------------------------------------------
let _spotModalSku = null;

function openSpotModal(skuName) {
    _spotModalSku = skuName;
    document.getElementById("spot-modal-sku").textContent = skuName;
    document.getElementById("spot-modal-instances").value = "1";
    document.getElementById("spot-modal-loading").style.display = "none";
    document.getElementById("spot-modal-result").style.display = "none";
    document.getElementById("spot-modal").style.display = "flex";
    const input = document.getElementById("spot-modal-instances");
    input.focus();
    input.select();
}

function closeSpotModal(event) {
    // If called from overlay click, only close if clicking the overlay itself
    if (event && event.target !== document.getElementById("spot-modal")) return;
    document.getElementById("spot-modal").style.display = "none";
    _spotModalSku = null;
}

function _onSpotModalKeydown(e) {
    const modal = document.getElementById("spot-modal");
    if (modal.style.display === "none") return;
    if (e.key === "Escape") { closeSpotModal(); }
    else if (e.key === "Enter") { e.preventDefault(); confirmSpotScore(); }
}
document.addEventListener("keydown", _onSpotModalKeydown);

async function confirmSpotScore() {
    const skuName = _spotModalSku;
    if (!skuName) return;

    const region = document.getElementById("region-select").value;
    const tenant = document.getElementById("tenant-select").value;
    const subscriptionId = selectedSkuSubscription || [...selectedSubscriptions][0];
    if (!subscriptionId || !region) return;

    const instanceCount = parseInt(document.getElementById("spot-modal-instances").value, 10) || 1;

    document.getElementById("spot-modal-loading").style.display = "flex";
    document.getElementById("spot-modal-result").style.display = "none";

    try {
        const payload = { region, subscriptionId, skus: [skuName], instanceCount };
        if (tenant) payload.tenantId = tenant;

        const result = await apiPost("/api/spot-scores", payload);

        // Accumulate into cache
        if (!lastSpotScores) {
            lastSpotScores = { scores: {}, errors: [] };
        }
        if (result.scores) {
            for (const [sku, zoneScores] of Object.entries(result.scores)) {
                lastSpotScores.scores[sku] = { ...(lastSpotScores.scores[sku] || {}), ...zoneScores };
            }
        }
        if (result.errors && result.errors.length > 0) {
            lastSpotScores.errors.push(...result.errors);
        }

        // Show result in modal – per-zone scores
        const zoneScores = result.scores?.[skuName] || {};
        const resultEl = document.getElementById("spot-modal-result");
        const zones = Object.keys(zoneScores).sort();
        if (zones.length > 0) {
            resultEl.innerHTML = '<div class="spot-modal-grid">' + zones.map(z => {
                const s = zoneScores[z] || "Unknown";
                const cls = "spot-badge spot-" + s.toLowerCase();
                return `<span class="spot-modal-zone">Z${escapeHtml(z)}</span><span class="${cls}">${escapeHtml(s)}</span>`;
            }).join("") + '</div>';
        } else {
            resultEl.innerHTML = `<span class="spot-badge spot-unknown">Unknown</span>`;
        }
        resultEl.style.display = "block";

        // Re-render table to update the cell
        const subscriptionName = getSubName(subscriptionId);
        renderSkuTable(lastSkuData, subscriptionName);

        if (result.errors && result.errors.length > 0) {
            showError("Spot score error: " + result.errors.join("; "));
        }
    } catch (err) {
        showError("Failed to fetch Spot Score: " + err.message);
    } finally {
        document.getElementById("spot-modal-loading").style.display = "none";
    }
}

function skuSortIndicator(col) {
    if (skuSortColumn !== col) return "";
    return skuSortAsc ? " ▲" : " ▼";
}

function onSkuSort(col, subscriptionName) {
    if (skuSortColumn === col) {
        skuSortAsc = !skuSortAsc;
    } else {
        skuSortColumn = col;
        skuSortAsc = true;
    }
    renderSkuTable(lastSkuData, subscriptionName);
}

function skuSortValue(sku, col) {
    switch (col) {
        case "name":     return (sku.name || "").toLowerCase();
        case "family":   return (sku.family || "").toLowerCase();
        case "vCPUs":    return parseFloat(sku.capabilities.vCPUs || "0") || 0;
        case "memory":   return parseFloat(sku.capabilities.MemoryGB || "0") || 0;
        case "qLimit":   { const q = sku.quota || {}; return q.limit != null ? q.limit : -1; }
        case "qUsed":    { const q = sku.quota || {}; return q.used  != null ? q.used  : -1; }
        case "qRemain":  { const q = sku.quota || {}; return q.remaining != null ? q.remaining : -1; }
        case "spot":     {
            const zoneScores = (lastSpotScores?.scores || {})[sku.name] || {};
            const vals = Object.values(zoneScores).map(s => {
                const l = s.toLowerCase();
                if (l === "high") return 3;
                if (l === "medium") return 2;
                if (l === "low") return 1;
                return 0;
            });
            return vals.length > 0 ? Math.max(...vals) : 0;
        }
        case "paygo":    { const p = sku.pricing || {}; return p.paygo != null ? p.paygo : Infinity; }
        case "spotPrice":{ const p = sku.pricing || {}; return p.spot  != null ? p.spot  : Infinity; }
        default:         return 0;
    }
}

function renderSkuTable(skus, subscriptionName) {
    const container = document.getElementById("sku-table-container");
    const filterInput = document.getElementById("sku-filter");
    
    if (!skus || skus.length === 0) {
        container.innerHTML = "<p class='empty-state-small'>No SKUs found for this region.</p>";
        container.style.display = "block";
        return;
    }
    
    // Apply filter
    const filterText = filterInput.value.toLowerCase();
    let filteredSkus = skus.filter(sku => 
        !filterText || sku.name.toLowerCase().includes(filterText)
    );
    
    if (filteredSkus.length === 0) {
        container.innerHTML = "<p class='empty-state-small'>No SKUs match the filter.</p>";
        container.style.display = "block";
        return;
    }
    
    // Apply sorting
    if (skuSortColumn) {
        filteredSkus = [...filteredSkus].sort((a, b) => {
            const va = skuSortValue(a, skuSortColumn);
            const vb = skuSortValue(b, skuSortColumn);
            let cmp = 0;
            if (typeof va === "string") cmp = va.localeCompare(vb);
            else cmp = va - vb;
            return skuSortAsc ? cmp : -cmp;
        });
    }
    
    // Get physical zone mappings using helper function
    const subscriptionId = selectedSkuSubscription || [...selectedSubscriptions][0];
    const physicalZoneMap = getPhysicalZoneMap(subscriptionId);
    
    // Determine all logical zones from SKUs
    const allLogicalZones = [...new Set(skus.flatMap(s => s.zones))].sort();
    
    // Map to physical zones
    const physicalZones = allLogicalZones.map(lz => physicalZoneMap[lz] || `Zone ${lz}`);
    
    // Escaped subscription name for onclick attribute
    const escapedSubForAttr = subscriptionName.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    
    // Build table with subscription context
    let html = `<div style="margin-bottom: 0.75rem; padding: 0.5rem; background: var(--azure-light); border-radius: 6px; font-size: 0.875rem;">
        <strong>Subscription:</strong> ${escapeHtml(subscriptionName)}
    </div>`;
    html += '<table class="sku-table">';
    html += "<thead><tr>";
    
    const sortCols = [
        ["name",    "SKU Name"],
        ["family",  "Family"],
        ["vCPUs",   "vCPUs"],
        ["memory",  "Memory (GB)"],
        ["qLimit",  "Quota Limit"],
        ["qUsed",   "Quota Used"],
        ["qRemain", "Quota Remaining"],
        ["spot",    "Spot Score"],
    ];
    // Conditionally add price columns when pricing data is present
    const hasPricing = filteredSkus.some(s => s.pricing);
    if (hasPricing) {
        const currency = filteredSkus.find(s => s.pricing)?.pricing?.currency || "USD";
        sortCols.push(["paygo", `PAYGO ${currency}/h`]);
        sortCols.push(["spotPrice", `Spot ${currency}/h`]);
    }
    sortCols.forEach(([col, label]) => {
        const active = skuSortColumn === col ? ' class="sort-active"' : '';
        html += `<th${active} style="cursor:pointer" onclick="onSkuSort('${col}','${escapedSubForAttr}')">${label}${skuSortIndicator(col)}</th>`;
    });
    
    // Render headers with zone number and physical zone name (with line break)
    allLogicalZones.forEach((lz, index) => {
        const pz = physicalZones[index];
        html += `<th>Zone ${escapeHtml(lz)}<br>${escapeHtml(pz)}</th>`;
    });
    
    html += "</tr></thead><tbody>";
    
    filteredSkus.forEach(sku => {
        const escapedSku = sku.name.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        html += "<tr>";
        html += `<td><button type="button" class="sku-name-btn" onclick="openPricingModal('${escapedSku}')">${escapeHtml(sku.name)}</button></td>`;
        html += `<td>${escapeHtml(sku.family || "—")}</td>`;
        html += `<td>${escapeHtml(sku.capabilities.vCPUs || "—")}</td>`;
        html += `<td>${escapeHtml(sku.capabilities.MemoryGB || "—")}</td>`;
        
        const quota = sku.quota || {};
        html += `<td>${quota.limit != null ? quota.limit : "—"}</td>`;
        html += `<td>${quota.used != null ? quota.used : "—"}</td>`;
        html += `<td>${quota.remaining != null ? quota.remaining : "—"}</td>`;
        
        // Spot Placement Score – clickable, per-zone
        const spotZoneScores = (lastSpotScores?.scores || {})[sku.name] || {};
        const spotZones = Object.keys(spotZoneScores).sort();
        if (spotZones.length > 0) {
            const badges = spotZones.map(z => {
                const s = spotZoneScores[z] || "Unknown";
                const cls = "spot-badge spot-" + s.toLowerCase();
                return `<span class="spot-zone-label">Z${escapeHtml(z)}</span><span class="${cls}">${escapeHtml(s)}</span>`;
            }).join(" ");
            html += `<td><button type="button" class="spot-cell-btn has-score" onclick="openSpotModal('${escapedSku}')" title="Click to refresh score">${badges}</button></td>`;
        } else {
            html += `<td><button type="button" class="spot-cell-btn" onclick="openSpotModal('${escapedSku}')" title="Get Spot Placement Score">Score?</button></td>`;
        }
        
        // Price columns (only if pricing data is present)
        if (hasPricing) {
            const pricing = sku.pricing || {};
            html += `<td class="price-cell">${pricing.paygo != null ? pricing.paygo.toFixed(4) : '<span title="No price available">—</span>'}</td>`;
            html += `<td class="price-cell">${pricing.spot != null ? pricing.spot.toFixed(4) : '<span title="No price available">—</span>'}</td>`;
        }
        
        allLogicalZones.forEach(logicalZone => {
            const isAvailable = sku.zones.includes(logicalZone);
            const isRestricted = sku.restrictions.includes(logicalZone);
            
            if (isRestricted) {
                html += '<td class="zone-restricted" title="Restricted: SKU not available in this zone" aria-label="Restricted">⚠</td>';
            } else if (isAvailable) {
                html += '<td class="zone-available" title="Available" aria-label="Available">✓</td>';
            } else {
                html += '<td class="zone-unavailable" title="Not available" aria-label="Not available">—</td>';
            }
        });
        
        html += "</tr>";
    });
    
    html += "</tbody></table>";
    container.innerHTML = html;
    container.style.display = "block";
}

function exportSkuCSV() {
    if (!lastSkuData || lastSkuData.length === 0) {
        showError("No SKU data to export.");
        return;
    }
    
    // Get physical zone mappings using helper function
    const subscriptionId = selectedSkuSubscription || [...selectedSubscriptions][0];
    const physicalZoneMap = getPhysicalZoneMap(subscriptionId);
    
    const allLogicalZones = [...new Set(lastSkuData.flatMap(s => s.zones))].sort();
    const physicalZones = allLogicalZones.map(lz => physicalZoneMap[lz] || `Zone ${lz}`);
    
    // Header row with zone number and physical zone information
    const zoneHeaders = allLogicalZones.map((lz, index) => 
        `Zone ${lz}\n${physicalZones[index]}`
    );
    const hasPricing = lastSkuData.some(s => s.pricing);
    const priceCurrency = lastSkuData.find(s => s.pricing)?.pricing?.currency || "USD";
    const priceHeaders = hasPricing
        ? [`PAYGO ${priceCurrency}/h`, `Spot ${priceCurrency}/h`]
        : [];
    const headers = ["SKU Name", "Family", "vCPUs", "Memory (GB)",
        "Quota Limit", "Quota Used", "Quota Remaining",
        "Spot Score",
        ...priceHeaders,
        ...zoneHeaders];
    
    // Data rows
    const rows = lastSkuData.map(sku => {
        const quota = sku.quota || {};
        const zoneCols = allLogicalZones.map(logicalZone => {
            const isAvailable = sku.zones.includes(logicalZone);
            const isRestricted = sku.restrictions.includes(logicalZone);
            
            if (isRestricted) return "Restricted";
            if (isAvailable) return "Available";
            return "Unavailable";
        });
        
        return [
            sku.name,
            sku.family || "",
            sku.capabilities.vCPUs || "",
            sku.capabilities.MemoryGB || "",
            quota.limit != null ? quota.limit : "",
            quota.used != null ? quota.used : "",
            quota.remaining != null ? quota.remaining : "",
            Object.entries((lastSpotScores?.scores || {})[sku.name] || {}).sort(([a],[b]) => a.localeCompare(b)).map(([z, s]) => `Z${z}:${s}`).join(" ") || "",
            ...(hasPricing ? [
                sku.pricing?.paygo != null ? sku.pricing.paygo : "",
                sku.pricing?.spot != null ? sku.pricing.spot : "",
            ] : []),
            ...zoneCols
        ];
    });
    
    // Build CSV string
    const csvContent = [headers, ...rows]
        .map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(","))
        .join("\n");
    
    const region = document.getElementById("region-select").value || "skus";
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const a = document.createElement("a");
    a.download = `az-skus-${region}.csv`;
    a.href = URL.createObjectURL(blob);
    a.click();
    URL.revokeObjectURL(a.href);
}

// ---------------------------------------------------------------------------
// SKU Pricing Detail Modal
// ---------------------------------------------------------------------------
let _pricingModalSku = null;

function openPricingModal(skuName) {
    _pricingModalSku = skuName;
    document.getElementById("pricing-modal-sku").textContent = skuName;
    document.getElementById("pricing-modal-loading").style.display = "none";
    document.getElementById("pricing-modal-content").style.display = "none";
    document.getElementById("pricing-modal").style.display = "flex";
    fetchPricingDetail();
}

function closePricingModal(event) {
    if (event && event.target !== document.getElementById("pricing-modal")) return;
    document.getElementById("pricing-modal").style.display = "none";
    _pricingModalSku = null;
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

    document.getElementById("pricing-modal-loading").style.display = "flex";
    document.getElementById("pricing-modal-content").style.display = "none";

    try {
        const params = new URLSearchParams({ region, skuName, currencyCode: currency });
        const data = await apiFetch(`/api/sku-pricing?${params}`);
        renderPricingDetail(data);
    } catch (err) {
        const content = document.getElementById("pricing-modal-content");
        content.innerHTML = `<p style="color:var(--danger);font-size:0.85rem;">Failed to load pricing: ${escapeHtml(err.message)}</p>`;
        content.style.display = "block";
    } finally {
        document.getElementById("pricing-modal-loading").style.display = "none";
    }
}

function renderPricingDetail(data) {
    const content = document.getElementById("pricing-modal-content");
    const currency = data.currency || "USD";
    const HOURS_PER_MONTH = 730;

    const rows = [
        { label: "Pay-As-You-Go",         hourly: data.paygo },
        { label: "Spot",                   hourly: data.spot },
        { label: "Reserved Instance 1Y",   hourly: data.ri_1y },
        { label: "Reserved Instance 3Y",   hourly: data.ri_3y },
        { label: "Savings Plan 1Y",        hourly: data.sp_1y },
        { label: "Savings Plan 3Y",        hourly: data.sp_3y },
    ];

    let html = '<table class="pricing-detail-table">';
    html += `<thead><tr><th>Type</th><th>${escapeHtml(currency)}/hour</th><th>${escapeHtml(currency)}/month</th></tr></thead>`;
    html += "<tbody>";
    rows.forEach(r => {
        const hourStr = r.hourly != null ? r.hourly.toFixed(4) : "—";
        const monthStr = r.hourly != null ? (r.hourly * HOURS_PER_MONTH).toFixed(2) : "—";
        html += `<tr><td>${escapeHtml(r.label)}</td><td class="price-cell">${hourStr}</td><td class="price-cell">${monthStr}</td></tr>`;
    });
    html += "</tbody></table>";

    content.innerHTML = html;
    content.style.display = "block";
}

document.addEventListener("keydown", (e) => {
    const modal = document.getElementById("pricing-modal");
    if (modal && modal.style.display !== "none" && e.key === "Escape") {
        closePricingModal();
    }
});
