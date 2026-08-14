const ui = {
  appShell: document.querySelector("#app-shell"),
  authGate: document.querySelector("#auth-gate"),
  authLoading: document.querySelector("#auth-loading"),
  authContent: document.querySelector("#auth-content"),
  authTitle: document.querySelector("#auth-title"),
  authDescription: document.querySelector("#auth-description"),
  authError: document.querySelector("#auth-error"),
  loginForm: document.querySelector("#login-form"),
  registerForm: document.querySelector("#register-form"),
  loginTab: document.querySelector("#show-login"),
  registerTab: document.querySelector("#show-register"),
  messages: document.querySelector("#messages"),
  form: document.querySelector("#chat-form"),
  input: document.querySelector("#message"),
  send: document.querySelector("#send-button"),
  threadList: document.querySelector("#thread-list"),
  threadCount: document.querySelector("#thread-count"),
  currentTitle: document.querySelector("#current-title"),
  customers: document.querySelector("#customers"),
  customerCount: document.querySelector("#customer-count"),
  customerSearch: document.querySelector("#customer-search"),
  pendingActions: document.querySelector("#pending-actions"),
  pendingCount: document.querySelector("#pending-count"),
  sidebar: document.querySelector("#thread-sidebar"),
  inspector: document.querySelector("#customer-inspector"),
  scrim: document.querySelector("#page-scrim"),
  toast: document.querySelector("#toast"),
  renameDialog: document.querySelector("#rename-dialog"),
  renameInput: document.querySelector("#rename-input"),
  mainSidebar: document.querySelector("#main-sidebar"),
  customerTableBody: document.querySelector("#customer-table-body"),
  customerTableEmpty: document.querySelector("#customer-table-empty"),
  customerStatusFilter: document.querySelector("#customer-status-filter"),
  customerResultCount: document.querySelector("#customer-result-count"),
  customerDialog: document.querySelector("#customer-dialog"),
  customerForm: document.querySelector("#customer-form"),
  customerDetailDialog: document.querySelector("#customer-detail-dialog"),
  userTableBody: document.querySelector("#user-table-body"),
  userTableEmpty: document.querySelector("#user-table-empty"),
  resourceSearch: document.querySelector("#resource-search"),
  resourceFilterContext: document.querySelector("#resource-filter-context"),
  resourceTableHead: document.querySelector("#resource-table-head"),
  resourceTableBody: document.querySelector("#resource-table-body"),
  resourceTableEmpty: document.querySelector("#resource-table-empty"),
  resourcePagination: document.querySelector("#resource-pagination"),
  resourcePrevPage: document.querySelector("#resource-prev-page"),
  resourceNextPage: document.querySelector("#resource-next-page"),
  resourcePageState: document.querySelector("#resource-page-state"),
  resourceDialog: document.querySelector("#resource-dialog"),
  resourceForm: document.querySelector("#resource-form"),
  resourceDetailDialog: document.querySelector("#resource-detail-dialog"),
  leadConvertDialog: document.querySelector("#lead-convert-dialog"),
  leadConvertForm: document.querySelector("#lead-convert-form"),
  accountTransferDialog: document.querySelector("#account-transfer-dialog"),
  accountTransferForm: document.querySelector("#account-transfer-form"),
};

const state = {
  currentUser: null,
  conversations: [],
  activeId: null,
  customers: [],
  pendingActions: [],
  pendingBusy: new Set(),
  busy: false,
  toastTimer: null,
  users: [],
  currentPage: "dashboard",
  dashboard: null,
  activeResource: null,
  resourceRecords: {},
  resourceOptionCache: {},
  resourceTotals: {},
  resourcePagination: {},
  resourceFilters: {},
  resourceLoadTokens: {},
  resourceSearchTimer: null,
  editingResourceRecord: null,
  workspaceGeneration: 0,
  conversionLead: null,
};

const RESOURCE_PAGE_SIZE = 50;

const icons = {
  chat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v11H9l-4 3V5Z"/><path d="M8 9h8M8 12h5"/></svg>',
  delete: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5"/></svg>',
  spark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 1.3 4.2a5 5 0 0 0 3.5 3.5L21 12l-4.2 1.3a5 5 0 0 0-3.5 3.5L12 21l-1.3-4.2a5 5 0 0 0-3.5-3.5L3 12l4.2-1.3a5 5 0 0 0 3.5-3.5L12 3Z"/></svg>',
  shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 5 6v5c0 4.8 2.8 8.1 7 10 4.2-1.9 7-5.2 7-10V6l-7-3Z"/><path d="m9 12 2 2 4-4"/></svg>',
};

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

class StaleWorkspaceError extends Error {
  constructor() {
    super("工作区已经切换");
    this.name = "StaleWorkspaceError";
    this.staleWorkspace = true;
  }
}

function workspaceSnapshot() {
  return {
    generation: state.workspaceGeneration,
    userId: state.currentUser?.id ? String(state.currentUser.id) : null,
  };
}

function workspaceIsCurrent(snapshot) {
  const userId = state.currentUser?.id ? String(state.currentUser.id) : null;
  return snapshot?.generation === state.workspaceGeneration && snapshot?.userId === userId;
}

function assertWorkspaceCurrent(snapshot) {
  if (!workspaceIsCurrent(snapshot)) throw new StaleWorkspaceError();
}

function commitIfWorkspaceCurrent(snapshot, commit) {
  if (!workspaceIsCurrent(snapshot)) return false;
  commit();
  return true;
}

function isStaleWorkspaceError(error) {
  return Boolean(error?.staleWorkspace || error?.name === "StaleWorkspaceError");
}

async function api(path, options = {}) {
  const requestWorkspace = workspaceSnapshot();
  const { skipAuthRedirect = false, ...fetchOptions } = options;
  const headers = new Headers(fetchOptions.headers || {});
  if (fetchOptions.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {
    ...fetchOptions,
    headers,
    credentials: "same-origin",
  });
  assertWorkspaceCurrent(requestWorkspace);
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    const text = await response.text();
    assertWorkspaceCurrent(requestWorkspace);
    if (text) {
      try {
        const body = JSON.parse(text);
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
      } catch (_) {
        detail = text;
      }
    }
    if (response.status === 401 && !skipAuthRedirect) showAuthGate("登录已过期，请重新登录。");
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return null;
  const text = await response.text();
  assertWorkspaceCurrent(requestWorkspace);
  return text ? JSON.parse(text) : null;
}

function activeConversationStorageKey() {
  const identity = state.currentUser?.id || state.currentUser?.username || "anonymous";
  return `crm-active-conversation:${identity}`;
}

function showToast(message) {
  clearTimeout(state.toastTimer);
  ui.toast.textContent = message;
  ui.toast.hidden = false;
  state.toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 2800);
}

function normalizeUser(value) {
  return value?.user && typeof value.user === "object" ? value.user : value;
}

const roleNames = { admin: "管理员", manager: "经理", sales: "销售", viewer: "只读" };
const statusNames = {
  new: "新线索", contacted: "已联系", qualified: "已确认", converted: "已转化", lost: "已流失",
  lead: "线索", customer: "客户", inactive: "暂停",
};
const standardStatuses = ["new", "contacted", "qualified", "converted", "lost"];
const enumNames = {
  new: "新建", contacted: "已联系", qualified: "已确认", converted: "已转化", lost: "已流失",
  prospect: "潜在客户", active: "活跃", inactive: "停用", customer: "正式客户",
  qualification: "需求确认", proposal: "方案报价", negotiation: "商务谈判", won: "赢单",
  call: "电话", meeting: "会议", email: "邮件", task: "任务", note: "记录",
  planned: "已计划", in_progress: "进行中", completed: "已完成", cancelled: "已取消",
  low: "低", normal: "普通", high: "高", urgent: "紧急",
};
const resourcePageNames = ["leads", "accounts", "contacts", "opportunities", "activities"];

const resourceDefinitions = {
  leads: {
    title: "线索", singular: "线索", description: "管理尚未转化的潜在客户和线索评分。", search: "搜索姓名、公司、邮箱或电话",
    titleOf: (row) => [row.first_name, row.last_name].filter(Boolean).join(" ") || row.company_name || "未命名线索",
    columns: [
      { key: "full_name", label: "姓名", value: (row) => [row.first_name, row.last_name].filter(Boolean).join(" ") },
      { key: "company_name", label: "公司" }, { key: "email", label: "邮箱" }, { key: "phone", label: "电话" },
      { key: "status", label: "状态", badge: true }, { key: "score", label: "评分" }, { key: "owner_id", label: "负责人", value: (row) => userNameById(row.owner_id) },
    ],
    fields: [
      { key: "first_name", label: "名", maxlength: 100 }, { key: "last_name", label: "姓", maxlength: 100 },
      { key: "company_name", label: "公司", maxlength: 255 }, { key: "job_title", label: "职位", maxlength: 150 },
      { key: "email", label: "邮箱", maxlength: 255 }, { key: "phone", label: "电话", maxlength: 50 },
      { key: "source", label: "来源", maxlength: 100 }, { key: "status", label: "状态", type: "suggest", options: ["new", "contacted", "qualified", "lost"], default: "new", maxlength: 50, required: true },
      { key: "score", label: "评分", type: "number", step: "1" },
      { key: "owner_id", label: "负责人", type: "user-select", managerOnly: true },
      { key: "description", label: "描述", type: "textarea", wide: true },
      { key: "extra", label: "扩展数据 (JSON)", type: "json", wide: true },
    ],
  },
  accounts: {
    title: "客户公司", singular: "客户公司", description: "维护公司主体、规模与商业信息。", search: "搜索公司名称、行业、邮箱或电话",
    titleOf: (row) => row.name || "未命名公司",
    columns: [
      { key: "name", label: "公司" }, { key: "industry", label: "行业" }, { key: "phone", label: "电话" },
      { key: "city", label: "城市" }, { key: "status", label: "状态", badge: true }, { key: "owner_id", label: "负责人", value: (row) => userNameById(row.owner_id) },
    ],
    fields: [
      { key: "name", label: "公司名称", required: true, wide: true, maxlength: 255 }, { key: "industry", label: "行业", maxlength: 100 },
      { key: "website", label: "网站", maxlength: 500 }, { key: "phone", label: "电话", maxlength: 50 }, { key: "email", label: "邮箱", maxlength: 255 },
      { key: "address", label: "地址", wide: true }, { key: "city", label: "城市", maxlength: 100 }, { key: "state", label: "省/州", maxlength: 100 }, { key: "country", label: "国家", maxlength: 100 },
      { key: "employee_count", label: "员工数", type: "number", step: "1" }, { key: "annual_revenue", label: "年营收", type: "number", numeric: "decimal", step: "0.01" },
      { key: "status", label: "状态", type: "suggest", options: ["prospecting", "prospect", "active", "inactive", "customer"], default: "active", maxlength: 50, required: true }, { key: "source", label: "来源", maxlength: 100 },
      { key: "owner_id", label: "负责人", type: "user-select", managerOnly: true },
      { key: "description", label: "描述", type: "textarea", wide: true },
    ],
  },
  contacts: {
    title: "联系人", singular: "联系人", description: "维护公司联系人、部门和多渠道联系方式。", search: "搜索姓名、部门、邮箱、手机或微信",
    titleOf: (row) => [row.first_name, row.last_name].filter(Boolean).join(" ") || row.email || "未命名联系人",
    columns: [
      { key: "full_name", label: "姓名", value: (row) => [row.first_name, row.last_name].filter(Boolean).join(" ") },
      { key: "account_id", label: "客户公司", relation: "accounts" }, { key: "title", label: "职位" }, { key: "email", label: "邮箱" },
      { key: "mobile", label: "手机" }, { key: "owner_id", label: "负责人", value: (row) => userNameById(row.owner_id) },
    ],
    fields: [
      { key: "account_id", label: "客户公司", type: "resource-select", resource: "accounts" },
      { key: "first_name", label: "名", maxlength: 100 }, { key: "last_name", label: "姓", maxlength: 100 },
      { key: "title", label: "职位", maxlength: 150 }, { key: "department", label: "部门", maxlength: 150 }, { key: "email", label: "邮箱", maxlength: 255 },
      { key: "phone", label: "电话", maxlength: 50 }, { key: "mobile", label: "手机", maxlength: 50 }, { key: "wechat", label: "微信", maxlength: 100 },
      { key: "linkedin", label: "LinkedIn", maxlength: 500 }, { key: "source", label: "来源", maxlength: 100 },
      { key: "owner_id", label: "负责人", type: "user-select", managerOnly: true },
      { key: "description", label: "描述", type: "textarea", wide: true },
    ],
  },
  opportunities: {
    title: "商机", singular: "商机", description: "跟踪交易金额、阶段、赢单概率与预计成交日期。", search: "搜索商机名称、公司、阶段或负责人",
    titleOf: (row) => row.name || "未命名商机",
    columns: [
      { key: "name", label: "商机" }, { key: "account_id", label: "客户公司", relation: "accounts" }, { key: "primary_contact_id", label: "主要联系人", relation: "contacts" }, { key: "stage", label: "阶段", badge: true },
      { key: "amount", label: "金额", value: (row) => formatMoney(row.amount, row.currency) }, { key: "probability", label: "概率", value: (row) => row.probability == null ? "—" : `${row.probability}%` },
      { key: "expected_close_date", label: "预计成交" }, { key: "owner_id", label: "负责人", value: (row) => userNameById(row.owner_id) },
    ],
    fields: [
      { key: "account_id", label: "客户公司", type: "resource-select", resource: "accounts" },
      { key: "primary_contact_id", label: "主要联系人", type: "resource-select", resource: "contacts" },
      { key: "name", label: "商机名称", required: true, maxlength: 255 },
      { key: "amount", label: "金额", type: "number", numeric: "decimal", step: "0.01" }, { key: "currency", label: "币种", default: "CNY", maxlength: 10, required: true },
      { key: "stage", label: "阶段", type: "suggest", options: ["prospecting", "qualification", "proposal", "negotiation", "won", "lost"], default: "prospecting", maxlength: 50, required: true },
      { key: "probability", label: "赢单概率", type: "number", step: "0.01" }, { key: "expected_close_date", label: "预计成交日期", type: "date" },
      { key: "source", label: "来源", maxlength: 100 }, { key: "owner_id", label: "负责人", type: "user-select", managerOnly: true },
      { key: "description", label: "描述", type: "textarea", wide: true },
    ],
  },
  activities: {
    title: "跟进活动", singular: "跟进活动", description: "统一安排电话、会议、邮件、任务和客户跟进。", search: "搜索活动主题、类型、状态或优先级",
    titleOf: (row) => row.subject || "未命名活动",
    columns: [
      { key: "subject", label: "主题" }, { key: "related_record", label: "关联记录", link: activityRelatedRecord }, { key: "type", label: "类型", badge: true }, { key: "status", label: "状态", badge: true },
      { key: "priority", label: "优先级" }, { key: "start_at", label: "开始时间", value: (row) => formatDateTime(row.start_at) },
      { key: "assigned_user_id", label: "负责人", value: (row) => userNameById(row.assigned_user_id) },
    ],
    fields: [
      { key: "type", label: "活动类型", type: "suggest", options: ["call", "meeting", "email", "task", "note"], maxlength: 50, required: true },
      { key: "subject", label: "主题", required: true, maxlength: 255 }, { key: "status", label: "状态", type: "suggest", options: ["planned", "in_progress", "completed", "cancelled"], default: "planned", maxlength: 50, required: true },
      { key: "priority", label: "优先级", type: "suggest", options: ["low", "normal", "high", "urgent"], default: "normal", maxlength: 50, required: true },
      { key: "start_at", label: "开始时间", type: "datetime-local" }, { key: "end_at", label: "结束时间", type: "datetime-local" },
      { key: "account_id", label: "客户公司", type: "resource-select", resource: "accounts" },
      { key: "contact_id", label: "联系人", type: "resource-select", resource: "contacts" },
      { key: "lead_id", label: "线索", type: "resource-select", resource: "leads" },
      { key: "opportunity_id", label: "商机", type: "resource-select", resource: "opportunities" },
      { key: "assigned_user_id", label: "指派给", type: "user-select", managerOnly: true },
      { key: "description", label: "描述", type: "textarea", wide: true },
    ],
  },
};

function canCreateCustomer() {
  return ["admin", "manager", "sales"].includes(state.currentUser?.role);
}

function canUpdateCustomer() {
  return ["admin", "manager", "sales"].includes(state.currentUser?.role);
}

function canDeleteCustomer() {
  return ["admin", "manager"].includes(state.currentUser?.role);
}

function canReviewPendingActions() {
  return ["admin", "manager", "sales"].includes(state.currentUser?.role);
}

function canCreateResource() {
  return ["admin", "manager", "sales"].includes(state.currentUser?.role);
}

