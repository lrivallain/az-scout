/* ===================================================================
   Public Signals Plugin – Tab UI
   =================================================================== */

(function () {
    "use strict";

    const BASE = "/plugins/public/api";

    // ---------------------------------------------------------------------------
    // DOM helpers
    // ---------------------------------------------------------------------------

    const $ = (sel) => document.querySelector(sel);
    const show = (el) => el?.classList.remove("d-none");
    const hide = (el) => el?.classList.add("d-none");

    // ---------------------------------------------------------------------------
    // State
    // ---------------------------------------------------------------------------

    let lastResult = null;

    // ---------------------------------------------------------------------------
    // Init – called once after the tab pane is injected
    // ---------------------------------------------------------------------------

    function init() {
        const form = $("#pub-strategy-form");
        if (form) form.addEventListener("submit", onSubmit);
    }

    // Run init when the DOM is ready or immediately if already loaded.
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // ---------------------------------------------------------------------------
    // Form submit
    // ---------------------------------------------------------------------------

    async function onSubmit(e) {
        e.preventDefault();

        const errEl = $("#pub-error");
        const loadingEl = $("#pub-loading");
        const resultsEl = $("#pub-results");
        const emptyEl = $("#pub-empty");

        hide(errEl);
        hide(resultsEl);
        hide(emptyEl);
        show(loadingEl);

        const skuName = $("#pub-sku").value.trim();
        const instanceCount = parseInt($("#pub-instances").value, 10) || 1;
        const currency = $("#pub-currency").value;
        const preferSpot = $("#pub-prefer-spot").checked;
        const latencySensitive = $("#pub-latency-sens").checked;
        const maxRegions = parseInt($("#pub-max-regions").value, 10) || 3;

        // Parse comma-separated regions (optional)
        const regionsRaw = $("#pub-regions").value.trim();
        const regions = regionsRaw ? regionsRaw.split(",").map((r) => r.trim()).filter(Boolean) : null;

        // Parse comma-separated target countries (optional)
        const countriesRaw = $("#pub-countries").value.trim();
        const targetCountries = countriesRaw ? countriesRaw.split(",").map((c) => c.trim()).filter(Boolean) : null;

        const body = {
            skuName,
            instanceCount,
            currency,
            constraints: {
                preferSpot,
                latencySensitive,
                maxRegions,
                targetCountries,
            },
        };
        if (regions) body.regions = regions;

        try {
            const resp = await fetch(`${BASE}/capacity-strategy`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            hide(loadingEl);

            if (!resp.ok || data.error) {
                errEl.textContent = data.error || `HTTP ${resp.status}`;
                show(errEl);
                return;
            }

            lastResult = data;
            renderResults(data);
            show(resultsEl);
        } catch (err) {
            hide(loadingEl);
            errEl.textContent = `Network error: ${err.message}`;
            show(errEl);
        }
    }

    // ---------------------------------------------------------------------------
    // Render
    // ---------------------------------------------------------------------------

    function renderResults(data) {
        renderSummary(data);
        renderRecommendations(data.recommendations || []);
        renderDisclaimers(data);
    }

    function renderSummary(data) {
        const el = $("#pub-summary-cards");
        if (!el) return;

        const cards = [
            {
                icon: "bi-cpu",
                label: "SKU",
                value: data.skuName || "—",
            },
            {
                icon: "bi-stack",
                label: "Instances",
                value: data.instanceCount ?? "—",
            },
            {
                icon: "bi-compass",
                label: "Strategy",
                value: (data.strategyHint || "—").replaceAll("_", " "),
            },
            {
                icon: "bi-exclamation-triangle",
                label: "Missing signals",
                value: (data.missingSignals || []).length,
            },
        ];

        el.innerHTML = cards
            .map(
                (c) => `
            <div class="col-6 col-md-3">
                <div class="card text-center h-100">
                    <div class="card-body py-2">
                        <i class="bi ${c.icon} fs-4 text-primary"></i>
                        <div class="fw-semibold">${escapeHtml(String(c.value))}</div>
                        <small class="text-body-secondary">${escapeHtml(c.label)}</small>
                    </div>
                </div>
            </div>`
            )
            .join("");
    }

    function renderRecommendations(recs) {
        const el = $("#pub-recommendations");
        if (!el) return;

        if (!recs.length) {
            el.innerHTML = '<p class="text-body-secondary">No recommendations available.</p>';
            return;
        }

        const rows = recs
            .map((r) => {
                const spot = r.pricing?.spot != null ? r.pricing.spot.toFixed(4) : "—";
                const paygo = r.pricing?.paygo != null ? r.pricing.paygo.toFixed(4) : "—";
                const badge = r.available
                    ? '<span class="badge bg-success">Available</span>'
                    : '<span class="badge bg-secondary">Unknown</span>';
                const notes = (r.notes || []).map((n) => `<li class="small">${escapeHtml(n)}</li>`).join("");
                return `<tr>
                    <td class="fw-semibold">${escapeHtml(r.region)}</td>
                    <td class="text-center">${badge}</td>
                    <td class="text-center">${r.score}</td>
                    <td class="text-end font-monospace">${paygo}</td>
                    <td class="text-end font-monospace">${spot}</td>
                    <td>${notes ? `<ul class="mb-0 ps-3">${notes}</ul>` : "—"}</td>
                </tr>`;
            })
            .join("");

        el.innerHTML = `
        <div class="table-responsive">
            <table class="table table-sm table-hover align-middle">
                <thead>
                    <tr>
                        <th>Region</th>
                        <th class="text-center">Status</th>
                        <th class="text-center">Score</th>
                        <th class="text-end">PayGo/hr</th>
                        <th class="text-end">Spot/hr</th>
                        <th>Notes</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>`;
    }

    function renderDisclaimers(data) {
        const el = $("#pub-disclaimers");
        if (!el) return;

        const missing = (data.missingSignals || [])
            .map((s) => `<span class="badge bg-warning text-dark me-1">${escapeHtml(s)}</span>`)
            .join("");

        const disclaimers = (data.disclaimers || [])
            .map((d) => `<li>${escapeHtml(d)}</li>`)
            .join("");

        el.innerHTML = `
        <div class="alert alert-warning small mt-3" role="alert">
            <i class="bi bi-exclamation-triangle-fill me-1"></i>
            <strong>Public mode – limited signals.</strong>
            <div class="mt-1">Missing: ${missing}</div>
            <ul class="mt-2 mb-0">${disclaimers}</ul>
        </div>`;
    }

    // ---------------------------------------------------------------------------
    // Utilities
    // ---------------------------------------------------------------------------

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }
})();
