<div id="plugin-catalog-table">
  <p><em>Loading plugin catalog…</em></p>
</div>
<script>
fetch("https://plugin-catalog.az-scout.com/catalog.json")
  .then(r => r.json())
  .then(plugins => {
    const rows = plugins.map(p => {
      const pypi = p.source === "pypi"
        ? `<a href="https://pypi.org/project/${p.name}/"><img src="https://img.shields.io/pypi/v/${p.name}?label=" alt="PyPI" style="vertical-align:middle"></a>`
        : `<span style="font-size:0.75em;color:#888">github</span>`;
      const tags = (p.tags || []).map(t => `<code>${t}</code>`).join(" ");
      const authors = (p.authors || []).map(a =>
        `<a href="https://github.com/${a}"><img src="https://github.com/${a}.png?size=20" width="20" height="20" style="border-radius:50%;vertical-align:middle" alt="${a}"> ${a}</a>`
      ).join(", ");
      return `<tr>
        <td><a href="${p.repository}"><strong>${p.name}</strong></a></td>
        <td>${p.description}</td>
        <td>${pypi}</td>
        <td>${tags}</td>
        <td>${authors}</td>
      </tr>`;
    }).join("");
    document.getElementById("plugin-catalog-table").innerHTML =
      `<table>
        <thead><tr><th>Plugin</th><th>Description</th><th>Version</th><th>Tags</th><th>Authors</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  })
  .catch(() => {
    document.getElementById("plugin-catalog-table").innerHTML =
      '<p>Could not load the plugin catalog. See <a href="https://github.com/az-scout/plugin-catalog">plugin-catalog</a> on GitHub.</p>';
  });
</script>
