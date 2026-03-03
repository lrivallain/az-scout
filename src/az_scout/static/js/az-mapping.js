/* ===================================================================
   Azure Scout – AZ Mapping / Topology Tab
   Requires: app.js (globals: subscriptions, apiFetch, tenantQS,
             escapeHtml, truncate, getSubName, showError, hideError,
             showPanel, getEffectiveTheme, downloadCSV)
   =================================================================== */

// ---------------------------------------------------------------------------
// Topology tab state
// ---------------------------------------------------------------------------
let topoSelectedSubs = new Set();           // selected subscription IDs for topology
let lastMappingData = null;                 // cached /api/mappings result

// ---------------------------------------------------------------------------
// Topology subscriptions (multi-select checklist)
// ---------------------------------------------------------------------------
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
    const measureText = (txt, fontSize, fontWeight = 500) => {
        const t = measurer.append("text").attr("font-size", fontSize).attr("font-weight", fontWeight)
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
    const maxSubText = Math.max(100, ...subLabels.map(l => measureText(l, 12, 600)));
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
