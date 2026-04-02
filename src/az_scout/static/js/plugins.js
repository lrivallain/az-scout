/* eslint-disable @microsoft/sdl/no-inner-html -- All dynamic values sanitized via escapeHtml()/escHtml(). Data from plugin manager API. */
/* Plugin Manager modal logic */
/* global apiFetch, apiPost, bootstrap */

(() => {
    const container = document.getElementById("plugin-manager-body");
    if (!container) return;

    const ARROW_CHAR = "→";

    let lastValidation = null;
    let initialized = false;
    let updateInfo = {};      // distribution_name → update status from /api/plugins/updates
    let catalogPlugins = [];  // cached catalog data

    const modalEl = document.getElementById("pluginModal");
    if (!modalEl) return;

    // Lazy-init: fetch HTML fragment + data only when the modal is first shown
    modalEl.addEventListener("show.bs.modal", initOnce);

    // Update URL hash when modal opens/closes
    modalEl.addEventListener("shown.bs.modal", () => {
        window.history.replaceState(null, "", "#plugin");
    });
    modalEl.addEventListener("hidden.bs.modal", () => {
        if (window.location.hash === "#plugin") {
            window.history.replaceState(null, "", window.location.pathname);
        }
    });

    // Open modal from #plugin hash on page load
    if (window.location.hash === "#plugin") {
        const bsModal = new bootstrap.Modal(modalEl);
        bsModal.show();
    }

    function initOnce() {
        if (initialized) return;
        initialized = true;
        container.innerHTML =
            '<div class="text-center py-4 text-muted">' +
            '<div class="spinner-border spinner-border-sm me-2" role="status"></div>' +
            "Loading…</div>";

        // Set up the callback BEFORE injecting catalog.html so the inline script can call it
        window.onCatalogRendered = (plugins) => {
            catalogPlugins = plugins;
            // Now that cards are rendered, fetch instance data and enhance
            loadPlugins();
            checkUpdatesQuiet();
        };

        fetch("/static/html/plugins.html")
            .then(r => r.text())
            .then(html => {
                container.innerHTML = html;
                // Inject catalog.html into the host container
                const host = document.getElementById("pm-catalog-host");
                if (host) {
                    fetch("/static/html/catalog.html")
                        .then(r => r.text())
                        .then(catalogHtml => {
                            host.innerHTML = catalogHtml;
                            // Execute inline scripts in the injected HTML
                            for (const script of host.querySelectorAll("script")) {
                                const newScript = document.createElement("script");
                                newScript.textContent = script.textContent;
                                script.replaceWith(newScript);
                            }
                        });
                }
            });
    }

    // ---- Data loading ----

    function loadPlugins() {
        apiFetch("/api/plugins").then(data => {
            enhanceCatalogCards(data.installed || [], data.loaded || []);
        }).catch(() => {});
    }

    // ---- Card enhancement (progressive enhancement of catalog.html) ----

    // Use global escapeHtml() from app.js for HTML entity escaping.
    const escHtml = escapeHtml;

    function escAttr(s) {
        return String(s || "")
            .replace(/\\/g, "\\\\")
            .replace(/'/g, "\\'")
            .replace(/\r/g, "\\r")
            .replace(/\n/g, "\\n")
            .replace(/\u2028/g, "\\u2028")
            .replace(/\u2029/g, "\\u2029")
            .replace(/"/g, "&quot;");
    }

    /** Inject an installed version label below the authors in a catalog card. */
    function injectVersion(col, ver) {
        if (!ver) return;
        const authorsEl = col.querySelector('.catalog-authors');
        if (!authorsEl) return;
        const old = authorsEl.parentElement.querySelector('.catalog-version-info');
        if (old) old.remove();
        authorsEl.insertAdjacentHTML('afterend',
            '<div class="catalog-version-info text-body-secondary" style="font-size:0.75rem">Installed: ' + escHtml(ver) + '</div>');
    }

    /**
     * Enhance catalog cards with PM features and append cards for
     * non-catalog plugins (built-in, external, UI-installed-not-in-catalog).
     */
    function enhanceCatalogCards(installed, loaded) {
        const grid = document.getElementById("catalog-grid");
        if (!grid) return;

        // Remove dynamically added cards from previous runs
        for (const el of grid.querySelectorAll(".pm-dynamic-card")) {
            el.remove();
        }

        // Build lookups
        const installedByDist = {};
        for (const r of installed) {
            installedByDist[r.distribution_name] = r;
        }
        const loadedByDist = {};
        for (const p of loaded) {
            if (p.distribution_name) loadedByDist[p.distribution_name] = p;
        }

        const catalogNames = new Set(catalogPlugins.map(p => p.name));
        const seenDists = new Set();
        let anyUpdate = false;

        // 1. Enhance existing catalog cards
        for (const col of grid.querySelectorAll(".catalog-card-col")) {
            const name = col.dataset.catalogName;
            const source = col.dataset.catalogSource;
            const actionsEl = col.querySelector(".catalog-actions");
            if (!actionsEl) continue;

            // Clear previously injected version info
            const oldVer = col.querySelector(".catalog-version-info");
            if (oldVer) oldVer.remove();

            seenDists.add(name);

            const record = installedByDist[name];
            const loadedPlugin = loadedByDist[name];
            const isInstalled = !!record || !!loadedPlugin;

            if (isInstalled && record) {
                // UI-managed: show version + actions
                const info = updateInfo[name];
                const ver = escHtml(record.ref || '');
                let btnsHtml = '';

                if ((info?.update_available) || record.update_available) {
                    const latest = escHtml((info?.latest_ref) || record.latest_ref || '');
                    const label = latest ? ver + ' \u2192 ' + latest : 'Update';
                    btnsHtml += '<button class="btn btn-outline-info btn-sm" data-bs-toggle="tooltip" data-bs-placement="top" data-bs-title="Update plugin to latest version" onclick="pmUpdate(\'' + escAttr(name) + '\')"><i class="bi bi-cloud-download me-1"></i>' + label + '</button>';
                    anyUpdate = true;
                }
                btnsHtml += '<button class="btn btn-outline-danger btn-sm" onclick="pmUninstall(\'' + escAttr(name) + '\')"><i class="bi bi-trash me-1"></i>Uninstall</button>';

                actionsEl.innerHTML = btnsHtml;
                injectVersion(col, ver);
            } else if (isInstalled && loadedPlugin?.in_packages_dir) {
                // Installed as a dependency via PM — manageable
                actionsEl.innerHTML =
                    '<button class="btn btn-outline-danger btn-sm" onclick="pmUninstall(\'' + escAttr(name) + '\')"><i class="bi bi-trash me-1"></i>Uninstall</button>';
                injectVersion(col, escHtml(loadedPlugin.version || ''));
            } else if (isInstalled) {
                // Loaded but truly external (system pip, Dockerfile)
                actionsEl.innerHTML = '<span class="badge bg-success" data-bs-toggle="tooltip" data-bs-placement="top" data-bs-title="Installed outside the plugin manager (e.g. pip, Dockerfile). Not manageable from this UI.">external</span>';
                if (loadedPlugin) injectVersion(col, escHtml(loadedPlugin.version || ''));
            } else {
                // Not installed: show install button
                actionsEl.innerHTML =
                    '<button class="btn btn-sm btn-outline-primary" onclick="pmQuickInstall(\'' + escAttr(source) + '\', \'\')"><i class="bi bi-download me-1"></i>Install</button>';
            }
        }

        // 2. Collect built-in plugins and append non-catalog external plugins
        const builtins = [];
        for (const p of loaded) {
            const distName = p.distribution_name || p.name;
            if (catalogNames.has(distName) || seenDists.has(distName)) continue;
            seenDists.add(distName);

            if (p.internal) {
                builtins.push(p);
                continue;
            }

            const record = installedByDist[distName];
            grid.insertAdjacentHTML("beforeend", buildExtraCard(p, record));

            if (record && (updateInfo[distName]?.update_available || record.update_available)) {
                anyUpdate = true;
            }
        }

        // 2b. Single card for all built-in plugins
        if (builtins.length > 0) {
            const items = builtins.map((p) => {
                const label = p.display_name || p.name;
                return '<li><strong>' + escHtml(label) + '</strong>' +
                    (p.description ? ' — ' + escHtml(p.description) : '') + '</li>';
            }).join("");
            grid.insertAdjacentHTML("beforeend",
                '<div class="col catalog-card-col pm-dynamic-card" data-catalog-name="built-in" data-catalog-tags="" data-catalog-desc="built-in">' +
                    '<div class="card h-100">' +
                        '<div class="card-body d-flex flex-column gap-1" style="font-size:0.85rem;">' +
                            '<div class="d-flex align-items-baseline justify-content-between gap-2">' +
                                '<span class="fw-semibold">Built-in plugins</span>' +
                            '</div>' +
                            '<ul class="text-body-secondary small mb-0 ps-3">' + items + '</ul>' +
                        '</div>' +
                    '</div>' +
                '</div>'
            );
        }

        // 3. Append installed-but-not-loaded plugins
        for (const r of installed) {
            if (seenDists.has(r.distribution_name)) continue;
            seenDists.add(r.distribution_name);
            grid.insertAdjacentHTML("beforeend", buildNotLoadedCard(r));
            if (updateInfo[r.distribution_name]?.update_available || r.update_available) {
                anyUpdate = true;
            }
        }

        // Show/hide "Update all" button
        const updateAllBtn = document.getElementById("pm-update-all-btn");
        if (updateAllBtn) {
            updateAllBtn.classList.toggle("d-none", !anyUpdate);
        }

        // Initialize Bootstrap tooltips
        for (const el of grid.querySelectorAll('[data-bs-toggle="tooltip"]')) {
            new bootstrap.Tooltip(el);
        }

        // 4. Append manual install card from template (if not already present)
        if (!grid.querySelector('[data-catalog-name="manual-install"]')) {
            const tpl = document.getElementById("pm-manual-install-template");
            if (tpl) {
                grid.appendChild(tpl.content.cloneNode(true));
            }
        }
    }

    /** Build a card for a loaded plugin that's NOT in the catalog. */
    function buildExtraCard(p, record) {
        const isInternal = p.internal;
        let badges = "";
        if (!isInternal && !record && !p.in_packages_dir) {
            badges = '<span class="badge text-bg-light border" data-bs-toggle="tooltip" data-bs-placement="top" ' +
                'data-bs-title="Installed outside the plugin manager (e.g. pip, Dockerfile). Not manageable from this UI.">external</span>';
        }

        let sourceHtml = "";
        if (isInternal) {
            sourceHtml = '<span class="text-body-secondary" style="font-size:0.75rem">built-in</span>';
        } else if (record) {
            sourceHtml = record.source === "pypi"
                ? '<span class="badge text-bg-success text-uppercase" style="font-size:0.65rem">pypi</span>'
                : '<span class="badge text-bg-secondary text-uppercase" style="font-size:0.65rem">github</span>';
        } else {
            sourceHtml = '<span class="text-body-secondary" style="font-size:0.75rem">pip / system</span>';
        }

        let actionsHtml = "";
        let extraVersionHtml = isInternal ? "" : escHtml(p.version);
        if (record) {
            const info = updateInfo[record.distribution_name];
            const ver = escHtml(record.ref || '');
            let btnsHtml = '';
            if ((info?.update_available) || record.update_available) {
                const latest = escHtml((info?.latest_ref) || record.latest_ref || '');
                const label = latest ? ver + ' \u2192 ' + latest : 'Update';
                btnsHtml += '<button class="btn btn-outline-info btn-sm" data-bs-toggle="tooltip" data-bs-placement="top" data-bs-title="Update plugin to latest version" onclick="pmUpdate(\'' + escAttr(record.distribution_name) + '\')"><i class="bi bi-cloud-download me-1"></i>' + label + '</button>';
            }
            btnsHtml += '<button class="btn btn-outline-danger btn-sm" onclick="pmUninstall(\'' + escAttr(record.distribution_name) + '\')"><i class="bi bi-trash me-1"></i>Uninstall</button>';
            actionsHtml = btnsHtml;
            extraVersionHtml = ver;
        } else if (p.in_packages_dir) {
            // Installed as a dependency via PM — manageable
            actionsHtml = '<button class="btn btn-outline-danger btn-sm" onclick="pmUninstall(\'' + escAttr(p.distribution_name || p.name) + '\')"><i class="bi bi-trash me-1"></i>Uninstall</button>';
        } else if (isInternal) {
            actionsHtml = '<span class="badge text-bg-secondary">built-in</span>';
        } else {
            actionsHtml = '<span class="badge bg-success" data-bs-toggle="tooltip" data-bs-placement="top" data-bs-title="Installed outside the plugin manager (e.g. pip, Dockerfile). Not manageable from this UI.">external</span>';
        }

        return '<div class="col catalog-card-col pm-dynamic-card" data-catalog-name="' + escHtml(p.name) + '" data-catalog-tags="" data-catalog-desc="' + escHtml(p.description || '') + '">' +
            '<div class="card catalog-card-plugin h-100">' +
                '<div class="card-body d-flex flex-column gap-1" style="font-size:0.85rem;">' +
                    '<div class="d-flex align-items-baseline justify-content-between gap-2">' +
                        '<span class="fw-semibold">' + escHtml(p.name) + '</span> ' +
                        sourceHtml +
                    '</div>' +
                    (p.description ? '<p class="text-body-secondary small mb-0">' + escHtml(p.description) + '</p>' : '') +
                    '<div class="d-flex flex-wrap gap-1">' + badges + '</div>' +
                    '<div class="d-flex align-items-center justify-content-between mt-auto pt-1">' +
                        '<span class="small text-body-secondary">' + extraVersionHtml + '</span>' +
                        '<span class="d-flex align-items-center gap-2 catalog-actions">' + actionsHtml + '</span>' +
                    '</div>' +
                '</div>' +
            '</div>' +
        '</div>';
    }

    /** Build a card for a plugin in installed.json but not loaded. */
    function buildNotLoadedCard(r) {
        let btnsLine = "";
        if (updateInfo[r.distribution_name]?.update_available || r.update_available) {
            const latest = escHtml(updateInfo[r.distribution_name]?.latest_ref || r.latest_ref || '');
            const ver = escHtml(r.ref || '');
            const label = latest ? ver + ' \u2192 ' + latest : 'Update';
            btnsLine += '<button class="btn btn-outline-info btn-sm" data-bs-toggle="tooltip" data-bs-placement="top" data-bs-title="Update plugin to latest version" onclick="pmUpdate(\'' + escAttr(r.distribution_name) + '\')"><i class="bi bi-cloud-download me-1"></i>' + label + '</button> ';
        }
        btnsLine += '<button class="btn btn-outline-danger btn-sm" onclick="pmUninstall(\'' + escAttr(r.distribution_name) + '\')"><i class="bi bi-trash me-1"></i>Uninstall</button>';

        const sourceBadge = r.source === "pypi"
            ? '<span class="badge text-bg-success text-uppercase" style="font-size:0.65rem">pypi</span>'
            : '<span class="badge text-bg-secondary text-uppercase" style="font-size:0.65rem">github</span>';

        return '<div class="col catalog-card-col pm-dynamic-card" data-catalog-name="' + escHtml(r.distribution_name) + '" data-catalog-tags="" data-catalog-desc="">' +
            '<div class="card catalog-card-plugin h-100 border-warning">' +
                '<div class="card-body d-flex flex-column gap-1" style="font-size:0.85rem;">' +
                    '<div class="d-flex align-items-baseline justify-content-between gap-2">' +
                        '<span class="fw-semibold">' + escHtml(r.distribution_name) + '</span> ' +
                        sourceBadge +
                    '</div>' +
                    '<div class="d-flex flex-wrap gap-1"><span class="badge text-bg-warning">not loaded</span></div>' +
                    '<div class="d-flex align-items-center justify-content-between mt-auto pt-1">' +
                        '<span class="small text-body-secondary">' + escHtml(r.ref || '') + '</span>' +
                        '<span class="d-flex align-items-center gap-2 catalog-actions">' + btnsLine + '</span>' +
                    '</div>' +
                '</div>' +
            '</div>' +
        '</div>';
    }

    // ---- Validate ----

    window.pmValidate = async () => {
        const repoUrl = (document.getElementById("pm-repo-url").value || "").trim();
        const ref = (document.getElementById("pm-ref").value || "").trim();
        if (!repoUrl) return;

        showSpinner("Validating…");
        hideResult();
        disableInstall();
        lastValidation = null;

        try {
            const data = await apiPost("/api/plugins/validate", { repo_url: repoUrl, ref: ref });
            lastValidation = data;
            showResult(data);
            if (data.ok) enableInstall();
        } catch (e) {
            showResultError(e.message);
        } finally {
            hideSpinner();
        }
    };

    // ---- Install ----

    window.pmInstall = async () => {
        const repoUrl = (document.getElementById("pm-repo-url").value || "").trim();
        const ref = (document.getElementById("pm-ref").value || "").trim();
        if (!repoUrl) return;

        showSpinner("Installing…");
        showGlobalStatus("Installing plugin…");
        disableInstall();

        try {
            const data = await apiPost("/api/plugins/install", { repo_url: repoUrl, ref: ref });
            if (data.ok) {
                if (data.restart_required) showRestartBanner();
                refreshAll();
                hideResult();
            } else {
                showResultError((data.errors || []).join("; "));
            }
        } catch (e) {
            showResultError(e.message);
        } finally {
            hideSpinner();
            hideGlobalStatus();
        }
    };

    // ---- Uninstall ----

    window.pmUninstall = async (distName) => {
        const safeDistName = String(distName).replace(/[\x00-\x1F\x7F]/g, "");
        if (!confirm("Uninstall plugin \"" + safeDistName + "\"?")) return;
        showGlobalStatus("Uninstalling " + safeDistName + "…");
        try {
            const data = await apiPost("/api/plugins/uninstall", { distribution_name: distName });
            if (data.ok) {
                refreshAll();
            } else {
                alert("Uninstall failed: " + (data.errors || []).join("; "));
            }
        } catch (e) {
            alert("Uninstall error: " + e.message);
        } finally {
            hideGlobalStatus();
        }
    };

    // ---- Check updates ----

    async function fetchUpdates() {
        const data = await apiFetch("/api/plugins/updates");
        updateInfo = {};
        for (const p of (data.plugins || [])) {
            updateInfo[p.distribution_name] = p;
        }
        loadPlugins();
    }

    function checkUpdatesQuiet() {
        fetchUpdates().catch(() => {});
    }

    window.pmCheckUpdates = async () => {
        showGlobalStatus("Checking for updates…");
        try {
            await fetchUpdates();
        } catch (e) {
            alert("Check updates error: " + e.message);
        } finally {
            hideGlobalStatus();
        }
    };

    // ---- Update single ----

    window.pmUpdate = async (distName) => {
        showGlobalStatus("Updating " + distName + "…");
        try {
            const data = await apiPost("/api/plugins/update", { distribution_name: distName });
            if (data.ok) {
                if (data.restart_required) showRestartBanner();
                updateInfo = {};
                refreshAll();
            } else {
                alert("Update failed: " + (data.errors || []).join("; "));
            }
        } catch (e) {
            alert("Update error: " + e.message);
        } finally {
            hideGlobalStatus();
        }
    };

    // ---- Update all ----

    window.pmUpdateAll = async () => {
        if (!confirm("Update all plugins with available updates?")) return;
        showGlobalStatus("Updating all plugins…");
        try {
            const data = await apiPost("/api/plugins/update-all", {});
            if (data.updated > 0 && data.restart_required) showRestartBanner();
            updateInfo = {};
            refreshAll();
            if (data.failed > 0) alert("Some plugins failed to update: " + data.failed);
        } catch (e) {
            alert("Update all error: " + e.message);
        } finally {
            hideGlobalStatus();
        }
    };

    // ---- Quick install (from catalog card) ----

    window.pmQuickInstall = async (source, version) => {
        showGlobalStatus("Installing plugin…");
        try {
            const data = await apiPost("/api/plugins/install", { repo_url: source, ref: version });
            if (data.ok) {
                if (data.restart_required) showRestartBanner();
                refreshAll();
            } else {
                alert("Install failed: " + (data.errors || []).join("; "));
            }
        } catch (e) {
            alert("Install error: " + e.message);
        } finally {
            hideGlobalStatus();
        }
    };

    // ---- Refresh ----

    /** Reload the page to pick up new/removed plugin tabs, routes, and JS. */
    function refreshAll() {
        window.location.reload();
    }

    // ---- UI helpers ----

    function showSpinner(text) {
        const el = document.getElementById("pm-spinner");
        const txt = document.getElementById("pm-spinner-text");
        if (el) el.classList.remove("d-none");
        if (txt) txt.textContent = text;
    }
    function hideSpinner() {
        const el = document.getElementById("pm-spinner");
        if (el) el.classList.add("d-none");
    }
    function showRestartBanner() {
        const el = document.getElementById("pm-restart-banner");
        if (el) el.classList.remove("d-none");
    }
    function showGlobalStatus(text) {
        const el = document.getElementById("pm-global-status");
        const txt = document.getElementById("pm-global-status-text");
        if (el) el.classList.remove("d-none");
        if (txt) txt.textContent = text;
    }
    function hideGlobalStatus() {
        const el = document.getElementById("pm-global-status");
        if (el) el.classList.add("d-none");
    }
    function hideResult() {
        const el = document.getElementById("pm-validation-result");
        if (el) el.classList.add("d-none");
    }

    function showResult(data) {
        const wrap = document.getElementById("pm-validation-result");
        const status = document.getElementById("pm-val-status");
        const meta = document.getElementById("pm-val-meta");
        const errEl = document.getElementById("pm-val-errors");
        const warnEl = document.getElementById("pm-val-warnings");
        if (!wrap || !status || !meta || !errEl || !warnEl) return;

        wrap.classList.remove("d-none");
        status.innerHTML = data.ok
            ? '<span class="badge bg-success">Valid</span>'
            : '<span class="badge bg-danger">Invalid</span>';

        const lines = [];
        if (data.distribution_name) lines.push("<strong>Distribution:</strong> " + escHtml(data.distribution_name));
        if (data.version) lines.push("<strong>Version:</strong> " + escHtml(data.version));
        if (data.resolved_sha) lines.push("<strong>SHA:</strong> <code>" + escHtml(data.resolved_sha) + "</code>");
        if (data.source) lines.push("<strong>Source:</strong> " + escHtml(data.source === "pypi" ? "PyPI" : "GitHub"));
        if (data.entry_points && Object.keys(data.entry_points).length) {
            lines.push("<strong>Entry points:</strong> " +
                Object.entries(data.entry_points).map(([k, v]) => escHtml(k) + " " + ARROW_CHAR + " " + escHtml(v)).join(", "));
        }
        meta.innerHTML = lines.join("<br>");
        renderList(errEl, data.errors, "danger");
        renderList(warnEl, data.warnings, "warning");
    }

    function showResultError(msg) {
        const wrap = document.getElementById("pm-validation-result");
        const status = document.getElementById("pm-val-status");
        const meta = document.getElementById("pm-val-meta");
        const errEl = document.getElementById("pm-val-errors");
        const warnEl = document.getElementById("pm-val-warnings");
        if (!wrap || !status || !meta || !errEl || !warnEl) return;

        wrap.classList.remove("d-none");
        status.innerHTML = '<span class="badge bg-danger">Error</span>';
        meta.innerHTML = "";
        errEl.classList.remove("d-none");
        errEl.innerHTML = '<div class="alert alert-danger alert-sm py-1 px-2 mb-0" style="font-size:0.82rem;">' +
            escHtml(msg) + '</div>';
        warnEl.classList.add("d-none");
    }

    function renderList(el, items, variant) {
        if (!el) return;
        if (!items || items.length === 0) { el.classList.add("d-none"); return; }
        el.classList.remove("d-none");
        el.innerHTML = items.map(i =>
            '<div class="alert alert-' + variant + ' alert-sm py-1 px-2 mb-1" style="font-size:0.82rem;">' +
            '<i class="bi bi-' + (variant === "danger" ? "x-circle" : "exclamation-triangle") + ' me-1"></i>' +
            escHtml(i) + '</div>'
        ).join("");
    }

    function enableInstall() {
        const btn = document.getElementById("pm-install-btn");
        if (btn) btn.disabled = false;
    }
    function disableInstall() {
        const btn = document.getElementById("pm-install-btn");
        if (btn) btn.disabled = true;
    }
})();
