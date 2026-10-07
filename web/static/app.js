"use strict";

const $ = id => document.getElementById(id);
const DAY = 86400000;
const options = ["theme", "contact", "dates", "viewports"];
const chartNames = ["daily", "hourly", "heatmap"];
const state = {
  preferences: JSON.parse($("initial-preferences").textContent),
  status: null, contacts: [], account: "", username: "", dates: {}, viewports: {},
  data: null, dataUsername: "", hourly: null, rendering: false, analysisSerial: 0,
  busy: false, loadingAnalysis: false, initializing: false, inspected: false,
  typeExpanded: false, activityVisible: true,
  panelTouched: false, finishedTask: "", saveTimer: null, saving: false, saveAgain: false,
};
const contactPicker = {items: [], index: -1};
const number = value => Number(value || 0).toLocaleString("zh-CN");
function say(message, error = false) {
  $("message").hidden = !message;
  $("message").textContent = message;
  $("message").className = error ? "error" : "";
}
async function api(path, method = "GET", body = null) {
  const response = await fetch(path, {
    method, headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : undefined, cache: "no-store",
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "本机服务请求失败，请重试。");
  return result;
}
function millis(value) {
  if (typeof value === "number") return value;
  const text = String(value).replace(" ", "T");
  return Date.parse(text.length === 10 ? text + "T00:00:00Z" : /Z$|[+-]\d\d:\d\d$/.test(text) ? text : text + "Z");
}
function iso(value) { return new Date(value).toISOString(); }
function rangeKey(name) { return name === "hourly" ? "xaxis2" : "xaxis"; }
function safeRange(value, bounds, minimum = DAY) {
  const full = bounds.map(millis);
  if (!value || value.length !== 2) return recentRange(bounds);
  const selected = value.map(millis);
  if (!selected.every(Number.isFinite) || selected[1] <= selected[0] ||
      selected[1] <= full[0] || selected[0] >= full[1]) return recentRange(bounds);
  const width = Math.min(full[1] - full[0], Math.max(Math.min(minimum, full[1] - full[0]), selected[1] - selected[0]));
  const left = Math.max(full[0], Math.min(full[1] - width, selected[0]));
  return [iso(left), iso(left + width)];
}
function recentRange(bounds) {
  const endDay = millis(bounds[1].slice(0, 10));
  return [iso(Math.max(millis(bounds[0]), endDay - 29 * DAY)), iso(millis(bounds[1]))];
}
function palette() {
  const dark = document.documentElement.dataset.theme === "dark";
  return dark ?
    {background: "#0e1117", surface: "#262730", text: "#fafafa", grid: "#343946", border: "#515b78", mine: "#8193ff", other: "#c4dd58", total: "#a4aecb"} :
    {background: "#ffffff", surface: "#f0f2f6", text: "#31333f", grid: "#e5e7eb", border: "#d5dae3", mine: "#5266ce", other: "#7b9424", total: "#68738f"};
}
function layout(height = 450) {
  const colors = palette();
  return {
    height, paper_bgcolor: colors.background, plot_bgcolor: colors.background,
    font: {color: colors.text, family: "Microsoft YaHei, sans-serif"},
    margin: {l: 58, r: 30, t: 38, b: 25},
    dragmode: "pan", hovermode: "x unified",
    xaxis: {showgrid: false, zeroline: false},
    yaxis: {fixedrange: true, gridcolor: colors.grid, zerolinecolor: colors.grid, rangemode: "tozero"},
    legend: {orientation: "h", x: .5, xanchor: "center", y: 1.13},
    hoverlabel: {bgcolor: colors.surface, bordercolor: colors.border, font: {color: colors.text}},
  };
}
function dateAxis(name, data) {
  const colors = palette();
  return {
    type: "date", tickformat: "%m/%d", showgrid: false, zeroline: false,
    range: safeRange(state.viewports[name], data.bounds, name === "daily" ? 1 : DAY),
    minallowed: data.bounds[0], maxallowed: data.bounds[1],
    rangeslider: {visible: true, thickness: .1, bgcolor: colors.surface, bordercolor: colors.border,
      borderwidth: 1, autorange: false, range: data.bounds},
  };
}
function metric(target, items) {
  $(target).replaceChildren();
  for (const [label, value, detail] of items) {
    const card = document.createElement("div");
    card.className = "metric";
    for (const [className, text] of [["metric-label", label], ["metric-value", value], ["metric-detail", detail]]) {
      if (text == null) continue;
      const element = document.createElement("div");
      element.className = className;
      element.textContent = text;
      card.append(element);
    }
    $(target).append(card);
  }
}
function table(target, columns, rows, message) {
  $(target).replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.style.padding = "0 16px";
    empty.textContent = message;
    $(target).append(empty);
    return;
  }
  const element = document.createElement("table");
  const head = element.createTHead().insertRow();
  columns.forEach(name => {
    const th = document.createElement("th");
    th.textContent = name;
    head.append(th);
  });
  const body = element.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const name of columns) {
      const cell = tr.insertCell();
      const value = row[name];
      if (name === "内容") {
        cell.className = "content";
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = value.slice(0, 160) + (value.length > 160 ? "…" : "");
        const full = document.createElement("pre");
        full.textContent = value;
        details.append(summary, full);
        cell.append(details);
      } else {
        cell.textContent = name === "占比" ? Number(value).toFixed(1) + "%" :
          name === "时间" ? String(value).replace("T", " ").slice(0, 19) : String(value);
        if (typeof value === "number") cell.className = "numeric";
      }
    }
  }
  $(target).append(element);
}
function renderMessageTypes(types) {
  const hiddenCount = Math.max(0, types.length - 3);
  if (!hiddenCount) state.typeExpanded = false;
  table("type-table", ["消息类型", "消息数量", "占比", "我发送", "对方发送"],
    state.typeExpanded ? types : types.slice(0, 3), "所选日期范围内没有可统计的消息类型。");
  $("type-toggle").hidden = !hiddenCount;
  $("type-toggle").setAttribute("aria-expanded", String(state.typeExpanded));
  $("type-toggle-label").textContent = state.typeExpanded ? "收起，只显示前三类" : "展开其余 " + hiddenCount + " 类";
}
function renderTextStatistics(stats) {
  $("text-metrics").hidden = !stats;
  if (stats) {
    metric("text-metrics", [["总计", "total"], ["我发送", "me"], ["对方发送", "other"]].map(([label, scope]) =>
      [label + " · 汉字数", number(stats[scope].chinese_chars), "所有字符数：" + number(stats[scope].all_chars)]));
  }
  const warning = stats?.warning || (!stats ? "请重启本机服务以启用文字统计。" : "");
  $("text-warning").hidden = !warning;
  $("text-warning").textContent = warning;
  table("word-table", ["词语", "总次数", "我发送", "对方发送"], stats?.words || [],
    warning ? "高频词暂不可用。" : "该联系人的全部记录中没有通过过滤的词语。");
}
function renderSummary(data) {
  const s = data.metrics;
  metric("metrics", [["总消息", number(s.total)], ["我发送", number(s.mine)],
    ["对方发送", number(s.other)], ["活跃天数", number(s.active_days)], ["文字消息", number(s.text_count)]]);
  metric("peak-metrics", [["消息最多的一天", s.busiest_day || "—", number(s.busiest_day_count) + " 条"],
    ["最活跃小时段", s.busiest_hour == null ? "—" :
      String(s.busiest_hour).padStart(2, "0") + ":00–" + String(s.busiest_hour + 1).padStart(2, "0") + ":00"]]);
  $("sender-note").textContent = data.system_count || data.unknown_count ?
    "总消息包含 " + number(data.system_count) + " 条系统消息、" + number(data.unknown_count) + " 条发送者未识别消息，不计入双方发送数量。" : "";
  metric("reference-metrics", [["引用次数", number(data.references.total)], ["我引用", number(data.references.mine)], ["对方引用", number(data.references.other)]]);
  renderMessageTypes(data.types);
  renderTextStatistics(data.text_stats);
  table("longest-table", ["排名", "字符数", "发送者", "时间", "内容"], data.longest, "所选日期范围内没有可解析的文字消息。");
  $("longest-count").textContent = data.longest.length + " 条";
  const available = data.available;
  $("available-range").textContent = data.empty ? "该联系人在当前副本中没有可用聊天记录。" :
    "已读取 " + available.shards + " 个消息分片；该联系人的可用记录：" + available.first + " 至 " + available.last + "，共 " + number(available.count) + " 条。";
  $("analysis-warnings").replaceChildren();
  const warnings = [...data.warnings];
  if (!data.empty && (state.dates.global.start < available.first || state.dates.global.end > available.last)) {
    warnings.push("超出该联系人可用记录范围的日期没有可统计数据；副本复制时间与聊天记录日期不同。");
  }
  for (const warning of warnings) {
    const div = document.createElement("div");
    div.className = "warning-box";
    div.textContent = warning;
    $("analysis-warnings").append(div);
  }
  chartNames.forEach(name => {
    const selected = state.dates[name];
    $(name + "-count").textContent = "统计区间：" + selected.start + " 至 " + selected.end +
      " · " + number(data.charts[name].count) + " 条消息";
  });
  renderActivityNote(data.charts.daily.period);
}
function renderActivityNote(period = state.data?.charts.daily.period) {
  $("activity-note").hidden = !state.activityVisible;
  $("activity-note").className = period ? "success" : "muted small";
  $("activity-note").textContent = period ?
    "持续高活跃：" + period.start + " 至 " + period.end + "（" + period.days + " 天），双方共 " + number(period.message_count) + " 条消息。" :
    "当前范围未识别出明显的持续高活跃区间。";
}
function emptyChart(id) {
  const gd = $(id);
  if (gd._fullLayout) Plotly.purge(gd);
  delete gd.dataset.wired;
  gd.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "empty-chart";
  empty.textContent = "本图所选日期范围内没有可浏览的记录。";
  gd.append(empty);
}
async function renderDaily(data) {
  if (!data.bounds) { emptyChart("daily-chart"); return; }
  const colors = palette();
  const traces = ["我", "对方", "总计"].map((name, index) => ({
    type: "scatter", mode: "lines+markers", name, x: data.dates, y: data.counts[name],
    line: {color: [colors.mine, colors.other, colors.total][index], width: 2, shape: "spline", smoothing: .5},
    marker: {size: 4}, fill: index === 2 ? "tozeroy" : "none", fillcolor: "rgba(113,131,236,.16)",
    hovertemplate: name + "：%{y} 条<extra></extra>",
  }));
  const figure = layout(480);
  figure.xaxis = dateAxis("daily", data);
  figure.xaxis.hoverformat = "%Y-%m-%d";
  figure.yaxis.title = {text: "消息数量"};
  figure.uirevision = state.account + state.username + JSON.stringify(state.dates.daily);
  if (data.period) {
    figure.shapes = [{type: "rect", xref: "x", yref: "paper", x0: data.period.start,
      x1: iso(millis(data.period.end) + DAY - 1), y0: 0, y1: 1,
      fillcolor: "rgba(239,68,68,.12)", line: {width: 0}, layer: "below",
      name: "高活跃", showlegend: true, legendgroup: "activity",
      visible: state.activityVisible ? true : "legendonly"}];
  }
  const gd = $("daily-chart");
  await Plotly.react(gd, traces, figure, {responsive: true, scrollZoom: true, displayModeBar: false, displaylogo: false});
  if (!gd.dataset.wired) {
    gd.dataset.wired = "true";
    gd.on("plotly_relayout", event => {
      if (state.rendering || state.loadingAnalysis) return;
      const visible = event["shapes[0].visible"] ?? event.shapes?.[0]?.visible;
      if (visible !== undefined) {
        state.activityVisible = visible !== false && visible !== "legendonly";
        renderActivityNote();
      }
      if (!Object.keys(event).some(key => key.startsWith("xaxis."))) return;
      state.viewports.daily = gd.layout.xaxis.range.map(value => iso(millis(value)));
      scheduleSave();
    });
  }
  state.viewports.daily = gd.layout.xaxis.range.map(value => iso(millis(value)));
}
function hourlyCounts(bounds) {
  const client = state.hourly;
  const first = Math.max(0, Math.min(client.dates.length - 1, Math.floor((millis(bounds[0]) - client.origin) / DAY)));
  const after = Math.max(first + 1, Math.min(client.dates.length, Math.ceil((millis(bounds[1]) - client.origin) / DAY)));
  return {counts: client.prefix[after].map((count, hour) => count - client.prefix[first][hour]), first, after};
}
function updateHourly() {
  const gd = $("hourly-chart");
  if (!state.hourly || !gd.layout) return;
  const bounds = gd.layout.xaxis2.range;
  const {counts, first, after} = hourlyCounts(bounds);
  const total = counts.reduce((sum, value) => sum + value, 0);
  $("hourly-visible").textContent = state.hourly.dates[first] + " 至 " + state.hourly.dates[after - 1] + " · " + number(total) + " 条消息";
  gd.dataset.start = state.hourly.dates[first];
  gd.dataset.end = state.hourly.dates[after - 1];
  gd.dataset.count = total;
  state.viewports.hourly = bounds.map(value => iso(millis(value)));
  Plotly.restyle(gd, {y: [counts]}, [0]);
}
function setViewport(name, value) {
  const data = state.data?.charts[name];
  const gd = $(name + "-chart");
  if (!data?.bounds || !gd.layout) return Promise.resolve();
  const safe = safeRange(value, data.bounds, name === "daily" ? 1 : DAY);
  state.viewports[name] = safe;
  return Plotly.relayout(gd, {[rangeKey(name) + ".range"]: safe});
}
function wireHourly(gd) {
  if (gd.dataset.wired) return;
  gd.dataset.wired = "true";
  gd.on("plotly_relayout", event => {
    if (state.rendering || state.loadingAnalysis || !state.hourly || !Object.keys(event).some(key => key.startsWith("xaxis2."))) return;
    const current = gd.layout.xaxis2.range.map(millis);
    const safe = safeRange(gd.layout.xaxis2.range, state.data.charts.hourly.bounds).map(millis);
    if (Math.abs(current[0] - safe[0]) > 1 || Math.abs(current[1] - safe[1]) > 1) {
      setViewport("hourly", safe);
    } else { updateHourly(); scheduleSave(); }
  });
  if (gd._hourDomWired) return;
  gd._hourDomWired = true;
  gd.addEventListener("wheel", event => {
    if (state.loadingAnalysis || !state.hourly || !gd.layout) return;
    event.preventDefault();
    const current = gd.layout.xaxis2.range.map(millis);
    const factor = Math.exp(Math.max(-180, Math.min(180, event.deltaY)) / 400);
    const size = gd._fullLayout._size, box = gd.getBoundingClientRect();
    const anchor = Math.max(0, Math.min(1, (event.clientX - box.left - size.l) / size.w));
    const width = (current[1] - current[0]) * factor;
    const center = current[0] + (current[1] - current[0]) * anchor;
    setViewport("hourly", [center - width * anchor, center + width * (1 - anchor)]);
  }, {passive: false, capture: true});
  let dragging = null;
  gd.addEventListener("mousedown", event => {
    if (state.loadingAnalysis || !state.hourly || !gd._fullLayout) return;
    const box = gd.getBoundingClientRect(), size = gd._fullLayout._size;
    const x = event.clientX - box.left, y = event.clientY - box.top;
    if (event.button === 0 && x >= size.l && x <= size.l + size.w && y >= size.t && y <= size.t + size.h * .57) {
      dragging = {x: event.clientX, bounds: gd.layout.xaxis2.range.map(millis), width: size.w};
      event.preventDefault();
    }
  }, {capture: true});
  document.addEventListener("mousemove", event => {
    if (!dragging) return;
    const shift = -(event.clientX - dragging.x) / dragging.width * (dragging.bounds[1] - dragging.bounds[0]);
    setViewport("hourly", [dragging.bounds[0] + shift, dragging.bounds[1] + shift]);
  }, {capture: true});
  document.addEventListener("mouseup", () => { dragging = null; }, {capture: true});
  gd.addEventListener("dblclick", () => {
    if (state.hourly) setViewport("hourly", recentRange(state.data.charts.hourly.bounds));
  });
}
async function renderHourly(data) {
  if (!data.bounds || !data.dates?.length) {
    state.hourly = null;
    $("hourly-visible").textContent = "没有可浏览的记录";
    emptyChart("hourly-chart"); return;
  }
  const colors = palette();
  const prefix = [Array(24).fill(0)];
  for (const row of data.hours) prefix.push(row.map((count, hour) => Number(count) + prefix[prefix.length - 1][hour]));
  state.hourly = {prefix, dates: data.dates, origin: millis(data.bounds[0])};
  const axis = dateAxis("hourly", data);
  const {counts} = hourlyCounts(axis.range);
  const traces = [
    {type: "bar", x: Array.from({length: 24}, (_, hour) => hour), y: counts,
      marker: {color: colors.mine}, hovertemplate: "%{x}:00<br>%{y} 条<extra></extra>"},
    {type: "scatter", x: data.dates, y: data.counts["总计"], xaxis: "x2", yaxis: "y2", mode: "lines",
      line: {color: colors.total, width: 1.5}, fill: "tozeroy", fillcolor: "rgba(113,131,236,.16)",
      hovertemplate: "%{x|%Y-%m-%d}<br>%{y} 条<extra></extra>"},
  ];
  const figure = layout(510);
  figure.showlegend = false;
  figure.hovermode = "closest";
  figure.xaxis = {title: {text: "小时"}, dtick: 1, fixedrange: true, showgrid: false, anchor: "y"};
  figure.yaxis = {...figure.yaxis, title: {text: "消息数量"}, domain: [.43, 1]};
  figure.xaxis2 = {...axis, anchor: "y2"};
  figure.yaxis2 = {domain: [.09, .24], fixedrange: true, showgrid: false, showticklabels: false, zeroline: false};
  const gd = $("hourly-chart");
  await Plotly.react(gd, traces, figure, {responsive: true, displaylogo: false, displayModeBar: false, scrollZoom: false, doubleClick: false});
  wireHourly(gd);
  updateHourly();
}
async function renderHeatmap(data) {
  const figure = layout(330);
  figure.hovermode = "closest"; figure.margin.t = 10;
  figure.xaxis = {title: {text: "小时"}, dtick: 1, showgrid: false, fixedrange: true};
  figure.yaxis = {autorange: "reversed", fixedrange: true};
  await Plotly.react($("heatmap-chart"), [{
    type: "heatmap", z: data.counts, x: Array.from({length: 24}, (_, i) => i),
    y: ["周一", "周二", "周三", "周四", "周五", "周六", "周日"], colorscale: "Reds", zmin: 0,
    colorbar: {title: {text: "消息数"}}, hovertemplate: "%{y} %{x}:00<br>%{z} 条<extra></extra>",
  }], figure, {responsive: true, displayModeBar: false, displaylogo: false});
}
async function renderCharts() {
  if (!state.data) return;
  state.rendering = true;
  try {
    await renderDaily(state.data.charts.daily);
    await renderHourly(state.data.charts.hourly);
    await renderHeatmap(state.data.charts.heatmap);
  } finally { state.rendering = false; }
}
function updateDateInputs() {
  for (const [name, value] of Object.entries(state.dates)) {
    $(name + "-start").value = value.start;
    $(name + "-end").value = value.end;
  }
}
function updateDisabled() {
  const status = state.status;
  document.querySelectorAll("[data-operation]").forEach(button => { button.disabled = state.busy; });
  $("refresh").disabled = state.busy || !status?.active.valid || !!status?.active.reference_test;
  $("reinitialize").disabled = state.busy || !status?.active.valid;
  $("copy").disabled = state.busy || !state.inspected;
  const pending = status?.pending;
  $("extract").disabled = state.busy || !pending || !["copied", "ready"].includes(pending.stage);
  $("decrypt").disabled = state.busy || !pending || !Object.keys(pending.keys).length || !Object.values(pending.keys).every(Boolean);
  $("version").disabled = state.busy;
  $("data-path").disabled = state.busy;
  $("contact-toggle").disabled = state.busy || !state.contacts.length;
  $("contact-search").disabled = state.busy;
  if (state.busy) closeContactPicker();
  document.querySelectorAll("input[type=date]").forEach(input => { input.disabled = state.busy; });
  document.querySelectorAll("[data-select-dates]").forEach(button => {
    button.disabled = state.busy || !state.data || state.data.empty;
  });
}
function extendDateRanges(dates, viewports, oldEnd, nextEnd) {
  const result = {dates: structuredClone(dates), viewports: structuredClone(viewports), changed: false};
  if (!oldEnd || !nextEnd || nextEnd <= oldEnd) return result;
  const shift = millis(nextEnd) - millis(oldEnd);
  for (const name of ["global", ...chartNames]) {
    const selected = dates[name];
    if (!selected || selected.end !== oldEnd) continue;
    result.dates[name].end = nextEnd;
    result.changed = true;
    const viewport = viewports[name];
    if (!Array.isArray(viewport) || viewport.length !== 2) continue;
    const bounds = viewport.map(millis);
    if (!bounds.every(Number.isFinite) || iso(bounds[1]).slice(0, 10) !== oldEnd) continue;
    // 已显示全部历史时保留左端；停在最新日期的局部视图则保持宽度向后移动。
    const left = bounds[0] <= millis(selected.start) ? bounds[0] : bounds[0] + shift;
    result.viewports[name] = [iso(left), iso(bounds[1] + shift)];
  }
  return result;
}
function applyDateRange(name, start, end, showAll = false) {
  const names = name === "global" ? ["global", ...chartNames] : [name];
  for (const scope of names) {
    state.dates[scope] = {start, end};
    if (showAll && ["daily", "hourly"].includes(scope)) {
      state.viewports[scope] = [start, end + "T23:59:59.999"];
    } else delete state.viewports[scope];
  }
  updateDateInputs(); say("");
  return loadAnalysis();
}
function selectAllDates(name) {
  if (!state.data || state.data.empty || state.busy) return;
  const available = state.data.available;
  const end = available.last > state.status.today ? state.status.today : available.last;
  return applyDateRange(name, available.first, end, true);
}
async function loadAnalysis() {
  if (!state.username || state.busy) return;
  const serial = ++state.analysisSerial;
  state.loadingAnalysis = true;
  $("loading").hidden = false;
  $("loading").textContent = "正在加载该联系人的分片与统计……";
  try {
    const username = state.username;
    const oldEnd = state.dataUsername === username && !state.data?.empty ? state.data?.available.last : null;
    let data = await api("/api/analysis", "POST", {username, global: state.dates.global, charts: state.dates});
    if (serial !== state.analysisSerial) return;
    const nextEnd = data.available.last > state.status.today ? state.status.today : data.available.last;
    const extended = extendDateRanges(state.dates, state.viewports, oldEnd, nextEnd);
    if (extended.changed) {
      data = await api("/api/analysis", "POST", {username, global: extended.dates.global, charts: extended.dates});
      if (serial !== state.analysisSerial) return;
      state.dates = extended.dates;
      state.viewports = extended.viewports;
    }
    state.data = data;
    state.dataUsername = username;
    const fallback = {start: data.available.first, end: data.available.last > state.status.today ? state.status.today : data.available.last};
    for (const name of ["global", ...chartNames]) state.dates[name] ||= {...(state.dates.global || fallback)};
    updateDateInputs();
    updateDisabled();
    $("analysis").hidden = false;
    renderSummary(data);
    await renderCharts();
    if (serial === state.analysisSerial) {
      state.loadingAnalysis = false;
      scheduleSave();
    }
  } catch (error) {
    if (serial === state.analysisSerial) say(error.message, true);
  } finally {
    if (serial === state.analysisSerial) {
      state.loadingAnalysis = false;
      $("loading").hidden = true;
    }
  }
}
function scopedView(username) {
  return state.preferences.accounts?.[state.account]?.views?.[username] || {};
}
async function chooseContact(username, preserve = false) {
  state.username = username;
  closeContactPicker();
  if (!preserve) {
    state.typeExpanded = false;
    $("longest-panel").open = false;
    const saved = scopedView(username);
    state.dates = state.preferences.remember.dates ? structuredClone(saved.dates || {}) : {};
    state.viewports = state.preferences.remember.viewports ? structuredClone(saved.viewports || {}) : {};
  }
  await loadAnalysis();
}
function contactName(contact) { return contact.display_name || contact.label || contact.username; }
function contactDetail(contact) {
  return "微信昵称：" + (contact.nick_name || "未设置昵称") + " · 微信号：" + (contact.wechat_id || contact.username);
}
function updateSelectedContact() {
  const contact = state.contacts.find(item => item.username === state.username);
  $("contact-search").value = contact ? contactName(contact) : "";
  $("contact-search").title = contact?.label || "";
  $("contact-subtitle").textContent = contact ? contactDetail(contact) : "选择一位好友查看聊天统计";
  $("contact-avatar").textContent = contact ? Array.from(contactName(contact))[0] : "微";
  $("contact-note").textContent = "仅显示通讯录好友 · 共 " + state.contacts.length + " 位";
}
function closeContactPicker() {
  $("contact-dropdown").hidden = true;
  $("contact-search").setAttribute("aria-expanded", "false");
  $("contact-search").removeAttribute("aria-activedescendant");
  $("contact-toggle").setAttribute("aria-expanded", "false");
  $("contact-toggle").setAttribute("aria-label", "展开联系人列表");
  updateSelectedContact();
}
function highlightContact(index, scroll = false) {
  contactPicker.index = index;
  const entries = $("contact-options").children;
  Array.from(entries).forEach((entry, position) => entry.classList.toggle("is-active", position === index));
  if (index < 0) { $("contact-search").removeAttribute("aria-activedescendant"); return; }
  $("contact-search").setAttribute("aria-activedescendant", entries[index].id);
  if (scroll) entries[index].scrollIntoView({block: "nearest"});
}
function pickContact(username) {
  if (state.busy) return;
  if (username === state.username) { closeContactPicker(); return; }
  say("");
  chooseContact(username);
}
function populateContacts() {
  const search = $("contact-search").value.toLocaleLowerCase().trim();
  contactPicker.items = state.contacts.filter(contact =>
    [contact.label, contact.display_name, contact.nick_name, contact.wechat_id, contact.username]
      .some(value => String(value || "").toLocaleLowerCase().includes(search)));
  $("contact-options").replaceChildren();
  contactPicker.items.forEach((contact, index) => {
    const entry = document.createElement("button");
    entry.type = "button"; entry.tabIndex = -1;
    entry.id = "contact-option-" + index; entry.className = "contact-option";
    entry.setAttribute("role", "option");
    entry.setAttribute("aria-selected", String(contact.username === state.username));
    const avatar = document.createElement("span");
    avatar.className = "contact-avatar"; avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = Array.from(contactName(contact))[0];
    const description = document.createElement("span");
    description.className = "contact-description";
    const name = document.createElement("span");
    name.className = "contact-name"; name.textContent = contactName(contact);
    const detail = document.createElement("span");
    detail.className = "muted small"; detail.textContent = contactDetail(contact);
    description.append(name, detail); entry.append(avatar, description);
    if (contact.username === state.username) {
      const check = document.createElement("span");
      check.className = "contact-check"; check.textContent = "✓"; check.setAttribute("aria-hidden", "true");
      entry.append(check);
    }
    entry.addEventListener("mousedown", event => event.preventDefault());
    entry.addEventListener("click", () => pickContact(contact.username));
    $("contact-options").append(entry);
  });
  $("contact-results").textContent = search ? "匹配 " + contactPicker.items.length + " 位好友" : "共 " + state.contacts.length + " 位好友 · 输入关键词搜索";
  $("contact-empty").hidden = !!contactPicker.items.length;
  const selected = contactPicker.items.findIndex(contact => contact.username === state.username);
  highlightContact(selected >= 0 ? selected : contactPicker.items.length ? 0 : -1);
}
function openContactPicker() {
  if (state.busy || !$("contact-dropdown").hidden) return;
  $("contact-dropdown").hidden = false;
  $("contact-search").value = "";
  $("contact-search").setAttribute("aria-expanded", "true");
  $("contact-toggle").setAttribute("aria-expanded", "true");
  $("contact-toggle").setAttribute("aria-label", "收起联系人列表");
  populateContacts();
  $("contact-search").focus();
}
function installContactEvents() {
  const input = $("contact-search");
  input.addEventListener("focus", openContactPicker);
  input.addEventListener("input", () => {
    if (state.busy) return;
    $("contact-dropdown").hidden = false;
    input.setAttribute("aria-expanded", "true");
    $("contact-toggle").setAttribute("aria-expanded", "true");
    $("contact-toggle").setAttribute("aria-label", "收起联系人列表");
    populateContacts();
  });
  input.addEventListener("keydown", event => {
    if (event.isComposing) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if ($("contact-dropdown").hidden) { openContactPicker(); return; }
      if (contactPicker.items.length) {
        const step = event.key === "ArrowDown" ? 1 : -1;
        highlightContact((contactPicker.index + step + contactPicker.items.length) % contactPicker.items.length, true);
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      if ($("contact-dropdown").hidden) openContactPicker();
      else if (contactPicker.index >= 0) pickContact(contactPicker.items[contactPicker.index].username);
    } else if (event.key === "Escape") {
      event.preventDefault(); closeContactPicker(); input.select();
    } else if (event.key === "Tab") closeContactPicker();
  });
  $("contact-toggle").addEventListener("click", () => {
    if ($("contact-dropdown").hidden) openContactPicker(); else closeContactPicker();
  });
  document.addEventListener("click", event => {
    if (!$("contact-picker").contains(event.target) && !$("contact-dropdown").hidden) closeContactPicker();
  });
}
async function loadContacts(preserve = false) {
  const response = await api("/api/contacts");
  state.contacts = response.contacts;
  updateDisabled();
  if (!state.contacts.length) { $("analysis").hidden = true; say("当前副本中没有找到通讯录好友。"); return; }
  const validCurrent = preserve && state.contacts.some(contact => contact.username === state.username);
  if (!validCurrent) {
    const remembered = state.preferences.remember.contact ?
      state.preferences.accounts?.[state.account]?.selected_contact : "";
    if (remembered && !state.contacts.some(contact => contact.username === remembered)) say("上次选择的联系人不在当前通讯录中，已选择默认联系人。");
    state.username = state.contacts.some(contact => contact.username === remembered) ? remembered : state.contacts[0].username;
  }
  await chooseContact(state.username, validCurrent);
}
function renderStatus(status) {
  state.status = status;
  const active = status.active;
  state.busy = status.task?.state === "running";
  if (!$("version").value) $("version").value = status.version || active.version || "4.1.13.65";
  $("auto-version").textContent = "自动检测：" + (status.version || "未能识别，请手动输入");
  $("version-warning").hidden = $("version").value === status.verified_version;
  $("database-badge").textContent = state.busy ? "处理中…" : active.valid ? "分析副本可用" : "需要初始化";
  $("workspace-status").textContent = active.valid ? "分析副本可用" : active.error || "尚无有效分析副本";
  $("workspace-status").className = active.valid ? "success" : "notice";
  $("source-path").textContent = active.source ? "原始路径：" + active.source : "";
  $("workspace-path").textContent = active.path ? "工作副本：" + active.path : "";
  $("copied-at").textContent = active.copied_at ? "复制时间：" + active.copied_at.replace("T", " ") + " · 数据版本：" + active.version : "";
  $("copy-note").textContent = active.copied_at ? "分析副本复制于 " + active.copied_at.replace("T", " ") + "（本机时间）。数据需要手动更新；截止日期停在记录末尾时会自动跟随新记录。" : "";
  $("database-count").textContent = "数据库状态 · " + (active.databases?.length || 0) + " 个库检查通过";
  $("database-list").replaceChildren();
  for (const name of active.databases || []) {
    const item = document.createElement("li"); item.textContent = "✓ " + name + " integrity_check = ok"; $("database-list").append(item);
  }
  if (!$("data-path").value && active.source && !active.reference_test) $("data-path").value = active.source;
  $("initialization").hidden = active.valid && !state.initializing;
  if (!state.panelTouched) $("database-panel").open = !active.valid || state.initializing;
  $("pending-status").replaceChildren();
  if (status.pending) {
    const pending = status.pending;
    const info = document.createElement("p");
    info.textContent = "待解析副本：" + pending.path + " · 阶段：" + ({copying: "复制未完成", copied: "已复制", ready: "已解密"}[pending.stage] || pending.stage);
    $("pending-status").append(info);
    const detail = document.createElement("details"), summary = document.createElement("summary");
    summary.textContent = "已复制文件与密钥状态"; detail.append(summary);
    const list = document.createElement("ul");
    for (const filename of pending.files) { const item = document.createElement("li"); item.textContent = filename; list.append(item); }
    for (const [name, valid] of Object.entries(pending.keys)) {
      const item = document.createElement("li"); item.textContent = (valid ? "✓ " : "❌ ") + name + (valid ? " key 已验证" : " 未找到有效 key"); list.append(item);
    }
    detail.append(list); $("pending-status").append(detail);
  }
  document.querySelectorAll("input[type=date]").forEach(input => { input.max = status.today; });
  updateDisabled();
  const task = status.task;
  if (task && (task.state === "running" || task.action !== "validate")) {
    $("task-panel").hidden = false;
    const labels = {validate: "验证分析副本", copy: "创建分析副本", extract: "提取数据库密钥", decrypt: "解密数据库", refresh: "更新聊天记录"};
    $("task-title").textContent = (labels[task.action] || "数据库任务") + " · " + ({running: "进行中", complete: "已完成", failed: "失败"}[task.state]);
    $("task-progress").textContent = [...task.messages, task.error].filter(Boolean).join("\n");
  } else $("task-panel").hidden = true;
}
async function pollStatus() {
  try {
    const status = await api("/api/status");
    const accountChanged = state.account !== status.active.account;
    renderStatus(status);
    const task = status.task;
    const completedUpdate = task?.state === "complete" && state.finishedTask !== task.id && ["decrypt", "refresh"].includes(task.action);
    if (task && task.state !== "running" && state.finishedTask !== task.id) {
      state.finishedTask = task.id;
      if (task.state === "failed") say(task.error, true);
      if (completedUpdate) {
        state.initializing = false; $("initialization").hidden = true;
        say("分析副本已更新；截止日期原本停在记录末尾的范围会自动延长，历史筛选保持不变。");
      }
    }
    if (status.active.valid && !state.busy &&
        (!state.contacts.length || accountChanged || completedUpdate)) {
      if (accountChanged) {
        state.dates = {}; state.viewports = {}; state.username = "";
        state.data = null; state.dataUsername = ""; $("contact-search").value = "";
      }
      state.account = status.active.account;
      await loadContacts(!accountChanged && !!state.username);
    }
    if (!state.busy && !state.loadingAnalysis) $("loading").hidden = true;
  } catch (error) { say("无法连接本机服务：" + error.message, true); }
  finally { setTimeout(pollStatus, state.busy ? 1000 : 4000); }
}
function snapshotPreferences() {
  return {remember: Object.fromEntries(options.map(key => [key, $("remember-" + key).checked])),
    theme: document.documentElement.dataset.theme, username: state.username,
    dates: state.dates, viewports: state.viewports};
}
function scheduleSave() {
  if (state.loadingAnalysis) return;
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(savePreferences, 500);
}
async function savePreferences() {
  if (state.saving) { state.saveAgain = true; return; }
  state.saving = true;
  $("save-status").textContent = "正在保存…";
  try {
    state.preferences = await api("/api/preferences", "PUT", snapshotPreferences());
    $("save-status").textContent = "已保存到本机";
  } catch (error) {
    $("save-status").textContent = "保存失败：" + error.message;
    say("界面设置保存失败：" + error.message, true);
  } finally {
    state.saving = false;
    if (state.saveAgain) { state.saveAgain = false; savePreferences(); }
  }
}
async function startTask(action) {
  try {
    if (state.busy) return;
    state.busy = true; updateDisabled(); say("");
    const task = await api("/api/tasks", "POST", {action, version: $("version").value.trim(), source: $("data-path").value.trim()});
    state.status.task = task; renderStatus(state.status);
  } catch (error) { state.busy = false; updateDisabled(); say(error.message, true); }
}
function installEvents() {
  for (const key of options) {
    $("remember-" + key).checked = state.preferences.remember[key];
    $("remember-" + key).addEventListener("change", () => {
      state.preferences.remember[key] = $("remember-" + key).checked;
      clearTimeout(state.saveTimer); savePreferences();
    });
  }
  $("dark-mode").addEventListener("change", async () => {
    document.documentElement.dataset.theme = $("dark-mode").checked ? "dark" : "light";
    try { await renderCharts(); scheduleSave(); } catch (error) { say(error.message, true); }
  });
  $("database-panel").querySelector("summary").addEventListener("click", () => { state.panelTouched = true; });
  $("version").addEventListener("input", () => { $("version-warning").hidden = $("version").value === "4.1.13.65"; });
  $("data-path").addEventListener("input", () => { state.inspected = false; $("inspection").textContent = ""; updateDisabled(); });
  $("inspect").addEventListener("click", async () => {
    try {
      const result = await api("/api/workspace/inspect", "POST", {source: $("data-path").value.trim()});
      state.inspected = true; $("inspection").textContent = "检测到：" + result.databases.join("、");
      updateDisabled(); say("");
    } catch (error) { state.inspected = false; updateDisabled(); say(error.message, true); }
  });
  ["copy", "extract", "decrypt"].forEach(action => { $(action).addEventListener("click", () => startTask(action)); });
  $("refresh").addEventListener("click", () => startTask("refresh"));
  $("reinitialize").addEventListener("click", () => {
    state.initializing = true; $("initialization").hidden = false; $("database-panel").open = true; state.panelTouched = true;
  });
  installContactEvents();
  $("type-toggle").addEventListener("click", () => {
    state.typeExpanded = !state.typeExpanded;
    renderMessageTypes(state.data?.types || []);
  });
  document.querySelectorAll(".date-controls").forEach(controls => {
    controls.addEventListener("change", () => {
      const name = controls.dataset.range;
      const start = $(name + "-start").value, end = $(name + "-end").value;
      if (!start || !end || start > end || end > state.status.today) { say("请选择有效的起止日期，不能选择未来日期。", true); return; }
      applyDateRange(name, start, end);
    });
  });
  document.querySelectorAll("[data-select-dates]").forEach(button => {
    button.addEventListener("click", () => selectAllDates(button.dataset.selectDates));
  });
  document.querySelectorAll("[data-viewport]").forEach(button => {
    button.addEventListener("click", () => {
      const name = button.dataset.viewport, bounds = state.data?.charts[name].bounds;
      if (bounds) setViewport(name, button.dataset.rangeAction === "all" ? bounds : recentRange(bounds));
    });
  });
  // 正常操作时实时保存；关闭页面前再尽力写入最后一次缩放。
  window.addEventListener("pagehide", () => {
    if (state.saveTimer) {
      fetch("/api/preferences", {method: "PUT", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(snapshotPreferences()), keepalive: true}).catch(() => {});
    }
  });
}
installEvents();
if (state.preferences.warning) say(state.preferences.warning, true);
pollStatus();
