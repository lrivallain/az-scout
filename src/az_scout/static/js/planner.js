/* ===================================================================
   Azure Scout – Deployment Planner Tab
   Requires: app.js (globals: subscriptions, apiFetch, apiPost,
             tenantQS, escapeHtml, formatNum, getSubName, showError,
             hideError, showPanel, downloadCSV)
   =================================================================== */

// ---------------------------------------------------------------------------
// Planner tab state
// ---------------------------------------------------------------------------
let plannerSubscriptionId = null;           // single selected subscription ID
let plannerZoneMappings = null;             // zone mappings fetched independently for planner
let lastSkuData = null;                     // cached SKU list
let lastSpotScores = null;                  // {scores: {sku: {zone: label}}, errors: []}
let _skuDataTable = null;                   // Simple-DataTables instance
let _skuFilterState = {};                   // {headerText: filterValue} – persists across re-renders

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
// Include Spot in Deployment Confidence (called from confidence controls)
// ---------------------------------------------------------------------------
async function includeSpotInConfidence() {
    const skuName = _pricingModalSku;
    if (!skuName) return;
    const region = document.getElementById("region-select").value;
    const tenant = document.getElementById("tenant-select").value;
    const subscriptionId = plannerSubscriptionId;
    if (!subscriptionId || !region) return;

    const btn = document.querySelector('.confidence-controls .btn-outline-primary');
    const origHtml = btn?.innerHTML;
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>Calculating…';
    }
    const instanceCount = parseInt(document.getElementById("confidence-instance-count")?.value, 10) || 1;
    const currency = document.getElementById("planner-currency")?.value || "USD";

    try {
        const payload = {
            subscriptionId,
            region,
            currencyCode: currency,
            preferSpot: true,
            instanceCount,
            skus: [skuName],
            includeSignals: false,
            includeProvenance: true,
        };
        if (tenant) payload.tenantId = tenant;

        const result = await apiPost("/api/deployment-confidence", payload);

        let spotIncluded = false;
        if (result.results) {
            for (const r of result.results) {
                const sku = (lastSkuData || []).find(s => s.name === r.sku);
                if (sku && r.deploymentConfidence) {
                    sku.confidence = r.deploymentConfidence;
                    if (r.deploymentConfidence.scoreType === "basic+spot") spotIncluded = true;
                }
            }
        }

        // Show feedback if spot could not be included
        if (!spotIncluded) {
            const reason = (result.warnings || []).join("; ") || "Spot Placement Scores unavailable or restricted for this SKU.";
            showError("planner-error", reason);
        }

        // Re-render table and region summary
        if (lastSkuData) {
            renderRegionSummary(lastSkuData);
            renderSkuTable(lastSkuData);
        }

        // Re-render modal in place, keeping open accordions
        if (_lastPricingData) {
            const content = document.getElementById("pricing-modal-content");
            const openIds = [...content.querySelectorAll('.accordion-collapse.show')].map(el => el.id).filter(Boolean);
            renderPricingDetail(_lastPricingData, openIds);
        }
    } catch (err) {
        showError("planner-error", "Failed to include Spot in confidence: " + err.message);
        if (btn) { btn.disabled = false; btn.innerHTML = origHtml; }
    }
}