function canUpdateResource() {
  return ["admin", "manager", "sales"].includes(state.currentUser?.role);
}

function canDeleteResource() {
  return ["admin", "manager"].includes(state.currentUser?.role);
}

function switchPage(page) {
  const resourcePage = resourcePageNames.includes(page);
  state.currentPage = page;
  const names = { dashboard: "工作台", assistant: "AI 助手", users: "账号管理", ...Object.fromEntries(Object.entries(resourceDefinitions).map(([key, value]) => [key, value.title])) };
  document.querySelectorAll(".page").forEach((section) => section.classList.toggle("active", section.id === (resourcePage ? "page-records" : `page-${page}`)));
  document.querySelectorAll(".nav-item[data-page]").forEach((button) => button.classList.toggle("active", button.dataset.page === page));
  document.querySelector("#page-title").textContent = names[page] || "CRM";
  document.querySelector("#page-breadcrumb").textContent = names[page] || "CRM";
  const globalAdd = document.querySelector("#global-add-customer");
  globalAdd.hidden = ["assistant", "users"].includes(page) || !canCreateResource();
  globalAdd.textContent = resourcePage ? `＋ 新增${resourceDefinitions[page].singular}` : "＋ 新增线索";
  document.querySelector("#global-refresh").hidden = page === "assistant";
  if (page === "users") loadUsers();
  if (page === "dashboard") loadDashboard();
  if (resourcePage) activateResource(page);
  if (page === "assistant") window.setTimeout(() => ui.input.focus(), 0);
  closeDrawers();
}

function setAuthError(message = "") {
  ui.authError.textContent = message;
  ui.authError.hidden = !message;
}

function setAuthMode(mode) {
  const registering = mode === "register";
  ui.loginForm.hidden = registering;
  ui.registerForm.hidden = !registering;
  ui.loginTab.classList.toggle("active", !registering);
  ui.registerTab.classList.toggle("active", registering);
  ui.loginTab.setAttribute("aria-selected", String(!registering));
  ui.registerTab.setAttribute("aria-selected", String(registering));
  ui.authTitle.textContent = registering ? "创建公司账号" : "欢迎回来";
  ui.authDescription.textContent = registering
    ? "注册后即可使用智能 CRM 独立会话与按角色授权的客户数据。"
    : "登录智能 CRM，继续访问你的客户与会话记忆。";
  setAuthError();
  const target = registering
    ? document.querySelector("#register-display-name")
    : document.querySelector("#login-username");
  window.setTimeout(() => target.focus(), 0);
}

function clearSensitiveWorkspaceDom() {
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  [ui.customerForm, ui.resourceForm, ui.leadConvertForm, ui.accountTransferForm, document.querySelector("#rename-form")]
    .filter(Boolean)
    .forEach((form) => form.reset());
  for (const selector of [
    "#customer-detail-content", "#customer-detail-actions", "#resource-detail-content", "#resource-detail-actions",
    "#resource-form-fields", "#conversion-confirmation", "#transfer-impact",
  ]) document.querySelector(selector)?.replaceChildren();
  for (const selector of ["#detail-customer-name", "#resource-detail-title", "#convert-lead-name", "#convert-lead-company"]) {
    const node = document.querySelector(selector);
    if (node) node.textContent = "";
  }
  const convertAvatar = document.querySelector("#convert-lead-avatar");
  if (convertAvatar) convertAvatar.textContent = "";
  for (const [selector, placeholder] of [
    ["#convert-account-id", "选择客户公司"],
    ["#convert-contact-id", "请先选择客户公司"],
    ["#transfer-owner-id", "选择新负责人"],
  ]) {
    const select = document.querySelector(selector);
    if (!select) continue;
    select.replaceChildren();
    const option = document.createElement("option");
    option.value = "";
    option.textContent = placeholder;
    select.append(option);
  }
  for (const [selector, value] of [
    ["#account-display-name", "当前账号"], ["#account-username", ""],
    ["#account-role", ""], ["#account-avatar", ""],
  ]) {
    const node = document.querySelector(selector);
    if (node) node.textContent = value;
  }
  ui.renameInput.value = "";
  ui.userTableBody.replaceChildren();
  ui.userTableEmpty.hidden = false;
  ui.userTableEmpty.textContent = "暂无账号";
  for (const selector of ["#save-resource", "#save-customer", "#submit-lead-convert", "#submit-account-transfer", "#logout"]) {
    const control = document.querySelector(selector);
    if (control) control.disabled = false;
  }
  document.querySelectorAll(".relation-search, #convert-account-search, #convert-contact-search").forEach((control) => { control.disabled = false; });
  clearTimeout(state.toastTimer);
  state.toastTimer = null;
  ui.toast.textContent = "";
  ui.toast.hidden = true;
}

function resetWorkspaceState() {
  state.workspaceGeneration += 1;
  clearSensitiveWorkspaceDom();
  state.conversations = [];
  state.activeId = null;
  state.customers = [];
  state.pendingActions = [];
  state.users = [];
  state.dashboard = null;
  state.activeResource = null;
  state.resourceRecords = {};
  state.resourceOptionCache = {};
  state.resourceTotals = {};
  state.resourcePagination = {};
  state.resourceFilters = {};
  state.resourceLoadTokens = {};
  clearTimeout(state.resourceSearchTimer);
  state.resourceSearchTimer = null;
  state.editingResourceRecord = null;
  state.conversionLead = null;
  state.pendingBusy.clear();
  state.busy = false;
  ui.input.value = "";
  ui.customerSearch.value = "";
  ui.resourceSearch.value = "";
  ui.resourceFilterContext.textContent = "";
  ui.resourceFilterContext.hidden = true;
  ui.customerStatusFilter.value = "";
  ui.currentTitle.textContent = "新会话";
  setBusy(false);
  renderThreads();
  renderCustomers();
  renderPendingActions();
  resetResourcePage();
  renderDashboardFromCustomers();
  renderEmptyState();
  closeDrawers();
}

function showAuthGate(message = "", { preserveMode = false } = {}) {
  state.currentUser = null;
  resetWorkspaceState();
  ui.appShell.hidden = true;
  ui.authGate.hidden = false;
  ui.authLoading.hidden = true;
  ui.authContent.hidden = false;
  if (!preserveMode) setAuthMode("login");
  setAuthError(message);
}

function updateAccountProfile() {
  const user = state.currentUser || {};
  const displayName = user.display_name || user.username || "当前账号";
  document.querySelector("#account-display-name").textContent = displayName;
  document.querySelector("#account-username").textContent = user.username ? `@${user.username}` : (user.email || "—");
  document.querySelector("#account-role").textContent = roleNames[user.role] || user.role || "成员";
  document.querySelector("#account-avatar").textContent = displayName.trim().slice(0, 1).toUpperCase() || "U";
  document.querySelector("#users-nav").hidden = user.role !== "admin";
  document.querySelector("#add-customer").hidden = !canCreateCustomer();
  document.querySelector("#global-add-customer").hidden = !canCreateCustomer();
  document.querySelector("#users-page-description").textContent = user.role === "admin"
    ? "管理公司成员的 CRM 角色和数据权限。"
    : "这里显示你的当前账号和权限；账号角色由管理员维护。";
  document.querySelector("#welcome-title").textContent = `${displayName}，欢迎回来`;
}

async function enterWorkspace(user) {
  state.workspaceGeneration += 1;
  clearSensitiveWorkspaceDom();
  state.currentUser = normalizeUser(user);
  state.conversations = [];
  state.customers = [];
  state.pendingActions = [];
  state.users = [];
  state.dashboard = null;
  state.activeResource = null;
  state.resourceRecords = {};
  state.resourceOptionCache = {};
  state.resourceTotals = {};
  state.resourcePagination = {};
  state.resourceFilters = {};
  state.resourceLoadTokens = {};
  clearTimeout(state.resourceSearchTimer);
  state.resourceSearchTimer = null;
  state.editingResourceRecord = null;
  state.conversionLead = null;
  state.pendingBusy.clear();
  state.busy = false;
  state.activeId = localStorage.getItem(activeConversationStorageKey());
  const workspace = workspaceSnapshot();
  renderDashboardFromCustomers();
  ui.authGate.hidden = true;
  ui.appShell.hidden = false;
  updateAccountProfile();
  switchPage("dashboard");
  renderThreads();
  renderEmptyState();
  renderCustomers();
  renderPendingActions();
  resizeComposer();
  updateScrim();
  const results = await Promise.allSettled([
    loadConversations(),
    loadCustomers(),
    loadPendingActions({ quiet: true }),
    loadDashboard(),
    loadBusinessContext(),
    loadUserDirectory(),
  ]);
  if (!workspaceIsCurrent(workspace)) return;
  const failed = results.find((result) => result.status === "rejected");
  if (failed && !isStaleWorkspaceError(failed.reason) && state.currentUser) showToast(`初始化失败：${failed.reason.message}`);
  if (state.currentUser) ui.input.focus();
}

async function submitAuth(form, path) {
  const submit = form.querySelector('[type="submit"]');
  const payload = Object.fromEntries(new FormData(form).entries());
  const workspace = workspaceSnapshot();
  let authenticated = false;
  submit.disabled = true;
  setAuthError();
  try {
    const user = await api(path, {
      method: "POST",
      body: JSON.stringify(payload),
      skipAuthRedirect: true,
    });
    assertWorkspaceCurrent(workspace);
    form.reset();
    submit.disabled = false;
    authenticated = true;
    await enterWorkspace(user);
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    setAuthError(error.message);
  } finally {
    if (!authenticated && workspaceIsCurrent(workspace)) submit.disabled = false;
  }
}

async function logout() {
  const button = document.querySelector("#logout");
  const workspace = workspaceSnapshot();
  button.disabled = true;
  try {
    await api("/api/auth/logout", { method: "POST", skipAuthRedirect: true });
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    if (error.status !== 401) {
      showToast(`退出失败，当前会话可能仍有效：${error.message}`);
      return;
    }
  } finally {
    if (workspaceIsCurrent(workspace)) button.disabled = false;
  }
  if (!workspaceIsCurrent(workspace)) return;
  showAuthGate();
}

function formatTime(value) {
  if (!value) return "刚刚";
  const date = new Date(value);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(date);
  }
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(date);
}

function activeConversation() {
  return state.conversations.find((item) => item.id === state.activeId) || null;
}

function setActiveConversation(id) {
  state.activeId = id;
  const storageKey = activeConversationStorageKey();
  if (id) localStorage.setItem(storageKey, id);
  else localStorage.removeItem(storageKey);
  const conversation = activeConversation();
  ui.currentTitle.textContent = conversation?.title || "新会话";
  renderThreads();
}

function renderThreads() {
  ui.threadCount.textContent = String(state.conversations.length);
  ui.threadList.replaceChildren();
  if (!state.conversations.length) {
    const empty = document.createElement("p");
    empty.className = "thread-empty";
    empty.textContent = "还没有会话，点击上方按钮开始。";
    ui.threadList.append(empty);
    return;
  }

  for (const conversation of state.conversations) {
    const row = document.createElement("div");
    row.className = `thread-item${conversation.id === state.activeId ? " active" : ""}`;

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "thread-trigger";
    trigger.setAttribute("aria-current", conversation.id === state.activeId ? "page" : "false");
    trigger.innerHTML = icons.chat;
    const copy = document.createElement("span");
    copy.className = "thread-copy";
    const title = document.createElement("span");
    title.className = "thread-title";
    title.textContent = conversation.title;
    const time = document.createElement("span");
    time.className = "thread-time";
    time.textContent = formatTime(conversation.last_message_at || conversation.created_at);
    copy.append(title, time);
    trigger.append(copy);
    trigger.addEventListener("click", () => selectConversation(conversation.id));

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "thread-archive";
    remove.title = "删除会话";
    remove.setAttribute("aria-label", `删除 ${conversation.title}`);
    remove.innerHTML = icons.delete;
    remove.addEventListener("click", () => deleteConversation(conversation.id));

    row.append(trigger, remove);
    ui.threadList.append(row);
  }
}

function renderEmptyState() {
  ui.messages.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.innerHTML = `
    <div class="empty-mark">${icons.spark}</div>
    <h2>今天想处理哪项 CRM 工作？</h2>
    <p>我可以在多轮对话中查询线索、公司、联系人、商机和活动；任何写入都要由你确认。</p>
    <div class="suggestion-grid">
      <button class="suggestion" type="button" data-prompt="帮我录入一家客户公司">
        <strong>录入客户公司</strong><span>收集公司字段并生成待确认动作</span>
      </button>
      <button class="suggestion" type="button" data-prompt="列出最近更新的线索和商机">
        <strong>查询销售数据</strong><span>读取当前账号可见的业务记录</span>
      </button>
      <button class="suggestion" type="button" data-prompt="帮我更新一条 CRM 业务记录">
        <strong>更新业务记录</strong><span>先精确查询，再确认更新字段</span>
      </button>
      <button class="suggestion" type="button" data-prompt="帮我安排一项客户跟进活动">
        <strong>安排客户跟进</strong><span>创建电话、会议、邮件或任务计划</span>
      </button>
    </div>`;
  empty.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      ui.input.value = button.dataset.prompt;
      resizeComposer();
      ui.input.focus();
    });
  });
  ui.messages.append(empty);
}

function appendMessage(content, role, options = {}) {
  const row = document.createElement("article");
  row.className = `message-row ${role}${options.error ? " error" : ""}`;
  if (options.id) row.dataset.messageId = options.id;

  if (role === "assistant") {
    const avatar = document.createElement("span");
    avatar.className = "message-avatar";
    avatar.textContent = "AI";
    row.append(avatar);
  }
  const body = document.createElement("div");
  body.className = "message-body";
  if (role === "assistant") {
    const author = document.createElement("span");
    author.className = "message-author";
    author.textContent = "智能 CRM AI";
    body.append(author);
  }
  const text = document.createElement("div");
  text.className = "message-content";
  if (options.thinking) {
    text.innerHTML = '<span class="thinking" aria-label="智能助手正在处理"><i></i><i></i><i></i></span>';
  } else {
    text.textContent = content;
  }
  body.append(text);
  row.append(body);
  ui.messages.append(row);
  ui.messages.scrollTop = ui.messages.scrollHeight;
  return row;
}

async function loadMessages(conversationId) {
  const workspace = workspaceSnapshot();
  ui.messages.innerHTML = '<div class="panel-empty">正在恢复会话…</div>';
  try {
    const history = await api(`/api/conversations/${conversationId}/messages`);
    if (!workspaceIsCurrent(workspace) || state.activeId !== conversationId) return;
    ui.messages.replaceChildren();
    if (!history.length) {
      renderEmptyState();
      return;
    }
    history.forEach((message) => appendMessage(message.content, message.role));
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace) || state.activeId !== conversationId) return;
    ui.messages.replaceChildren();
    appendMessage(`读取会话失败：${error.message}`, "assistant", { error: true });
  }
}

async function loadConversations() {
  const workspace = workspaceSnapshot();
  const conversations = await api("/api/conversations");
  assertWorkspaceCurrent(workspace);
  state.conversations = conversations;
  let preferred = state.conversations.find((item) => item.id === state.activeId)?.id;
  if (!preferred) preferred = state.conversations[0]?.id;
  if (!preferred) {
    const created = await api("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ title: "新会话" }),
    });
    assertWorkspaceCurrent(workspace);
    state.conversations.unshift(created);
    preferred = created.id;
  }
  setActiveConversation(preferred);
  await loadMessages(preferred);
}

async function refreshConversations() {
  const workspace = workspaceSnapshot();
  const conversations = await api("/api/conversations");
  assertWorkspaceCurrent(workspace);
  state.conversations = conversations;
  setActiveConversation(state.activeId);
}

async function createConversation() {
  if (state.busy) return;
  const workspace = workspaceSnapshot();
  try {
    const created = await api("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ title: "新会话" }),
    });
    assertWorkspaceCurrent(workspace);
    state.conversations.unshift(created);
    setActiveConversation(created.id);
    renderEmptyState();
    closeDrawers();
    ui.input.focus();
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    showToast(`新建失败：${error.message}`);
  }
}

async function selectConversation(id) {
  if (state.busy || id === state.activeId) return;
  setActiveConversation(id);
  closeDrawers();
  await loadMessages(id);
}

async function deleteConversation(id) {
  if (state.busy) return;
  const conversation = state.conversations.find((item) => item.id === id);
  if (!conversation) return;
  const confirmed = window.confirm(
    `确定删除“${conversation.title}”吗？\n\n该会话的聊天、长期记忆和压缩状态会永久删除；CRM 业务数据仍然保留。`
  );
  if (!confirmed) return;
  const workspace = workspaceSnapshot();
  try {
    await api(`/api/conversations/${id}`, {
      method: "DELETE",
    });
    assertWorkspaceCurrent(workspace);
    state.conversations = state.conversations.filter((item) => item.id !== id);
    if (state.activeId === id) {
      if (state.conversations.length) {
        setActiveConversation(state.conversations[0].id);
        await loadMessages(state.activeId);
        assertWorkspaceCurrent(workspace);
      } else {
        setActiveConversation(null);
        await createConversation();
        assertWorkspaceCurrent(workspace);
      }
    } else {
      renderThreads();
    }
    showToast("会话及其记忆已删除，CRM 业务数据仍然保留");
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    showToast(`删除失败：${error.message}`);
  }
}

