/* ===================================================================
   Shared SKU detail renderers — available to all plugins.
   Namespace: window.azScout.components

   Provides accordion-panel renderers for the SKU detail modal:
   VM Profile, Zone Availability, Quota, Confidence breakdown, Pricing.

   Requires: escapeHtml, formatNum (from app.js)
             azScout.components.scoreLabel, renderSpotBadge (from sku-badges.js)
   =================================================================== */
window.azScout = window.azScout || {};
window.azScout.components = window.azScout.components || {};

((C) => {
    // --- shared inner helpers ---
    function _row(label, value) {
        return '<div class="vm-profile-row"><span class="vm-profile-label">' + escapeHtml(label) + '</span><span>' + value + '</span></div>';
    }
    function _val(v, suffix) {
        if (v == null) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        return escapeHtml(String(v) + (suffix || ""));
    }
    function _badge(val, trueLabel, falseLabel) {
        if (val === true) return '<span class="vm-badge vm-badge-yes">' + escapeHtml(trueLabel || "Yes") + '</span>';
        if (val === false) return '<span class="vm-badge vm-badge-no">' + escapeHtml(falseLabel || "No") + '</span>';
        return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
    }
    function _bytesToMBs(v) {
        if (v == null) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        return escapeHtml((Number(v) / (1024 * 1024)).toFixed(0) + " MB/s");
    }
    function _bytesToGB(v) {
        if (v == null) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        return escapeHtml((Number(v) / (1024 * 1024 * 1024)).toFixed(0) + " GB");
    }
    function _mbpsToGbps(v) {
        if (v == null) return '<span class="vm-badge vm-badge-unknown">\u2014</span>';
        const gbps = Number(v) / 1000;
        return escapeHtml(gbps >= 1 ? gbps.toFixed(1) + " Gbps" : v + " Mbps");
    }
    function _accordion(id, icon, title, body) {
        return '<div class="accordion mt-3" id="' + id + 'Accordion">' +
            '<div class="accordion-item">' +
            '<h2 class="accordion-header">' +
            '<button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#' + id + 'Panel" aria-expanded="false">' +
            '<i class="bi ' + icon + ' me-2"></i>' + escapeHtml(title) +
            '</button></h2>' +
            '<div id="' + id + 'Panel" class="accordion-collapse collapse">' +
            '<div class="accordion-body p-2">' + body + '</div></div></div></div>';
    }

    /**
     * Render VM Profile accordion panel.
     * @param {object} profile - SKU profile with capabilities dict.
     * @returns {string} HTML string.
     */
    C.renderVmProfile = (profile) => {
        const caps = profile.capabilities || {};
        let html = '<div class="vm-profile-section">';
        html += '<h4 class="vm-profile-title">VM Profile</h4>';
        html += '<div class="vm-profile-grid">';

        html += '<div class="vm-profile-card"><div class="vm-profile-card-title">Compute</div>';
        html += _row("vCPUs", _val(caps.vCPUs));
        html += _row("Memory", _val(caps.MemoryGB, " GB"));
        html += _row("Architecture", _val(caps.CpuArchitectureType));
        html += _row("GPUs", _val(caps.GPUs || caps.GpuCount));
        html += _row("HyperV Gen.", _val(caps.HyperVGenerations));
        html += _row("Encryption at Host", _badge(caps.EncryptionAtHostSupported));
        html += _row("Confidential", _val(caps.ConfidentialComputingType || null));
        html += '</div>';

        html += '<div class="vm-profile-card"><div class="vm-profile-card-title">Storage</div>';
        html += _row("Premium IO", _badge(caps.PremiumIO));
        html += _row("Ultra SSD", _badge(caps.UltraSSDAvailable));
        html += _row("Ephemeral OS Disk", _badge(caps.EphemeralOSDiskSupported));
        html += _row("Max Data Disks", _val(caps.MaxDataDiskCount));
        html += _row("Uncached Disk IOPS", _val(caps.UncachedDiskIOPS));
        html += _row("Uncached Disk BW", _bytesToMBs(caps.UncachedDiskBytesPerSecond));
        html += _row("Cached Disk Size", _bytesToGB(caps.CachedDiskBytes));
        html += _row("Write Accelerator", _val(caps.MaxWriteAcceleratorDisksAllowed));
        html += _row("Temp Disk", _val(caps.TempDiskSizeInGiB, " GiB"));
        html += '</div>';

        html += '<div class="vm-profile-card"><div class="vm-profile-card-title">Network</div>';
        html += _row("Accelerated Net.", _badge(caps.AcceleratedNetworkingEnabled));
        html += _row("Max NICs", _val(caps.MaxNetworkInterfaces || caps.MaximumNetworkInterfaces));
        html += _row("Max Bandwidth", _mbpsToGbps(caps.MaxBandwidthMbps));
        html += _row("RDMA", _badge(caps.RdmaEnabled));
        html += '</div>';

        html += '</div></div>';
        return html;
    };

    /**
     * Render Zone Availability accordion panel.
     * @param {object} profile - SKU profile with zones and restrictions.
     * @param {object} [confidence] - Confidence object with breakdown.
     * @param {object} [opts] - Optional settings.
     * @param {object} [opts.physicalZoneMap] - {logicalZone: physicalName} map.
     * @param {object} [opts.spotZoneScores] - {zone: scoreLabel} map.
     * @returns {string} HTML accordion string.
     */
    C.renderZoneAvailability = (profile, confidence, opts) => {
        const o = opts || {};
        const zones = profile.zones || [];
        const restrictions = profile.restrictions || [];
        const components = confidence?.breakdown?.components || [];
        const zoneSignal = components.find((b) => b.name === "zones");
        const zoneScore = zoneSignal?.score100;
        const spotSignal = components.find((b) => b.name === "spot");
        const spotScore = spotSignal?.score100;
        const physicalZoneMap = o.physicalZoneMap || {};
        const spotZoneScores = o.spotZoneScores || {};

        const reasonLabels = {
            NotAvailableForSubscription: "Not available for this subscription",
            QuotaId: "Subscription offer type not eligible",
        };

        const hasLocationRestriction = restrictions.some((r) => r.type === "Location");
        const zoneRestrictionZones = new Set(restrictions.filter((r) => r.type === "Zone").flatMap((r) => r.zones || []));
        const allZoneIds = [...new Set(["1", "2", "3", ...zones])].sort();
        const availableCount = zones.filter((z) => !zoneRestrictionZones.has(z) && !hasLocationRestriction).length;

        let body = '<div class="vm-profile-grid">';

        // Zone status card
        body += '<div class="vm-profile-card"><div class="vm-profile-card-title">Zones</div>';
        if (hasLocationRestriction) body += _row("Region", '<span class="vm-badge vm-badge-no">Restricted</span>');
        body += _row("Available", _val(availableCount + " / 3"));
        allZoneIds.forEach((z) => {
            const pz = physicalZoneMap[z];
            const pzTip = pz ? ' data-bs-toggle="tooltip" data-bs-title="' + escapeHtml(pz) + '"' : "";
            const offered = zones.includes(z);
            const restricted = zoneRestrictionZones.has(z) || hasLocationRestriction;
            if (!offered && !restricted) body += '<div class="vm-profile-row"><span class="vm-profile-label"' + pzTip + '>' + escapeHtml("Zone " + z) + '</span><span class="vm-badge vm-badge-unknown">Not offered</span></div>';
            else if (restricted) body += '<div class="vm-profile-row"><span class="vm-profile-label"' + pzTip + '>' + escapeHtml("Zone " + z) + '</span><span class="vm-badge vm-badge-no">Restricted</span></div>';
            else body += '<div class="vm-profile-row"><span class="vm-profile-label"' + pzTip + '>' + escapeHtml("Zone " + z) + '</span><span class="vm-badge vm-badge-yes">Available</span></div>';
        });
        if (zoneScore != null) {
            const lbl = C.scoreLabel(zoneScore).toLowerCase().replace(/\s+/g, "-");
            body += '<div class="vm-profile-row"><span class="vm-profile-label">Breadth Score</span><span class="confidence-badge confidence-' + lbl + '" data-bs-toggle="tooltip" data-bs-title="Zone Breadth signal: ' + availableCount + '/3 zones available.">' + zoneScore + '/100</span></div>';
        }
        body += '</div>';

        // Spot placement card
        const hasSpotData = Object.keys(spotZoneScores).length > 0;
        body += '<div class="vm-profile-card"><div class="vm-profile-card-title">Spot Placement</div>';
        if (!hasSpotData) {
            body += _row("Status", '<span class="vm-badge vm-badge-unknown">No data</span>');
        } else {
            allZoneIds.forEach((z) => {
                const pz = physicalZoneMap[z];
                const pzTip = pz ? ' data-bs-toggle="tooltip" data-bs-title="' + escapeHtml(pz) + '"' : "";
                const s = spotZoneScores[z];
                if (s) body += '<div class="vm-profile-row"><span class="vm-profile-label"' + pzTip + '>' + escapeHtml("Zone " + z) + '</span><span>' + C.renderSpotBadge(s) + '</span></div>';
                else body += '<div class="vm-profile-row"><span class="vm-profile-label"' + pzTip + '>' + escapeHtml("Zone " + z) + '</span><span class="vm-badge vm-badge-unknown">\u2014</span></div>';
            });
            if (spotScore != null) {
                const lbl = C.scoreLabel(spotScore).toLowerCase().replace(/\s+/g, "-");
                body += '<div class="vm-profile-row"><span class="vm-profile-label">Spot Score</span><span class="confidence-badge confidence-' + lbl + '">' + spotScore + '/100</span></div>';
            }
        }
        body += '</div>';

        // Restrictions card
        body += '<div class="vm-profile-card"><div class="vm-profile-card-title">Restrictions</div>';
        if (restrictions.length === 0) {
            body += _row("Status", '<span class="vm-badge vm-badge-yes">None</span>');
        } else {
            restrictions.forEach((r) => {
                const reason = reasonLabels[r.reasonCode] || r.reasonCode || "Unknown reason";
                const scope = r.type === "Location" ? "Entire region" : r.type === "Zone" && r.zones?.length ? "Zone" + (r.zones.length > 1 ? "s" : "") + " " + r.zones.join(", ") : r.type || "Unknown";
                body += '<div class="vm-profile-row vm-profile-row-stacked"><span class="vm-profile-label">' + escapeHtml(scope) + '</span><span class="vm-badge vm-badge-limited vm-badge-block">' + escapeHtml(reason) + '</span></div>';
            });
        }
        body += '</div></div>';

        return _accordion("zone", "bi-pin-map", "Zone Availability", body);
    };

    /**
     * Render Quota accordion panel.
     * @param {object} quota - {limit, used, remaining}.
     * @param {number} vcpus - vCPUs per instance.
     * @param {object} [confidence] - Confidence object with breakdown.
     * @returns {string} HTML accordion string.
     */
    C.renderQuotaPanel = (quota, vcpus, confidence) => {
        const limit = quota.limit;
        const used = quota.used;
        const remaining = quota.remaining;
        const pct = (limit != null && limit > 0) ? Math.round((used / limit) * 100) : null;
        const deployable = (remaining != null && vcpus > 0) ? Math.floor(remaining / vcpus) : null;

        const components = confidence?.breakdown?.components || [];
        const quotaSignal = components.find((b) => b.name === "quota");
        const quotaScore = quotaSignal?.score100;

        let barClass = "bg-success";
        if (pct != null) { if (pct >= 90) barClass = "bg-danger"; else if (pct >= 70) barClass = "bg-warning"; }

        let body = '<div class="vm-profile-grid">';
        body += '<div class="vm-profile-card"><div class="vm-profile-card-title">vCPU Family Quota</div>';
        body += _row("Limit", _val(limit != null ? formatNum(limit, 0) : null));
        body += _row("Used", _val(used != null ? formatNum(used, 0) : null));
        body += _row("Remaining", _val(remaining != null ? formatNum(remaining, 0) : null));
        if (pct != null) {
            body += '<div class="vm-profile-row"><span class="vm-profile-label">Usage</span><span>' + pct + '%</span></div>';
            body += '<div class="progress mt-1" style="height:6px;" role="progressbar" aria-valuenow="' + pct + '" aria-valuemin="0" aria-valuemax="100">';
            body += '<div class="progress-bar ' + barClass + '" style="width:' + pct + '%"></div></div>';
        }
        body += '</div>';

        body += '<div class="vm-profile-card"><div class="vm-profile-card-title">Deployment Headroom</div>';
        body += _row("vCPUs per Instance", vcpus > 0 ? escapeHtml(String(vcpus)) : "\u2014");
        if (deployable != null) {
            const dbadge = deployable === 0 ? '<span class="vm-badge vm-badge-no">' + formatNum(deployable, 0) + '</span>' : deployable <= 5 ? '<span class="vm-badge vm-badge-limited">' + formatNum(deployable, 0) + '</span>' : '<span class="vm-badge vm-badge-yes">' + formatNum(deployable, 0) + '</span>';
            body += '<div class="vm-profile-row"><span class="vm-profile-label">Deployable Instances</span>' + dbadge + '</div>';
        } else {
            body += _row("Deployable Instances", "\u2014");
        }
        if (quotaScore != null) {
            const lbl = C.scoreLabel(quotaScore).toLowerCase().replace(/\s+/g, "-");
            body += '<div class="vm-profile-row"><span class="vm-profile-label">Headroom Score</span><span class="confidence-badge confidence-' + lbl + '" data-bs-toggle="tooltip" data-bs-title="Quota signal score.">' + quotaScore + '/100</span></div>';
        }
        body += '</div></div>';

        return _accordion("quota", "bi-speedometer", "Quota", body);
    };

    /**
     * Render Confidence breakdown accordion panel.
     * @param {object} conf - Confidence object with breakdown, knockout reasons, etc.
     * @returns {string} HTML string (NOT wrapped in accordion — rendered as top-level section).
     */
    C.renderConfidenceBreakdown = (conf) => {
        const lbl = (conf.label || "").toLowerCase().replace(/\s+/g, "-");
        const isBlocked = conf.scoreType === "blocked";
        const isBasicWithSpot = conf.scoreType === "basic+spot";
        const titleText = isBlocked ? "Deployment Confidence (Blocked)" : isBasicWithSpot ? "Deployment Confidence (with Spot)" : "Basic Deployment Confidence";
        const tooltipText = isBlocked
            ? "Deployment is blocked due to hard constraints (quota or zone availability)."
            : isBasicWithSpot
            ? "Composite score (0\u2013100) including Spot Placement signal."
            : "Composite score (0\u2013100) based on quota, zones, restrictions and price pressure. Spot excluded by default.";

        let html = '<div class="confidence-section">';
        html += '<h4 class="confidence-title">' + escapeHtml(titleText) + ' <span class="confidence-badge confidence-' + lbl + '">' + conf.score + ' ' + escapeHtml(conf.label || '') + '</span> <i class="bi bi-info-circle text-body-secondary confidence-info-icon" data-bs-toggle="tooltip" data-bs-title="' + escapeHtml(tooltipText) + '"></i></h4>';

        const knockoutReasons = conf.knockoutReasons || [];
        if (knockoutReasons.length) {
            html += '<div class="alert alert-danger py-1 px-2 mb-2 small"><i class="bi bi-x-octagon me-1"></i><strong>Blocked:</strong><ul class="mb-0 ps-3">';
            knockoutReasons.forEach((r) => { html += '<li>' + escapeHtml(r) + '</li>'; });
            html += '</ul></div>';
        }

        const signalLabels = { quotaPressure: "Quota Pressure", spot: "Spot Placement", zones: "Zone Breadth", restrictionDensity: "Restriction Density", pricePressure: "Price Pressure" };
        const signalDescriptions = {
            quotaPressure: "Non-linear quota usage pressure. Penalises heavily when family usage exceeds 80% or cannot fit a single VM.",
            spot: "Best per-zone Spot Placement Score (Azure API). Higher means better spot allocation likelihood.",
            zones: "Number of available (non-restricted) availability zones where the SKU is offered (out of 3).",
            restrictionDensity: "Fraction of zones not restricted. Partial restrictions reduce the score proportionally.",
            pricePressure: "Spot-to-PAYGO price ratio. A lower ratio indicates better spot savings."
        };

        const components = conf.breakdown?.components || conf.breakdown || [];
        const usedComponents = components.filter((c) => c.status === "used");
        if (usedComponents.length) {
            html += '<table class="table table-sm confidence-breakdown-table"><thead><tr><th>Signal</th><th>Score</th><th>Weight</th><th>Contribution</th></tr></thead><tbody>';
            usedComponents.forEach((b) => {
                const name = b.name || b.signal || "";
                const desc = signalDescriptions[name] || "";
                const score = b.score100 != null ? b.score100 : b.score;
                const contribution = b.contribution != null ? (b.contribution * 100).toFixed(1) : "0.0";
                const infoIcon = desc ? ' <i class="bi bi-info-circle text-body-secondary" data-bs-toggle="tooltip" data-bs-title="' + escapeHtml(desc) + '"></i>' : '';
                html += '<tr><td>' + escapeHtml(signalLabels[name] || name) + infoIcon + '</td><td>' + score + '</td><td>' + (b.weight * 100).toFixed(1) + '%</td><td>' + contribution + '</td></tr>';
            });
            html += '</tbody></table>';
        }

        const allMissing = conf.missingSignals || conf.missing || [];
        const otherMissing = allMissing.filter((m) => m !== "spot");
        if (otherMissing.length) {
            const names = otherMissing.map((m) => signalLabels[m] || m).join(", ");
            html += '<p class="confidence-missing"><i class="bi bi-exclamation-circle"></i> Missing signals (excluded from score): ' + escapeHtml(names) + '</p>';
        }

        if (conf.disclaimers?.length) {
            html += '<p class="confidence-disclaimer text-body-secondary small fst-italic mt-1 mb-0">' + escapeHtml(conf.disclaimers[0]) + ' <a href="https://docs.az-scout.com/scoring/" target="_blank" rel="noopener" class="text-body-secondary">Learn more</a> about scoring methodology.</p>';
        }

        html += '</div>';
        return html;
    };

    /**
     * Render pricing accordion panel.
     * @param {object} data - Pricing data {paygo, spot, ri_1y, ri_3y, sp_1y, sp_3y, currency}.
     * @param {object} [opts] - Optional settings.
     * @param {function} [opts.onCurrencyChange] - Callback when currency selector changes.
     * @returns {string} HTML accordion string.
     */
    C.renderPricingPanel = (data, opts) => {
        const o = opts || {};
        const currency = data.currency || "USD";
        const HOURS_PER_MONTH = 730;

        const spotDiscount = (data.paygo != null && data.spot != null && data.paygo > 0) ? Math.round((1 - data.spot / data.paygo) * 100) : null;
        const spotLabel = spotDiscount != null ? 'Spot <span class="badge bg-success-subtle text-success-emphasis ms-1">\u2212' + spotDiscount + '%</span>' : "Spot";

        const rows = [
            { label: "Pay-As-You-Go", hourly: data.paygo },
            { label: spotLabel, raw: true, hourly: data.spot },
            { label: "Reserved Instance 1Y", hourly: data.ri_1y },
            { label: "Reserved Instance 3Y", hourly: data.ri_3y },
            { label: "Savings Plan 1Y", hourly: data.sp_1y },
            { label: "Savings Plan 3Y", hourly: data.sp_3y },
        ];

        let table = '<table class="table table-sm pricing-detail-table mb-0">';
        table += '<thead><tr><th>Type</th><th>' + escapeHtml(currency) + '/hour</th><th>' + escapeHtml(currency) + '/month</th></tr></thead><tbody>';
        rows.forEach((r) => {
            const hourStr = r.hourly != null ? formatNum(r.hourly, 4) : "\u2014";
            const monthStr = r.hourly != null ? formatNum(r.hourly * HOURS_PER_MONTH, 2) : "\u2014";
            const labelHtml = r.raw ? r.label : escapeHtml(r.label);
            table += '<tr><td>' + labelHtml + '</td><td class="price-cell">' + hourStr + '</td><td class="price-cell">' + monthStr + '</td></tr>';
        });
        table += "</tbody></table>";

        // Currency selector
        const currencies = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "SEK", "BRL", "INR"];
        const currencyOpts = currencies.map((c) => '<option value="' + c + '"' + (c === currency ? " selected" : "") + '>' + c + '</option>').join("");
        const changeAttr = o.onCurrencyChange ? ' onchange="' + o.onCurrencyChange + '"' : "";

        let body = '<div class="d-flex align-items-center gap-2 mb-2">';
        body += '<label class="form-label small mb-0">Currency:</label>';
        body += '<select class="form-select form-select-sm" id="pricing-modal-currency-select"' + changeAttr + ' style="width:100px;">' + currencyOpts + '</select>';
        body += '</div>';
        body += table;

        return _accordion("pricing", "bi-currency-exchange", "Pricing", body);
    };

    // -------------------------------------------------------------------
    // showSkuDetailModal – full-flow modal callable from any plugin
    // -------------------------------------------------------------------

    let _sharedModal = null;
    let _sharedModalContext = {};
    let _sharedModalData = null;   // last API response for re-render

    function _ensureModalElement() {
        if (document.getElementById("sharedSkuDetailModal")) return;
        const style = document.createElement("style");
        style.textContent =
            "@media (min-width: 1200px) { #sharedSkuDetailModal .modal-dialog { max-width: 900px; } } " +
            "#sharedSkuDetailModal .accordion-button { font-size: 0.88rem; padding: 0.5rem 0.75rem; } " +
            "#sharedSkuDetailModal .accordion-body { font-size: 0.85rem; }";
        document.head.appendChild(style);
        const div = document.createElement("div");
        div.innerHTML =
            '<div class="modal fade" id="sharedSkuDetailModal" tabindex="-1" aria-labelledby="sharedSkuDetailModalLabel" aria-hidden="true">' +
                '<div class="modal-dialog modal-xl modal-fullscreen-md-down modal-dialog-centered modal-dialog-scrollable">' +
                    '<div class="modal-content">' +
                        '<div class="modal-header">' +
                            '<h5 class="modal-title" id="sharedSkuDetailModalLabel">SKU Detail \u2014 <span id="shared-sku-detail-name"></span></h5>' +
                            '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>' +
                        '</div>' +
                        '<div class="modal-body">' +
                            '<div id="shared-sku-detail-loading" class="text-center d-none">' +
                                '<div class="spinner-border spinner-border-sm text-primary" role="status"></div>' +
                                '<span class="ms-2 small">Fetching SKU detail\u2026</span>' +
                            '</div>' +
                            '<div id="shared-sku-detail-content" class="d-none"></div>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
        document.body.appendChild(div.firstChild);
    }

    /**
     * Open, fetch data, and render the shared SKU detail modal.
     *
     * @param {string} skuName - ARM SKU name.
     * @param {object} opts
     * @param {string} opts.region - Azure region.
     * @param {string} [opts.subscriptionId] - Subscription ID.
     * @param {string} [opts.tenantId] - Tenant ID.
     * @param {string} [opts.currency] - Initial currency code (default "USD").
     * @param {object} [opts.enrichedSku] - Enriched SKU from plugin cache (quota, confidence, spot_zones).
     * @param {object} [opts.physicalZoneMap] - {logicalZone: physicalName} for zone tooltips.
     * @param {function} [opts.extraSections] - function(data, enrichedSku) → HTML.
     * @param {function} [opts.prependSections] - function(data, enrichedSku) → HTML inserted before confidence.
     * @param {function} [opts.onRecalculate] - function(skuName, instanceCount, includeSpot) → Promise<{confidence}>.
     *     If provided, replaces the default recalculation (re-fetch /api/sku-detail).
     *     Must return an object with at least {confidence} to merge into the display.
     * @param {function} [opts.onAfterRecalculate] - function(confidence) called after recalculation completes.
     */
    C.showSkuDetailModal = (skuName, opts) => {
        const o = opts || {};
        _ensureModalElement();
        _sharedModalContext = { skuName: skuName, opts: o, currency: o.currency || "USD" };
        _sharedModalData = null;

        document.getElementById("shared-sku-detail-name").textContent = skuName;
        document.getElementById("shared-sku-detail-loading").classList.remove("d-none");
        document.getElementById("shared-sku-detail-content").classList.add("d-none");
        if (!_sharedModal) _sharedModal = new bootstrap.Modal(document.getElementById("sharedSkuDetailModal"));
        _sharedModal.show();

        _fetchAndRender();
    };

    function _fetchAndRender(openAccordionIds) {
        var ctx = _sharedModalContext;
        var o = ctx.opts || {};
        var params = new URLSearchParams({ region: o.region || "", sku: ctx.skuName, currencyCode: ctx.currency || "USD" });
        if (o.subscriptionId) params.set("subscriptionId", o.subscriptionId);
        var tqs = typeof tenantQS === "function" ? tenantQS("&") : "";

        document.getElementById("shared-sku-detail-loading").classList.remove("d-none");
        document.getElementById("shared-sku-detail-content").classList.add("d-none");

        apiFetch("/api/sku-detail?" + params + tqs)
            .then((data) => {
                _sharedModalData = data;
                _renderSharedDetail(data, ctx.skuName, o, openAccordionIds);
            })
            .catch((err) => {
                var content = document.getElementById("shared-sku-detail-content");
                content.innerHTML = '<p class="text-danger small">Failed to load: ' + escapeHtml(err.message) + '</p>';
                content.classList.remove("d-none");
                document.getElementById("shared-sku-detail-loading").classList.add("d-none");
            });
    }

    function _getOpenAccordionIds() {
        var content = document.getElementById("shared-sku-detail-content");
        if (!content) return [];
        return Array.from(content.querySelectorAll('.accordion-collapse.show'))
            .map((el) => el.id)
            .filter(Boolean);
    }

    function _restoreAccordionState(content, openIds) {
        if (!openIds || !openIds.length) return;
        openIds.forEach((id) => {
            var panel = content.querySelector('#' + id);
            if (panel) panel.classList.add("show");
            var btn = content.querySelector('[data-bs-target="#' + id + '"]');
            if (btn) { btn.classList.remove("collapsed"); btn.setAttribute("aria-expanded", "true"); }
        });
    }

    function _renderSharedDetail(data, _skuName, opts, openAccordionIds) {
        var enriched = opts.enrichedSku || {};
        var html = "";

        // 0. Plugin-specific prepend sections (before confidence)
        if (typeof opts.prependSections === "function") {
            html += opts.prependSections(data, enriched);
            html += '<hr class="my-3 opacity-25">';
        }

        // 1. Confidence breakdown
        var conf = enriched.confidence || data.confidence;
        if (conf) {
            html += C.renderConfidenceBreakdown(conf);
            // Recalculate controls
            html += '<div class="confidence-controls mt-2 pt-2 border-top">';
            html += '<div class="d-flex align-items-center gap-2 flex-wrap">';
            html += '<label class="text-body-secondary small mb-0" for="shared-confidence-instance-count">Instances:</label>';
            html += '<input type="number" id="shared-confidence-instance-count" class="form-control form-control-sm" value="1" min="1" max="1000" style="width:70px;" title="Number of instances to deploy (affects quota pressure)">';
            html += '<button class="btn btn-sm btn-outline-success" id="shared-recalc-basic"><i class="bi bi-arrow-counterclockwise me-1"></i>Recalculate</button>';
            html += '<button class="btn btn-sm btn-outline-primary" id="shared-recalc-spot"><i class="bi bi-lightning-charge me-1"></i>Recalculate with Spot</button>';
            html += '</div></div>';
        }

        // 2. VM Profile
        if (data.profile) html += C.renderVmProfile(data.profile);

        // 3. Zone Availability
        if (data.profile) {
            const zoneOpts = {};
            if (opts.physicalZoneMap) zoneOpts.physicalZoneMap = opts.physicalZoneMap;
            if (enriched.spot_zones) zoneOpts.spotZoneScores = enriched.spot_zones;
            html += C.renderZoneAvailability(data.profile, conf, zoneOpts);
        }

        // 4. Quota
        var quota = enriched.quota;
        if (quota && quota.limit != null) {
            const vcpus = parseInt(data.profile?.capabilities?.vCPUs || enriched.capabilities?.vCPUs || "0", 10);
            html += C.renderQuotaPanel(quota, vcpus, conf);
        }

        // 5. Pricing (with currency change wired to re-fetch)
        html += C.renderPricingPanel(data);

        // 6. Plugin-specific extra sections
        if (typeof opts.extraSections === "function") {
            html += opts.extraSections(data, enriched);
        }

        var content = document.getElementById("shared-sku-detail-content");
        content.innerHTML = html;
        content.classList.remove("d-none");
        document.getElementById("shared-sku-detail-loading").classList.add("d-none");

        // Restore accordion state
        _restoreAccordionState(content, openAccordionIds);

        // Init tooltips
        content.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((t) => {
            new bootstrap.Tooltip(t, { delay: { show: 0, hide: 100 }, placement: "top" });
        });

        // Wire recalculate buttons
        var recalcBasic = document.getElementById("shared-recalc-basic");
        var recalcSpot = document.getElementById("shared-recalc-spot");
        if (recalcBasic) recalcBasic.addEventListener("click", () => { _handleRecalc(false); });
        if (recalcSpot) recalcSpot.addEventListener("click", () => { _handleRecalc(true); });

        // Wire currency selector
        var currSel = document.getElementById("pricing-modal-currency-select");
        if (currSel) {
            currSel.addEventListener("change", () => {
                _sharedModalContext.currency = currSel.value;
                var openIds = _getOpenAccordionIds();
                _fetchAndRender(openIds);
            });
        }
    }

    function _handleRecalc(includeSpot) {
        var ctx = _sharedModalContext;
        if (!ctx.skuName) return;
        var o = ctx.opts || {};
        var instanceCount = parseInt(document.getElementById("shared-confidence-instance-count")?.value, 10) || 1;
        var openIds = _getOpenAccordionIds();

        // Spinner on button
        var btnId = includeSpot ? "shared-recalc-spot" : "shared-recalc-basic";
        var btn = document.getElementById(btnId);
        var origHtml = btn ? btn.innerHTML : "";
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>Calculating\u2026';
        }

        // If plugin provides custom recalculate handler, use it
        if (typeof o.onRecalculate === "function") {
            Promise.resolve(o.onRecalculate(ctx.skuName, instanceCount, includeSpot))
                .then((result) => {
                    // Merge updated confidence into enriched SKU
                    if (result?.confidence && o.enrichedSku) {
                        o.enrichedSku.confidence = result.confidence;
                    }
                    if (typeof o.onAfterRecalculate === "function") {
                        o.onAfterRecalculate(result ? result.confidence : null);
                    }
                    // Re-render with preserved accordion state
                    if (_sharedModalData) _renderSharedDetail(_sharedModalData, ctx.skuName, o, openIds);
                })
                .catch((err) => {
                    if (btn) { btn.disabled = false; btn.innerHTML = origHtml; }
                    alert("Recalculation failed: " + err.message);
                });
        } else {
            // Default: re-fetch /api/sku-detail with instanceCount
            _fetchAndRender(openIds);
        }
    }
})(window.azScout.components);
