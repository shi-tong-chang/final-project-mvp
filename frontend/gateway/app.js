"use strict";

const API_ROOT = "/api/v1/gateway";
const WORKFLOW_API_ROOT = `${API_ROOT}/workflows`;
const CATALOG_TIMEOUT_MS = 10_000;
const WORKFLOW_REQUEST_TIMEOUT_MS = 60_000;
const WORKFLOW_POLL_INTERVAL_MS = 1_000;
const WORKFLOW_POLL_MAX_RETRIES = 5;
const WORKFLOW_POLL_MAX_DELAY_MS = 15_000;
const tabOrder = ["character", "scene", "storyboard"];
const horizontalTabsMedia = window.matchMedia("(max-width: 680px)");

const previewClasses = {
  cel: "style-cel",
  manga: "style-manga",
  "line-art": "style-line-art",
  "dark-fairytale": "style-dark-fairytale",
  oil: "style-oil",
  cinematic: "style-cinematic",
  realistic: "style-realistic",
  watercolor: "style-watercolor",
  gouache: "style-gouache",
  storybook: "style-storybook",
  noir: "style-noir",
  cyberpunk: "style-cyberpunk",
  "art-nouveau": "style-art-nouveau",
  fantasy: "style-fantasy",
  charcoal: "style-charcoal",
  "ukiyo-e": "style-ukiyo-e",
  "paper-cut": "style-paper-cut",
  pixel: "style-pixel",
  "retro-comic": "style-retro-comic",
  "stylized-3d": "style-stylized-3d",
};

const elements = {
  tabs: [...document.querySelectorAll('[role="tab"][data-tab]')],
  panels: [...document.querySelectorAll('[role="tabpanel"][data-panel]')],
  tablist: document.querySelector("#workspace-tabs"),
  catalogVersion: document.querySelector("#catalog-version"),
  styleGrid: document.querySelector("#character-style-grid"),
  characterForm: document.querySelector("#character-style-form"),
  characterPrompt: document.querySelector("#character-prompt"),
  characterHeroArt: document.querySelector("#character-hero-art"),
  selectedStyleName: document.querySelector("#selected-style-name"),
  selectedStyleDescription: document.querySelector(
    "#selected-style-description",
  ),
  selectedStylePrompt: document.querySelector("#selected-style-prompt"),
  selectedStyleTags: document.querySelector("#selected-style-tags"),
  confirmCharacterButtonLabel: document.querySelector(
    "#confirm-character-generation-label",
  ),
  characterGenerationStatus: document.querySelector(
    "#character-generation-status",
  ),
  sceneForm: document.querySelector("#scene-generation-form"),
  scenePrompt: document.querySelector("#scene-prompt"),
  sceneLight: document.querySelector("#scene-light"),
  sceneScale: document.querySelector("#scene-scale"),
  confirmSceneButtonLabel: document.querySelector(
    "#confirm-scene-generation-label",
  ),
  sceneGenerationStatus: document.querySelector(
    "#scene-generation-status",
  ),
  sceneHeroArt: document.querySelector("#scene-hero-art"),
  selectedSceneDirection: document.querySelector(
    "#selected-scene-direction",
  ),
  storyboardComposeForm: document.querySelector(
    "#storyboard-compose-form",
  ),
  storyboardSceneFile: document.querySelector("#storyboard-scene-file"),
  storyboardCharacterFile: document.querySelector(
    "#storyboard-character-file",
  ),
  storyboardPrompt: document.querySelector("#storyboard-prompt"),
  storyboardCandidateCount: document.querySelector(
    "#storyboard-candidate-count",
  ),
  generateStoryboardButton: document.querySelector(
    "#generate-storyboard-button",
  ),
  storyboardGenerationStatus: document.querySelector(
    "#storyboard-generation-status",
  ),
  storyboardCandidateSection: document.querySelector(
    "#storyboard-candidate-section",
  ),
  storyboardCandidateGrid: document.querySelector(
    "#storyboard-candidate-grid",
  ),
  storyboardSelectionStatus: document.querySelector(
    "#storyboard-selection-status",
  ),
  confirmStoryboardButton: document.querySelector(
    "#confirm-storyboard-button",
  ),
  storyboardUpscaleForm: document.querySelector(
    "#storyboard-upscale-form",
  ),
  upscaleRefinePrompt: document.querySelector(
    "#upscale-refine-prompt",
  ),
  upscaleStoryboardButton: document.querySelector(
    "#upscale-storyboard-button",
  ),
  storyboardUpscaleStatus: document.querySelector(
    "#storyboard-upscale-status",
  ),
  storyboardAssetState: document.querySelector(
    "#storyboard-asset-state",
  ),
  storyboardResultEmpty: document.querySelector(
    "#storyboard-result-empty",
  ),
  storyboardResultImage: document.querySelector(
    "#storyboard-result-image",
  ),
  storyboardResultTitle: document.querySelector(
    "#storyboard-result-title",
  ),
  storyboardResultDescription: document.querySelector(
    "#storyboard-result-description",
  ),
  storyboardDownloads: document.querySelector("#storyboard-downloads"),
  storyboardDownloadLink: document.querySelector(
    "#storyboard-download-link",
  ),
  upscale4kDownloadLink: document.querySelector(
    "#upscale4k-download-link",
  ),
  storyboardGenerationRetryButton: document.querySelector(
    "#storyboard-generation-retry-button",
  ),
  storyboardUpscaleRetryButton: document.querySelector(
    "#storyboard-upscale-retry-button",
  ),
};

const workflowState = {
  runId: "",
  runStatus: "",
  candidates: [],
  activeCandidateId: "",
  confirmedCandidateId: "",
  pollTimerId: 0,
  pollToken: 0,
  pollFailureCount: 0,
  pollMode: "",
  composeBusy: false,
  sourceLockReason: "",
  selectionBusy: false,
  selectionLocked: false,
  upscaleBusy: false,
  upscaleCompleted: false,
};