function renderCustomers() {
  const query = ui.customerSearch.value.trim().toLocaleLowerCase("zh-CN");
  const selectedStatus = ui.customerStatusFilter.value;
  const filtered = state.customers.filter((customer) => {
    const haystack = [customer.name, customer.company, customer.email, customer.phone].filter(Boolean).join(" ").toLocaleLowerCase("zh-CN");
    return (!query || haystack.includes(query)) && (!selectedStatus || customer.status === selectedStatus);
  });
  ui.customerResultCount.textContent = `${filtered.length} 位客户`;
  ui.customers.replaceChildren();
  ui.customerTableBody.replaceChildren();
  ui.customerTableEmpty.hidden = filtered.length > 0;
  if (!filtered.length) {
    const empty = document.createElement("p");
    empty.className = "panel-empty";
    empty.textContent = state.customers.length ? "没有匹配的客户" : "还没有客户，可让智能助手协助录入。";
    ui.customers.append(empty);
    ui.customerTableEmpty.textContent = state.customers.length ? "没有符合筛选条件的客户。" : "还没有客户，点击“新增客户”录入第一位。";
    return;
  }

  for (const customer of state.customers.slice(0, 8)) {
    const card = document.createElement("article");
    card.className = "customer-card";
    const avatar = document.createElement("span");
    avatar.className = "customer-avatar";
    avatar.textContent = customer.name.trim().slice(0, 1).toUpperCase() || "客";
    const copy = document.createElement("div");
    copy.className = "customer-copy";
    const name = document.createElement("strong");
    name.textContent = customer.name;
    const detail = document.createElement("span");
    detail.textContent = customer.company || customer.email || customer.phone || "暂无补充信息";
    copy.append(name, detail);
    const status = document.createElement("span");
    status.className = `customer-status ${customer.status}`;
    status.textContent = statusNames[customer.status] || customer.status;
    card.append(avatar, copy, status);
    card.tabIndex = 0;
    card.addEventListener("click", () => openCustomerDetail(customer.id));
    card.addEventListener("keydown", (event) => { if (event.key === "Enter") openCustomerDetail(customer.id); });
    ui.customers.append(card);
  }

  for (const customer of filtered) {
    const row = document.createElement("tr");
    const identity = document.createElement("td");
    const identityWrap = document.createElement("div");
    identityWrap.className = "table-customer";
    const avatar = document.createElement("span");
    avatar.className = "customer-avatar";
    avatar.textContent = customer.name.trim().slice(0, 1).toUpperCase() || "客";
    const identityCopy = document.createElement("span");
    const customerName = document.createElement("strong");
    customerName.textContent = customer.name;
    const company = document.createElement("small");
    company.textContent = [customer.company, customer.title].filter(Boolean).join(" · ") || "暂无公司信息";
    identityCopy.append(customerName, company);
    identityWrap.append(avatar, identityCopy);
    identity.append(identityWrap);

    const contact = document.createElement("td");
    const contactWrap = document.createElement("div");
    contactWrap.className = "table-lines";
    const email = document.createElement("span"); email.textContent = customer.email || "—";
    const phone = document.createElement("small"); phone.textContent = customer.phone || "—";
    contactWrap.append(email, phone); contact.append(contactWrap);

    const statusCell = document.createElement("td");
    const status = document.createElement("span"); status.className = `customer-status ${customer.status}`; status.textContent = statusNames[customer.status] || customer.status; statusCell.append(status);
    const owner = document.createElement("td"); owner.textContent = customer.owner_name || "—";
    const updated = document.createElement("td"); updated.textContent = formatDateTime(customer.updated_at);
    const actions = document.createElement("td"); actions.className = "row-actions";
    const detail = document.createElement("button"); detail.type = "button"; detail.className = "table-action"; detail.textContent = "详情"; detail.addEventListener("click", () => openCustomerDetail(customer.id)); actions.append(detail);
    if (canUpdateCustomer()) { const edit = document.createElement("button"); edit.type = "button"; edit.className = "table-action"; edit.textContent = "编辑"; edit.addEventListener("click", () => openCustomerForm(customer)); actions.append(edit); }
    if (canDeleteCustomer()) { const remove = document.createElement("button"); remove.type = "button"; remove.className = "table-action danger"; remove.textContent = "删除"; remove.addEventListener("click", () => deleteCustomer(customer)); actions.append(remove); }
    row.append(identity, contact, statusCell, owner, updated, actions);
    ui.customerTableBody.append(row);
  }
}

async function loadCustomers() {
  const workspace = workspaceSnapshot();
  ui.customers.innerHTML = '<p class="panel-empty">正在读取客户数据…</p>';
  try {
    const customers = await api("/api/customers?limit=100");
    assertWorkspaceCurrent(workspace);
    state.customers = customers;
    renderCustomers();
    renderDashboardFromCustomers();
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    state.customers = [];
    ui.customerCount.textContent = "—";
    ui.customers.replaceChildren();
    const empty = document.createElement("p");
    empty.className = "panel-empty";
    empty.textContent = `读取失败：${error.message}`;
    ui.customers.append(empty);
    ui.customerTableEmpty.hidden = false;
    ui.customerTableEmpty.textContent = `客户读取失败：${error.message}`;
  }
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function formatMoney(value, currency = "CNY") {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return String(value);
  try {
    return new Intl.NumberFormat("zh-CN", { style: "currency", currency: currency || "CNY", maximumFractionDigits: 2 }).format(amount);
  } catch (_) {
    return `${currency || ""} ${amount.toLocaleString("zh-CN")}`.trim();
  }
}

function userNameById(userId) {
  if (!userId) return "—";
  if (String(state.currentUser?.id) === String(userId)) return state.currentUser.display_name || state.currentUser.username || String(userId);
  const user = state.users.find((item) => String(item.id) === String(userId));
  return user?.display_name || user?.username || String(userId);
}

function resourceRecordName(resource, recordId) {
  if (!recordId) return "—";
  const definition = resourceDefinitions[resource];
  const record = recordById(resource, recordId);
  return record && definition ? definition.titleOf(record) : String(recordId);
}

function resourceOptionRecords(resource) {
  const records = [...(state.resourceRecords[resource] || []), ...Object.values(state.resourceOptionCache[resource] || {})];
  return records.filter((record, index) => record?.id && records.findIndex((item) => String(item?.id) === String(record.id)) === index);
}

const relationResources = {
  account_id: "accounts",
  contact_id: "contacts",
  primary_contact_id: "contacts",
  lead_id: "leads",
  opportunity_id: "opportunities",
};

function activityRelatedRecord(record) {
  for (const [key, resource] of [
    ["opportunity_id", "opportunities"],
    ["contact_id", "contacts"],
    ["account_id", "accounts"],
    ["lead_id", "leads"],
  ]) {
    if (record?.[key]) return { resource, id: record[key], label: resourceRecordName(resource, record[key]) };
  }
  return null;
}

function relationDescriptor(record, column) {
  if (column.link) return column.link(record);
  const resource = column.relation || relationResources[column.key];
  const id = record?.[column.key];
  return resource && id ? { resource, id, label: resourceRecordName(resource, id) } : null;
}

function navigateToResourceRecord(resource, recordId) {
  if (!resourceDefinitions[resource] || !recordId) return;
  if (ui.resourceDetailDialog.open) ui.resourceDetailDialog.close();
  switchPage(resource);
  window.setTimeout(() => openResourceDetail(recordId), 0);
}

function createResourceLink(descriptor, fallback = "—") {
  if (!descriptor?.id || !descriptor?.resource) return document.createTextNode(fallback);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "resource-link-button";
  button.textContent = descriptor.label || resourceRecordName(descriptor.resource, descriptor.id);
  button.title = `打开${resourceDefinitions[descriptor.resource]?.singular || "关联记录"}`;
  button.addEventListener("click", () => navigateToResourceRecord(descriptor.resource, descriptor.id));
  return button;
}

function renderBusinessCounts() {
  const labels = { leads: "线索", accounts: "公司", contacts: "联系人", opportunities: "商机", activities: "活动" };
  const container = document.querySelector("#business-counts");
  container.replaceChildren();
  let total = 0;
  for (const resource of resourcePageNames) {
    const count = Number(state.dashboard?.entity_counts?.[resource] ?? state.resourceTotals[resource] ?? state.resourceRecords[resource]?.length ?? 0);
    total += count;
    const item = document.createElement("button"); item.type = "button"; item.className = "business-count";
    const value = document.createElement("strong"); value.textContent = String(count);
    const label = document.createElement("span"); label.textContent = labels[resource];
    item.append(value, label); item.addEventListener("click", () => switchPage(resource)); container.append(item);
  }
  ui.customerCount.textContent = String(total);
}

async function loadBusinessContext() {
  const workspace = workspaceSnapshot();
  await Promise.all(resourcePageNames.map((resource) => loadResource(resource, { quiet: true, offset: 0, query: "" })));
  if (!workspaceIsCurrent(workspace)) return;
  renderBusinessCounts();
  renderDashboardFromCustomers();
}

function normalizeResourceList(value, resource = null) {
  if (Array.isArray(value)) return { items: value, total: value.length };
  const candidates = [value?.items, value?.results, value?.data, resource ? value?.[resource] : null, value?.data?.items];
  const items = candidates.find((candidate) => Array.isArray(candidate)) || [];
  return { items: Array.isArray(items) ? items : [], total: Number(value?.total ?? value?.count ?? items.length) };
}

function resetResourcePage() {
  ui.resourceTableHead.replaceChildren();
  ui.resourceTableBody.replaceChildren();
  ui.resourceTableEmpty.hidden = false;
  ui.resourceTableEmpty.textContent = "选择左侧模块以读取数据。";
  document.querySelector("#resource-result-count").textContent = "0 条记录";
  ui.resourcePagination.hidden = true;
  renderBusinessCounts();
}

function resourcePageState(resource) {
  if (!state.resourcePagination[resource]) state.resourcePagination[resource] = { offset: 0, query: "", hasNext: false, loading: false };
  return state.resourcePagination[resource];
}

function buildResourceListUrl(resource, pagination, filters = {}) {
  const params = new URLSearchParams({ limit: String(RESOURCE_PAGE_SIZE + 1), offset: String(pagination.offset) });
  if (pagination.query) params.set("query", pagination.query);
  for (const [key, value] of Object.entries(filters)) if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  return `/api/${resource}?${params.toString()}`;
}

function updateResourcePagination() {
  const resource = state.activeResource;
  if (!resource) { ui.resourcePagination.hidden = true; return; }
  const pagination = resourcePageState(resource);
  const page = Math.floor(pagination.offset / RESOURCE_PAGE_SIZE) + 1;
  ui.resourcePagination.hidden = false;
  ui.resourcePrevPage.disabled = pagination.loading || pagination.offset === 0;
  ui.resourceNextPage.disabled = pagination.loading || !pagination.hasNext;
  ui.resourcePageState.textContent = pagination.query ? `搜索结果 · 第 ${page} 页` : `第 ${page} 页`;
}

function updateResourceFilterContext(resource) {
  const filters = state.resourceFilters[resource] || {};
  const entries = Object.entries(filters).filter(([, value]) => value !== null && value !== undefined && value !== "");
  ui.resourceFilterContext.hidden = !entries.length;
  if (!entries.length) return;
  const labels = entries.map(([key, value]) => {
    const related = relationResources[key];
    return related ? `${resourceDefinitions[related].singular}：${resourceRecordName(related, value)}` : `${key}：${value}`;
  });
  ui.resourceFilterContext.textContent = `${labels.join(" · ")}　× 清除`;
}

async function activateResource(resource) {
  const definition = resourceDefinitions[resource];
  if (!definition) return;
  state.activeResource = resource;
  document.querySelector("#resource-title").textContent = definition.title;
  document.querySelector("#resource-description").textContent = definition.description;
  document.querySelector("#resource-overline").textContent = resource.toUpperCase();
  ui.resourceSearch.placeholder = definition.search;
  const pagination = resourcePageState(resource);
  ui.resourceSearch.value = pagination.query;
  updateResourceFilterContext(resource);
  const addButton = document.querySelector("#add-resource");
  addButton.textContent = `＋ 新增${definition.singular}`;
  addButton.hidden = !canCreateResource();
  renderResourceTable();
  await loadResource(resource, { offset: pagination.offset, query: pagination.query });
}

async function loadResource(resource = state.activeResource, { quiet = false, offset = 0, query = "", filters = null, preservePage = false } = {}) {
  const definition = resourceDefinitions[resource];
  if (!definition) return;
  const workspace = workspaceSnapshot();
  const pagination = resourcePageState(resource);
  if (preservePage) { offset = pagination.offset; query = pagination.query; }
  const effectiveFilters = filters || state.resourceFilters[resource] || {};
  state.resourceFilters[resource] = effectiveFilters;
  if (state.activeResource === resource) updateResourceFilterContext(resource);
  pagination.offset = Math.max(0, Number(offset) || 0);
  pagination.query = String(query || "").trim();
  pagination.loading = true;
  const token = (state.resourceLoadTokens[resource] || 0) + 1;
  state.resourceLoadTokens[resource] = token;
  const isActive = state.activeResource === resource;
  if (isActive) {
    ui.resourceTableBody.replaceChildren();
    ui.resourceTableEmpty.hidden = false;
    ui.resourceTableEmpty.textContent = `正在读取${definition.title}…`;
    updateResourcePagination();
  }
  try {
    const result = normalizeResourceList(await api(buildResourceListUrl(resource, pagination, effectiveFilters)), resource);
    if (!workspaceIsCurrent(workspace) || state.resourceLoadTokens[resource] !== token) return;
    if (!result.items.length && pagination.offset > 0) {
      pagination.offset = Math.max(0, pagination.offset - RESOURCE_PAGE_SIZE);
      pagination.loading = false;
      if (state.activeResource === resource) showToast("已经是最后一页");
      return loadResource(resource, { offset: pagination.offset, query: pagination.query, filters: effectiveFilters, quiet });
    }
    pagination.hasNext = result.items.length > RESOURCE_PAGE_SIZE;
    state.resourceRecords[resource] = result.items.slice(0, RESOURCE_PAGE_SIZE);
    rememberOverviewRecords(resource, state.resourceRecords[resource]);
    if (!pagination.query && !Object.keys(effectiveFilters).length) {
      const knownMinimum = pagination.offset + state.resourceRecords[resource].length + (pagination.hasNext ? 1 : 0);
      state.resourceTotals[resource] = Math.max(Number(state.dashboard?.entity_counts?.[resource] || 0), Number(state.resourceTotals[resource] || 0), knownMinimum);
      if (!pagination.hasNext) state.resourceTotals[resource] = pagination.offset + state.resourceRecords[resource].length;
    }
    renderBusinessCounts();
    if (state.activeResource === resource) renderResourceTable();
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace) || state.resourceLoadTokens[resource] !== token) return;
    state.resourceRecords[resource] = [];
    renderBusinessCounts();
    if (state.activeResource === resource) {
      renderResourceTable(0);
      ui.resourceTableEmpty.hidden = false;
      ui.resourceTableEmpty.textContent = `无法读取${definition.title}：${error.message}`;
    }
    if (!quiet && error.status !== 401) showToast(`${definition.title}接口读取失败：${error.message}`);
  } finally {
    if (workspaceIsCurrent(workspace) && state.resourceLoadTokens[resource] === token) pagination.loading = false;
    if (workspaceIsCurrent(workspace) && state.activeResource === resource) updateResourcePagination();
  }
}

function resourceColumnValue(record, column) {
  const value = column.value ? column.value(record) : record[column.key];
  if (value === null || value === undefined || value === "") return "—";
  return enumNames[value] || String(value);
}

