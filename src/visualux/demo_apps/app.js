"use strict";

const params = new URLSearchParams(location.search);
const bad = params.get("variant") === "confusing";
const variant = bad ? "confusing" : "clear";
const route = location.pathname.split("/").pop().replace(".html", "");
const app = route.startsWith("lab") ? "lab" : route.startsWith("report") ? "reports" : "device";
const titles = { lab: "Lab Monitor", reports: "Report Center", device: "Device Setup" };
const escapeText = value => String(value).replace(/[&<>"']/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[character]));

function url(file, values = {}) {
  return `${file}.html?${new URLSearchParams({ variant, ...values })}`;
}

function link(file, values, label, attributes = "") {
  return `<a href="${escapeText(url(file, values))}" ${attributes}>${escapeText(label)}</a>`;
}

function heading(title, subtitle, action = "") {
  return `<div class="page-heading"><div><h1>${escapeText(title)}</h1>
    <p class="muted">${escapeText(subtitle)}</p></div>${action}</div>`;
}

function breadcrumbs(items) {
  return `<div class="breadcrumbs">${items.join('<span aria-hidden="true"> / </span>')}</div>`;
}

function missing(message, entry = app) {
  content.innerHTML = heading("Record unavailable", message) +
    `<p>${link(entry, {}, "Return to the list")}</p>`;
}

const labs = {
  north: { name: "Lab North", temperatures: [32, 35, 41, 37, 39, 44, 92, 36] },
  south: { name: "Lab South", temperatures: [29, 33, 84, 31, 87, 40] },
  west: { name: "Lab West", temperatures: [28, 34, 30, 32] }
};
const reports = [
  { id: "north-thermal", name: "North thermal survey", location: "Lab North", count: 4 },
  { id: "maintenance", name: "Maintenance overview", location: "All labs", count: 3 },
  { id: "calibration", name: "Calibration history", location: "Lab West", count: 2 }
];
const reportRows = [
  { name: "North thermal survey", status: "Active", date: "2026-10-30", count: 128 },
  { name: "Cooling inspection", status: "Archived", date: "2026-10-28", count: 24 },
  { name: "Sensor calibration", status: "Active", date: "2026-10-27", count: 64 },
  { name: "North follow-up survey", status: "Active", date: "2026-10-26", count: 96 }
];

function labDevices(lab) {
  return lab.temperatures.map((temperature, index) => ({
    id: `device-${String(index + 1).padStart(2, "0")}`,
    name: `Device-${String(index + 1).padStart(2, "0")}`,
    temperature,
    warning: temperature > 80,
    location: index < 4 ? "North wing" : "South wing"
  }));
}

function currentLab() {
  const id = params.get("lab");
  return Object.hasOwn(labs, id) ? labs[id] : null;
}

function currentReport() {
  return reports.find(report => report.id === params.get("report"));
}

function rowsFor(report, active) {
  const rows = reportRows.slice(0, report.count);
  return active ? rows.filter(row => row.status === "Active") : rows;
}

function storedDevices() {
  try {
    const stored = JSON.parse(localStorage.getItem("visualux-devices") || "[]");
    if (!Array.isArray(stored) || stored.some(device =>
      !device || ["name", "serial", "threshold"].some(field => typeof device[field] !== "string")
    )) throw new Error("Invalid stored records");
    return stored.map((device, index) => ({ ...device, id: `created-${index + 1}` }));
  } catch {
    content.innerHTML = heading("Demo storage unavailable", "Saved records could not be read. Use Reset demo data to clear this synthetic data explicitly.");
    return null;
  }
}

const inventory = [
  { id: "seed-1", name: "Intake sensor", serial: "TEST-1001", threshold: "75" },
  { id: "seed-2", name: "Cooling sensor", serial: "TEST-1002", threshold: "80" }
];

document.body.classList.toggle("bad", bad);
document.title = titles[app];
document.querySelector("#app").innerHTML = `
  <div class="shell">
    <aside>
      <div class="brand">field<span>work</span> /</div>
      <nav aria-label="Applications">${Object.entries(titles).map(([key, title]) =>
        link(key, {}, title, `class="${key === app ? "active" : ""}"`)
      ).join("")}</nav>
      <div class="sidebar-note">WORKSPACE<br>Operations<br><br>SYNTHETIC TEST DATA</div>
    </aside>
    <main class="workspace">
      <div class="topline"><span>FIELDWORK / OPERATIONS</span><button id="reset">Reset demo data</button></div>
      <div id="content"></div><div id="message" role="status"></div>
    </main>
  </div>`;
const content = document.querySelector("#content");
document.querySelector("#reset").onclick = () => {
  try {
    localStorage.removeItem("visualux-devices");
    location.assign(url(app));
  } catch {
    document.querySelector("#message").textContent = "Browser storage could not be reset. Check that local storage is enabled.";
  }
};

function labIndex() {
  content.innerHTML = heading("Laboratories", "Choose a location to inspect its devices and alerts.") + `
    <div class="location-grid">${Object.entries(labs).map(([id, lab]) => {
      const warnings = lab.temperatures.filter(value => value > 80).length;
      return `<section class="panel location-card">
        <span class="eyebrow">LOCATION / ${escapeText(id.toUpperCase())}</span>
        <h2>${escapeText(lab.name)}</h2>
        <p>${lab.temperatures.length} devices · ${warnings} ${bad ? "other" : "temperature warnings"}</p>
        ${link("lab-devices", { lab: id }, bad ? "Open" : `View ${lab.name} devices →`, `data-lab="${id}" class="button"`)}
      </section>`;
    }).join("")}</div>`;
}

function labList() {
  const lab = currentLab();
  if (!lab) return missing("This laboratory does not exist.", "lab");
  const labId = params.get("lab");
  const devices = labDevices(lab);
  let page = 0;
  let warningsOnly = false;
  content.innerHTML = breadcrumbs([link("lab", {}, "Laboratories"), escapeText(lab.name)]) +
    heading(lab.name, bad ? "Overview / list" : "Filter the device list or inspect a device for measurements and maintenance details.") + `
    <section class="panel">
      <div class="panel-head"><h2>Devices</h2><span class="legend">${bad ? "T / S" : "Temperature warning above 80 °C"}</span></div>
      <div class="filters"><div class="search-field"><label for="device-search">Search devices</label>
        <input id="device-search" type="search" placeholder="Device name" autocomplete="off"></div>
        <button id="warnings" aria-pressed="false">${bad ? "W" : "Warnings only"}</button>
        <button id="show-all">${bad ? "Reset" : "All devices"}</button></div>
      <table><thead><tr><th>Device</th><th>Location</th><th>${bad ? "T" : "Temperature (°C)"}</th>
        <th>${bad ? "S" : "Health"}</th><th>${bad ? "" : "Details"}</th></tr></thead><tbody id="device-rows"></tbody></table>
      <div class="pagination"><span id="device-scope" class="muted"></span>
        <button id="previous">Previous</button><button id="next">Next</button></div>
    </section>`;
  const draw = () => {
    const query = document.querySelector("#device-search").value.trim().toLowerCase();
    const filtered = devices.filter(device => (!warningsOnly || device.warning) && device.name.toLowerCase().includes(query));
    const totalPages = Math.max(1, Math.ceil(filtered.length / 4));
    page = Math.min(page, totalPages - 1);
    const visible = filtered.slice(page * 4, page * 4 + 4);
    document.querySelector("#device-rows").innerHTML = visible.map(device => `<tr>
      <td>${escapeText(device.name)}</td><td>${escapeText(device.location)}</td>
      <td>${device.temperature}${bad ? "" : " °C"}</td>
      <td><span class="status ${device.warning ? "warning" : ""}">${device.warning ? (bad ? "2" : "Temperature warning") : (bad ? "1" : "Healthy")}</span></td>
      <td>${link("lab-device", { lab: labId, device: device.id }, bad ? "·" : "View details →",
        `data-device="${device.id}" class="button ${bad ? "tiny" : "row-action"}" title="${bad ? "More" : "View device details"}"`)}</td>
      </tr>`).join("") || '<tr><td colspan="5">No matching devices.</td></tr>';
    document.querySelector("#device-scope").textContent = `${filtered.length} of ${devices.length} devices · Page ${page + 1} of ${totalPages}`;
    document.querySelector("#previous").disabled = page === 0;
    document.querySelector("#next").disabled = page + 1 >= totalPages;
    document.querySelector("#warnings").setAttribute("aria-pressed", String(warningsOnly));
  };
  document.querySelector("#device-search").oninput = () => { page = 0; draw(); };
  document.querySelector("#warnings").onclick = () => { warningsOnly = !warningsOnly; page = 0; draw(); };
  document.querySelector("#show-all").onclick = () => {
    warningsOnly = false;
    page = 0;
    document.querySelector("#device-search").value = "";
    draw();
  };
  document.querySelector("#previous").onclick = () => { page -= 1; draw(); };
  document.querySelector("#next").onclick = () => { page += 1; draw(); };
  draw();
}

function labDetail() {
  const lab = currentLab();
  const device = lab && labDevices(lab).find(item => item.id === params.get("device"));
  if (!device) return missing("This device is not available in the selected laboratory.", "lab");
  content.innerHTML = breadcrumbs([
    link("lab", {}, "Laboratories"), link("lab-devices", { lab: params.get("lab") }, lab.name), escapeText(device.name)
  ]) + heading(device.name, `${lab.name} · ${device.location}`) + `
    <div class="stats">
      <div class="stat ${device.warning ? "warn" : ""}"><strong>${device.temperature} °C</strong><span>Current temperature</span></div>
      <div class="stat"><strong>80 °C</strong><span>Warning threshold</span></div>
      <div class="stat"><strong>${device.warning ? "Warning" : "Healthy"}</strong><span>Current health</span></div>
    </div>
    <section id="detail" class="detail"><h2>${device.warning ? "Temperature warning" : "No active warnings"}</h2>
      <p><strong>Cause:</strong> ${device.warning ? "Cooling fan blocked by dust." : "Device is operating within its temperature range."}</p>
      <p><strong>Recommended action:</strong> ${device.warning ? "Inspect and clean the fan before resuming work." : "Continue normal monitoring."}</p>
      <p>Last maintenance: 2026-10-15 · Next calibration: 2026-12-15</p>
    </section>`;
}

function reportIndex() {
  content.innerHTML = heading("Report Center", "Choose a report to inspect its records and prepare an export.") + `
    <section class="panel"><div class="panel-head"><h2>Available reports</h2><span class="legend">3 reports</span></div>
      <table><thead><tr><th>Report</th><th>Scope</th><th>Last generated</th><th>Records</th><th>Open</th></tr></thead>
      <tbody>${reports.map(report => `<tr><td>${escapeText(report.name)}</td><td>${escapeText(report.location)}</td>
        <td>2026-10-30</td><td>${report.count}</td><td>${link("report-detail", { report: report.id }, bad ? "Open" : "View report →", `data-report="${report.id}" class="button row-action"`)}</td></tr>`).join("")}</tbody>
    </section>`;
}

function reportDetail() {
  const report = currentReport();
  if (!report) return missing("This report does not exist.", "reports");
  let active = params.get("status") === "active";
  content.innerHTML = breadcrumbs([link("reports", {}, "Report Center"), escapeText(report.name)]) +
    heading(report.name, `${report.location} · Select the rows to export.`,
      link("report-export", { report: report.id, status: active ? "active" : "all" }, bad ? "Process" : "Configure export →", 'id="export" class="button primary"')) + `
    <section class="panel"><div class="panel-head"><h2>Report records</h2><div class="toolbar">
      <button id="all" aria-pressed="${!active}">All records</button>
      <button id="active" aria-pressed="${active}">${bad ? "A" : "Active only"}</button></div></div>
      <table><thead><tr><th>Record</th><th>Status</th><th>Date</th><th>${bad ? "N" : "Record count"}</th></tr></thead>
        <tbody id="rows"></tbody></table></section><p id="scope" class="muted"></p>`;
  const draw = () => {
    const rows = rowsFor(report, active);
    document.querySelector("#rows").innerHTML = rows.map(row => `<tr><td>${escapeText(row.name)}</td>
      <td><span class="status">${row.status}</span></td><td>${row.date}</td><td>${row.count}</td></tr>`).join("");
    document.querySelector("#scope").textContent = bad ? `${rows.length} / ${report.count}` : `Showing ${rows.length} of ${report.count} records. Export includes this filtered view.`;
    document.querySelector("#all").setAttribute("aria-pressed", String(!active));
    document.querySelector("#active").setAttribute("aria-pressed", String(active));
    document.querySelector("#export").href = url("report-export", { report: report.id, status: active ? "active" : "all" });
  };
  document.querySelector("#all").onclick = () => { active = false; draw(); };
  document.querySelector("#active").onclick = () => { active = true; draw(); };
  draw();
}

function reportExport() {
  const report = currentReport();
  if (!report) return missing("This report does not exist.", "reports");
  const active = params.get("status") === "active";
  const rows = rowsFor(report, active);
  content.innerHTML = breadcrumbs([
    link("reports", {}, "Report Center"), link("report-detail", { report: report.id, status: active ? "active" : "all" }, report.name), "Export"
  ]) + heading("Export report", bad ? "Output options" : "Confirm the scope and format before downloading.") + `
    <div class="form-layout"><section class="panel"><div class="panel-head"><h2>${escapeText(report.name)}</h2></div>
      <form id="export-form" class="form-body">
        <fieldset><legend>${bad ? "Mode" : "File format"}</legend>
          <label class="choice"><input type="radio" name="format" value="csv" checked> CSV</label>
          <label class="choice"><input type="radio" name="format" value="json"> JSON</label>
        </fieldset>
        <label class="choice"><input id="headers" type="checkbox" checked> ${bad ? "H" : "Include column headers in CSV"}</label>
        <button id="csv" class="primary" type="submit">${bad ? "Process" : "Download file"}</button>
      </form></section>
      <section class="help-card" id="export-scope"><h2>Export scope</h2>
        <p>${escapeText(report.location)} · ${active ? "Active records only" : "All records"}</p>
        <p><strong>${rows.length} rows</strong> from ${escapeText(report.name)}</p>
        <p>CSV filename: lab-report.csv</p>
      </section></div>`;
  document.querySelector("#export-form").onsubmit = event => {
    event.preventDefault();
    const format = new FormData(event.currentTarget).get("format");
    const headers = document.querySelector("#headers").checked;
    const data = format === "json" ? JSON.stringify(rows, null, 2) :
      (headers ? "report,status,date,count\n" : "") + rows.map(row => [row.name, row.status, row.date, row.count].join(",")).join("\n");
    const blobUrl = URL.createObjectURL(new Blob([data], { type: format === "json" ? "application/json" : "text/csv" }));
    const anchor = document.createElement("a");
    anchor.href = blobUrl;
    anchor.download = `lab-report.${format}`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    document.querySelector("#message").innerHTML = bad ? '<p class="muted">Done.</p>' :
      `<p class="notice">Download started: lab-report.${format} · ${rows.length} ${active ? "active " : ""}records from ${escapeText(report.name)}.</p>`;
  };
}

function deviceIndex() {
  const saved = storedDevices();
  if (saved === null) return;
  const devices = [...inventory, ...saved];
  content.innerHTML = heading("Device inventory", "Lab North · Inspect existing devices or register a new one.",
    link("device-new", {}, bad ? "Process" : "Add device →", 'id="new-device" class="button primary"')) + `
    <section class="panel"><div class="panel-head"><h2>Registered devices</h2><span class="legend">${devices.length} devices</span></div>
      <table><thead><tr><th>Device</th><th>Serial number</th><th>Location</th><th>Open</th></tr></thead>
      <tbody id="created">${devices.map(device => `<tr><td>${escapeText(device.name)}</td><td>${escapeText(device.serial)}</td><td>Lab North</td>
        <td>${link("device-detail", { device: device.id }, bad ? "Open" : "View device →", 'class="button row-action"')}</td></tr>`).join("")}</tbody></table></section>`;
}

function deviceNew() {
  const saved = storedDevices();
  if (saved === null) return;
  content.innerHTML = breadcrumbs([link("device", {}, "Device inventory"), "New device"]) +
    heading(bad ? "Entry" : "Create a device", bad ? "Complete the following" : "Add a device to Lab North. All fields are required.") + `
    <div class="form-layout"><section class="panel"><form id="device-form" novalidate><div class="form-body">
      ${[
        ["name", "Device name", "review-device-01", "A name you can recognize later."],
        ["serial", "Serial number", "TEST-0001", "Format: TEST- followed by four digits. Must be unique."],
        ["threshold", "Temperature warning threshold (°C)", "80", "Enter a whole number between 1 and 120."]
      ].map(([name, label, placeholder, hint], index) => `<div class="field">
        <label for="${name}">${bad ? ["Reference", "Code", "Value"][index] : label}</label>
        <input id="${name}" name="${name}" ${bad ? "" : `placeholder="${placeholder}" aria-describedby="hint-${name}"`} autocomplete="off" maxlength="120">
        ${bad ? "" : `<p class="hint" id="hint-${name}">${hint}</p>`}<p id="error-${name}" class="error" hidden></p>
      </div>`).join("")}
      <p id="form-error" class="error" role="alert" hidden></p>
      <button class="primary" type="submit">${bad ? "Process" : "Create device"}</button>
    </div></form></section>${bad ? "" : `<section class="help-card"><h2>Before you begin</h2>
      <p>A serial identifies a unit. The threshold controls when its temperature needs attention.</p>
      <p>Location: Lab North. Successful registration opens the saved device record.</p>
    </section>`}</div>`;
  document.querySelector("#device-form").onsubmit = event => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    values.name = values.name.trim();
    const errors = {};
    if (!values.name) errors.name = "Device name is required.";
    if (!/^TEST-\d{4}$/.test(values.serial)) errors.serial = "Serial number must be TEST- followed by four digits.";
    if ([...inventory, ...saved].some(device => device.serial === values.serial)) errors.serial = "This serial number is already registered.";
    if (!/^\d+$/.test(values.threshold) || Number(values.threshold) < 1 || Number(values.threshold) > 120) {
      errors.threshold = "Temperature threshold must be a whole number from 1 to 120 °C.";
    }
    document.querySelectorAll(".error").forEach(element => { element.hidden = true; });
    if (Object.keys(errors).length) {
      if (bad) {
        document.querySelector("#form-error").textContent = Object.values(errors).join(" ");
        document.querySelector("#form-error").hidden = false;
        form.reset();
      } else {
        for (const [key, message] of Object.entries(errors)) {
          const element = document.querySelector(`#error-${key}`);
          element.textContent = message;
          element.hidden = false;
        }
      }
      return;
    }
    try {
      localStorage.setItem("visualux-devices", JSON.stringify([...saved, values]));
    } catch {
      const error = document.querySelector("#form-error");
      error.textContent = "The device could not be saved. Check browser storage and retry; your values are retained.";
      error.hidden = false;
      return;
    }
    location.assign(url("device-detail", { device: `created-${saved.length + 1}` }));
  };
}

function deviceDetail() {
  const saved = storedDevices();
  if (saved === null) return;
  const device = [...inventory, ...saved].find(item => item.id === params.get("device"));
  if (!device) return missing("This saved device does not exist.", "device");
  content.innerHTML = breadcrumbs([link("device", {}, "Device inventory"), escapeText(device.name)]) +
    heading(device.name, "Registered device · Lab North") + `
    <section class="panel"><div class="panel-head"><h2>Saved configuration</h2><span class="badge">Registered</span></div>
      <dl id="saved-device" class="record-details">
        <dt>Device name</dt><dd>${escapeText(device.name)}</dd>
        <dt>Serial number</dt><dd>${escapeText(device.serial)}</dd>
        <dt>Location</dt><dd>Lab North</dd>
        <dt>Temperature warning threshold</dt><dd>${escapeText(device.threshold)} °C</dd>
      </dl></section>
    <p>${link("device", {}, "Back to device inventory")}</p>`;
}

const screens = {
  lab: labIndex,
  "lab-devices": labList,
  "lab-device": labDetail,
  reports: reportIndex,
  "report-detail": reportDetail,
  "report-export": reportExport,
  device: deviceIndex,
  "device-new": deviceNew,
  "device-detail": deviceDetail
};
if (Object.hasOwn(screens, route)) screens[route]();
else missing("This demo page is not available.");
