"use strict";

// 仅用 Node 内置模块检查网页状态逻辑；无浏览器、npm 或真实聊天数据。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const script = fs.readFileSync(path.join(__dirname, "../web/static/app.js"), "utf8");
const scopes = ["global", "daily", "hourly", "heatmap"];
const plain = value => JSON.parse(JSON.stringify(value));
const dates = end => Object.fromEntries(scopes.map(name => [name, {start: "2026-09-01", end}]));
const available = last => ({empty: false, available: {first: "2026-09-01", last}});

function setup() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      textContent: id === "initial-preferences" ? "{}" : "", hidden: false,
      dataset: {}, handlers: {}, value: "", style: {},
      on(name, handler) { this.handlers[name] = handler; },
    });
    return elements.get(id);
  }
  const context = vm.createContext({
    document: {getElementById: element, querySelectorAll: () => [],
      documentElement: {dataset: {theme: "light"}}},
    structuredClone, setTimeout: () => 1, clearTimeout: () => {},
    Plotly: {react: async (gd, traces, figure) => {
      gd.layout = plain(figure); gd.traces = plain(traces);
    }},
  });
  vm.runInContext(script.slice(0, script.lastIndexOf("installEvents();")), context);
  vm.runInContext("renderSummary = () => {}; renderCharts = async () => {}; scheduleSave = () => {};", context);
  const functions = vm.runInContext("({state, extendDateRanges, loadAnalysis, selectAllDates, renderDaily, pollStatus})", context);
  Object.assign(functions.state, {
    account: "synthetic_account", username: "synthetic_user", dataUsername: "synthetic_user",
    status: {today: "2026-10-07", active: {valid: true}},
    dates: dates("2026-10-02"), data: available("2026-10-02"),
  });
  const requests = [], replies = [];
  context.fetch = async (url, options) => {
    requests.push({url, body: options.body ? JSON.parse(options.body) : null});
    const reply = replies.shift();
    if (reply instanceof Error) throw reply;
    assert.ok(reply, "Unexpected extra API request");
    return {ok: true, json: async () => reply};
  };
  return {...functions, context, element, requests, replies};
}

test("更新只延长停在旧末尾的日期与视图，不改历史范围", () => {
  const h = setup();
  h.state.dates.heatmap.end = "2026-09-20";
  const viewports = {
    daily: ["2026-09-01", "2026-10-02T23:59:59.999Z"],
    hourly: ["2026-09-03", "2026-10-02T23:59:59.999Z"],
  };
  const before = plain(h.state.dates);
  const result = h.extendDateRanges(h.state.dates, viewports, "2026-10-02", "2026-10-07");
  assert.equal(result.changed, true);
  for (const name of ["global", "daily", "hourly"]) assert.equal(result.dates[name].end, "2026-10-07");
  assert.equal(result.dates.heatmap.end, "2026-09-20");
  assert.deepEqual(plain(result.viewports.daily), ["2026-09-01T00:00:00.000Z", "2026-10-07T23:59:59.999Z"]);
  assert.deepEqual(plain(result.viewports.hourly), ["2026-09-08T00:00:00.000Z", "2026-10-07T23:59:59.999Z"]);
  assert.deepEqual(plain(h.state.dates), before);
  assert.equal(viewports.daily[0], "2026-09-01");
  const historical = {daily: ["2026-09-02", "2026-09-15"]};
  assert.deepEqual(plain(h.extendDateRanges(h.state.dates, historical, "2026-10-02", "2026-10-07").viewports), historical);
  assert.equal(h.extendDateRanges(h.state.dates, {}, "2026-10-02", "2026-10-02").changed, false);
});

test("更新后重新请求新范围统计，并限制截止日期不超过今天", async () => {
  const h = setup();
  h.state.dates.hourly.end = "2026-09-20";
  h.replies.push(available("2026-10-08"), available("2026-10-08"));
  await h.loadAnalysis();
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[0].body.global.end, "2026-10-02");
  assert.equal(h.requests[1].body.global.end, "2026-10-07");
  assert.equal(h.requests[1].body.charts.hourly.end, "2026-09-20");
  assert.equal(h.state.dates.daily.end, "2026-10-07");
  assert.equal(h.element("global-end").value, "2026-10-07");
  assert.equal(h.state.loadingAnalysis, false);

  const different = setup();
  different.state.username = "synthetic_other_user";
  different.replies.push(available("2026-10-07"));
  await different.loadAnalysis();
  assert.equal(different.requests.length, 1);
  assert.equal(different.state.dates.global.end, "2026-10-02");
});