function renderResourceTable() {
  const resource = state.activeResource;
  const definition = resourceDefinitions[resource];
  if (!definition) return;
  const records = state.resourceRecords[resource] || [];
  ui.resourceTableHead.replaceChildren();
  for (const column of definition.columns) {
    const th = document.createElement("th"); th.textContent = column.label; ui.resourceTableHead.append(th);
  }
  const actionHead = document.createElement("th"); actionHead.textContent = "操作"; actionHead.className = "actions-column"; ui.resourceTableHead.append(actionHead);
  ui.resourceTableBody.replaceChildren();
  ui.resourceTableEmpty.hidden = records.length > 0;
  ui.resourceTableEmpty.textContent = resourcePageState(resource).query ? "没有符合搜索条件的记录。" : `还没有${definition.title}数据。`;
  const pagination = resourcePageState(resource);
  const start = records.length ? pagination.offset + 1 : 0;
  const end = pagination.offset + records.length;
  document.querySelector("#resource-result-count").textContent = records.length ? `显示 ${start}–${end}${pagination.hasNext ? "+" : ""} 条` : "0 条记录";
  for (const record of records) {
    const row = document.createElement("tr");
    for (const column of definition.columns) {
      const cell = document.createElement("td");
      const value = resourceColumnValue(record, column);
      if (column.badge) {
        const badge = document.createElement("span"); badge.className = `record-badge ${String(record[column.key] || "").toLowerCase()}`; badge.textContent = value; cell.append(badge);
      } else if (column.relation || column.link) {
        cell.append(createResourceLink(relationDescriptor(record, column), value));
      } else {
        cell.textContent = value;
      }
      row.append(cell);
    }
    const actions = document.createElement("td"); actions.className = "row-actions";
    const detail = document.createElement("button"); detail.type = "button"; detail.className = "table-action"; detail.textContent = "详情"; detail.addEventListener("click", () => openResourceDetail(record.id)); actions.append(detail);
    if (resource === "leads" && record.status !== "converted" && canUpdateResource()) {
      const convert = document.createElement("button"); convert.type = "button"; convert.className = "table-action convert"; convert.textContent = "转为客户"; convert.addEventListener("click", () => openLeadConversion(record)); actions.append(convert);
    }
    if (canUpdateResource()) { const edit = document.createElement("button"); edit.type = "button"; edit.className = "table-action"; edit.textContent = "编辑"; edit.addEventListener("click", () => openResourceForm(record)); actions.append(edit); }
    if (canDeleteResource()) { const remove = document.createElement("button"); remove.type = "button"; remove.className = "table-action danger"; remove.textContent = "删除"; remove.addEventListener("click", () => deleteResource(record)); actions.append(remove); }
    row.append(actions); ui.resourceTableBody.append(row);
  }
  updateResourcePagination();
}

function toDateTimeLocal(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function createResourceInput(field, value) {
  const label = document.createElement("label");
  if (field.wide) label.className = "span-2";
  const title = document.createElement("span"); title.textContent = `${field.label}${field.required ? " *" : ""}`;
  let input;
  let suggestions = null;
  if (field.type === "textarea" || field.type === "json") {
    input = document.createElement("textarea"); input.rows = 4;
  } else if (field.type === "suggest") {
    input = document.createElement("input"); input.type = "text";
    suggestions = document.createElement("datalist");
    suggestions.id = `resource-options-${state.activeResource}-${field.key}`;
    input.setAttribute("list", suggestions.id);
    for (const optionValue of field.options || []) {
      const option = document.createElement("option"); option.value = optionValue; option.label = enumNames[optionValue] || optionValue; suggestions.append(option);
    }
  } else if (field.type === "select") {
    input = document.createElement("select");
    if (!field.required) { const empty = document.createElement("option"); empty.value = ""; empty.textContent = "请选择"; input.append(empty); }
    for (const optionValue of field.options || []) { const option = document.createElement("option"); option.value = optionValue; option.textContent = enumNames[optionValue] || optionValue; input.append(option); }
  } else if (field.type === "resource-select") {
    input = document.createElement("select");
    const empty = document.createElement("option"); empty.value = ""; empty.textContent = "请选择"; input.append(empty);
    const targetDefinition = resourceDefinitions[field.resource];
    for (const record of resourceOptionRecords(field.resource)) {
      const option = document.createElement("option"); option.value = record.id; option.textContent = targetDefinition.titleOf(record); input.append(option);
    }
  } else if (field.type === "user-select") {
    input = document.createElement("select");
    const empty = document.createElement("option"); empty.value = ""; empty.textContent = "使用当前负责人"; input.append(empty);
    for (const user of state.users || []) {
      const option = document.createElement("option"); option.value = user.id; option.textContent = `${user.display_name || user.username}（${roleNames[user.role] || user.role}）`; input.append(option);
    }
  } else {
    input = document.createElement("input"); input.type = field.type || "text";
  }
  input.name = field.key;
  input.id = `resource-field-${field.key}`;
  input.required = Boolean(field.required);
  if (field.min !== undefined) input.min = field.min;
  if (field.max !== undefined) input.max = field.max;
  if (field.step !== undefined) input.step = field.step;
  if (field.maxlength !== undefined) input.maxLength = field.maxlength;
  input.value = field.type === "datetime-local"
    ? toDateTimeLocal(value)
    : field.type === "json" && value && typeof value === "object"
      ? JSON.stringify(value, null, 2)
      : (value ?? field.default ?? "");
  label.append(title, input);
  if (suggestions) label.append(suggestions);
  return label;
}

async function ensureResourceOptions(definition) {
  const workspace = workspaceSnapshot();
  const dependencies = [...new Set(definition.fields.filter((field) => field.type === "resource-select").map((field) => field.resource))];
  await Promise.all(dependencies.map((resource) => state.resourceRecords[resource] ? Promise.resolve() : loadResource(resource, { quiet: true })));
  assertWorkspaceCurrent(workspace);
  if (state.currentUser?.role === "admin" && definition.fields.some((field) => field.type === "user-select") && !state.users.length) {
    try {
      const users = normalizeResourceList(await api("/api/users?limit=100")).items;
      assertWorkspaceCurrent(workspace);
      state.users = users;
    } catch (error) {
      if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) throw error;
      state.users = state.currentUser ? [state.currentUser] : [];
    }
  }
}

async function ensureCurrentResourceOptions(definition, record) {
  if (!record) return;
  const workspace = workspaceSnapshot();
  const dependencies = definition.fields.filter((field) => field.type === "resource-select" && record[field.key]);
  await Promise.all(dependencies.map(async (field) => {
    if (recordById(field.resource, record[field.key])) return;
    try {
      const related = await api(`/api/${field.resource}/${encodeURIComponent(record[field.key])}`);
      assertWorkspaceCurrent(workspace);
      if (related?.id) rememberOverviewRecords(field.resource, [related]);
    } catch (error) {
      if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) throw error;
      // Keep the form usable if an old relation is no longer visible; the fallback
      // option below preserves its ID instead of silently clearing the relationship.
    }
  }));
}

function buildRemoteResourceSearchUrl(resource, query, filters = {}, limit = 50) {
  const params = new URLSearchParams({ limit: String(limit), offset: "0" });
  if (String(query || "").trim()) params.set("query", String(query).trim());
  for (const [key, value] of Object.entries(filters || {})) {
    if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  }
  return `/api/${resource}?${params.toString()}`;
}

function remoteRelationFilters(formResource, field, fields) {
  const accountId = fields.querySelector('[name="account_id"]')?.value || "";
  if (!accountId) return {};
  if (formResource === "opportunities" && field.key === "primary_contact_id") return { account_id: accountId };
  if (formResource === "activities" && ["contact_id", "opportunity_id"].includes(field.key)) return { account_id: accountId };
  return {};
}

function wireRemoteResourceSearches(fields) {
  for (const select of fields.querySelectorAll('select[name]')) {
    const field = resourceDefinitions[state.activeResource]?.fields.find((item) => item.key === select.name && item.type === "resource-select");
    if (!field) continue;
    const label = select.closest("label");
    const search = document.createElement("input");
    search.type = "search"; search.className = "relation-search"; search.placeholder = `搜索${resourceDefinitions[field.resource].title}…`;
    search.setAttribute("aria-label", `搜索${field.label}`);
    let timer = null;
    search.addEventListener("input", () => {
      clearTimeout(timer);
      const scheduledWorkspace = workspaceSnapshot();
      timer = setTimeout(async () => {
        if (!workspaceIsCurrent(scheduledWorkspace)) return;
        const query = search.value.trim();
        if (!query) return;
        const workspace = scheduledWorkspace;
        const formResource = state.activeResource;
        const filters = remoteRelationFilters(formResource, field, fields);
        search.disabled = true;
        try {
          const result = normalizeResourceList(await api(buildRemoteResourceSearchUrl(field.resource, query, filters)), field.resource);
          if (!workspaceIsCurrent(workspace) || state.activeResource !== formResource) return;
          rememberOverviewRecords(field.resource, result.items);
          refillResourceSelect(select, field.resource, result.items, select.value);
          reapplyAssociationFilters(state.activeResource, fields);
          if (!result.items.length) showToast(`没有找到匹配的${resourceDefinitions[field.resource].title}`);
        } catch (error) {
          if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
          showToast(`关联搜索失败：${error.message}`);
        } finally {
          if (workspaceIsCurrent(workspace)) { search.disabled = false; search.focus(); }
        }
      }, 320);
    });
    label?.insertBefore(search, select);
  }
}

function refillResourceSelect(select, resource, records, preferredValue = select?.value || "") {
  if (!select) return;
  const preferred = preferredValue === null || preferredValue === undefined ? "" : String(preferredValue);
  select.replaceChildren();
  const empty = document.createElement("option"); empty.value = ""; empty.textContent = "请选择"; select.append(empty);
  const definition = resourceDefinitions[resource];
  for (const record of records) {
    const option = document.createElement("option"); option.value = record.id; option.textContent = definition.titleOf(record); select.append(option);
  }
  if (preferred && !records.some((record) => String(record.id) === preferred)) {
    const preserved = document.createElement("option");
    preserved.value = preferred;
    preserved.textContent = `当前关联（${resourceRecordName(resource, preferred)}）`;
    select.append(preserved);
  }
  select.value = preferred;
}

function recordById(resource, id) {
  return (state.resourceRecords[resource] || []).find((record) => String(record.id) === String(id)) || state.resourceOptionCache[resource]?.[String(id)];
}

function addAssociationGuidance(fields, text) {
  const note = document.createElement("p");
  note.className = "association-guidance span-2";
  note.textContent = text;
  fields.append(note);
}

function wireOpportunityAssociations(fields) {
  const account = fields.querySelector('[name="account_id"]');
  const contact = fields.querySelector('[name="primary_contact_id"]');
  if (!account || !contact) return;
  const filterContacts = () => {
    const selected = contact.value;
    const contacts = resourceOptionRecords("contacts").filter((item) => !account.value || String(item.account_id || "") === String(account.value));
    refillResourceSelect(contact, "contacts", contacts, selected);
  };
  account.addEventListener("change", filterContacts);
  contact.addEventListener("change", () => {
    const selected = recordById("contacts", contact.value);
    if (selected?.account_id) account.value = String(selected.account_id);
    filterContacts();
  });
  filterContacts();
  addAssociationGuidance(fields, "选择公司后，只显示该公司的联系人；选择联系人时会自动对齐所属公司。");
}

function wireActivityAssociations(fields) {
  const account = fields.querySelector('[name="account_id"]');
  const contact = fields.querySelector('[name="contact_id"]');
  const opportunity = fields.querySelector('[name="opportunity_id"]');
  const lead = fields.querySelector('[name="lead_id"]');
  if (!account || !contact || !opportunity || !lead) return;
  const filterDependents = () => {
    const contactValue = contact.value;
    const opportunityValue = opportunity.value;
    const contacts = resourceOptionRecords("contacts").filter((item) => !account.value || String(item.account_id || "") === String(account.value));
    const opportunities = resourceOptionRecords("opportunities").filter((item) => !account.value || String(item.account_id || "") === String(account.value));
    refillResourceSelect(contact, "contacts", contacts, contactValue);
    refillResourceSelect(opportunity, "opportunities", opportunities, opportunityValue);
  };
  account.addEventListener("change", () => { if (account.value) lead.value = ""; filterDependents(); });
  contact.addEventListener("change", () => {
    if (contact.value) lead.value = "";
    const selected = recordById("contacts", contact.value);
    if (selected?.account_id) account.value = String(selected.account_id);
    const selectedOpportunity = recordById("opportunities", opportunity.value);
    if (selectedOpportunity?.primary_contact_id && String(selectedOpportunity.primary_contact_id) !== String(contact.value)) opportunity.value = "";
    filterDependents();
  });
  opportunity.addEventListener("change", () => {
    if (opportunity.value) lead.value = "";
    const selected = recordById("opportunities", opportunity.value);
    if (selected?.account_id) account.value = String(selected.account_id);
    filterDependents();
    if (selected?.primary_contact_id) contact.value = String(selected.primary_contact_id);
  });
  lead.addEventListener("change", () => {
    if (!lead.value) return;
    account.value = ""; contact.value = ""; opportunity.value = "";
    filterDependents();
  });
  filterDependents();
  addAssociationGuidance(fields, "公司会约束联系人和商机范围；选择联系人或商机时会自动对齐公司及主要联系人。选择线索会切换为独立线索关联，避免形成互相矛盾的关系。");
}

function reapplyAssociationFilters(resource, fields) {
  if (resource === "opportunities") {
    const account = fields.querySelector('[name="account_id"]');
    const contact = fields.querySelector('[name="primary_contact_id"]');
    if (!account || !contact) return;
    refillResourceSelect(contact, "contacts", resourceOptionRecords("contacts").filter((item) => !account.value || String(item.account_id || "") === String(account.value)), contact.value);
  } else if (resource === "activities") {
    const account = fields.querySelector('[name="account_id"]');
    const contact = fields.querySelector('[name="contact_id"]');
    const opportunity = fields.querySelector('[name="opportunity_id"]');
    if (!account || !contact || !opportunity) return;
    refillResourceSelect(contact, "contacts", resourceOptionRecords("contacts").filter((item) => !account.value || String(item.account_id || "") === String(account.value)), contact.value);
    refillResourceSelect(opportunity, "opportunities", resourceOptionRecords("opportunities").filter((item) => !account.value || String(item.account_id || "") === String(account.value)), opportunity.value);
  }
}

function validateResourceAssociations(resource, payload) {
  if (resource === "opportunities" && payload.account_id && payload.primary_contact_id) {
    const contact = recordById("contacts", payload.primary_contact_id);
    if (contact && String(contact.account_id || "") !== String(payload.account_id)) throw new Error("商机主要联系人必须属于所选公司");
  }
  if (resource !== "activities") return;
  const contact = recordById("contacts", payload.contact_id);
  const opportunity = recordById("opportunities", payload.opportunity_id);
  if (payload.account_id && contact && String(contact.account_id || "") !== String(payload.account_id)) throw new Error("活动联系人必须属于所选公司");
  if (payload.account_id && opportunity && String(opportunity.account_id || "") !== String(payload.account_id)) throw new Error("活动商机必须属于所选公司");
  if (payload.contact_id && opportunity?.primary_contact_id && String(opportunity.primary_contact_id) !== String(payload.contact_id)) throw new Error("活动联系人必须与商机主要联系人一致");
}

async function openResourceForm(record = null) {
  const resource = state.activeResource;
  const definition = resourceDefinitions[resource];
  if (!definition || (record ? !canUpdateResource() : !canCreateResource())) return;
  const workspace = workspaceSnapshot();
  try {
    await ensureResourceOptions(definition);
    assertWorkspaceCurrent(workspace);
    await ensureCurrentResourceOptions(definition, record);
    assertWorkspaceCurrent(workspace);
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    showToast(`无法打开${definition.singular}表单：${error.message}`);
    return;
  }
  state.editingResourceRecord = record ? structuredClone(record) : null;
  ui.resourceForm.reset();
  document.querySelector("#resource-id").value = record?.id || "";
  document.querySelector("#resource-dialog-overline").textContent = resource.toUpperCase();
  document.querySelector("#resource-dialog-title").textContent = `${record ? "编辑" : "新增"}${definition.singular}`;
  document.querySelector("#save-resource").textContent = record ? "保存修改" : `创建${definition.singular}`;
  const fields = document.querySelector("#resource-form-fields"); fields.replaceChildren();
  definition.fields
    .filter((field) => !field.managerOnly || state.currentUser?.role === "admin")
    .forEach((field) => fields.append(createResourceInput(field, record?.[field.key])));
  for (const field of definition.fields.filter((item) => item.type === "resource-select" && record?.[item.key])) {
    const select = fields.querySelector(`[name="${field.key}"]`);
    if (select && ![...select.options].some((option) => String(option.value) === String(record[field.key]))) {
      const preserved = document.createElement("option"); preserved.value = record[field.key]; preserved.textContent = `当前关联（${record[field.key]}）`; select.append(preserved); select.value = record[field.key];
    }
  }
  if (resource === "opportunities") wireOpportunityAssociations(fields);
  if (resource === "activities") wireActivityAssociations(fields);
  wireRemoteResourceSearches(fields);
  if (resource === "leads" && record?.status === "converted") {
    const status = fields.querySelector('[name="status"]');
    if (status) {
      status.disabled = true;
      status.closest("label")?.classList.add("locked-field");
      const note = document.createElement("small"); note.className = "field-lock-note"; note.textContent = "已转换状态由正式转换流程锁定，不能在普通编辑中回退。"; status.closest("label")?.append(note);
    }
  }
  document.querySelector("#resource-form-error").hidden = true;
  ui.resourceDialog.showModal();
  fields.querySelector("input, select, textarea")?.focus();
}