class WorkflowRequestError extends Error {
  constructor(
    message,
    {
      status = 0,
      code = "",
      retryable = false,
    } = {},
  ) {
    super(message);
    this.name = "WorkflowRequestError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

async function apiRequest(path) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(
    () => controller.abort(),
    CATALOG_TIMEOUT_MS,
  );
  try {
    const response = await fetch(`${API_ROOT}${path}`, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Catalog 載入失敗（HTTP ${response.status}）`);
    }
    return await response.json();
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function workflowRequest(
  path,
  {
    method = "GET",
    body,
    json,
    timeoutMs = WORKFLOW_REQUEST_TIMEOUT_MS,
  } = {},
) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(
    () => controller.abort(),
    timeoutMs,
  );
  const headers = { Accept: "application/json" };
  let requestBody = body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    requestBody = JSON.stringify(json);
  }

  try {
    const response = await fetch(`${WORKFLOW_API_ROOT}${path}`, {
      method,
      body: requestBody,
      credentials: "same-origin",
      headers,
      signal: controller.signal,
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    if (!response.ok) {
      const serverMessage = payload?.error?.message;
      const errorCode =
        typeof payload?.error?.code === "string"
          ? payload.error.code
          : "";
      throw new WorkflowRequestError(
        typeof serverMessage === "string" && serverMessage.trim()
          ? serverMessage.trim()
          : `工作流請求失敗（HTTP ${response.status}）`,
        {
          status: response.status,
          code: errorCode,
          retryable: response.status >= 500,
        },
      );
    }
    if (!payload || typeof payload !== "object") {
      throw new Error("工作流服務回傳了無法辨識的資料。");
    }
    return payload;
  } catch (error) {
    if (error instanceof WorkflowRequestError) {
      throw error;
    }
    if (error?.name === "AbortError") {
      throw new WorkflowRequestError(
        "工作流服務回應逾時，請確認本機服務仍在運作。",
        { retryable: true },
      );
    }
    if (error instanceof TypeError) {
      throw new WorkflowRequestError(
        "暫時無法連上本機工作流服務。",
        { retryable: true },
      );
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function catalogItemId(item, index) {
  const candidate = item?.item_id;
  return typeof candidate === "string" && candidate.trim()
    ? candidate.trim()
    : `style-${index + 1}`;
}

function catalogText(value, fallback) {
  return typeof value === "string" && value.trim()
    ? value.trim()
    : fallback;
}

function catalogTags(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((tag) => typeof tag === "string" && tag.trim())
    .map((tag) => tag.trim())
    .slice(0, 4);
}

function safePreviewUrl(value) {
  if (typeof value !== "string" || !value.startsWith("/")) {
    return "";
  }
  try {
    const parsed = new URL(value, window.location.origin);
    return parsed.origin === window.location.origin ? parsed.href : "";
  } catch (_error) {
    return "";
  }
}

function previewClassFor(item) {
  const previewKind = catalogText(item?.preview_kind, "");
  return previewClasses[previewKind] || "style-cel";
}

function createPreviewImage(url, className) {
  const image = document.createElement("img");
  image.className = className;
  image.src = url;
  image.alt = "";
  image.loading = "lazy";
  image.decoding = "async";
  image.addEventListener("error", () => image.remove(), { once: true });
  return image;
}

function appendCharacterLayers(container, scale = "card") {
  for (const className of [
    "art-sun",
    "art-portrait",
    "art-coat",
    "art-texture",
  ]) {
    const layer = document.createElement("span");
    layer.className = className;
    layer.dataset.scale = scale;
    container.append(layer);
  }
}

function appendHeroLayers(container) {
  for (const className of [
    "hero-horizon",
    "hero-halo",
    "hero-hair",
    "hero-face",
    "hero-body",
    "hero-texture",
  ]) {
    const layer = document.createElement("span");
    layer.className = className;
    container.append(layer);
  }
}

function createStyleCard(item, index) {
  const itemId = catalogItemId(item, index);
  const title = catalogText(item.title, `風格 ${index + 1}`);
  const description = catalogText(
    item.description,
    "正式風格說明待補。",
  );
  const promptFragment = catalogText(
    item.prompt_fragment,
    "使用所選視覺風格呈現。",
  );
  const previewClass = previewClassFor(item);
  const previewUrl = safePreviewUrl(item.preview_url);

  const label = document.createElement("label");
  label.className = "style-card";

  const input = document.createElement("input");
  input.type = "radio";
  input.name = "character-style";
  input.value = itemId;
  input.checked = index === 0;
  input.dataset.styleName = title;
  input.dataset.styleDescription = description;
  input.dataset.stylePrompt = promptFragment;
  input.dataset.previewClass = previewClass;
  input.dataset.previewUrl = previewUrl;
  input.dataset.tags = JSON.stringify(catalogTags(item.tags));

  const visual = document.createElement("span");
  visual.className = `style-visual ${previewClass}`;
  visual.setAttribute("aria-hidden", "true");
  if (previewUrl) {
    visual.classList.add("has-catalog-preview");
    visual.append(
      createPreviewImage(previewUrl, "catalog-preview-image"),
    );
  } else {
    appendCharacterLayers(visual);
  }

  const sequence = document.createElement("span");
  sequence.className = "style-sequence";
  sequence.textContent = String(index + 1).padStart(2, "0");

  const copy = document.createElement("span");
  copy.className = "style-copy";
  const strong = document.createElement("strong");
  strong.textContent = title;
  const small = document.createElement("small");
  small.textContent = description;
  copy.append(strong, small);

  label.append(input, visual, sequence, copy);
  return label;
}

function renderCharacterStyles(items) {
  if (!Array.isArray(items) || items.length === 0) {
    elements.styleGrid.dataset.state = "empty";
    elements.styleGrid.textContent = "目前沒有可用的角色風格。";
    return;
  }
  elements.styleGrid.dataset.state = "ready";
  elements.styleGrid.replaceChildren(
    ...items.slice(0, 40).map(createStyleCard),
  );
  updateCharacterShowcase();
}

function selectedInput(name) {
  return document.querySelector(`input[name="${name}"]:checked`);
}

function selectedStyleTags(selected) {
  try {
    const value = JSON.parse(selected.dataset.tags || "[]");
    return Array.isArray(value) ? value : [];
  } catch (_error) {
    return [];
  }
}

function updateCharacterShowcase() {
  const selected = selectedInput("character-style");
  if (!selected) {
    return;
  }
  const title = selected.dataset.styleName || selected.value;
  const description =
    selected.dataset.styleDescription || "正式風格說明待補。";
  const promptFragment =
    selected.dataset.stylePrompt || "使用所選視覺風格呈現。";
  const previewClass = selected.dataset.previewClass || "style-cel";
  const previewUrl = safePreviewUrl(selected.dataset.previewUrl);

  elements.selectedStyleName.textContent = title;
  elements.selectedStyleDescription.textContent = description;
  elements.selectedStylePrompt.textContent = promptFragment;
  elements.selectedStyleTags.replaceChildren(
    ...selectedStyleTags(selected).map((tag) => {
      const badge = document.createElement("span");
      badge.textContent = tag;
      return badge;
    }),
  );

  elements.characterHeroArt.className = `hero-art ${previewClass}`;
  elements.characterHeroArt.replaceChildren();
  if (previewUrl) {
    elements.characterHeroArt.classList.add("has-catalog-preview");
    elements.characterHeroArt.append(
      createPreviewImage(previewUrl, "catalog-preview-image"),
    );
  } else {
    appendHeroLayers(elements.characterHeroArt);
  }
  clearCharacterGenerationStatus();
}

function showGenerationConfirmation(status, label, message) {
  status.textContent = message;
  status.dataset.kind = "warning";
  label.textContent = "設定已確認";
}

function clearGenerationConfirmation(status, label) {
  status.textContent = "";
  status.dataset.kind = "";
  label.textContent = "確認生成";
}

function clearCharacterGenerationStatus() {
  clearGenerationConfirmation(
    elements.characterGenerationStatus,
    elements.confirmCharacterButtonLabel,
  );
}

function clearSceneGenerationStatus() {
  clearGenerationConfirmation(
    elements.sceneGenerationStatus,
    elements.confirmSceneButtonLabel,
  );
}

function confirmCharacterGeneration() {
  if (!elements.characterForm.reportValidity()) {
    return;
  }
  showGenerationConfirmation(
    elements.characterGenerationStatus,
    elements.confirmCharacterButtonLabel,
    "角色生成 Agent 尚未接入；目前只確認本頁設定，不會送出或建立圖片。",
  );
}

function confirmSceneGeneration() {
  if (!elements.sceneForm.reportValidity()) {
    return;
  }
  showGenerationConfirmation(
    elements.sceneGenerationStatus,
    elements.confirmSceneButtonLabel,
    "場景生成 Agent 尚未接入；目前只確認本頁設定，不會送出或建立圖片。",
  );
}

function applySceneShowcaseCatalog(items) {
  if (!Array.isArray(items) || items.length === 0) {
    return;
  }
  const slots = [
    ...document.querySelectorAll(".scene-showcase .empty-shelf span"),
  ];
  for (const [index, slot] of slots.entries()) {
    const item = items[index];
    if (!item) {
      continue;
    }
    const title = catalogText(item.title, `場景候選 ${index + 1}`);
    const previewUrl = safePreviewUrl(item.preview_url);
    slot.classList.toggle("has-catalog-preview", Boolean(previewUrl));
    slot.replaceChildren();
    if (previewUrl) {
      slot.append(
        createPreviewImage(previewUrl, "catalog-preview-image"),
      );
      const caption = document.createElement("small");
      caption.textContent = title;
      slot.append(caption);
    } else {
      slot.textContent = title;
    }
    slot.setAttribute("aria-label", title);
  }
}

function applyCatalog(payload) {
  if (!payload || typeof payload !== "object") {
    elements.catalogVersion.textContent = "Catalog：使用內建展示";
    return;
  }
  renderCharacterStyles(payload.character_styles);
  applySceneShowcaseCatalog(payload.scene_showcase);
  const version = catalogText(payload.schema_version, "已載入");
  elements.catalogVersion.textContent = `Catalog：${version}`;
}

async function loadCatalog() {
  try {
    applyCatalog(await apiRequest("/catalog"));
  } catch (_error) {
    elements.catalogVersion.textContent = "Catalog：載入失敗";
    elements.styleGrid.dataset.state = "error";
    elements.styleGrid.textContent = "風格目錄暫時無法載入，請重新整理頁面。";
  }
}

function workflowErrorMessage(value, fallback) {
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  if (
    value &&
    typeof value === "object" &&
    typeof value.message === "string" &&
    value.message.trim()
  ) {
    return value.message.trim();
  }
  return fallback;
}

function showWorkflowStatus(element, message, kind = "") {
  element.textContent = message;
  element.dataset.kind = kind;
}

function clearWorkflowStatus(element) {
  showWorkflowStatus(element, "");
}

function isWorkflowConflict(error) {
  return (
    error instanceof WorkflowRequestError && error.status === 409
  );
}

function isTransientWorkflowError(error) {
  return (
    error instanceof WorkflowRequestError && error.retryable === true
  );
}

function retryButtonForMode(mode) {
  return mode === "upscale"
    ? elements.storyboardUpscaleRetryButton
    : elements.storyboardGenerationRetryButton;
}

function hideWorkflowRetryButtons() {
  elements.storyboardGenerationRetryButton.hidden = true;
  elements.storyboardUpscaleRetryButton.hidden = true;
}

function clearWorkflowPoll({ resetFailures = true } = {}) {
  if (workflowState.pollTimerId) {
    window.clearTimeout(workflowState.pollTimerId);
    workflowState.pollTimerId = 0;
  }
  workflowState.pollToken += 1;
  workflowState.pollMode = "";
  if (resetFailures) {
    workflowState.pollFailureCount = 0;
  }
}

function setComposeBusy(isBusy, reason = isBusy ? "compose" : "") {
  workflowState.composeBusy = isBusy;
  workflowState.sourceLockReason = isBusy ? reason : "";
  for (const control of [
    elements.storyboardSceneFile,
    elements.storyboardCharacterFile,
    elements.storyboardPrompt,
    elements.storyboardCandidateCount,
  ]) {
    control.disabled = isBusy;
  }
  elements.generateStoryboardButton.disabled = isBusy;
  const busyLabels = {
    compose: "候選生成中…",
    selection: "正在確認候選…",
    upscale: "4K 處理中，素材已鎖定",
    retry: "正在恢復工作進度…",
  };
  elements.generateStoryboardButton.textContent = isBusy
    ? busyLabels[reason] || busyLabels.compose
    : "產生候選分鏡";
  elements.storyboardComposeForm.setAttribute(
    "aria-busy",
    String(isBusy),
  );
}

function setCandidateControlsDisabled(isDisabled) {
  for (const input of elements.storyboardCandidateGrid.querySelectorAll(
    'input[name="storyboard-candidate"]',
  )) {
    input.disabled =
      isDisabled || input.dataset.candidateReady !== "true";
  }
}

function refreshSelectionControls() {
  const controlsLocked =
    workflowState.selectionBusy || workflowState.selectionLocked;
  setCandidateControlsDisabled(
    controlsLocked,
  );
  const activeIsConfirmed =
    Boolean(workflowState.activeCandidateId) &&
    workflowState.activeCandidateId ===
      workflowState.confirmedCandidateId;
  elements.confirmStoryboardButton.disabled =
    controlsLocked ||
    !workflowState.activeCandidateId ||
    activeIsConfirmed;
  if (workflowState.selectionBusy) {
    elements.confirmStoryboardButton.textContent = "正在確認…";
  } else if (workflowState.selectionLocked) {
    elements.confirmStoryboardButton.textContent = "4K 工作已鎖定選片";
  } else if (activeIsConfirmed) {
    elements.confirmStoryboardButton.textContent = "已確認這張分鏡";
  } else {
    elements.confirmStoryboardButton.textContent = "確定這張分鏡";
  }
}

function setSelectionBusy(isBusy) {
  workflowState.selectionBusy = isBusy;
  refreshSelectionControls();
}

function setSelectionLocked(isLocked) {
  workflowState.selectionLocked = isLocked;
  refreshSelectionControls();
}

function setUpscaleBusy(isBusy) {
  workflowState.upscaleBusy = isBusy;
  elements.upscaleRefinePrompt.disabled =
    isBusy || workflowState.upscaleCompleted;
  elements.upscaleStoryboardButton.disabled =
    isBusy ||
    workflowState.upscaleCompleted ||
    !workflowState.confirmedCandidateId ||
    workflowState.activeCandidateId !==
      workflowState.confirmedCandidateId ||
    !elements.upscaleRefinePrompt.value.trim();
  elements.upscaleStoryboardButton.textContent = isBusy
    ? "4K 處理中…"
    : "放大已選分鏡至 4K";
  elements.storyboardUpscaleForm.setAttribute(
    "aria-busy",
    String(isBusy),
  );
}

function clearResultLink(link) {
  link.hidden = true;
  link.removeAttribute("href");
}

function setResultLink(link, value) {
  const safeUrl = safePreviewUrl(value);
  if (!safeUrl) {
    clearResultLink(link);
    return;
  }
  link.href = safeUrl;
  link.hidden = false;
}

function updateDownloadsVisibility() {
  elements.storyboardDownloads.hidden =
    elements.storyboardDownloadLink.hidden &&
    elements.upscale4kDownloadLink.hidden;
}

function showResultImage(url, alt) {
  const safeUrl = safePreviewUrl(url);
  if (!safeUrl) {
    return false;
  }
  elements.storyboardResultImage.src = safeUrl;
  elements.storyboardResultImage.alt = alt;
  elements.storyboardResultImage.hidden = false;
  elements.storyboardResultEmpty.hidden = true;
  return true;
}

function resetResultShowcase() {
  elements.storyboardResultImage.hidden = true;
  elements.storyboardResultImage.removeAttribute("src");
  elements.storyboardResultEmpty.hidden = false;
  elements.storyboardAssetState.textContent = "等待素材";
  elements.storyboardResultTitle.textContent = "尚未產生候選";
  elements.storyboardResultDescription.textContent =
    "請先準備場景、單一角色正面參考與詳細合成描述。";
  clearResultLink(elements.storyboardDownloadLink);
  clearResultLink(elements.upscale4kDownloadLink);
  updateDownloadsVisibility();
}

function resetWorkflowRun() {
  clearWorkflowPoll();
  workflowState.runId = "";
  workflowState.runStatus = "";
  workflowState.candidates = [];
  workflowState.activeCandidateId = "";
  workflowState.confirmedCandidateId = "";
  workflowState.selectionBusy = false;
  workflowState.selectionLocked = false;
  workflowState.upscaleCompleted = false;
  elements.storyboardCandidateGrid.replaceChildren();
  elements.storyboardCandidateSection.hidden = true;
  elements.storyboardUpscaleForm.hidden = true;
  elements.upscaleRefinePrompt.value = "";
  updateTextareaCount(elements.upscaleRefinePrompt);
  clearWorkflowStatus(elements.storyboardGenerationStatus);
  clearWorkflowStatus(elements.storyboardSelectionStatus);
  clearWorkflowStatus(elements.storyboardUpscaleStatus);
  hideWorkflowRetryButtons();
  setComposeBusy(false);
  setSelectionBusy(false);
  setSelectionLocked(false);
  setUpscaleBusy(false);
  resetResultShowcase();
}

function candidateIsComplete(candidate) {
  return (
    candidate?.status === "completed" &&
    Boolean(safePreviewUrl(candidate.image_url))
  );
}

function candidateSeedText(candidate) {
  if (
    typeof candidate?.seed !== "number" &&
    typeof candidate?.seed !== "string"
  ) {
    return "Seed 待回傳";
  }
  return `Seed ${String(candidate.seed)}`;
}

function candidateStatusText(candidate) {
  const statusText = {
    queued: "等待執行",
    running: "生成中",
    completed: "可以選擇",
    failed: "生成失敗",
  };
  return statusText[candidate?.status] || "狀態未知";
}

function createCandidateCard(candidate, index) {
  const candidateId =
    typeof candidate?.candidate_id === "string"
      ? candidate.candidate_id
      : "";
  const isReady = Boolean(candidateId) && candidateIsComplete(candidate);
  const imageUrl = safePreviewUrl(candidate?.image_url);
  const label = document.createElement("label");
  label.className = "candidate-card";
  label.dataset.status = catalogText(candidate?.status, "unknown");

  const input = document.createElement("input");
  input.type = "radio";
  input.name = "storyboard-candidate";
  input.value = candidateId;
  input.dataset.candidateReady = String(isReady);
  input.checked =
    candidateId === workflowState.activeCandidateId ||
    candidateId === workflowState.confirmedCandidateId;
  input.disabled =
    !isReady ||
    workflowState.selectionBusy ||
    workflowState.selectionLocked;

  const visual = document.createElement("span");
  visual.className = "candidate-visual";
  if (imageUrl && candidate?.status === "completed") {
    const image = createPreviewImage(imageUrl, "candidate-image");
    image.alt = `候選分鏡 ${index + 1}`;
    visual.append(image);
  } else {
    const placeholder = document.createElement("span");
    placeholder.className = "candidate-placeholder";
    placeholder.textContent =
      candidate?.status === "failed" ? "無法產生" : "處理中";
    visual.append(placeholder);
  }

  const copy = document.createElement("span");
  copy.className = "candidate-copy";
  const title = document.createElement("strong");
  title.textContent = `候選 ${String(index + 1).padStart(2, "0")}`;
  const seed = document.createElement("small");
  seed.textContent = candidateSeedText(candidate);
  const status = document.createElement("small");
  status.textContent =
    candidate?.status === "failed"
      ? workflowErrorMessage(candidate.error, "生成失敗")
      : candidateStatusText(candidate);
  copy.append(title, seed, status);
  label.append(input, visual, copy);
  return label;
}

function renderCandidates(candidates) {
  workflowState.candidates = Array.isArray(candidates)
    ? candidates.slice(0, 3)
    : [];
  if (workflowState.candidates.length === 0) {
    elements.storyboardCandidateSection.hidden = true;
    elements.storyboardCandidateGrid.replaceChildren();
    return;
  }
  elements.storyboardCandidateSection.hidden = false;
  elements.storyboardCandidateGrid.replaceChildren(
    ...workflowState.candidates.map(createCandidateCard),
  );
  refreshSelectionControls();
}

function findCandidate(candidateId) {
  return workflowState.candidates.find(
    (candidate) => candidate?.candidate_id === candidateId,
  );
}

function showCandidatePreview(candidate, { isConfirmed = false } = {}) {
  const candidateIndex = workflowState.candidates.indexOf(candidate);
  const imageShown = showResultImage(
    candidate?.image_url,
    isConfirmed ? "已確認的分鏡" : "目前選取的候選分鏡",
  );
  if (!imageShown) {
    return;
  }
  elements.storyboardAssetState.textContent = isConfirmed
    ? "已確認"
    : "候選預覽";
  elements.storyboardResultTitle.textContent = isConfirmed
    ? "已確認的分鏡"
    : `候選 ${String(candidateIndex + 1).padStart(2, "0")}`;
  elements.storyboardResultDescription.textContent = isConfirmed
    ? "只有這張候選能進入 4K 精修放大。"
    : `${candidateSeedText(candidate)}；尚未確認，不會自動進入 4K。`;
}

function revealUpscaleForSelection(candidate) {
  workflowState.activeCandidateId = candidate.candidate_id;
  workflowState.confirmedCandidateId = candidate.candidate_id;
  showCandidatePreview(candidate, { isConfirmed: true });
  setResultLink(
    elements.storyboardDownloadLink,
    candidate.download_url,
  );
  clearResultLink(elements.upscale4kDownloadLink);
  updateDownloadsVisibility();
  elements.storyboardUpscaleForm.hidden = false;
  if (!elements.upscaleRefinePrompt.value.trim()) {
    elements.upscaleRefinePrompt.value =
      elements.storyboardPrompt.value.trim();
    updateTextareaCount(elements.upscaleRefinePrompt);
  }
  setSelectionBusy(false);
  setUpscaleBusy(false);
}

function upscaleStatusMessage(upscale) {
  const messages = {
    idle: "",
    queued: "已排入 4K 工作佇列。",
    running: "正在精修並放大已確認的單一候選…",
    completed: "4K 定稿完成，可在右側預覽或下載。",
    failed: workflowErrorMessage(
      upscale?.error,
      "4K 放大失敗，請確認本機工作流環境。",
    ),
  };
  return messages[upscale?.status] || "";
}

function applyUpscaleState(upscale) {
  if (!upscale || typeof upscale !== "object") {
    return;
  }
  const status = upscale.status;
  if (status === "idle") {
    const wasRecoveringUpscale =
      workflowState.sourceLockReason === "upscale" ||
      workflowState.pollMode === "upscale";
    if (
      !workflowState.confirmedCandidateId &&
      !wasRecoveringUpscale
    ) {
      return;
    }
    workflowState.upscaleCompleted = false;
    setComposeBusy(false);
    setSelectionLocked(false);
    setUpscaleBusy(false);
    if (wasRecoveringUpscale) {
      showWorkflowStatus(
        elements.storyboardUpscaleStatus,
        "同步完成：目前沒有進行中的 4K 工作，可再次送出已確認候選。",
        "warning",
      );
    }
    return;
  }
  if (status === "queued" || status === "running") {
    workflowState.upscaleCompleted = false;
    setComposeBusy(true, "upscale");
    setSelectionLocked(true);
    setUpscaleBusy(true);
    elements.storyboardAssetState.textContent = "4K 處理中";
    showWorkflowStatus(
      elements.storyboardUpscaleStatus,
      upscaleStatusMessage(upscale),
    );
    return;
  }
  if (status === "failed") {
    workflowState.upscaleCompleted = false;
    setComposeBusy(false);
    setSelectionLocked(false);
    setUpscaleBusy(false);
    elements.storyboardAssetState.textContent = "4K 失敗";
    showWorkflowStatus(
      elements.storyboardUpscaleStatus,
      upscaleStatusMessage(upscale),
      "error",
    );
    return;
  }
  if (status !== "completed") {
    return;
  }
  const imageShown = showResultImage(
    upscale.image_url,
    "3840 × 2160 的 4K 分鏡定稿",
  );
  if (!imageShown) {
    workflowState.upscaleCompleted = true;
    setComposeBusy(false);
    setSelectionLocked(true);
    setUpscaleBusy(false);
    showWorkflowStatus(
      elements.storyboardUpscaleStatus,
      "4K 任務已完成，但回傳的圖片網址無法使用。",
      "error",
    );
    return;
  }
  workflowState.upscaleCompleted = true;
  setComposeBusy(false);
  setSelectionLocked(true);
  setUpscaleBusy(false);
  elements.storyboardAssetState.textContent = "4K 完成";
  elements.storyboardResultTitle.textContent = "4K 分鏡定稿";
  elements.storyboardResultDescription.textContent =
    "已確認候選已完成 3840 × 2160 精修放大；其他候選沒有被放大。";
  setResultLink(elements.upscale4kDownloadLink, upscale.download_url);
  updateDownloadsVisibility();
  showWorkflowStatus(
    elements.storyboardUpscaleStatus,
    upscaleStatusMessage(upscale),
    "success",
  );
}

function applyRunPayload(run) {
  const runId =
    typeof run?.run_id === "string" ? run.run_id.trim() : "";
  if (!runId) {
    throw new Error("工作流服務沒有回傳有效的任務 ID。");
  }
  if (workflowState.runId && workflowState.runId !== runId) {
    throw new Error("工作流服務回傳了不相符的任務。");
  }
  workflowState.runId = runId;
  workflowState.runStatus = catalogText(run.status, "unknown");
  renderCandidates(run.candidates);

  const selectedCandidateId =
    typeof run.selected_candidate_id === "string"
      ? run.selected_candidate_id
      : "";
  if (selectedCandidateId) {
    const selectedCandidate = findCandidate(selectedCandidateId);
    if (selectedCandidate && candidateIsComplete(selectedCandidate)) {
      revealUpscaleForSelection(selectedCandidate);
    }
  }
  applyUpscaleState(run.upscale);

  if (run.status === "queued") {
    setComposeBusy(true, "compose");
    setSelectionLocked(true);
    showWorkflowStatus(
      elements.storyboardGenerationStatus,
      "候選任務已排入本機工作佇列。",
    );
  } else if (run.status === "running") {
    setComposeBusy(true, "compose");
    setSelectionLocked(true);
    showWorkflowStatus(
      elements.storyboardGenerationStatus,
      "正在合成角色與場景，完成前請保持本機服務運作…",
    );
  } else if (run.status === "awaiting_selection") {
    setComposeBusy(false);
    setSelectionLocked(false);
    setSelectionBusy(false);
    showWorkflowStatus(
      elements.storyboardGenerationStatus,
      "候選已完成，請逐張檢查並明確選定一張。",
      "success",
    );
  } else if (run.status === "failed") {
    setComposeBusy(false);
    setSelectionLocked(false);
    setSelectionBusy(false);
    if (!selectedCandidateId || run?.upscale?.status !== "failed") {
      showWorkflowStatus(
        elements.storyboardGenerationStatus,
        "候選生成失敗，請檢查圖片與本機工作流環境後重試。",
        "error",
      );
    }
  }
}

function runNeedsPolling(mode, run) {
  if (mode === "compose") {
    return run.status === "queued" || run.status === "running";
  }
  return (
    run.status === "upscaling" ||
    run?.upscale?.status === "queued" ||
    run?.upscale?.status === "running"
  );
}

function lockWorkflowDuringPoll(mode) {
  setComposeBusy(true, "retry");
  setSelectionLocked(true);
  if (mode === "upscale") {
    setUpscaleBusy(true);
  }
}

function pollStatusElement(mode) {
  return mode === "upscale"
    ? elements.storyboardUpscaleStatus
    : elements.storyboardGenerationStatus;
}

function pollRetryDelayMs(failureCount) {
  return Math.min(
    WORKFLOW_POLL_INTERVAL_MS * 2 ** Math.max(0, failureCount - 1),
    WORKFLOW_POLL_MAX_DELAY_MS,
  );
}

function pollWorkflowRun(
  mode,
  token,
  delayMs = WORKFLOW_POLL_INTERVAL_MS,
) {
  if (!workflowState.runId || token !== workflowState.pollToken) {
    return;
  }
  workflowState.pollTimerId = window.setTimeout(async () => {
    workflowState.pollTimerId = 0;
    try {
      const run = await workflowRequest(
        `/storyboards/${encodeURIComponent(workflowState.runId)}`,
      );
      if (token !== workflowState.pollToken) {
        return;
      }
      workflowState.pollFailureCount = 0;
      retryButtonForMode(mode).hidden = true;
      applyRunPayload(run);
      if (runNeedsPolling(mode, run)) {
        pollWorkflowRun(mode, token, WORKFLOW_POLL_INTERVAL_MS);
      } else {
        workflowState.pollMode = "";
      }
    } catch (error) {
      if (token !== workflowState.pollToken) {
        return;
      }
      const canRetry =
        isTransientWorkflowError(error) ||
        isWorkflowConflict(error);
      if (
        canRetry &&
        workflowState.pollFailureCount <
          WORKFLOW_POLL_MAX_RETRIES
      ) {
        workflowState.pollFailureCount += 1;
        const retryDelay = pollRetryDelayMs(
          workflowState.pollFailureCount,
        );
        lockWorkflowDuringPoll(mode);
        showWorkflowStatus(
          pollStatusElement(mode),
          `進度連線暫時中斷，${Math.ceil(retryDelay / 1000)} 秒後重試` +
            `（${workflowState.pollFailureCount} / ${WORKFLOW_POLL_MAX_RETRIES}）；` +
            "既有工作仍保留，請勿重新送出。",
          "warning",
        );
        pollWorkflowRun(mode, token, retryDelay);
        return;
      }
      lockWorkflowDuringPoll(mode);
      showWorkflowStatus(
        pollStatusElement(mode),
        "暫時無法確認工作進度；既有工作與畫面均已保留。" +
          "恢復連線後請按「重新查詢進度」，不要重新建立工作。",
        "error",
      );
      retryButtonForMode(mode).hidden = false;
    }
  }, delayMs);
}

function startWorkflowPolling(mode) {
  clearWorkflowPoll();
  workflowState.pollMode = mode;
  workflowState.pollFailureCount = 0;
  retryButtonForMode(mode).hidden = true;
  lockWorkflowDuringPoll(mode);
  const token = workflowState.pollToken;
  pollWorkflowRun(mode, token);
}

async function createStoryboardRun() {
  if (!elements.storyboardComposeForm.reportValidity()) {
    return;
  }
  const sceneImage = elements.storyboardSceneFile.files?.[0];
  const characterImage = elements.storyboardCharacterFile.files?.[0];
  if (!sceneImage || !characterImage) {
    showWorkflowStatus(
      elements.storyboardGenerationStatus,
      "請選擇場景圖與單一角色正面參考。",
      "error",
    );
    return;
  }

  resetWorkflowRun();
  setComposeBusy(true);
  elements.storyboardAssetState.textContent = "上傳素材";
  showWorkflowStatus(
    elements.storyboardGenerationStatus,
    "正在安全上傳兩張參考圖並建立候選任務…",
  );

  const candidateCount = Number.parseInt(
    elements.storyboardCandidateCount.value,
    10,
  );
  const request = {
    prompt: elements.storyboardPrompt.value.trim(),
    candidate_count:
      Number.isInteger(candidateCount) &&
      candidateCount >= 1 &&
      candidateCount <= 3
        ? candidateCount
        : 3,
  };
  const formData = new FormData();
  formData.append("request", JSON.stringify(request));
  formData.append("scene_image", sceneImage);
  formData.append("character_image", characterImage);

  try {
    const run = await workflowRequest("/storyboards", {
      method: "POST",
      body: formData,
    });
    applyRunPayload(run);
    if (runNeedsPolling("compose", run)) {
      startWorkflowPolling("compose");
    }
  } catch (error) {
    setComposeBusy(false);
    elements.storyboardAssetState.textContent = "建立失敗";
    showWorkflowStatus(
      elements.storyboardGenerationStatus,
      workflowErrorMessage(
        error,
        "無法建立候選任務，請確認本機服務與圖片格式。",
      ),
      "error",
    );
  }
}

function selectCandidate(candidateId) {
  const candidate = findCandidate(candidateId);
  if (!candidate || !candidateIsComplete(candidate)) {
    return;
  }
  workflowState.activeCandidateId = candidateId;
  showCandidatePreview(candidate);
  if (candidateId !== workflowState.confirmedCandidateId) {
    elements.storyboardUpscaleForm.hidden = true;
    clearResultLink(elements.storyboardDownloadLink);
    clearResultLink(elements.upscale4kDownloadLink);
    updateDownloadsVisibility();
    showWorkflowStatus(
      elements.storyboardSelectionStatus,
      "這張候選尚未確認；確認前不能送入 4K。",
    );
  } else {
    clearWorkflowStatus(elements.storyboardSelectionStatus);
  }
  setSelectionBusy(false);
  setUpscaleBusy(workflowState.upscaleBusy);
}

async function confirmSelectedCandidate() {
  const candidateId = workflowState.activeCandidateId;
  if (
    !workflowState.runId ||
    !candidateId ||
    !candidateIsComplete(findCandidate(candidateId))
  ) {
    return;
  }
  setSelectionBusy(true);
  setComposeBusy(true, "selection");
  showWorkflowStatus(
    elements.storyboardSelectionStatus,
    "正在鎖定唯一的 4K 來源候選…",
  );
  try {
    const run = await workflowRequest(
      `/storyboards/${encodeURIComponent(workflowState.runId)}/selection`,
      {
        method: "POST",
        json: { candidate_id: candidateId },
      },
    );
    applyRunPayload(run);
    if (run.selected_candidate_id !== candidateId) {
      throw new Error("後端沒有確認目前選取的候選。");
    }
    setComposeBusy(false);
    showWorkflowStatus(
      elements.storyboardSelectionStatus,
      "已確認此候選；現在可以填寫 4K 細節描述。",
      "success",
    );
  } catch (error) {
    if (
      isWorkflowConflict(error) ||
      isTransientWorkflowError(error)
    ) {
      showWorkflowStatus(
        elements.storyboardSelectionStatus,
        isWorkflowConflict(error)
          ? "候選狀態已變更，正在重新查詢既有工作；不會重複送出選片。"
          : "選片回應暫時中斷，正在查詢既有工作；不會重複送出選片。",
        "warning",
      );
      startWorkflowPolling("compose");
      return;
    }
    setComposeBusy(false);
    setSelectionBusy(false);
    showWorkflowStatus(
      elements.storyboardSelectionStatus,
      workflowErrorMessage(error, "無法確認候選，請稍後重試。"),
      "error",
    );
  }
}

async function upscaleConfirmedStoryboard() {
  if (!elements.storyboardUpscaleForm.reportValidity()) {
    return;
  }
  if (!workflowState.runId || !workflowState.confirmedCandidateId) {
    showWorkflowStatus(
      elements.storyboardUpscaleStatus,
      "請先確認一張候選分鏡。",
      "error",
    );
    return;
  }
  workflowState.upscaleCompleted = false;
  setComposeBusy(true, "upscale");
  setSelectionLocked(true);
  setUpscaleBusy(true);
  elements.storyboardAssetState.textContent = "建立 4K 任務";
  showWorkflowStatus(
    elements.storyboardUpscaleStatus,
    "正在建立只包含已確認候選的 4K 任務…",
  );
  try {
    const run = await workflowRequest(
      `/storyboards/${encodeURIComponent(workflowState.runId)}/upscale`,
      {
        method: "POST",
        json: {
          refine_prompt: elements.upscaleRefinePrompt.value.trim(),
          expected_candidate_id:
            workflowState.confirmedCandidateId,
        },
      },
    );
    applyRunPayload(run);
    if (runNeedsPolling("upscale", run)) {
      startWorkflowPolling("upscale");
    }
  } catch (error) {
    if (
      isWorkflowConflict(error) ||
      isTransientWorkflowError(error)
    ) {
      showWorkflowStatus(
        elements.storyboardUpscaleStatus,
        isWorkflowConflict(error)
          ? "4K 工作狀態已變更，正在重新查詢既有工作；不會重複建立工作。"
          : "4K 送出回應暫時中斷，正在查詢既有工作；不會重複送出。",
        "warning",
      );
      startWorkflowPolling("upscale");
      return;
    }
    setComposeBusy(false);
    setSelectionLocked(false);
    setUpscaleBusy(false);
    elements.storyboardAssetState.textContent = "4K 建立失敗";
    showWorkflowStatus(
      elements.storyboardUpscaleStatus,
      workflowErrorMessage(error, "無法建立 4K 任務，請稍後重試。"),
      "error",
    );
  }
}

function updateSceneShowcase() {
  const selected = selectedInput("scene-direction");
  if (!selected) {
    return;
  }
  elements.selectedSceneDirection.textContent =
    selected.dataset.directionName || selected.value;
  const allowedDirections = new Set([
    "architectural",
    "atmospheric",
    "object-led",
  ]);
  const direction = allowedDirections.has(selected.value)
    ? selected.value
    : "architectural";
  elements.sceneHeroArt.className = `scene-stage direction-${direction}`;
  clearSceneGenerationStatus();
}

function updateHash(tabName) {
  const nextHash = `#${tabName}`;
  if (window.location.hash !== nextHash) {
    window.history.replaceState(null, "", nextHash);
  }
}

function activateTab(tabName, { shouldFocus = false } = {}) {
  if (!tabOrder.includes(tabName)) {
    return;
  }
  for (const tab of elements.tabs) {
    const isActive = tab.dataset.tab === tabName;
    tab.classList.toggle("is-active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
    tab.tabIndex = isActive ? 0 : -1;
    if (isActive && shouldFocus) {
      tab.focus();
    }
  }
  for (const panel of elements.panels) {
    panel.hidden = panel.dataset.panel !== tabName;
  }
  updateHash(tabName);
}

function handleTabKeydown(event) {
  const currentIndex = elements.tabs.indexOf(event.currentTarget);
  const movements = {
    ArrowDown: 1,
    ArrowRight: 1,
    ArrowUp: -1,
    ArrowLeft: -1,
  };
  let nextIndex = currentIndex;
  if (event.key in movements) {
    nextIndex =
      (currentIndex + movements[event.key] + elements.tabs.length) %
      elements.tabs.length;
  } else if (event.key === "Home") {
    nextIndex = 0;
  } else if (event.key === "End") {
    nextIndex = elements.tabs.length - 1;
  } else {
    return;
  }
  event.preventDefault();
  activateTab(elements.tabs[nextIndex].dataset.tab, {
    shouldFocus: true,
  });
}

function updateTextareaCount(textarea) {
  const target = document.getElementById(textarea.dataset.countTarget);
  if (!target) {
    return;
  }
  target.textContent =
    `${textarea.value.length} / ${textarea.maxLength || "∞"}`;
}

function syncTabOrientation() {
  elements.tablist.setAttribute(
    "aria-orientation",
    horizontalTabsMedia.matches ? "horizontal" : "vertical",
  );
}

for (const tab of elements.tabs) {
  tab.addEventListener("click", () => activateTab(tab.dataset.tab));
  tab.addEventListener("keydown", handleTabKeydown);
}

for (const textarea of document.querySelectorAll(
  "textarea[data-count-target]",
)) {
  textarea.addEventListener("input", () => updateTextareaCount(textarea));
  updateTextareaCount(textarea);
}

elements.styleGrid.addEventListener("change", updateCharacterShowcase);
elements.characterPrompt.addEventListener(
  "input",
  clearCharacterGenerationStatus,
);
elements.characterForm.addEventListener("submit", (event) => {
  event.preventDefault();
  confirmCharacterGeneration();
});
elements.scenePrompt.addEventListener("input", clearSceneGenerationStatus);
elements.sceneLight.addEventListener("change", clearSceneGenerationStatus);
elements.sceneScale.addEventListener("change", clearSceneGenerationStatus);
elements.sceneForm.addEventListener("submit", (event) => {
  event.preventDefault();
  confirmSceneGeneration();
});

for (const input of document.querySelectorAll(
  'input[name="scene-direction"]',
)) {
  input.addEventListener("change", updateSceneShowcase);
}

elements.storyboardComposeForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void createStoryboardRun();
});
elements.storyboardCandidateGrid.addEventListener("change", (event) => {
  const input = event.target.closest(
    'input[name="storyboard-candidate"]',
  );
  if (input) {
    selectCandidate(input.value);
  }
});
elements.confirmStoryboardButton.addEventListener("click", () => {
  void confirmSelectedCandidate();
});
elements.storyboardUpscaleForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void upscaleConfirmedStoryboard();
});
elements.upscaleRefinePrompt.addEventListener("input", () => {
  clearWorkflowStatus(elements.storyboardUpscaleStatus);
  setUpscaleBusy(workflowState.upscaleBusy);
});
for (const [button, mode] of [
  [elements.storyboardGenerationRetryButton, "compose"],
  [elements.storyboardUpscaleRetryButton, "upscale"],
]) {
  button.addEventListener("click", () => {
    if (!workflowState.runId) {
      return;
    }
    showWorkflowStatus(
      pollStatusElement(mode),
      "正在重新查詢既有工作，請勿重新送出…",
      "warning",
    );
    startWorkflowPolling(mode);
  });
}
for (const control of [
  elements.storyboardSceneFile,
  elements.storyboardCharacterFile,
  elements.storyboardPrompt,
  elements.storyboardCandidateCount,
]) {
  control.addEventListener("change", () => {
    if (workflowState.runId && !workflowState.composeBusy) {
      resetWorkflowRun();
    }
  });
}

window.addEventListener("hashchange", () => {
  const tabName = window.location.hash.slice(1);
  if (tabOrder.includes(tabName)) {
    activateTab(tabName);
  }
});
horizontalTabsMedia.addEventListener("change", syncTabOrientation);
window.addEventListener("pagehide", clearWorkflowPoll);

const initialTab = window.location.hash.slice(1);
activateTab(tabOrder.includes(initialTab) ? initialTab : "character");
syncTabOrientation();
updateSceneShowcase();
resetWorkflowRun();
void loadCatalog();