// ---------------------------------------------------------------------------
// Reset to Basic confidence (exclude Spot)
// ---------------------------------------------------------------------------
async function resetToBasicConfidence() {
    const skuName = _pricingModalSku;
    if (!skuName) return;
    const region = document.getElementById("region-select").value;
    const tenant = document.getElementById("tenant-select").value;
    const subscriptionId = plannerSubscriptionId;
    if (!subscriptionId || !region) return;

    const btn = document.querySelector('.confidence-controls .btn-outline-success');
    const origHtml = btn?.innerHTML;
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>Recalculating…';
    }
    const currency = document.getElementById("planner-currency")?.value || "USD";
    const instanceCount = parseInt(document.getElementById("confidence-instance-count")?.value, 10) || 1;

    try {
        const payload = {
            subscriptionId,
            region,
            currencyCode: currency,
            preferSpot: false,
            instanceCount,
            skus: [skuName],
            includeSignals: false,
            includeProvenance: true,
        };
        if (tenant) payload.tenantId = tenant;

        const result = await apiPost("/api/deployment-confidence", payload);
        if (result.results) {
            for (const r of result.results) {
                const sku = (lastSkuData || []).find(s => s.name === r.sku);
                if (sku && r.deploymentConfidence) {
                    sku.confidence = r.deploymentConfidence;
                }
            }
        }

        // Re-render table and region summary
        if (lastSkuData) {
            renderRegionSummary(lastSkuData);
            renderSkuTable(lastSkuData);
        }

        // Re-render modal in place, keeping open accordions
        if (_lastPricingData) {
            const content = document.getElementById("pricing-modal-content");
            const openIds = [...content.querySelectorAll('.accordion-collapse.show')].map(el => el.id).filter(Boolean);
            renderPricingDetail(_lastPricingData, openIds);
        }
    } catch (err) {
        showError("planner-error", "Failed to reset confidence: " + err.message);
        if (btn) { btn.disabled = false; btn.innerHTML = origHtml; }
    }
}

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

    // Build sections in order: Confidence → VM Profile → Zone Availability → Quota → Pricing
    let html = "";
    if (confSku?.confidence) html += renderConfidenceBreakdown(confSku.confidence);
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
    const isBlocked = conf.scoreType === "blocked";
    const isBasicWithSpot = conf.scoreType === "basic+spot";
    const titleText = isBlocked ? "Deployment Confidence (Blocked)"
        : isBasicWithSpot ? "Deployment Confidence (with Spot)"
        : "Basic Deployment Confidence";
    const tooltipText = isBlocked
        ? "Deployment is blocked due to hard constraints (quota or zone availability)."
        : isBasicWithSpot
        ? "Composite score (0\u2013100) including Spot Placement signal."
        : "Composite score (0\u2013100) based on quota, zones, restrictions and price pressure. Spot excluded by default.";
    let html = '<div class="confidence-section">';
    html += `<h4 class="confidence-title">${escapeHtml(titleText)} <span class="confidence-badge confidence-${lbl}">${conf.score} ${escapeHtml(conf.label || "")}</span> <i class="bi bi-info-circle text-body-secondary confidence-info-icon" data-bs-toggle="tooltip" data-bs-title="${escapeHtml(tooltipText)}"></i></h4>`;
    // Knockout reasons (hard blockers)
    const knockoutReasons = conf.knockoutReasons || [];
    if (knockoutReasons.length) {
        html += '<div class="alert alert-danger py-1 px-2 mb-2 small"><i class="bi bi-x-octagon me-1"></i><strong>Blocked:</strong><ul class="mb-0 ps-3">';
        knockoutReasons.forEach(r => { html += `<li>${escapeHtml(r)}</li>`; });
        html += '</ul></div>';
    }
    const components = conf.breakdown?.components || conf.breakdown || [];
    const usedComponents = components.filter(c => c.status === "used");
    if (usedComponents.length) {
        html += '<table class="table table-sm confidence-breakdown-table"><thead><tr><th>Signal</th><th>Score</th><th>Weight</th><th>Contribution</th></tr></thead><tbody>';
        const signalLabels = { quotaPressure: "Quota Pressure", spot: "Spot Placement", zones: "Zone Breadth", restrictionDensity: "Restriction Density", pricePressure: "Price Pressure" };
        const signalDescriptions = {
            quotaPressure: "Non-linear quota usage pressure. Penalises heavily when family usage exceeds 80% or cannot fit a single VM.",
            spot: "Best per-zone Spot Placement Score (Azure API). Higher means better spot allocation likelihood.",
            zones: "Number of available (non-restricted) availability zones where the SKU is offered (out of 3).",
            restrictionDensity: "Fraction of zones not restricted. Partial restrictions reduce the score proportionally.",
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
    // Separate spot from other missing signals
    const allMissing = conf.missingSignals || conf.missing || [];
    const spotMissing = allMissing.includes("spot");
    const otherMissing = allMissing.filter(m => m !== "spot");
    if (otherMissing.length) {
        const signalLabels = { quotaPressure: "Quota Pressure", zones: "Zone Breadth", restrictionDensity: "Restriction Density", pricePressure: "Price Pressure" };
        const names = otherMissing.map(m => signalLabels[m] || m).join(", ");
        html += `<p class="confidence-missing"><i class="bi bi-exclamation-circle"></i> Missing signals (excluded from score): ${escapeHtml(names)}</p>`;
    }
    // Scoring controls: recalculate basic or with spot
    html += '<div class="confidence-controls mt-2 pt-2 border-top">';
    html += '<div class="d-flex align-items-center gap-2 flex-wrap">';
    html += '<label class="text-body-secondary small mb-0" for="confidence-instance-count">Instances:</label>';
    html += '<input type="number" id="confidence-instance-count" class="form-control form-control-sm" value="1" min="1" max="1000" style="width:70px;" title="Number of instances to deploy (affects quota pressure)">';
    html += '<button class="btn btn-sm btn-outline-success" onclick="resetToBasicConfidence()"><i class="bi bi-arrow-counterclockwise me-1"></i>Recalculate</button>';
    html += '<button class="btn btn-sm btn-outline-primary" onclick="includeSpotInConfidence()"><i class="bi bi-lightning-charge me-1"></i>Recalculate with Spot</button>';
    html += '</div></div>';
    if (conf.disclaimers?.length) {
        html += '<p class="confidence-disclaimer text-body-secondary small fst-italic mt-1 mb-0">' + escapeHtml(conf.disclaimers[0]) + ' <a href="https://github.com/lrivallain/az-scout/blob/main/docs/SCORING.md" target="_blank" rel="noopener" class="text-body-secondary">Learn more</a> about scoring methodology.</p>';
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
        html += `<div class="region-score-value"><span class="confidence-badge confidence-${readinessLbl}" data-bs-toggle="tooltip" data-bs-title="Average basic deployment confidence across ${scores.total} SKUs. Reflects quota, zone coverage, restrictions and price pressure (spot excluded)."><i class="bi ${icons[readinessLbl] || 'bi-shield'}"></i> ${scores.readiness}</span></div>`;
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
            html += `<td data-sort="${conf.score}"><span class="confidence-badge confidence-${lbl}" data-bs-toggle="tooltip" data-bs-title="Basic deployment confidence: ${conf.score}/100 (${escapeHtml(conf.label || '')}). Spot excluded."><i class="bi ${icon}"></i> ${conf.score}</span></td>`;
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
    // Columns: SKU(0) Family(1) vCPUs(2) Mem(3) QLimit(4) QUsed(5) QRem(6) [Spot(7)] Conf(7|8) [PAYGO Spot]
    const confCol = showSpot ? 8 : 7;
    const colConfig = [
        { select: [2, 3, 4, 5, 6], type: "number" },   // vCPUs, Memory, Quota
        { select: confCol, type: "number" },              // Confidence (uses data-sort attr)
    ];
    let nextCol = confCol + 1;
    if (showPricing) {
        colConfig.push({ select: [nextCol, nextCol + 1], type: "number" });
        nextCol += 2;
    }

    // Init Simple-DataTables
    const tableEl = document.getElementById("sku-datatable");

    // Build per-column header filter config
    // Only text-filterable columns get an input; Zone columns are excluded
    const filterableCols = [];
    for (let i = 0; i <= confCol; i++) filterableCols.push(i);
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
    const numericCols = new Set([2, 3, 4, 5, 6, confCol]);
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