function stableSerialize(value) {
  if (Array.isArray(value)) return `[${value.map(stableSerialize).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableSerialize(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

function normalizeResourceComparable(field, value) {
  if (value === "" || value === null || value === undefined) return null;
  if (field.type === "number") return field.numeric === "decimal" ? String(value) : Number(value);
  if (field.type === "json") return stableSerialize(value);
  if (field.type === "date") return String(value).slice(0, 10);
  if (field.type === "datetime-local") {
    const time = new Date(value).getTime();
    return Number.isNaN(time) ? String(value).slice(0, 16) : time;
  }
  return String(value);
}

function parseResourceFieldValue(field, raw, isEdit) {
  if (!raw) return isEdit && !field.required && field.type !== "json" ? null : undefined;
  if (field.type === "json") {
    try { return JSON.parse(raw); }
    catch (_) { throw new Error(`${field.label}必须是有效的 JSON`); }
  }
  if (field.type === "datetime-local") {
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) throw new Error(`${field.label}必须是有效的日期时间`);
    return date.toISOString();
  }
  return field.type === "number" ? (field.numeric === "decimal" ? raw : Number(raw)) : raw;
}

function resourceFormPayload(isEdit = false) {
  const definition = resourceDefinitions[state.activeResource];
  const formData = new FormData(ui.resourceForm);
  const payload = {};
  for (const field of definition.fields) {
    const control = ui.resourceForm.elements.namedItem(field.key);
    if (!control || control.disabled) continue;
    const raw = String(formData.get(field.key) ?? "").trim();
    const value = parseResourceFieldValue(field, raw, isEdit);
    if (value === undefined) continue;
    if (isEdit && normalizeResourceComparable(field, value) === normalizeResourceComparable(field, state.editingResourceRecord?.[field.key])) continue;
    payload[field.key] = value;
  }
  return payload;
}

if (typeof globalThis !== "undefined" && globalThis.__CRM_ENABLE_TEST_HOOKS__) {
  globalThis.__crmFrontendTestHooks = {
    stableSerialize,
    normalizeResourceComparable,
    parseResourceFieldValue,
    workspaceSnapshot,
    workspaceIsCurrent,
    commitIfWorkspaceCurrent,
    buildRemoteResourceSearchUrl,
    remoteRelationFilters,
    conversionRemoteSearchContext,
  };
}

async function saveResource(event) {
  event.preventDefault();
  const resource = state.activeResource;
  const definition = resourceDefinitions[resource];
  const id = document.querySelector("#resource-id").value;
  if (!definition || (id ? !canUpdateResource() : !canCreateResource())) return;
  const button = document.querySelector("#save-resource");
  const errorBox = document.querySelector("#resource-form-error");
  const workspace = workspaceSnapshot();
  button.disabled = true; errorBox.hidden = true;
  try {
    const payload = resourceFormPayload(Boolean(id));
    if (id && !Object.keys(payload).length) {
      ui.resourceDialog.close(); state.editingResourceRecord = null; showToast("没有需要保存的修改"); return;
    }
    validateResourceAssociations(resource, id ? { ...state.editingResourceRecord, ...payload } : payload);
    await api(id ? `/api/${resource}/${encodeURIComponent(id)}` : `/api/${resource}`, { method: id ? "PATCH" : "POST", body: JSON.stringify(payload) });
    assertWorkspaceCurrent(workspace);
    ui.resourceDialog.close(); state.editingResourceRecord = null;
    await loadResource(resource, { preservePage: true });
    assertWorkspaceCurrent(workspace);
    showToast(`${definition.singular}${id ? "已更新" : "已创建"}`);
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    errorBox.textContent = error.message; errorBox.hidden = false;
  } finally { if (workspaceIsCurrent(workspace)) button.disabled = false; }
}

function conversionValue(id) {
  return document.querySelector(`#${id}`)?.value.trim() || "";
}

function compactObject(value) {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== "" && item !== null && item !== undefined));
}

function populateConversionSelect(selectId, resource, records, emptyText) {
  const select = document.querySelector(`#${selectId}`);
  const previous = select.value;
  select.replaceChildren();
  const empty = document.createElement("option"); empty.value = ""; empty.textContent = emptyText; select.append(empty);
  for (const record of records) {
    const option = document.createElement("option"); option.value = record.id; option.textContent = resourceDefinitions[resource].titleOf(record); select.append(option);
  }
  if (records.some((record) => String(record.id) === String(previous))) select.value = previous;
  return select;
}

function conversionRemoteSearchContext(resource) {
  if (resource === "accounts") {
    const ownerId = state.conversionLead?.owner_id || "";
    return {
      filters: { owner_id: ownerId },
      accept: (record) => !ownerId || String(record.owner_id || "") === String(ownerId),
    };
  }
  const accountId = selectedConversionAccountId();
  return {
    filters: { account_id: accountId },
    accept: (record) => !accountId || String(record.account_id || "") === String(accountId),
  };
}

function wireConversionRemoteSearch(inputId, selectId, resource) {
  const input = document.querySelector(`#${inputId}`);
  const select = document.querySelector(`#${selectId}`);
  if (!input || !select || input.dataset.remoteBound) return;
  input.dataset.remoteBound = "true";
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const scheduledWorkspace = workspaceSnapshot();
    timer = setTimeout(async () => {
      if (!workspaceIsCurrent(scheduledWorkspace)) return;
      const query = input.value.trim();
      if (!query) return;
      const workspace = scheduledWorkspace;
      const context = conversionRemoteSearchContext(resource);
      if (resource === "contacts" && !context.filters.account_id) {
        showToast("请先选择客户公司，再搜索该公司的联系人");
        return;
      }
      input.disabled = true;
      try {
        const result = normalizeResourceList(await api(buildRemoteResourceSearchUrl(resource, query, context.filters)), resource);
        if (!workspaceIsCurrent(workspace)) return;
        const records = result.items.filter(context.accept);
        rememberOverviewRecords(resource, records);
        const previous = select.value;
        populateConversionSelect(selectId, resource, records, `没有匹配的${resourceDefinitions[resource].title}`);
        if (previous && !records.some((record) => String(record.id) === String(previous))) {
          const preserved = document.createElement("option");
          preserved.value = previous;
          preserved.textContent = `当前关联（${resourceRecordName(resource, previous)}）`;
          select.append(preserved);
        }
        if (previous) select.value = previous;
        if (!records.length) showToast(`没有找到可用于此线索的${resourceDefinitions[resource].title}`);
        renderConversionConfirmation();
      } catch (error) {
        if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
        showToast(`关联搜索失败：${error.message}`);
      } finally {
        if (workspaceIsCurrent(workspace)) { input.disabled = false; input.focus(); }
      }
    }, 320);
  });
}

function selectedConversionAccountId() {
  return document.querySelector("#convert-account-mode").value === "existing" ? conversionValue("convert-account-id") : "";
}

function refreshConversionContacts(preferredId = "") {
  const accountId = selectedConversionAccountId();
  const ownerId = state.conversionLead?.owner_id;
  const contacts = resourceOptionRecords("contacts").filter((record) => accountId && String(record.account_id || "") === String(accountId) && (!ownerId || String(record.owner_id || "") === String(ownerId)));
  const select = populateConversionSelect("convert-contact-id", "contacts", contacts, accountId ? "选择该公司的联系人" : "请先选择客户公司");
  if (contacts.some((record) => String(record.id) === String(preferredId))) select.value = String(preferredId);
  select.disabled = !accountId;
}

function setConversionModes() {
  const accountMode = document.querySelector("#convert-account-mode").value;
  const contactMode = document.querySelector("#convert-contact-mode");
  const createOpportunity = document.querySelector("#convert-create-opportunity").checked;
  document.querySelector("#convert-existing-account").hidden = accountMode !== "existing";
  document.querySelector("#convert-new-account").hidden = accountMode !== "new";
  if (accountMode === "new" && contactMode.value === "existing") contactMode.value = "new";
  contactMode.querySelector('option[value="existing"]').disabled = accountMode !== "existing";
  document.querySelector("#convert-existing-contact").hidden = contactMode.value !== "existing";
  document.querySelector("#convert-new-contact").hidden = contactMode.value !== "new";
  document.querySelector("#convert-opportunity-fields").hidden = !createOpportunity;
  document.querySelector("#convert-account-id").required = accountMode === "existing";
  document.querySelector("#convert-account-name").required = accountMode === "new";
  document.querySelector("#convert-contact-id").required = contactMode.value === "existing";
  document.querySelector("#convert-opportunity-name").required = createOpportunity;
  refreshConversionContacts(conversionValue("convert-contact-id"));
  document.querySelector("#convert-confirm").checked = false;
  renderConversionConfirmation();
}

function buildLeadConversionPayload() {
  const accountMode = document.querySelector("#convert-account-mode").value;
  const contactMode = document.querySelector("#convert-contact-mode").value;
  const payload = {};
  if (accountMode === "existing") {
    const accountId = conversionValue("convert-account-id");
    if (!accountId) throw new Error("请选择要关联的客户公司");
    payload.account_id = accountId;
  } else {
    const name = conversionValue("convert-account-name");
    if (!name) throw new Error("请填写新公司名称");
    payload.account = compactObject({
      name,
      industry: conversionValue("convert-account-industry"),
      phone: conversionValue("convert-account-phone"),
      email: conversionValue("convert-account-email"),
      source: conversionValue("convert-account-source"),
      status: "active",
    });
  }
  if (contactMode === "existing") {
    const contactId = conversionValue("convert-contact-id");
    if (!contactId) throw new Error("请选择要关联的联系人");
    const contact = recordById("contacts", contactId);
    if (payload.account_id && contact && String(contact.account_id || "") !== String(payload.account_id)) throw new Error("联系人必须属于所选公司");
    payload.contact_id = contactId;
  } else if (contactMode === "new") {
    payload.contact = compactObject({
      first_name: conversionValue("convert-contact-first-name"),
      last_name: conversionValue("convert-contact-last-name"),
      title: conversionValue("convert-contact-title"),
      email: conversionValue("convert-contact-email"),
      mobile: conversionValue("convert-contact-mobile"),
      source: conversionValue("convert-contact-source"),
    });
  }
  if (document.querySelector("#convert-create-opportunity").checked) {
    const name = conversionValue("convert-opportunity-name");
    if (!name) throw new Error("请填写商机名称");
    const amount = conversionValue("convert-opportunity-amount");
    const probability = conversionValue("convert-opportunity-probability");
    payload.opportunity = compactObject({
      name,
      amount: amount || undefined,
      currency: conversionValue("convert-opportunity-currency") || "CNY",
      stage: conversionValue("convert-opportunity-stage") || "prospecting",
      probability: probability ? Number(probability) : undefined,
      expected_close_date: conversionValue("convert-opportunity-close-date"),
      source: conversionValue("convert-opportunity-source"),
      description: conversionValue("convert-opportunity-description"),
    });
  }
  if (state.conversionLead?.version != null) payload.expected_version = Number(state.conversionLead.version);
  return payload;
}

function conversionTargetSummary() {
  const accountMode = document.querySelector("#convert-account-mode").value;
  const contactMode = document.querySelector("#convert-contact-mode").value;
  const account = accountMode === "existing"
    ? resourceRecordName("accounts", conversionValue("convert-account-id"))
    : conversionValue("convert-account-name") || "待填写的新公司";
  let contact = "按线索信息自动创建";
  if (contactMode === "existing") contact = resourceRecordName("contacts", conversionValue("convert-contact-id"));
  if (contactMode === "new") contact = [conversionValue("convert-contact-first-name"), conversionValue("convert-contact-last-name")].filter(Boolean).join(" ") || conversionValue("convert-contact-email") || "待填写的新联系人";
  const opportunity = document.querySelector("#convert-create-opportunity").checked ? (conversionValue("convert-opportunity-name") || "待填写的商机") : "本次不创建";
  return { account, contact, opportunity };
}

function renderConversionConfirmation() {
  const container = document.querySelector("#conversion-confirmation");
  const summary = conversionTargetSummary();
  container.replaceChildren();
  const title = document.createElement("strong"); title.textContent = "本次将原子执行"; container.append(title);
  const list = document.createElement("dl");
  for (const [label, value] of [["客户公司", summary.account], ["联系人", summary.contact], ["首个商机", summary.opportunity], ["原线索", "标记为已转化并保留来源关系"]]) {
    const term = document.createElement("dt"); term.textContent = label;
    const detail = document.createElement("dd"); detail.textContent = value;
    list.append(term, detail);
  }
  container.append(list);
}

async function openLeadConversion(leadOrId) {
  if (!canUpdateResource()) return;
  const workspace = workspaceSnapshot();
  try {
    const lead = typeof leadOrId === "object" ? leadOrId : await api(`/api/leads/${encodeURIComponent(leadOrId)}`);
    assertWorkspaceCurrent(workspace);
    if (lead.status === "converted") { showToast("该线索已经转换"); return; }
    if (!lead.owner_id) { showToast("请先为线索分配负责人，再执行转换"); return; }
    await Promise.all([
      state.resourceRecords.accounts ? Promise.resolve() : loadResource("accounts", { quiet: true }),
      state.resourceRecords.contacts ? Promise.resolve() : loadResource("contacts", { quiet: true }),
    ]);
    assertWorkspaceCurrent(workspace);
    state.conversionLead = lead;
    ui.leadConvertForm.reset();
    document.querySelector("#convert-lead-id").value = lead.id;
    const leadName = resourceDefinitions.leads.titleOf(lead);
    document.querySelector("#convert-lead-avatar").textContent = leadName.slice(0, 1) || "线";
    document.querySelector("#convert-lead-name").textContent = leadName;
    document.querySelector("#convert-lead-company").textContent = [lead.company_name, lead.job_title, lead.email || lead.phone].filter(Boolean).join(" · ") || "暂无补充信息";
    const accounts = resourceOptionRecords("accounts").filter((record) => String(record.owner_id || "") === String(lead.owner_id));
    const accountSelect = populateConversionSelect("convert-account-id", "accounts", accounts, "选择客户公司");
    const matchingAccount = accounts.find((record) => lead.company_name && String(record.name || "").trim().toLocaleLowerCase("zh-CN") === String(lead.company_name).trim().toLocaleLowerCase("zh-CN"));
    document.querySelector("#convert-account-mode").value = matchingAccount ? "existing" : "new";
    if (matchingAccount) accountSelect.value = matchingAccount.id;
    document.querySelector("#convert-account-name").value = lead.company_name || "";
    document.querySelector("#convert-account-phone").value = lead.phone || "";
    document.querySelector("#convert-account-email").value = lead.email || "";
    document.querySelector("#convert-account-source").value = lead.source || "";
    document.querySelector("#convert-contact-mode").value = "new";
    document.querySelector("#convert-contact-first-name").value = lead.first_name || "";
    document.querySelector("#convert-contact-last-name").value = lead.last_name || "";
    document.querySelector("#convert-contact-title").value = lead.job_title || "";
    document.querySelector("#convert-contact-email").value = lead.email || "";
    document.querySelector("#convert-contact-mobile").value = lead.phone || "";
    document.querySelector("#convert-contact-source").value = lead.source || "";
    document.querySelector("#convert-opportunity-name").value = `${lead.company_name || leadName} 商机`;
    document.querySelector("#convert-opportunity-source").value = lead.source || "";
    document.querySelector("#lead-convert-error").hidden = true;
    wireConversionRemoteSearch("convert-account-search", "convert-account-id", "accounts");
    wireConversionRemoteSearch("convert-contact-search", "convert-contact-id", "contacts");
    setConversionModes();
    ui.leadConvertDialog.showModal();
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    showToast(`无法打开转换流程：${error.message}`);
  }
}

async function submitLeadConversion(event) {
  event.preventDefault();
  const errorBox = document.querySelector("#lead-convert-error");
  const button = document.querySelector("#submit-lead-convert");
  const workspace = workspaceSnapshot();
  errorBox.hidden = true;
  button.disabled = true; button.textContent = "转换中…";
  try {
    if (!document.querySelector("#convert-confirm").checked) throw new Error("请先核对并确认转换内容");
    const leadId = conversionValue("convert-lead-id");
    const latestLead = await api(`/api/leads/${encodeURIComponent(leadId)}`);
    assertWorkspaceCurrent(workspace);
    if (state.conversionLead?.version != null && latestLead?.version != null && Number(latestLead.version) !== Number(state.conversionLead.version)) {
      throw new Error("线索已被其他操作更新。请关闭转换窗口，重新打开并核对最新资料。");
    }
    const result = await api(`/api/leads/${encodeURIComponent(leadId)}/convert`, { method: "POST", body: JSON.stringify(buildLeadConversionPayload()) });
    assertWorkspaceCurrent(workspace);
    ui.leadConvertDialog.close();
    state.conversionLead = null;
    await Promise.all(resourcePageNames.map((resource) => loadResource(resource, { quiet: true })));
    assertWorkspaceCurrent(workspace);
    renderBusinessCounts();
    showToast("线索已转换，公司、联系人及商机关系已保存");
    const accountId = result?.account?.id || result?.conversion?.account_id;
    if (accountId) navigateToResourceRecord("accounts", accountId);
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    errorBox.textContent = error.message; errorBox.hidden = false;
  } finally {
    if (workspaceIsCurrent(workspace)) { button.disabled = false; button.textContent = "确认转换"; }
  }
}

