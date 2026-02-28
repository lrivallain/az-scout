/* Plugin Manager offcanvas logic */
/* global apiFetch, apiPost, bootstrap */

(function () {
    "use strict";

    const container = document.getElementById("plugin-manager-body");
    if (!container) return;

    let lastValidation = null;
    let initialized = false;
    let updateInfo = {};  // distribution_name → update status from /api/plugins/updates

    const offcanvasEl = document.getElementById("pluginOffcanvas");
    if (!offcanvasEl) return;

    // Lazy-init: fetch HTML fragment + data only when the offcanvas is first shown
    offcanvasEl.addEventListener("show.bs.offcanvas", initOnce);

    // Update URL hash when offcanvas opens/closes
    offcanvasEl.addEventListener("shown.bs.offcanvas", () => {
        window.history.replaceState(null, "", "#plugin");
    });
    offcanvasEl.addEventListener("hidden.bs.offcanvas", () => {
        if (window.location.hash === "#plugin") {
            window.history.replaceState(null, "", window.location.pathname);
        }
    });

    // Open offcanvas from #plugin hash on page load
    if (window.location.hash === "#plugin") {
        const bsOffcanvas = new bootstrap.Offcanvas(offcanvasEl);
        bsOffcanvas.show();
    }

    function initOnce() {
        if (initialized) return;
        initialized = true;
        container.innerHTML =
            '<div class="text-center py-4 text-muted">' +
            '<div class="spinner-border spinner-border-sm me-2" role="status"></div>' +
            "Loading…</div>";
        fetch("/static/html/plugins.html")
            .then(r => r.text())
            .then(html => {
                container.innerHTML = html;
                loadPlugins();
            });
    }

    // ---- Data loading ----

    function loadPlugins() {
        apiFetch("/api/plugins").then(data => {
            renderInstalled(data.installed || []);
            renderLoaded(data.loaded || []);
        }).catch(() => {});
    }

    function renderInstalled(list) {
        const empty = document.getElementById("pm-installed-empty");
        const wrap = document.getElementById("pm-installed-table-wrap");
        const tbody = document.getElementById("pm-installed-tbody");
        if (!empty || !wrap || !tbody) return;

        if (list.length === 0) {
            empty.classList.remove("d-none");
            wrap.classList.add("d-none");
            return;
        }
        empty.classList.add("d-none");
        wrap.classList.remove("d-none");
        tbody.innerHTML = "";
        let anyUpdate = false;
        for (const r of list) {
            const tr = document.createElement("tr");
            const shaShort = (r.resolved_sha || "").substring(0, 8);
            const repoLink = r.repo_url
                ? `<a href="${escHtml(r.repo_url)}" target="_blank" rel="noopener">${escHtml(r.repo_url)}</a>`
                : "";
            const installed = r.installed_at ? new Date(r.installed_at).toLocaleString() : "";

            // Installed version column
            const installedVer = `${escHtml(r.ref)} <code title="${escHtml(r.resolved_sha)}">${escHtml(shaShort)}</code>`;

            // Latest version column
            const info = updateInfo[r.distribution_name];
            let latestVer = '<span class="text-body-secondary">—</span>';
            let statusBadge = '<span class="badge bg-secondary">Unknown</span>';
            let updateBtn = "";

            if (info) {
                if (info.error) {
                    latestVer = '<span class="text-danger" title="' + escHtml(info.error) + '">Error</span>';
                    statusBadge = '<span class="badge bg-warning text-dark">Unknown</span>';
                } else if (info.latest_ref) {
                    const latestShaShort = (info.latest_sha || "").substring(0, 8);
                    latestVer = `${escHtml(info.latest_ref)} <code title="${escHtml(info.latest_sha)}">${escHtml(latestShaShort)}</code>`;
                    if (info.update_available) {
                        statusBadge = '<span class="badge bg-info text-dark">Update available</span>';
                        updateBtn = ` <button class="btn btn-outline-info btn-sm py-0 px-1"
                                              title="Update"
                                              onclick="pmUpdate('${escAttr(r.distribution_name)}')">
                                          <i class="bi bi-cloud-download"></i>
                                      </button>`;
                        anyUpdate = true;
                    } else {
                        statusBadge = '<span class="badge bg-success">Up to date</span>';
                    }
                }
            } else if (r.update_available === true && r.latest_ref) {
                // Use persisted data from installed.json
                const latestShaShort = (r.latest_sha || "").substring(0, 8);
                latestVer = `${escHtml(r.latest_ref)} <code title="${escHtml(r.latest_sha)}">${escHtml(latestShaShort)}</code>`;
                statusBadge = '<span class="badge bg-info text-dark">Update available</span>';
                updateBtn = ` <button class="btn btn-outline-info btn-sm py-0 px-1"
                                      title="Update"
                                      onclick="pmUpdate('${escAttr(r.distribution_name)}')">
                                  <i class="bi bi-cloud-download"></i>
                              </button>`;
                anyUpdate = true;
            } else if (r.update_available === false) {
                const latestShaShort = (r.latest_sha || "").substring(0, 8);
                if (r.latest_ref) {
                    latestVer = `${escHtml(r.latest_ref)} <code title="${escHtml(r.latest_sha)}">${escHtml(latestShaShort)}</code>`;
                }
                statusBadge = '<span class="badge bg-success">Up to date</span>';
            }

            tr.innerHTML = `
                <td><code>${escHtml(r.distribution_name)}</code></td>
                <td>${repoLink}</td>
                <td>${installedVer}</td>
                <td>${latestVer}</td>
                <td>${statusBadge}</td>
                <td>${escHtml(installed)}</td>
                <td class="text-nowrap">
                    ${updateBtn}
                    <button class="btn btn-outline-danger btn-sm py-0 px-1"
                            title="Uninstall"
                            onclick="pmUninstall('${escAttr(r.distribution_name)}')">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>`;
            tbody.appendChild(tr);
        }

        // Show/hide "Update all" button
        const updateAllBtn = document.getElementById("pm-update-all-btn");
        if (updateAllBtn) {
            if (anyUpdate) {
                updateAllBtn.classList.remove("d-none");
            } else {
                updateAllBtn.classList.add("d-none");
            }
        }
    }

    function renderLoaded(list) {
        const empty = document.getElementById("pm-loaded-empty");
        const wrap = document.getElementById("pm-loaded-table-wrap");
        const tbody = document.getElementById("pm-loaded-tbody");
        if (!empty || !wrap || !tbody) return;

        if (list.length === 0) {
            empty.classList.remove("d-none");
            wrap.classList.add("d-none");
            return;
        }
        empty.classList.add("d-none");
        wrap.classList.remove("d-none");
        tbody.innerHTML = "";
        for (const p of list) {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td>${escHtml(p.name)}</td><td>${escHtml(p.version)}</td>`;
            tbody.appendChild(tr);
        }
    }

    // ---- Validate ----

    window.pmValidate = async function () {
        const repoUrl = (document.getElementById("pm-repo-url").value || "").trim();
        const ref = (document.getElementById("pm-ref").value || "").trim();
        if (!repoUrl || !ref) return;

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

    window.pmInstall = async function () {
        const repoUrl = (document.getElementById("pm-repo-url").value || "").trim();
        const ref = (document.getElementById("pm-ref").value || "").trim();
        if (!repoUrl || !ref) return;

        showSpinner("Installing…");
        disableInstall();

        try {
            const data = await apiPost("/api/plugins/install", { repo_url: repoUrl, ref: ref });
            if (data.ok) {
                showRestart();
                loadPlugins();
                hideResult();
            } else {
                showResultError((data.errors || []).join("; "));
            }
        } catch (e) {
            showResultError(e.message);
        } finally {
            hideSpinner();
        }
    };

    // ---- Uninstall ----

    window.pmUninstall = async function (distName) {
        if (!confirm("Uninstall plugin \"" + distName + "\"?")) return;

        try {
            const data = await apiPost("/api/plugins/uninstall", { distribution_name: distName });
            if (data.ok) {
                showRestart();
                loadPlugins();
            } else {
                alert("Uninstall failed: " + (data.errors || []).join("; "));
            }
        } catch (e) {
            alert("Uninstall error: " + e.message);
        }
    };

    // ---- Check updates ----

    window.pmCheckUpdates = async function () {
        showSpinner("Checking for updates…");
        try {
            const data = await apiFetch("/api/plugins/updates");
            updateInfo = {};
            for (const p of (data.plugins || [])) {
                updateInfo[p.distribution_name] = p;
            }
            loadPlugins();
        } catch (e) {
            alert("Check updates error: " + e.message);
        } finally {
            hideSpinner();
        }
    };

    // ---- Update single ----

    window.pmUpdate = async function (distName) {
        showSpinner("Updating " + distName + "…");
        try {
            const data = await apiPost("/api/plugins/update", { distribution_name: distName });
            if (data.ok) {
                showRestart();
                // Refresh update info
                delete updateInfo[distName];
                loadPlugins();
            } else {
                alert("Update failed: " + (data.errors || []).join("; "));
            }
        } catch (e) {
            alert("Update error: " + e.message);
        } finally {
            hideSpinner();
        }
    };

    // ---- Update all ----

    window.pmUpdateAll = async function () {
        if (!confirm("Update all plugins with available updates?")) return;

        showSpinner("Updating all plugins…");
        try {
            const data = await apiPost("/api/plugins/update-all", {});
            if (data.restart_required) {
                showRestart();
            }
            updateInfo = {};
            loadPlugins();
            const msg = "Updated: " + data.updated + ", Failed: " + data.failed;
            alert(msg);
        } catch (e) {
            alert("Update all error: " + e.message);
        } finally {
            hideSpinner();
        }
    };

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

        if (data.ok) {
            status.innerHTML = '<span class="badge bg-success">Valid</span>';
        } else {
            status.innerHTML = '<span class="badge bg-danger">Invalid</span>';
        }

        const lines = [];
        if (data.distribution_name) lines.push("<strong>Distribution:</strong> " + escHtml(data.distribution_name));
        if (data.resolved_sha) lines.push("<strong>SHA:</strong> <code>" + escHtml(data.resolved_sha) + "</code>");
        if (data.entry_points && Object.keys(data.entry_points).length) {
            lines.push("<strong>Entry points:</strong> " +
                Object.entries(data.entry_points).map(([k, v]) => escHtml(k) + " → " + escHtml(v)).join(", "));
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
        if (!items || items.length === 0) {
            el.classList.add("d-none");
            return;
        }
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

    function showRestart() {
        const el = document.getElementById("pm-restart-banner");
        if (el) el.classList.remove("d-none");
    }

    function escHtml(s) {
        const d = document.createElement("div");
        d.textContent = String(s || "");
        return d.innerHTML;
    }

    function escAttr(s) {
        return String(s || "").replace(/'/g, "\\'").replace(/"/g, "&quot;");
    }
})();
