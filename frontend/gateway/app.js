"use strict";

const API_ROOT = "/api/v1/gateway";
const CATALOG_TIMEOUT_MS = 10_000;
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
  copyPromptButtonLabel: document.querySelector(
    "#copy-character-prompt-label",
  ),
  promptCopyStatus: document.querySelector("#prompt-copy-status"),
  sceneHeroArt: document.querySelector("#scene-hero-art"),
  selectedSceneDirection: document.querySelector(
    "#selected-scene-direction",
  ),
  selectedShotPattern: document.querySelector("#selected-shot-pattern"),
};

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
  clearCopyStatus();
}

function buildCharacterPrompt() {
  const selected = selectedInput("character-style");
  const characterDescription = elements.characterPrompt.value.trim();
  const stylePrompt =
    selected?.dataset.stylePrompt || "使用所選視覺風格呈現。";
  return [
    `角色設定：${characterDescription}`,
    "角色一致性規則：保持角色的五官、髮型、年齡、身形比例、服裝、配色與配件完全一致；只改變視覺媒材、筆觸、光線與畫面質感，不重新設計角色。",
    `視覺風格：${stylePrompt}`,
  ].join("\n\n");
}

async function writeClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const fallback = document.createElement("textarea");
  fallback.className = "clipboard-fallback";
  fallback.value = text;
  fallback.setAttribute("readonly", "");
  document.body.append(fallback);
  fallback.select();
  const didCopy = document.execCommand("copy");
  fallback.remove();
  if (!didCopy) {
    throw new Error("瀏覽器不允許複製");
  }
}

function showCopyStatus(message, kind) {
  elements.promptCopyStatus.textContent = message;
  elements.promptCopyStatus.dataset.kind = kind;
  elements.copyPromptButtonLabel.textContent =
    kind === "success" ? "已複製提示詞" : "複製完整提示詞";
}

function clearCopyStatus() {
  elements.promptCopyStatus.textContent = "";
  elements.promptCopyStatus.dataset.kind = "";
  elements.copyPromptButtonLabel.textContent = "複製完整提示詞";
}

async function copyCharacterPrompt() {
  if (!elements.characterForm.reportValidity()) {
    return;
  }
  try {
    await writeClipboard(buildCharacterPrompt());
    showCopyStatus(
      "已複製角色設定、一致性規則與所選風格提示詞。",
      "success",
    );
  } catch (_error) {
    showCopyStatus(
      "瀏覽器無法自動複製，請從風格櫥窗內手動選取提示詞。",
      "error",
    );
  }
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

function applyStoryboardShowcaseCatalog(items) {
  if (!Array.isArray(items) || items.length === 0) {
    return;
  }
  const frames = [
    ...document.querySelectorAll(".storyboard-showcase .story-frame"),
  ];
  for (const [index, frame] of frames.entries()) {
    const item = items[index];
    if (!item) {
      continue;
    }
    const title = catalogText(item.title, `分鏡展示 ${index + 1}`);
    const previewUrl = safePreviewUrl(item.preview_url);
    frame.setAttribute("role", "img");
    frame.setAttribute("aria-label", title);
    if (previewUrl) {
      const numberBadge = frame.querySelector("b");
      frame.replaceChildren(
        createPreviewImage(previewUrl, "catalog-preview-image"),
      );
      if (numberBadge) {
        frame.append(numberBadge);
      }
    }
  }
}

function applyCatalog(payload) {
  if (!payload || typeof payload !== "object") {
    elements.catalogVersion.textContent = "Catalog：使用內建展示";
    return;
  }
  renderCharacterStyles(payload.character_styles);
  applySceneShowcaseCatalog(payload.scene_showcase);
  applyStoryboardShowcaseCatalog(payload.storyboard_showcase);
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
}

function updateStoryboardShowcase() {
  const selected = selectedInput("shot-pattern");
  if (!selected) {
    return;
  }
  elements.selectedShotPattern.textContent =
    selected.dataset.patternName || selected.value;
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
elements.characterPrompt.addEventListener("input", clearCopyStatus);
elements.characterForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void copyCharacterPrompt();
});

for (const input of document.querySelectorAll(
  'input[name="scene-direction"]',
)) {
  input.addEventListener("change", updateSceneShowcase);
}
for (const input of document.querySelectorAll('input[name="shot-pattern"]')) {
  input.addEventListener("change", updateStoryboardShowcase);
}

window.addEventListener("hashchange", () => {
  const tabName = window.location.hash.slice(1);
  if (tabOrder.includes(tabName)) {
    activateTab(tabName);
  }
});
horizontalTabsMedia.addEventListener("change", syncTabOrientation);

const initialTab = window.location.hash.slice(1);
activateTab(tabOrder.includes(initialTab) ? initialTab : "character");
syncTabOrientation();
updateSceneShowcase();
updateStoryboardShowcase();
void loadCatalog();