function recordDetailValue(key, value, record) {
  if (value === null || value === undefined || value === "") return "—";
  if (key === "amount") return formatMoney(value, record.currency);
  if (key.endsWith("_at")) return formatDateTime(value);
  if (typeof value === "object") { try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); } }
  return enumNames[value] || String(value);
}

function rememberOverviewRecords(resource, records = []) {
  if (!state.resourceOptionCache[resource]) state.resourceOptionCache[resource] = {};
  for (const record of records) if (record?.id) state.resourceOptionCache[resource][String(record.id)] = record;
}

function createOverviewGroup(title, resource, records, detailOf, { total = records.length, truncated = false, filters = {} } = {}) {
  const section = document.createElement("section"); section.className = "overview-group";
  const heading = document.createElement("div"); heading.className = "overview-group-heading";
  const name = document.createElement("h4"); name.textContent = title;
  const headingActions = document.createElement("div"); headingActions.className = "overview-group-actions";
  const count = document.createElement("span"); count.textContent = String(total);
  const showAll = document.createElement("button"); showAll.type = "button"; showAll.textContent = truncated ? `显示前 ${records.length} / 共 ${total} · 查看全部` : "查看全部"; showAll.addEventListener("click", () => {
    if (ui.resourceDetailDialog.open) ui.resourceDetailDialog.close();
    state.resourceFilters[resource] = filters;
    const pagination = resourcePageState(resource); pagination.offset = 0; pagination.query = "";
    switchPage(resource);
  });
  headingActions.append(count, showAll); heading.append(name, headingActions); section.append(heading);
  const list = document.createElement("div"); list.className = "overview-records";
  if (!records.length) {
    const empty = document.createElement("p"); empty.className = "overview-empty"; empty.textContent = "暂无记录"; list.append(empty);
  } else {
    for (const record of records) {
      const button = document.createElement("button"); button.type = "button"; button.className = "overview-record";
      const copy = document.createElement("span");
      const primary = document.createElement("strong"); primary.textContent = resourceDefinitions[resource].titleOf(record);
      const secondary = document.createElement("small"); secondary.textContent = detailOf(record) || "查看详情";
      copy.append(primary, secondary);
      const arrow = document.createElement("span"); arrow.className = "overview-arrow"; arrow.textContent = "›";
      button.append(copy, arrow);
      button.addEventListener("click", () => navigateToResourceRecord(resource, record.id));
      list.append(button);
    }
  }
  section.append(list); return section;
}

async function openAccountTransfer(account, overview) {
  if (state.currentUser?.role !== "admin") return;
  const workspace = workspaceSnapshot();
  try {
    const users = normalizeResourceList(await api("/api/users")).items;
    assertWorkspaceCurrent(workspace);
    state.users = users;
    const select = document.querySelector("#transfer-owner-id"); select.replaceChildren();
    const empty = document.createElement("option"); empty.value = ""; empty.textContent = "选择新负责人"; select.append(empty);
    for (const user of state.users.filter((item) => item.is_active !== false && String(item.id) !== String(account.owner_id))) {
      const option = document.createElement("option"); option.value = user.id; option.textContent = `${user.display_name || user.username}（${roleNames[user.role] || user.role}）`; select.append(option);
    }
    document.querySelector("#transfer-account-id").value = account.id;
    document.querySelector("#transfer-confirm").checked = false;
    document.querySelector("#account-transfer-error").hidden = true;
    const impact = document.querySelector("#transfer-impact"); impact.replaceChildren();
    const title = document.createElement("strong"); title.textContent = account.name || "当前公司";
    const text = document.createElement("span"); text.textContent = `当前负责人：${userNameById(account.owner_id)} · 完整关系：联系人 ${overview?.totals?.contacts ?? overview?.contacts?.length ?? 0}、商机 ${overview?.totals?.opportunities ?? overview?.opportunities?.length ?? 0}、来源线索 ${overview?.totals?.conversion_sources ?? overview?.conversion_sources?.length ?? 0}、活动 ${overview?.totals?.activities ?? overview?.activities?.length ?? 0}。`;
    impact.append(title, text);
    ui.accountTransferDialog.showModal();
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    showToast(`无法读取账号目录：${error.message}`);
  }
}

async function submitAccountTransfer(event) {
  event.preventDefault();
  if (state.currentUser?.role !== "admin") return;
  const button = document.querySelector("#submit-account-transfer");
  const errorBox = document.querySelector("#account-transfer-error");
  const workspace = workspaceSnapshot();
  button.disabled = true; button.textContent = "转移中…"; errorBox.hidden = true;
  try {
    const accountId = conversionValue("transfer-account-id");
    const newOwnerId = conversionValue("transfer-owner-id");
    if (!newOwnerId) throw new Error("请选择新负责人");
    if (!document.querySelector("#transfer-confirm").checked) throw new Error("请勾选二次确认");
    const outcome = await api(`/api/accounts/${encodeURIComponent(accountId)}/transfer`, { method: "POST", body: JSON.stringify({ new_owner_id: newOwnerId }) });
    assertWorkspaceCurrent(workspace);
    ui.accountTransferDialog.close();
    if (ui.resourceDetailDialog.open) ui.resourceDetailDialog.close();
    await Promise.all(resourcePageNames.map((resource) => loadResource(resource, { quiet: true })));
    assertWorkspaceCurrent(workspace);
    renderBusinessCounts();
    showToast(`负责人已转移：联系人 ${outcome.contacts_updated}、商机 ${outcome.opportunities_updated}、线索 ${outcome.leads_updated}、活动 ${outcome.activities_updated}`);
    navigateToResourceRecord("accounts", accountId);
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    errorBox.textContent = error.message; errorBox.hidden = false;
  } finally {
    if (workspaceIsCurrent(workspace)) { button.disabled = false; button.textContent = "确认转移"; }
  }
}

function renderAccountOverview(overview) {
  const contacts = overview?.contacts || [];
  const opportunities = overview?.opportunities || [];
  const activities = overview?.activities || [];
  const conversions = overview?.conversion_sources || [];
  const totals = overview?.totals || {};
  const truncated = overview?.truncated || {};
  const sourceLeads = conversions.map((conversion) => ({ ...(conversion.snapshot?.lead || {}), id: conversion.lead_id, _converted_at: conversion.converted_at }));
  rememberOverviewRecords("contacts", contacts);
  rememberOverviewRecords("opportunities", opportunities);
  rememberOverviewRecords("activities", activities);
  rememberOverviewRecords("leads", sourceLeads);

  const wrapper = document.createElement("section"); wrapper.className = "account-overview span-2";
  const heading = document.createElement("div"); heading.className = "account-overview-heading";
  const copy = document.createElement("div");
  const overline = document.createElement("p"); overline.className = "overline"; overline.textContent = "360° VIEW";
  const title = document.createElement("h3"); title.textContent = "客户公司全景";
  const hint = document.createElement("p"); hint.textContent = "联系人、商机、活动与来源线索均为真实关联记录，点击即可进入详情。";
  copy.append(overline, title, hint); heading.append(copy); wrapper.append(heading);
  const counts = document.createElement("div"); counts.className = "overview-counts";
  for (const [label, records, key] of [["联系人", contacts, "contacts"], ["商机", opportunities, "opportunities"], ["活动", activities, "activities"], ["来源线索", sourceLeads, "conversion_sources"]]) {
    const item = document.createElement("div"); const strong = document.createElement("strong"); strong.textContent = String(totals[key] ?? records.length); const span = document.createElement("span"); span.textContent = label; item.append(strong, span); counts.append(item);
  }
  wrapper.append(counts);
  const groups = document.createElement("div"); groups.className = "overview-groups";
  groups.append(
    createOverviewGroup("联系人", "contacts", contacts, (record) => [record.title, record.mobile || record.phone || record.email].filter(Boolean).join(" · "), { total: totals.contacts ?? contacts.length, truncated: truncated.contacts, filters: { account_id: overview.account.id } }),
    createOverviewGroup("商机", "opportunities", opportunities, (record) => [enumNames[record.stage] || record.stage, formatMoney(record.amount, record.currency)].filter(Boolean).join(" · "), { total: totals.opportunities ?? opportunities.length, truncated: truncated.opportunities, filters: { account_id: overview.account.id } }),
    createOverviewGroup("跟进活动", "activities", activities, (record) => [enumNames[record.type] || record.type, formatDateTime(record.start_at), enumNames[record.status] || record.status].filter(Boolean).join(" · "), { total: totals.activities ?? activities.length, truncated: truncated.activities, filters: { account_id: overview.account.id } }),
    createOverviewGroup("来源线索", "leads", sourceLeads, (record) => [record.company_name, `转换于 ${formatDateTime(record._converted_at)}`].filter(Boolean).join(" · "), { total: totals.conversion_sources ?? sourceLeads.length, truncated: truncated.conversion_sources }),
  );
  wrapper.append(groups); return wrapper;
}

function detailValueNode(key, value, record) {
  if (key === "owner_id" || key === "assigned_user_id" || key === "converted_by") return document.createTextNode(userNameById(value));
  const resource = relationResources[key];
  if (resource && value) return createResourceLink({ resource, id: value, label: resourceRecordName(resource, value) });
  return document.createTextNode(recordDetailValue(key, value, record));
}

async function openResourceDetail(recordId) {
  const resource = state.activeResource;
  const definition = resourceDefinitions[resource];
  if (!definition) return;
  const workspace = workspaceSnapshot();
  try {
    const overview = resource === "accounts" ? await api(`/api/accounts/${encodeURIComponent(recordId)}/overview`) : null;
    const result = overview || await api(`/api/${resource}/${encodeURIComponent(recordId)}`);
    assertWorkspaceCurrent(workspace);
    const record = overview?.account || result?.item || (result?.data && !Array.isArray(result.data) ? result.data : null) || result;
    document.querySelector("#resource-detail-overline").textContent = resource.toUpperCase();
    document.querySelector("#resource-detail-title").textContent = definition.titleOf(record);
    ui.resourceDetailDialog.classList.toggle("account-overview-dialog", resource === "accounts");
    const content = document.querySelector("#resource-detail-content"); content.replaceChildren();
    const labelMap = Object.fromEntries(definition.fields.map((field) => [field.key, field.label]));
    for (const [key, value] of Object.entries(record)) {
      if (key === "id") continue;
      content.append(detailRow(labelMap[key] || key.replaceAll("_", " "), detailValueNode(key, value, record)));
    }
    if (overview) content.append(renderAccountOverview(overview));
    const actions = document.querySelector("#resource-detail-actions"); actions.replaceChildren();
    const close = document.createElement("button"); close.type = "button"; close.className = "button-secondary"; close.textContent = "关闭"; close.addEventListener("click", () => ui.resourceDetailDialog.close()); actions.append(close);
    if (resource === "accounts" && state.currentUser?.role === "admin") {
      const transfer = document.createElement("button"); transfer.type = "button"; transfer.className = "button-secondary transfer-owner-button"; transfer.textContent = "转移负责人"; transfer.addEventListener("click", () => openAccountTransfer(record, overview)); actions.append(transfer);
    }
    if (resource === "leads" && record.status !== "converted" && canUpdateResource()) {
      const convert = document.createElement("button"); convert.type = "button"; convert.className = "button-primary"; convert.textContent = "转为客户"; convert.addEventListener("click", () => { ui.resourceDetailDialog.close(); openLeadConversion(record); }); actions.append(convert);
    }
    if (canUpdateResource()) { const edit = document.createElement("button"); edit.type = "button"; edit.className = "button-primary"; edit.textContent = "编辑"; edit.addEventListener("click", () => { ui.resourceDetailDialog.close(); openResourceForm(record); }); actions.append(edit); }
    ui.resourceDetailDialog.showModal();
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    showToast(`详情读取失败：${error.message}`);
  }
}

async function deleteResource(record) {
  const resource = state.activeResource;
  const definition = resourceDefinitions[resource];
  if (!definition || !canDeleteResource()) return;
  const title = definition.titleOf(record);
  if (!window.confirm(`确定删除${definition.singular}“${title}”吗？此操作无法撤销。`)) return;
  const workspace = workspaceSnapshot();
  try {
    await api(`/api/${resource}/${encodeURIComponent(record.id)}`, { method: "DELETE" });
    assertWorkspaceCurrent(workspace);
    await loadResource(resource, { preservePage: true });
    assertWorkspaceCurrent(workspace);
    showToast(`${definition.singular}已删除`);
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    showToast(`删除失败：${error.message}`);
  }
}

function customerStatusCounts(customers) {
  return customers.reduce((counts, customer) => {
    counts[customer.status] = (counts[customer.status] || 0) + 1;
    return counts;
  }, {});
}

function renderDashboard(data) {
  const apiCounts = data?.entity_counts || {};
  const counts = Object.fromEntries(resourcePageNames.map((resource) => [resource, Number(apiCounts[resource] ?? state.resourceTotals[resource] ?? state.resourceRecords[resource]?.length ?? 0)]));
  const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
  document.querySelector("#metric-total").textContent = String(total);
  document.querySelector("#metric-contacted").textContent = String(counts.leads || 0);
  document.querySelector("#metric-converted").textContent = String(counts.opportunities || 0);
  document.querySelector("#metric-pending").textContent = String(state.pendingActions.length);
  const overview = document.querySelector("#status-overview");
  overview.replaceChildren();
  const labels = { leads: "线索", accounts: "客户公司", contacts: "联系人", opportunities: "商机", activities: "跟进活动" };
  for (const resource of resourcePageNames) {
    const count = counts[resource];
    const item = document.createElement("div"); item.className = "status-row";
    const label = document.createElement("span"); label.className = "status-label";
    const dot = document.createElement("i"); dot.className = `status-color resource-${resource}`; label.append(dot, document.createTextNode(labels[resource]));
    const track = document.createElement("span"); track.className = "status-track";
    const bar = document.createElement("i"); bar.className = `status-bar resource-${resource}`; bar.style.width = `${total ? Math.max(3, (count / total) * 100) : 0}%`; track.append(bar);
    const value = document.createElement("strong"); value.textContent = String(count);
    item.append(label, track, value); overview.append(item);
  }
  const recent = resourcePageNames.flatMap((resource) => (state.resourceRecords[resource] || []).map((record) => ({ ...record, _resource: resource })))
    .sort((a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0)).slice(0, 5);
  const list = document.querySelector("#recent-customers"); list.replaceChildren();
  if (!recent.length) { const empty = document.createElement("p"); empty.className = "panel-empty"; empty.textContent = "还没有业务数据"; list.append(empty); return; }
  for (const record of recent) {
    const definition = resourceDefinitions[record._resource];
    const button = document.createElement("button"); button.type = "button"; button.className = "recent-item";
    const avatar = document.createElement("span"); avatar.className = "customer-avatar"; avatar.textContent = definition.title.slice(0, 1);
    const copy = document.createElement("span"); const name = document.createElement("strong"); name.textContent = definition.titleOf(record); const detail = document.createElement("small"); detail.textContent = `${definition.title} · ${formatDateTime(record.updated_at || record.created_at)}`; copy.append(name, detail);
    const badge = document.createElement("span"); badge.className = "record-badge"; badge.textContent = definition.title;
    button.append(avatar, copy, badge); button.addEventListener("click", () => { switchPage(record._resource); window.setTimeout(() => openResourceDetail(record.id), 0); }); list.append(button);
  }
}

function renderDashboardFromCustomers() {
  renderDashboard(state.dashboard || { entity_counts: state.resourceTotals });
}

async function loadDashboard() {
  const workspace = workspaceSnapshot();
  try {
    const dashboard = await api("/api/dashboard");
    assertWorkspaceCurrent(workspace);
    state.dashboard = dashboard;
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    if (![404, 405].includes(error.status) && error.status !== 401) showToast(`工作台读取失败，已使用客户数据：${error.message}`);
    state.dashboard = null;
  }
  if (!workspaceIsCurrent(workspace)) return;
  renderBusinessCounts();
  renderDashboardFromCustomers();
}