test("全选统一日期同步各图，本图全选只更新本图并展开完整视图", async () => {
  const h = setup();
  h.state.data = available("2026-10-08");
  h.replies.push(available("2026-10-08"));
  await h.selectAllDates("global");
  assert.deepEqual(plain(h.state.dates), dates("2026-10-07"));
  assert.deepEqual(plain(h.state.viewports.daily), ["2026-09-01", "2026-10-07T23:59:59.999"]);
  assert.deepEqual(plain(h.state.viewports.hourly), plain(h.state.viewports.daily));

  const specific = setup();
  specific.state.dates = dates("2026-09-20");
  specific.replies.push(available("2026-10-02"));
  await specific.selectAllDates("daily");
  assert.equal(specific.requests[0].body.charts.daily.end, "2026-10-02");
  for (const name of ["global", "hourly", "heatmap"]) assert.equal(specific.state.dates[name].end, "2026-09-20");
  assert.deepEqual(plain(specific.state.viewports.daily), ["2026-09-01", "2026-10-02T23:59:59.999"]);
});

test("新范围请求失败或被更新请求替代时不提交旧响应", async () => {
  const h = setup();
  const before = plain(h.state.dates);
  h.replies.push(available("2026-10-07"), new Error("synthetic request failure"));
  await h.loadAnalysis();
  assert.deepEqual(plain(h.state.dates), before);
  assert.equal(h.state.data.available.last, "2026-10-02");
  assert.equal(h.element("message").textContent, "synthetic request failure");

  const stale = setup();
  let finish;
  stale.context.fetch = () => new Promise(resolve => { finish = resolve; });
  const pending = stale.loadAnalysis();
  stale.state.analysisSerial++;
  finish({ok: true, json: async () => available("2026-10-07")});
  await pending;
  assert.equal(stale.state.data.available.last, "2026-10-02");
  assert.equal(stale.state.dates.global.end, "2026-10-02");
});

test("高活跃使用独立形状图例，隐藏状态与说明随重绘保留", async () => {
  const h = setup();
  const data = {bounds: ["2026-09-01", "2026-10-02T23:59:59.999"],
    dates: ["2026-09-01", "2026-10-02"], counts: {"我": [1, 2], "对方": [2, 3], "总计": [3, 5]},
    period: {start: "2026-09-01", end: "2026-10-02", days: 32, message_count: 8}};
  h.state.data.charts = {daily: data};
  await h.renderDaily(data);
  const gd = h.element("daily-chart");
  assert.equal(gd.traces.length, 3);
  assert.equal(gd.layout.shapes[0].name, "高活跃");
  assert.equal(gd.layout.shapes[0].showlegend, true);
  gd.handlers.plotly_relayout({"shapes[0].visible": "legendonly"});
  assert.equal(h.state.activityVisible, false);
  assert.equal(h.element("activity-note").hidden, true);
  await h.renderDaily(data);
  assert.equal(gd.layout.shapes[0].visible, "legendonly");
  gd.handlers.plotly_relayout({"shapes[0].visible": true});
  assert.equal(h.state.activityVisible, true);
  assert.equal(h.element("activity-note").hidden, false);
  assert.match(h.element("activity-note").textContent, /32 天/);
});

test("首次轮询即已完成的更新也加载新分析，同一任务仅加载一次", async () => {
  const h = setup();
  h.state.contacts = [{username: "synthetic_user"}];
  const calls = [];
  h.context.reloadContacts = preserve => { calls.push(preserve); };
  vm.runInContext("renderStatus = status => { state.status = status; state.busy = status.task?.state === 'running'; }; loadContacts = async preserve => reloadContacts(preserve);", h.context);
  const complete = {active: {valid: true, account: "synthetic_account"},
    task: {id: "refresh-fixture", action: "refresh", state: "complete"}};
  h.replies.push(complete, complete, {...complete, task: {...complete.task, id: "failed-fixture", state: "failed", error: "synthetic failure"}});
  await h.pollStatus();
  await h.pollStatus();
  await h.pollStatus();
  assert.deepEqual(calls, [true]);
  assert.equal(h.element("message").textContent, "synthetic failure");

  // 换账号时清除旧联系人的末尾日期，避免对新账号应用旧记录范围。
  const changed = setup();
  changed.context.reloadContacts = () => {};
  vm.runInContext("renderStatus = status => { state.status = status; }; loadContacts = async () => reloadContacts();", changed.context);
  changed.replies.push({...complete, active: {valid: true, account: "synthetic_other_account"}});
  await changed.pollStatus();
  assert.equal(changed.state.data, null);
  assert.equal(changed.state.dataUsername, "");
});