function customerFormPayload() {
  const textOrNull = (selector) => document.querySelector(selector).value.trim() || null;
  return {
    name: document.querySelector("#customer-name").value.trim(),
    company: textOrNull("#customer-company"),
    title: textOrNull("#customer-title"),
    email: textOrNull("#customer-email"),
    phone: textOrNull("#customer-phone"),
    status: document.querySelector("#customer-status").value,
    source: textOrNull("#customer-source"),
    notes: textOrNull("#customer-notes"),
  };
}

function openCustomerForm(customer = null) {
  if (customer ? !canUpdateCustomer() : !canCreateCustomer()) return;
  ui.customerForm.reset();
  const statusSelect = document.querySelector("#customer-status");
  statusSelect.querySelectorAll("option[data-legacy]").forEach((option) => option.remove());
  if (customer && !standardStatuses.includes(customer.status)) {
    const legacyOption = document.createElement("option");
    legacyOption.value = customer.status;
    legacyOption.textContent = `历史状态：${statusNames[customer.status] || customer.status}`;
    legacyOption.dataset.legacy = "true";
    statusSelect.append(legacyOption);
  }
  document.querySelector("#customer-id").value = customer?.id || "";
  document.querySelector("#customer-dialog-title").textContent = customer ? "编辑客户" : "新增客户";
  document.querySelector("#save-customer").textContent = customer ? "保存修改" : "创建客户";
  const fields = ["name", "company", "title", "email", "phone", "status", "source", "notes"];
  fields.forEach((field) => { const input = document.querySelector(`#customer-${field}`); if (input) input.value = customer?.[field] ?? (field === "status" ? "new" : ""); });
  if (!["new", "contacted", "qualified", "converted", "lost"].includes(document.querySelector("#customer-status").value)) {
    document.querySelector("#customer-status").value = "new";
  }
  document.querySelector("#customer-form-error").hidden = true;
  ui.customerDialog.showModal();
  document.querySelector("#customer-name").focus();
}

async function saveCustomer(event) {
  event.preventDefault();
  const id = document.querySelector("#customer-id").value;
  const payload = customerFormPayload();
  if (id && !standardStatuses.includes(payload.status)) delete payload.status;
  const errorBox = document.querySelector("#customer-form-error");
  if (!id && !payload.email && !payload.phone) { errorBox.textContent = "邮箱和电话至少填写一个。"; errorBox.hidden = false; return; }
  const button = document.querySelector("#save-customer"); button.disabled = true; errorBox.hidden = true;
  const workspace = workspaceSnapshot();
  try {
    await api(id ? `/api/customers/${id}` : "/api/customers", { method: id ? "PATCH" : "POST", body: JSON.stringify(payload) });
    assertWorkspaceCurrent(workspace);
    ui.customerDialog.close();
    await Promise.all([loadCustomers(), loadDashboard()]);
    assertWorkspaceCurrent(workspace);
    showToast(id ? "客户资料已更新" : "客户已创建");
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    errorBox.textContent = error.message; errorBox.hidden = false;
  } finally { if (workspaceIsCurrent(workspace)) button.disabled = false; }
}

function detailRow(label, value) {
  const row = document.createElement("div"); row.className = "detail-row";
  const term = document.createElement("span"); term.textContent = label;
  const detail = document.createElement("strong");
  if (value instanceof Node) detail.append(value);
  else detail.textContent = value || "—";
  row.append(term, detail); return row;
}

async function openCustomerDetail(customerId) {
  const workspace = workspaceSnapshot();
  try {
    const customer = await api(`/api/customers/${customerId}`);
    assertWorkspaceCurrent(workspace);
    document.querySelector("#detail-customer-name").textContent = customer.name;
    const content = document.querySelector("#customer-detail-content"); content.replaceChildren();
    const status = document.createElement("div"); status.className = "detail-status";
    const statusBadge = document.createElement("span"); statusBadge.className = `customer-status ${customer.status}`; statusBadge.textContent = statusNames[customer.status] || customer.status;
    const idText = document.createElement("small"); idText.textContent = `客户 ID：${customer.id}`;
    status.append(statusBadge, idText); content.append(status);
    content.append(detailRow("公司", customer.company), detailRow("职位", customer.title), detailRow("邮箱", customer.email), detailRow("电话", customer.phone), detailRow("来源", customer.source), detailRow("负责人", customer.owner_name), detailRow("创建时间", formatDateTime(customer.created_at)), detailRow("最近更新", formatDateTime(customer.updated_at)), detailRow("备注", customer.notes));
    const actions = document.querySelector("#customer-detail-actions"); actions.replaceChildren();
    const close = document.createElement("button"); close.type = "button"; close.className = "button-secondary"; close.textContent = "关闭"; close.addEventListener("click", () => ui.customerDetailDialog.close()); actions.append(close);
    if (canUpdateCustomer()) { const edit = document.createElement("button"); edit.type = "button"; edit.className = "button-primary"; edit.textContent = "编辑客户"; edit.addEventListener("click", () => { ui.customerDetailDialog.close(); openCustomerForm(customer); }); actions.append(edit); }
    ui.customerDetailDialog.showModal();
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    showToast(`读取客户详情失败：${error.message}`);
  }
}

async function deleteCustomer(customer) {
  if (!canDeleteCustomer()) return;
  if (!window.confirm(`第一次确认：确定要删除客户“${customer.name}”吗？`)) return;
  if (!window.confirm(`第二次确认：此操作无法撤销。再次确认永久删除“${customer.name}”？`)) return;
  const workspace = workspaceSnapshot();
  try {
    await api(`/api/customers/${customer.id}`, { method: "DELETE" });
    assertWorkspaceCurrent(workspace);
    await Promise.all([loadCustomers(), loadDashboard()]);
    assertWorkspaceCurrent(workspace);
    showToast("客户已删除");
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    showToast(`删除失败：${error.message}`);
  }
}

async function loadUsers() {
  const workspace = workspaceSnapshot();
  ui.userTableBody.replaceChildren(); ui.userTableEmpty.hidden = false; ui.userTableEmpty.textContent = "正在读取账号…";
  if (state.currentUser?.role !== "admin") {
    state.users = [state.currentUser];
    renderUsers();
    return;
  }
  try {
    const users = normalizeResourceList(await api("/api/users")).items;
    assertWorkspaceCurrent(workspace);
    state.users = users;
    renderUsers();
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    ui.userTableEmpty.textContent = `账号读取失败：${error.message}`;
  }
}

async function loadUserDirectory() {
  const workspace = workspaceSnapshot();
  if (state.currentUser?.role !== "admin") {
    state.users = state.currentUser ? [state.currentUser] : [];
    return;
  }
  try {
    const users = normalizeResourceList(await api("/api/users")).items;
    assertWorkspaceCurrent(workspace);
    state.users = users;
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    state.users = state.currentUser ? [state.currentUser] : [];
  }
  if (resourcePageNames.includes(state.currentPage)) renderResourceTable();
}

function renderUsers() {
  ui.userTableBody.replaceChildren(); ui.userTableEmpty.hidden = state.users.length > 0; ui.userTableEmpty.textContent = "暂无账号";
  for (const user of state.users) {
    const row = document.createElement("tr");
    const member = document.createElement("td");
    const memberWrap = document.createElement("div"); memberWrap.className = "table-customer";
    const memberAvatar = document.createElement("span"); memberAvatar.className = "avatar"; memberAvatar.textContent = String(user.display_name || user.username).slice(0, 1);
    const memberCopy = document.createElement("span"); const memberName = document.createElement("strong"); memberName.textContent = user.display_name; const memberHint = document.createElement("small"); memberHint.textContent = user.id === state.currentUser.id ? "当前账号" : "公司成员"; memberCopy.append(memberName, memberHint); memberWrap.append(memberAvatar, memberCopy); member.append(memberWrap);
    const account = document.createElement("td"); account.innerHTML = `<div class="table-lines"><span></span><small></small></div>`; account.querySelector("span").textContent = `@${user.username}`; account.querySelector("small").textContent = user.email;
    const roleCell = document.createElement("td");
    if (state.currentUser?.role === "admin") {
      const select = document.createElement("select"); select.className = "role-select"; ["admin", "manager", "sales", "viewer"].forEach((role) => { const option = document.createElement("option"); option.value = role; option.textContent = roleNames[role]; option.selected = role === user.role; select.append(option); }); select.addEventListener("change", () => updateUserRole(user, select)); roleCell.append(select);
    } else {
      const badge = document.createElement("span"); badge.className = "record-badge"; badge.textContent = roleNames[user.role] || user.role; roleCell.append(badge);
    }
    const active = document.createElement("td"); const badge = document.createElement("span"); badge.className = user.is_active ? "active-badge" : "inactive-badge"; badge.textContent = user.is_active ? "正常" : "停用"; active.append(badge);
    const login = document.createElement("td"); login.textContent = formatDateTime(user.last_login_at);
    row.append(member, account, roleCell, active, login); ui.userTableBody.append(row);
  }
}

async function updateUserRole(user, select) {
  const previous = user.role; select.disabled = true;
  const workspace = workspaceSnapshot();
  try {
    const updated = await api(`/api/users/${user.id}/role`, { method: "PATCH", body: JSON.stringify({ role: select.value }) });
    assertWorkspaceCurrent(workspace);
    state.users = state.users.map((item) => item.id === updated.id ? updated : item);
    if (updated.id === state.currentUser.id) { state.currentUser = updated; updateAccountProfile(); if (updated.role !== "admin") switchPage("dashboard"); }
    showToast(`${updated.display_name} 已设为${roleNames[updated.role]}`);
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    select.value = previous;
    showToast(`角色修改失败：${error.message}`);
  } finally { if (workspaceIsCurrent(workspace)) select.disabled = false; }
}

function pendingActionList(value) {
  const list = Array.isArray(value)
    ? value
    : value?.pending_actions || value?.actions || value?.items || [];
  return list.filter((action) => action && (!action.status || ["pending", "awaiting_approval"].includes(action.status)));
}

function actionTitle(actionType = "") {
  const normalized = String(actionType).toLowerCase();
  const names = {
    insert: "新增客户",
    create: "新增客户",
    create_customer: "新增客户",
    insert_customer: "新增客户",
    insert_lead: "新增线索",
    insert_account: "新增客户公司",
    insert_contact: "新增联系人",
    insert_opportunity: "新增商机",
    insert_activity: "新增跟进活动",
    update: "更新客户",
    update_customer: "更新客户",
    update_lead: "更新线索",
    update_account: "更新客户公司",
    update_contact: "更新联系人",
    update_opportunity: "更新商机",
    update_activity: "更新跟进活动",
    convert_lead: "线索转为客户",
    delete: "删除客户",
    delete_customer: "删除客户",
  };
  return names[normalized] || `执行 ${actionType || "CRM 数据变更"}`;
}

function displayPendingValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") {
    try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); }
  }
  return String(value);
}

function conversionPreviewSection(number, title, mode, rows) {
  const section = document.createElement("section"); section.className = "pending-conversion-section";
  const heading = document.createElement("div"); heading.className = "pending-conversion-heading";
  const index = document.createElement("span"); index.textContent = String(number);
  const copy = document.createElement("div"); const name = document.createElement("strong"); name.textContent = title; const badge = document.createElement("small"); badge.textContent = mode; copy.append(name, badge); heading.append(index, copy); section.append(heading);
  const list = document.createElement("dl");
  for (const [label, value] of rows.filter(([, value]) => value !== undefined && value !== null && value !== "")) {
    const term = document.createElement("dt"); term.textContent = label;
    const detail = document.createElement("dd"); detail.textContent = displayPendingValue(value);
    list.append(term, detail);
  }
  section.append(list); return section;
}

function appendPendingConversionPreview(card, payload) {
  const request = payload.request || payload.fields || {};
  const lead = payload.current || payload.lead || {};
  const wrapper = document.createElement("div"); wrapper.className = "pending-conversion";
  const source = document.createElement("div"); source.className = "pending-conversion-source";
  const sourceCopy = document.createElement("div");
  const label = document.createElement("span"); label.textContent = "来源线索";
  const name = document.createElement("strong"); name.textContent = resourceDefinitions.leads.titleOf(lead) || resourceRecordName("leads", payload.lead_id);
  const meta = document.createElement("small"); meta.textContent = [lead.company_name, lead.email || lead.phone].filter(Boolean).join(" · ") || payload.lead_id || "线索详情将在执行时校验";
  sourceCopy.append(label, name, meta);
  const arrow = document.createElement("span"); arrow.className = "pending-conversion-arrow"; arrow.textContent = "↓";
  source.append(sourceCopy, arrow); wrapper.append(source);

  const accountRows = request.account_id
    ? [["客户公司", resourceRecordName("accounts", request.account_id)], ["记录 ID", request.account_id]]
    : [["公司名称", request.account?.name || lead.company_name], ["行业", request.account?.industry], ["电话", request.account?.phone || lead.phone], ["邮箱", request.account?.email || lead.email], ["来源", request.account?.source || lead.source]];
  const contactRows = request.contact_id
    ? [["联系人", resourceRecordName("contacts", request.contact_id)], ["记录 ID", request.contact_id]]
    : [["姓名", [request.contact?.first_name ?? lead.first_name, request.contact?.last_name ?? lead.last_name].filter(Boolean).join(" ")], ["职位", request.contact?.title ?? lead.job_title], ["邮箱", request.contact?.email ?? lead.email], ["电话", request.contact?.mobile || request.contact?.phone || lead.phone]];
  const opportunityRows = request.opportunity
    ? [["商机名称", request.opportunity.name], ["金额", request.opportunity.amount == null ? "—" : formatMoney(request.opportunity.amount, request.opportunity.currency)], ["阶段", enumNames[request.opportunity.stage] || request.opportunity.stage], ["赢单概率", request.opportunity.probability == null ? "—" : `${request.opportunity.probability}%`], ["预计成交", request.opportunity.expected_close_date]]
    : [["处理方式", "本次不创建商机"]];
  wrapper.append(
    conversionPreviewSection(1, "客户公司", request.account_id ? "关联现有" : "创建新公司", accountRows),
    conversionPreviewSection(2, "联系人", request.contact_id ? "关联现有" : "创建并关联", contactRows),
    conversionPreviewSection(3, "商机", request.opportunity ? "创建并关联" : "可选项", opportunityRows),
  );
  const guarantee = document.createElement("p"); guarantee.className = "pending-conversion-guarantee"; guarantee.textContent = "确认后将一次性完成公司、联系人、可选商机与来源线索的关联；任一步失败都不会写入部分数据。"; wrapper.append(guarantee);
  card.append(wrapper);
}

function createPendingActionCard(action) {
  const fieldNames = {
    entity_type: "数据类型",
    entity_id: "记录 ID",
    name: "名称",
    first_name: "名",
    last_name: "姓",
    company: "公司",
    company_name: "公司",
    industry: "行业",
    website: "网站",
    address: "地址",
    city: "城市",
    state: "省/州",
    country: "国家",
    employee_count: "员工数",
    annual_revenue: "年营收",
    account_id: "客户公司 ID",
    lead_id: "线索 ID",
    contact_id: "联系人 ID",
    opportunity_id: "商机 ID",
    title: "职位",
    job_title: "职位",
    department: "部门",
    email: "邮箱",
    phone: "电话",
    mobile: "手机",
    wechat: "微信",
    status: "状态",
    stage: "阶段",
    amount: "金额",
    currency: "币种",
    probability: "概率",
    expected_close_date: "预计成交日期",
    type: "活动类型",
    subject: "主题",
    priority: "优先级",
    start_at: "开始时间",
    end_at: "结束时间",
    source: "来源",
    description: "描述",
    owner_id: "负责人 ID",
    assigned_user_id: "指派人 ID",
    notes: "备注",
    customer_id: "客户 ID",
    id: "客户 ID",
  };
  const card = document.createElement("article");
  card.className = "pending-card";
  card.dataset.actionId = action.id;

  const header = document.createElement("div");
  header.className = "pending-card-header";
  const title = document.createElement("div");
  title.className = "pending-card-title";
  title.innerHTML = icons.shield;
  const titleText = document.createElement("span");
  titleText.textContent = actionTitle(action.action_type || action.type);
  title.append(titleText);
  const time = document.createElement("span");
  time.className = "pending-card-time";
  time.textContent = formatTime(action.created_at);
  header.append(title, time);
  card.append(header);

  let payload = action.payload || {};
  if (typeof payload === "string") {
    try { payload = JSON.parse(payload); } catch (_) { payload = { details: payload }; }
  }
  const isLeadConversion = String(action.action_type || action.type || "").toLowerCase() === "convert_lead";
  if (isLeadConversion) appendPendingConversionPreview(card, payload);
  let candidate;
  if (payload.entity_type && payload.fields && typeof payload.fields === "object") {
    candidate = {
      entity_type: payload.entity_type,
      ...(payload.entity_id ? { entity_id: payload.entity_id } : {}),
      ...payload.fields,
    };
  } else if (String(action.action_type || "").startsWith("update_") && payload.fields && typeof payload.fields === "object") {
    const identifiers = Object.fromEntries(Object.entries(payload).filter(([key]) => key !== "fields"));
    candidate = { ...identifiers, ...payload.fields };
  } else {
    candidate = payload.customer && typeof payload.customer === "object"
      ? { ...payload, ...payload.customer }
      : { ...payload };
    delete candidate.customer;
  }
  const isEntityUpdate = String(action.action_type || "").startsWith("update_");
  const entries = isLeadConversion ? [] : Object.entries(candidate)
    .filter(([, value]) => value !== undefined && (isEntityUpdate || (value !== null && value !== "")))
    .slice(0, 12);
  if (entries.length) {
    const fields = document.createElement("dl");
    fields.className = "pending-fields";
    for (const [key, value] of entries) {
      const label = document.createElement("dt");
      label.textContent = fieldNames[key] || key.replaceAll("_", " ");
      const detail = document.createElement("dd");
      detail.textContent = key === "entity_type"
        ? ({ lead: "线索", account: "客户公司", contact: "联系人", opportunity: "商机", activity: "跟进活动" }[value] || displayPendingValue(value))
        : displayPendingValue(value);
      fields.append(label, detail);
    }
    card.append(fields);
  }

  if (canReviewPendingActions()) {
    const actions = document.createElement("div");
    actions.className = "pending-card-actions";
    const reject = document.createElement("button");
    reject.type = "button";
    reject.className = "pending-reject";
    reject.textContent = "拒绝";
    reject.disabled = state.pendingBusy.has(action.id);
    reject.addEventListener("click", () => resolvePendingAction(action, "reject"));
    const approve = document.createElement("button");
    approve.type = "button";
    approve.className = "pending-approve";
    approve.textContent = state.pendingBusy.has(action.id) ? "处理中…" : "确认执行";
    approve.disabled = state.pendingBusy.has(action.id);
    approve.addEventListener("click", () => resolvePendingAction(action, "approve"));
    actions.append(reject, approve);
    card.append(actions);
  } else {
    const readonly = document.createElement("p");
    readonly.className = "pending-readonly";
    readonly.textContent = "你的账号是只读角色，不能确认或拒绝数据变更。";
    card.append(readonly);
  }
  return card;
}

function renderPendingActions() {
  ui.pendingActions.replaceChildren();
  ui.pendingCount.textContent = String(state.pendingActions.length);
  document.querySelector("#assistant-pending-badge").textContent = String(state.pendingActions.length);
  document.querySelector("#metric-pending").textContent = String(state.pendingActions.length);
  document.querySelector("#pending-review-count").textContent = String(state.pendingActions.length);
  document.querySelector("#pending-review-banner").hidden = state.pendingActions.length === 0;
  document.querySelector("#pending-review-banner small").textContent = canReviewPendingActions()
    ? "查看详情并决定是否写入数据库 →"
    : "查看只读详情 →";
  document.querySelector("#inspector-hint").textContent = canReviewPendingActions()
    ? "AI 提交的新增或更新会先停在这里，只有你确认后才写入数据库。"
    : "AI 提交的数据变更会显示在这里；只读账号不能确认或拒绝。";
  const navBadge = document.querySelector("#nav-pending-count");
  navBadge.textContent = String(state.pendingActions.length);
  navBadge.hidden = state.pendingActions.length === 0;
  if (!state.pendingActions.length) {
    const empty = document.createElement("p");
    empty.className = "pending-empty";
    empty.textContent = "没有等待确认的数据变更";
    ui.pendingActions.append(empty);
    return;
  }
  state.pendingActions.forEach((action) => ui.pendingActions.append(createPendingActionCard(action)));
}

async function loadPendingActions({ quiet = false } = {}) {
  const workspace = workspaceSnapshot();
  try {
    const result = await api("/api/pending-actions");
    assertWorkspaceCurrent(workspace);
    state.pendingActions = pendingActionList(result);
    renderPendingActions();
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    if (!quiet && error.status !== 401) showToast(`读取待确认操作失败：${error.message}`);
  }
}

async function resolvePendingAction(action, decision) {
  if (!canReviewPendingActions() || !action?.id || state.pendingBusy.has(action.id)) return;
  const workspace = workspaceSnapshot();
  state.pendingBusy.add(action.id);
  renderPendingActions();
  try {
    const result = await api(`/api/pending-actions/${encodeURIComponent(action.id)}/${decision}`, { method: "POST" });
    assertWorkspaceCurrent(workspace);
    if (decision === "approve" && result?.status !== "approved") {
      throw new Error(result?.error_message || result?.result?.error || "操作没有成功执行");
    }
    if (decision === "reject" && result?.status !== "rejected") {
      throw new Error("操作没有成功拒绝");
    }
    state.pendingActions = state.pendingActions.filter((item) => item.id !== action.id);
    renderPendingActions();
    await Promise.all([
      loadCustomers(),
      loadBusinessContext(),
      loadDashboard(),
      loadPendingActions({ quiet: true }),
    ]);
    assertWorkspaceCurrent(workspace);
    showToast(decision === "approve" ? "操作已确认并执行" : "操作已拒绝");
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    await loadPendingActions({ quiet: true });
    if (!workspaceIsCurrent(workspace)) return;
    if (error.status !== 401) showToast(`${decision === "approve" ? "确认" : "拒绝"}失败：${error.message}`);
  } finally {
    if (workspaceIsCurrent(workspace)) {
      state.pendingBusy.delete(action.id);
      renderPendingActions();
    }
  }
}

function resizeComposer() {
  ui.input.style.height = "auto";
  ui.input.style.height = `${Math.min(ui.input.scrollHeight, 160)}px`;
}

function setBusy(value) {
  state.busy = value;
  ui.send.disabled = value;
  ui.input.disabled = value;
}

async function sendMessage(event) {
  event.preventDefault();
  const content = ui.input.value.trim();
  if (!content || state.busy) return;
  const workspace = workspaceSnapshot();
  if (!state.activeId) await createConversation();
  if (!workspaceIsCurrent(workspace)) return;
  if (!state.activeId) return;

  if (ui.messages.querySelector(".empty-state")) ui.messages.replaceChildren();
  appendMessage(content, "user");
  const thinking = appendMessage("", "assistant", { id: "pending", thinking: true });
  ui.input.value = "";
  resizeComposer();
  setBusy(true);
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        message: content,
        thread_id: state.activeId,
      }),
    });
    assertWorkspaceCurrent(workspace);
    thinking.remove();
    appendMessage(result.answer, "assistant");
    const returnedActions = pendingActionList(result.pending_actions);
    if (returnedActions.length) {
      const byId = new Map(state.pendingActions.map((action) => [action.id, action]));
      returnedActions.forEach((action) => byId.set(action.id, action));
      state.pendingActions = [...byId.values()];
      renderPendingActions();
      showToast(`智能助手有 ${returnedActions.length} 项 CRM 数据变更等待你确认`);
      if (window.matchMedia("(max-width: 1180px)").matches) {
        ui.inspector.classList.add("open");
        updateScrim();
      }
    }
    await Promise.all([
      refreshConversations(),
      loadCustomers(),
      loadBusinessContext(),
      loadDashboard(),
      loadPendingActions({ quiet: returnedActions.length > 0 }),
    ]);
    assertWorkspaceCurrent(workspace);
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    thinking.remove();
    appendMessage(`请求失败：${error.message}`, "assistant", { error: true });
  } finally {
    if (workspaceIsCurrent(workspace)) {
      setBusy(false);
      ui.input.focus();
    }
  }
}

function updateScrim() {
  const mainSidebarIsDrawer = window.matchMedia("(max-width: 840px)").matches;
  const sidebarIsDrawer = window.matchMedia("(max-width: 680px)").matches;
  const inspectorIsDrawer = window.matchMedia("(max-width: 1180px)").matches;
  const mainSidebarOpen = mainSidebarIsDrawer && ui.mainSidebar.classList.contains("open");
  const mobileSidebarOpen = sidebarIsDrawer && ui.sidebar.classList.contains("open");
  const inspectorDrawerOpen = inspectorIsDrawer && ui.inspector.classList.contains("open");
  ui.sidebar.inert = sidebarIsDrawer && !mobileSidebarOpen;
  ui.inspector.inert = inspectorIsDrawer && !inspectorDrawerOpen;
  ui.sidebar.setAttribute("aria-hidden", String(sidebarIsDrawer && !mobileSidebarOpen));
  ui.inspector.setAttribute("aria-hidden", String(inspectorIsDrawer && !inspectorDrawerOpen));
  ui.mainSidebar.inert = mainSidebarIsDrawer && !mainSidebarOpen;
  ui.mainSidebar.setAttribute("aria-hidden", String(mainSidebarIsDrawer && !mainSidebarOpen));
  ui.scrim.hidden = !(mainSidebarOpen || mobileSidebarOpen || inspectorDrawerOpen);
}

function closeDrawers() {
  ui.mainSidebar.classList.remove("open");
  ui.sidebar.classList.remove("open");
  ui.inspector.classList.remove("open");
  updateScrim();
}

function openRenameDialog() {
  const conversation = activeConversation();
  if (!conversation) return;
  ui.renameInput.value = conversation.title;
  ui.renameDialog.showModal();
  ui.renameInput.select();
}

ui.loginTab.addEventListener("click", () => setAuthMode("login"));
ui.registerTab.addEventListener("click", () => setAuthMode("register"));
ui.loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuth(ui.loginForm, "/api/auth/login");
});
ui.registerForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuth(ui.registerForm, "/api/auth/register");
});
document.querySelector("#logout").addEventListener("click", logout);
document.querySelector("#new-thread").addEventListener("click", createConversation);
document.querySelector("#refresh").addEventListener("click", () => {
  Promise.all([loadBusinessContext(), loadPendingActions()]);
});
document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => switchPage(button.dataset.page)));
document.querySelectorAll("[data-go-page]").forEach((button) => button.addEventListener("click", () => switchPage(button.dataset.goPage)));
document.querySelector("#global-refresh").addEventListener("click", () => Promise.all([
  loadCustomers(),
  loadBusinessContext(),
  loadDashboard(),
  loadPendingActions(),
  state.currentPage === "users" ? loadUsers() : Promise.resolve(),
]));
document.querySelector("#global-add-customer").addEventListener("click", async () => {
  const resource = resourcePageNames.includes(state.currentPage) ? state.currentPage : "leads";
  if (state.currentPage !== resource) switchPage(resource);
  await openResourceForm();
});
document.querySelector("#add-customer").addEventListener("click", () => openCustomerForm());
document.querySelector("#open-main-sidebar").addEventListener("click", () => { ui.mainSidebar.classList.add("open"); updateScrim(); });
document.querySelector("#close-main-sidebar").addEventListener("click", closeDrawers);
document.querySelector("#rename-thread").addEventListener("click", openRenameDialog);
document.querySelector("#open-sidebar").addEventListener("click", () => { ui.sidebar.classList.add("open"); updateScrim(); });
document.querySelector("#close-sidebar").addEventListener("click", closeDrawers);
document.querySelector("#toggle-inspector").addEventListener("click", () => { ui.inspector.classList.add("open"); updateScrim(); });
document.querySelector("#close-inspector").addEventListener("click", closeDrawers);
ui.scrim.addEventListener("click", closeDrawers);
ui.customerSearch.addEventListener("input", renderCustomers);
ui.customerStatusFilter.addEventListener("change", renderCustomers);
ui.customerForm.addEventListener("submit", saveCustomer);
document.querySelector("#close-customer-dialog").addEventListener("click", () => ui.customerDialog.close());
document.querySelector("#cancel-customer").addEventListener("click", () => ui.customerDialog.close());
document.querySelector("#close-customer-detail").addEventListener("click", () => ui.customerDetailDialog.close());
document.querySelector("#add-resource").addEventListener("click", () => openResourceForm());
ui.resourceSearch.addEventListener("input", () => {
  clearTimeout(state.resourceSearchTimer);
  const resource = state.activeResource;
  const query = ui.resourceSearch.value.trim();
  const workspace = workspaceSnapshot();
  state.resourceSearchTimer = setTimeout(() => {
    if (!workspaceIsCurrent(workspace) || resource !== state.activeResource) return;
    loadResource(resource, { offset: 0, query });
  }, 320);
});
ui.resourceFilterContext.addEventListener("click", () => {
  const resource = state.activeResource; if (!resource) return;
  state.resourceFilters[resource] = {};
  updateResourceFilterContext(resource);
  loadResource(resource, { offset: 0, query: resourcePageState(resource).query, filters: {} });
});
ui.resourcePrevPage.addEventListener("click", () => {
  const resource = state.activeResource; if (!resource) return;
  const pagination = resourcePageState(resource);
  loadResource(resource, { offset: Math.max(0, pagination.offset - RESOURCE_PAGE_SIZE), query: pagination.query });
});
ui.resourceNextPage.addEventListener("click", () => {
  const resource = state.activeResource; if (!resource) return;
  const pagination = resourcePageState(resource);
  if (pagination.hasNext) loadResource(resource, { offset: pagination.offset + RESOURCE_PAGE_SIZE, query: pagination.query });
});
ui.resourceForm.addEventListener("submit", saveResource);
document.querySelector("#close-resource-dialog").addEventListener("click", () => ui.resourceDialog.close());
document.querySelector("#cancel-resource").addEventListener("click", () => ui.resourceDialog.close());
document.querySelector("#close-resource-detail").addEventListener("click", () => ui.resourceDetailDialog.close());
ui.leadConvertForm.addEventListener("submit", submitLeadConversion);
ui.leadConvertForm.addEventListener("change", (event) => {
  if (["convert-account-mode", "convert-contact-mode", "convert-create-opportunity"].includes(event.target.id)) setConversionModes();
  else if (event.target.id === "convert-account-id") { refreshConversionContacts(); document.querySelector("#convert-confirm").checked = false; renderConversionConfirmation(); }
});
ui.leadConvertForm.addEventListener("input", (event) => {
  if (event.target.id === "convert-confirm") return;
  document.querySelector("#convert-confirm").checked = false;
  renderConversionConfirmation();
});
const closeLeadConversion = () => { state.conversionLead = null; ui.leadConvertDialog.close(); };
document.querySelector("#close-lead-convert").addEventListener("click", closeLeadConversion);
document.querySelector("#cancel-lead-convert").addEventListener("click", closeLeadConversion);
ui.leadConvertDialog.addEventListener("close", () => { state.conversionLead = null; });
ui.accountTransferForm.addEventListener("submit", submitAccountTransfer);
const closeAccountTransfer = () => ui.accountTransferDialog.close();
document.querySelector("#close-account-transfer").addEventListener("click", closeAccountTransfer);
document.querySelector("#cancel-account-transfer").addEventListener("click", closeAccountTransfer);
document.querySelector("#transfer-owner-id").addEventListener("change", () => { document.querySelector("#transfer-confirm").checked = false; });
document.querySelector("#pending-review-banner").addEventListener("click", () => { ui.inspector.classList.add("open"); updateScrim(); });
ui.form.addEventListener("submit", sendMessage);
ui.input.addEventListener("input", resizeComposer);
ui.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    ui.form.requestSubmit();
  }
});
document.addEventListener("keydown", (event) => {
  if (state.currentUser && (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    switchPage("assistant");
    createConversation();
  }
});
document.querySelector("#cancel-rename").addEventListener("click", () => ui.renameDialog.close());
document.querySelector("#cancel-rename-x").addEventListener("click", () => ui.renameDialog.close());
document.querySelector("#rename-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = ui.renameInput.value.trim();
  if (!title || !state.activeId) return;
  const workspace = workspaceSnapshot();
  const conversationId = state.activeId;
  try {
    const updated = await api(`/api/conversations/${conversationId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
    assertWorkspaceCurrent(workspace);
    state.conversations = state.conversations.map((item) => item.id === updated.id ? updated : item);
    if (state.activeId === conversationId) setActiveConversation(updated.id);
    ui.renameDialog.close();
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    showToast(`重命名失败：${error.message}`);
  }
});
window.addEventListener("resize", updateScrim);

async function initialize() {
  renderThreads();
  renderEmptyState();
  renderCustomers();
  renderPendingActions();
  resizeComposer();
  updateScrim();
  const workspace = workspaceSnapshot();
  try {
    const user = await api("/api/auth/me", { skipAuthRedirect: true });
    assertWorkspaceCurrent(workspace);
    await enterWorkspace(user);
  } catch (error) {
    if (isStaleWorkspaceError(error) || !workspaceIsCurrent(workspace)) return;
    showAuthGate(error.status === 401 ? "" : `暂时无法连接服务器：${error.message}`);
  }
}

initialize();
