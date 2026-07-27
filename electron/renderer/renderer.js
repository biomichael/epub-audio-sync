const state = {
  epub: "",
  audioFiles: [],
  out: "",
  inspection: null,
  chapterConfigs: [],
  assignments: [],
  result: null,
  preview: null,
  logs: [],
  segmentPicker: null,
};
const SEGMENT_SELECTOR = '[data-epubsync-segment="1"]';

function $(id) {
  return document.getElementById(id);
}

function setStatus(label, mode) {
  const node = $("statusText");
  node.textContent = label;
  node.className = `status-pill ${mode}`;
}

function appendLog(message) {
  state.logs.push(message);
  $("logBox").value = state.logs.join("\n");
  $("logBox").scrollTop = $("logBox").scrollHeight;
}

function refreshProjectSummary() {
  $("epubValue").textContent = state.epub || "Use File > Add EPUB";
  $("epubValue").classList.toggle("muted", !state.epub);
  $("audioCountValue").textContent = state.audioFiles.length
    ? `${state.audioFiles.length} file(s) selected`
    : "Use File > Add Audio Files";
  $("audioCountValue").classList.toggle("muted", state.audioFiles.length === 0);
  $("outputValue").textContent = state.out || "Choose a folder for exports";
  $("outputValue").classList.toggle("muted", !state.out);
}

function showWarning(message) {
  const node = $("warningBanner");
  if (!message) {
    node.classList.add("hidden");
    node.textContent = "";
    return;
  }
  node.classList.remove("hidden");
  node.textContent = message;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function getPayloadBase() {
  return {
    epub: state.epub,
    audio_files: state.audioFiles,
    out: state.out,
    language: $("languageInput").value.trim() || "eng",
    external_audio: $("audioModeSelect").value === "external",
    aeneas_repo_path: $("repoPathInput").value.trim(),
    alignment_engine: $("alignmentEngineSelect").value,
    mfa: getMfaSettings(),
  };
}

function getMfaSettings() {
  return {
    executable_path: $("mfaExecutablePathInput").value.trim(),
    acoustic_model: $("mfaAcousticModelInput").value.trim(),
    dictionary: $("mfaDictionaryInput").value.trim(),
    output_folder: $("mfaOutputFolderInput").value.trim(),
    corpus_dir: $("mfaCorpusFolderInput").value.trim(),
    audio_conversion: $("mfaAudioConversionSelect").value,
    proof_mode: $("mfaProofModeInput").checked,
  };
}

function defaultAssignment(chapterHref, index) {
  return {
    id: `assignment-${index + 1}`,
    audio: "",
    startChapter: chapterHref,
    startSegment: 1,
    endChapter: chapterHref,
    endSegment: "",
  };
}

function formatSegmentLabel(value) {
  if (!value) {
    return "chapter start/end";
  }
  return `segment ${value}`;
}

function initializeChapterState() {
  if (!state.inspection) {
    state.chapterConfigs = [];
    state.assignments = [];
    return;
  }
  state.chapterConfigs = state.inspection.spine_items.map((item) => ({
    chapter: item.root_href,
    segment: "sentence",
  }));
  state.assignments = state.inspection.spine_items.map((item, index) => defaultAssignment(item.root_href, index));
}

function chapterOptionsHtml(selectedValue) {
  return state.inspection.spine_items
    .map((item) => {
      const selected = item.root_href === selectedValue ? " selected" : "";
      return `<option value="${escapeHtml(item.root_href)}"${selected}>${escapeHtml(item.root_href)}</option>`;
    })
    .join("");
}

function audioOptionsHtml(selectedValue) {
  const options = [`<option value="">Choose audio file</option>`];
  for (const audioPath of state.audioFiles) {
    const selected = audioPath === selectedValue ? " selected" : "";
    options.push(`<option value="${escapeHtml(audioPath)}"${selected}>${escapeHtml(audioPath)}</option>`);
  }
  return options.join("");
}

function renderChapterConfigs() {
  const empty = $("chapterConfigEmpty");
  const wrap = $("chapterConfigWrap");
  const body = $("chapterConfigBody");
  if (!state.inspection || state.chapterConfigs.length === 0) {
    empty.classList.remove("hidden");
    wrap.classList.add("hidden");
    body.innerHTML = "";
    return;
  }
  empty.classList.add("hidden");
  wrap.classList.remove("hidden");
  body.innerHTML = state.chapterConfigs.map((config, index) => `
    <tr>
      <td>${escapeHtml(config.chapter)}</td>
      <td>
        <select data-chapter-index="${index}" data-kind="chapter-segment">
          <option value="sentence"${config.segment === "sentence" ? " selected" : ""}>Sentence</option>
          <option value="paragraph"${config.segment === "paragraph" ? " selected" : ""}>Paragraph</option>
        </select>
      </td>
    </tr>
  `).join("");
}

function renderAssignments() {
  const empty = $("assignmentEmpty");
  const wrap = $("assignmentWrap");
  const body = $("assignmentBody");
  if (!state.inspection || state.assignments.length === 0) {
    empty.classList.remove("hidden");
    wrap.classList.add("hidden");
    body.innerHTML = "";
    return;
  }
  empty.classList.add("hidden");
  wrap.classList.remove("hidden");
  body.innerHTML = state.assignments.map((assignment, index) => `
    <tr>
      <td>
        <select data-assignment-index="${index}" data-kind="audio">
          ${audioOptionsHtml(assignment.audio)}
        </select>
      </td>
      <td>
        <select data-assignment-index="${index}" data-kind="startChapter">
          ${chapterOptionsHtml(assignment.startChapter)}
        </select>
      </td>
      <td>
        <div class="segment-cell">
          <input data-assignment-index="${index}" data-kind="startSegment" type="number" min="1" value="${escapeHtml(assignment.startSegment || 1)}">
          <button class="button button-subtle" data-pick-segment="${index}" data-pick-kind="startSegment">Pick</button>
        </div>
        <div class="segment-readout">${escapeHtml(formatSegmentLabel(assignment.startSegment || 1))}</div>
      </td>
      <td>
        <button class="button button-subtle button-find-sync" data-find-sync="${index}" title="Auto-detect start and end segments from audio">Find Sync</button>
      </td>
      <td>
        <select data-assignment-index="${index}" data-kind="endChapter">
          ${chapterOptionsHtml(assignment.endChapter)}
        </select>
      </td>
      <td>
        <div class="segment-cell">
          <input data-assignment-index="${index}" data-kind="endSegment" type="number" min="1" value="${escapeHtml(assignment.endSegment || "")}" placeholder="chapter end">
          <button class="button button-subtle" data-pick-segment="${index}" data-pick-kind="endSegment">Pick</button>
        </div>
        <div class="segment-readout">${escapeHtml(formatSegmentLabel(assignment.endSegment || ""))}</div>
      </td>
      <td><button class="button button-subtle" data-remove-assignment="${index}">Remove</button></td>
    </tr>
  `).join("");
}

function syncChapterConfigStateFromDom() {
  state.chapterConfigs = state.chapterConfigs.map((config, index) => ({
    chapter: config.chapter,
    segment: document.querySelector(`[data-chapter-index="${index}"][data-kind="chapter-segment"]`)?.value || "sentence",
  }));
}

function syncAssignmentStateFromDom() {
  state.assignments = state.assignments.map((assignment, index) => ({
    id: assignment.id,
    audio: document.querySelector(`[data-assignment-index="${index}"][data-kind="audio"]`)?.value || "",
    startChapter: document.querySelector(`[data-assignment-index="${index}"][data-kind="startChapter"]`)?.value || assignment.startChapter,
    startSegment: Number(document.querySelector(`[data-assignment-index="${index}"][data-kind="startSegment"]`)?.value || "1"),
    endChapter: document.querySelector(`[data-assignment-index="${index}"][data-kind="endChapter"]`)?.value || assignment.endChapter,
    endSegment: document.querySelector(`[data-assignment-index="${index}"][data-kind="endSegment"]`)?.value || "",
  }));
}

async function testMfaSettings() {
  setStatus("Testing MFA", "running");
  try {
    const payload = getPayloadBase();
    payload.mfa.proof_mode = true;
    const response = await window.desktopBridge.proofMfa(payload);
    const result = response.result;
    if (result && result.mfa_proof) {
      appendLog(`MFA proof command: ${result.mfa_proof.command.join(" ")}`);
      appendLog(`MFA proof return code: ${result.mfa_proof.return_code}`);
      if (result.mfa_proof.stdout) {
        appendLog(result.mfa_proof.stdout);
      }
      if (result.mfa_proof.stderr) {
        appendLog(result.mfa_proof.stderr);
      }
      setStatus(result.mfa_proof.success ? "MFA Ready" : "MFA Failed", result.mfa_proof.success ? "done" : "error");
    }
  } catch (error) {
    appendLog(String(error.message || error));
    setStatus("MFA Failed", "error");
  }
}

function renderPlanner() {
  renderChapterConfigs();
  renderAssignments();
}

function renderExportSummary(result) {
  const previewSelect = $("previewSelect");
  previewSelect.innerHTML = "";
  for (let index = 0; index < result.preview.length; index += 1) {
    const chapter = result.preview[index];
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = chapter.chapter_href;
    previewSelect.appendChild(option);
  }
  $("exportSummary").innerHTML = `
    <div><strong>Patched EPUB</strong><br>${escapeHtml(result.patched_epub)}</div>
    <div><strong>JSON Folder</strong><br>${escapeHtml(result.json_dir)}</div>
    <div><strong>SMIL Folder</strong><br>${escapeHtml(result.smil_dir)}</div>
    <div><strong>Debug Log</strong><br>${escapeHtml(result.debug_log)}</div>
  `;
}

function setAudioSource(audio, source) {
  if (audio.dataset.sourceId === source.id) {
    return;
  }
  audio.dataset.sourceId = source.id;
  audio.src = `file:///${source.path.replaceAll("\\", "/")}`;
}

function renderXhtmlFragment(container, xhtml) {
  const wrapped = `<?xml version="1.0" encoding="UTF-8"?><wrapper xmlns="http://www.w3.org/1999/xhtml">${xhtml}</wrapper>`;
  const parsed = new DOMParser().parseFromString(wrapped, "application/xhtml+xml");
  if (parsed.querySelector("parsererror")) {
    container.innerHTML = xhtml;
    return;
  }
  const wrapper = parsed.documentElement;
  const fragment = document.createDocumentFragment();
  Array.from(wrapper.childNodes).forEach((node) => {
    fragment.appendChild(document.importNode(node, true));
  });
  container.replaceChildren(fragment);
}

function hookPreviewInteractions(data) {
  const audio = $("audioPlayer");
  const byId = new Map(data.segments.map((segment) => [segment.textId, segment]));
  const sourceById = new Map(data.audioSources.map((source) => [source.id, source]));
  const wordTimingsBySource = buildWordTimingMap(data);
  document.querySelectorAll(`#previewPane ${SEGMENT_SELECTOR}`).forEach((node) => {
    const segment = byId.get(node.id);
    if (!segment) {
      return;
    }
    node.style.cursor = "pointer";
    node.addEventListener("click", () => {
      const source = sourceById.get(segment.audioSourceId);
      if (source) {
        setAudioSource(audio, source);
      }
      audio.currentTime = segment.begin;
      audio.play();
    });
  });
  audio.ontimeupdate = () => {
    const currentSourceId = audio.dataset.sourceId || "";
    applyWordHighlight(currentSourceId, audio.currentTime, wordTimingsBySource);
  };
}

function buildWordTimingMap(data) {
  const result = new Map();
  const lingerSeconds = 1.4;
  for (const segment of data.segments) {
    const node = document.getElementById(segment.textId);
    if (!node) {
      continue;
    }
    const words = wrapWordsInNode(node);
    const totalWeight = words.reduce((sum, item) => sum + item.weight, 0) || 1;
    let cursor = 0;
    const timedWords = words.map((item) => {
      const startRatio = cursor / totalWeight;
      cursor += item.weight;
      const endRatio = cursor / totalWeight;
      const duration = Math.max(segment.end - segment.begin, 0.001);
      return {
        node: item.node,
        startTime: segment.begin + (startRatio * duration),
        endTime: segment.begin + (endRatio * duration) + lingerSeconds,
        segmentStart: segment.begin,
      };
    });
    if (!result.has(segment.audioSourceId)) {
      result.set(segment.audioSourceId, []);
    }
    result.get(segment.audioSourceId).push(...timedWords);
  }
  return result;
}

function extractBodyHtml(xhtml) {
  const parsed = new DOMParser().parseFromString(xhtml, "application/xhtml+xml");
  const body = parsed.querySelector("body");
  return body ? body.innerHTML : xhtml;
}

function getChapterPath(chapterRootHref) {
  const chapter = state.inspection?.spine_items?.find((item) => item.root_href === chapterRootHref);
  return chapter ? chapter.full_path : null;
}

function buildFallbackPickerSegments(pickerPane) {
  const seen = new Set();
  const candidates = pickerPane.querySelectorAll(SEGMENT_SELECTOR).length > 0
    ? pickerPane.querySelectorAll(SEGMENT_SELECTOR)
    : pickerPane.querySelectorAll("[id]");
  return Array.from(candidates)
    .filter((node) => {
      if (seen.has(node.id)) {
        return false;
      }
      seen.add(node.id);
      return true;
    })
    .map((node, index) => ({
      textId: node.id,
      segmentIndex: index + 1,
    }));
}

function wrapWordsInNode(root) {
  const textNodes = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) {
        return NodeFilter.FILTER_REJECT;
      }
      if (node.parentElement && node.parentElement.closest(".audio-word")) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  while (walker.nextNode()) {
    textNodes.push(walker.currentNode);
  }
  const words = [];
  for (const textNode of textNodes) {
    const fragment = document.createDocumentFragment();
    const text = textNode.nodeValue;
    const pattern = /(\S+)/g;
    let lastIndex = 0;
    let match = null;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > lastIndex) {
        fragment.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
      }
      const span = document.createElement("span");
      span.className = "audio-word";
      span.textContent = match[0];
      fragment.appendChild(span);
      words.push({
        node: span,
        weight: Math.max(match[0].replace(/[^\p{L}\p{N}]+/gu, "").length, 1),
      });
      lastIndex = match.index + match[0].length;
    }
    if (lastIndex < text.length) {
      fragment.appendChild(document.createTextNode(text.slice(lastIndex)));
    }
    textNode.parentNode.replaceChild(fragment, textNode);
  }
  return words;
}

function bindPickerTargets(pickerPane, segments, onPick) {
  const byId = new Map(segments.map((segment) => [segment.textId, segment]));
  const candidates = pickerPane.querySelectorAll(SEGMENT_SELECTOR).length > 0
    ? pickerPane.querySelectorAll(SEGMENT_SELECTOR)
    : pickerPane.querySelectorAll("[id]");
  candidates.forEach((node) => {
    const segment = byId.get(node.id);
    if (!segment) {
      return;
    }
    node.classList.add("picker-target");
    node.addEventListener("click", () => onPick(segment));
  });
}

function applyWordHighlight(sourceId, currentTime, wordTimingsBySource) {
  for (const [audioSourceId, words] of wordTimingsBySource.entries()) {
    const isCurrentSource = audioSourceId === sourceId;
    for (const word of words) {
      const active = isCurrentSource && currentTime >= word.startTime && currentTime <= word.endTime;
      word.node.classList.toggle("audio-word-trace", active);
    }
  }
}

async function inspectEpub() {
  if (!state.epub || !state.out) {
    showWarning("Select an EPUB and an output folder before inspection.");
    return;
  }
  setStatus("Inspecting", "running");
  state.logs = [];
  $("logBox").value = "";
  try {
    state.inspection = await window.desktopBridge.inspect(getPayloadBase());
    initializeChapterState();
    renderPlanner();
    showWarning(
      state.inspection.has_encryption_xml
        ? "META-INF/encryption.xml was detected. Run this only on a decrypted, editable EPUB. DRM bypass is not supported."
        : ""
    );
    appendLog(`Loaded ${state.inspection.spine_items.length} spine XHTML file(s).`);
    appendLog("Use assignment rows to span one audio file across many chapters or split one chapter across several audio files.");
    setStatus("Ready", "idle");
  } catch (error) {
    appendLog(String(error.message || error));
    setStatus("Inspect Failed", "error");
  }
}

async function runBuild() {
  if (!state.inspection) {
    await inspectEpub();
    if (!state.inspection) {
      return;
    }
  }
  syncChapterConfigStateFromDom();
  syncAssignmentStateFromDom();
  setStatus("Running", "running");
  state.logs = [];
  $("logBox").value = "";
  $("exportSummary").textContent = "Build in progress...";
  try {
    const payload = Object.assign(getPayloadBase(), {
      chapters: state.chapterConfigs,
      assignments: state.assignments,
    });
    const response = await window.desktopBridge.runBuild(payload);
    state.result = response.result;
    renderExportSummary(state.result);
    if (state.result.warnings && state.result.warnings.length > 0) {
      showWarning(state.result.warnings.join(" "));
    }
    setStatus("Build Complete", "done");
  } catch (error) {
    appendLog(String(error.message || error));
    setStatus("Build Failed", "error");
  }
}

async function loadPreview() {
  if (!state.result || !state.result.preview || state.result.preview.length === 0) {
    return;
  }
  const index = Number($("previewSelect").value || "0");
  const chapter = state.result.preview[index];
  const preview = await window.desktopBridge.loadPreview({
    aeneas_repo_path: $("repoPathInput").value.trim(),
    result: state.result,
    chapter_root_href: chapter.chapter_root_href,
  });
  state.preview = preview;
  renderXhtmlFragment($("previewPane"), preview.xhtml);
  const firstSource = preview.audioSources[0];
  const audio = $("audioPlayer");
  if (firstSource) {
    setAudioSource(audio, firstSource);
  }
  hookPreviewInteractions(preview);
}

function showErrorPopup(message) {
  $("errorPopupMessage").textContent = message;
  $("errorPopup").classList.remove("hidden");
}

function closeErrorPopup(event) {
  if (!event || event.target === $("errorPopup") || event.target === $("errorPopupClose")) {
    $("errorPopup").classList.add("hidden");
  }
}

function closeSegmentPicker() {
  state.segmentPicker = null;
  $("segmentPickerModal").classList.add("hidden");
  $("segmentPickerPane").innerHTML = '<p class="muted">Load a chapter preview to pick a point.</p>';
}

async function openSegmentPicker(assignmentIndex, fieldKind) {
  if (!state.inspection) {
    return;
  }
  syncChapterConfigStateFromDom();
  syncAssignmentStateFromDom();
  const assignment = state.assignments[assignmentIndex];
  const chapterRootHref = fieldKind === "startSegment" ? assignment.startChapter : assignment.endChapter;
  const pickerMeta = `${chapterRootHref} • click a highlighted segment to set ${fieldKind === "startSegment" ? "the start point" : "the end point"}.`;
  let preview = null;
  let segments = null;
  try {
    preview = await window.desktopBridge.inspectPreview({
      aeneas_repo_path: $("repoPathInput").value.trim(),
      inspection: state.inspection,
      chapters: state.chapterConfigs,
      chapter_root_href: chapterRootHref,
    });
    segments = preview.segments;
    renderXhtmlFragment($("segmentPickerPane"), preview.xhtml);
  } catch (error) {
    appendLog(`Picker preview fallback for ${chapterRootHref}: ${error.message || error}`);
    const chapterPath = getChapterPath(chapterRootHref);
    if (!chapterPath) {
      throw error;
    }
    const rawXhtml = await window.desktopBridge.readTextFile(chapterPath);
    renderXhtmlFragment($("segmentPickerPane"), extractBodyHtml(rawXhtml));
    segments = buildFallbackPickerSegments($("segmentPickerPane"));
  }
  state.segmentPicker = { assignmentIndex, fieldKind, chapterRootHref, preview, segments };
  $("segmentPickerMeta").textContent = pickerMeta;
  bindPickerTargets($("segmentPickerPane"), segments, (segment) => {
    state.assignments[assignmentIndex][fieldKind] = segment.segmentIndex;
    renderAssignments();
    closeSegmentPicker();
  });
  $("segmentPickerModal").classList.remove("hidden");
}

async function chooseOutputFolder() {
  const selected = await window.desktopBridge.chooseOutputFolder();
  if (selected) {
    state.out = selected;
    refreshProjectSummary();
  }
}

function addAssignment() {
  if (!state.inspection || state.inspection.spine_items.length === 0) {
    return;
  }
  const fallbackChapter = state.inspection.spine_items[0].root_href;
  state.assignments.push(defaultAssignment(fallbackChapter, state.assignments.length));
  renderAssignments();
}

async function handleFindSync(assignmentIndex) {
  if (!state.inspection) {
    return;
  }
  syncChapterConfigStateFromDom();
  syncAssignmentStateFromDom();
  const assignment = state.assignments[assignmentIndex];
  if (!assignment.audio) {
    showWarning("Select an audio file before using Find Sync.");
    return;
  }
  const btn = document.querySelector(`[data-find-sync="${assignmentIndex}"]`);
  if (!btn) {
    return;
  }
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Searching...";
  let timedOut = false;
  const timeout = new Promise((_, reject) => setTimeout(() => { timedOut = true; reject(new Error("Request timed out (120s)")); }, 120000));
  try {
    // Send the previous assignment's end segment so find_sync can avoid overlap
    const prevEndSegment = assignmentIndex > 0
      ? (state.assignments[assignmentIndex - 1].endSegment || null)
      : null;
    const result = await Promise.race([
      window.desktopBridge.findSync({
        aeneas_repo_path: $("repoPathInput").value.trim(),
        inspection: state.inspection,
        chapters: state.chapterConfigs,
        audio: assignment.audio,
        startChapter: assignment.startChapter,
        endChapter: assignment.endChapter,
        language: $("languageInput").value.trim() || "eng",
        previousEndSegment: prevEndSegment,
      }),
      timeout,
    ]);
    if (result.startSegment) {
      state.assignments[assignmentIndex].startSegment = result.startSegment;
    }
    if (result.endSegment) {
      state.assignments[assignmentIndex].endSegment = result.endSegment;
    }
    renderAssignments();
    if (result.confidence === "low") {
      showErrorPopup("Find Sync result: start=" + (result.startSegment || "?") + ", end=" + (result.endSegment || "?") + " (low confidence)\n\nVerify the detected segments with Pick.");
    } else {
      showErrorPopup("Find Sync result: start=" + (result.startSegment || "?") + ", end=" + (result.endSegment || "?") + " (" + result.confidence + " confidence)");
    }
  } catch (error) {
    showErrorPopup("Find Sync could not determine segments.\n" + (error.message || error) + "\n\nTry using Pick manually.");
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

function handleTableClicks(event) {
  const pickIndex = event.target.getAttribute("data-pick-segment");
  if (pickIndex !== null) {
    openSegmentPicker(Number(pickIndex), event.target.getAttribute("data-pick-kind"));
    return;
  }
  const findSyncIndex = event.target.getAttribute("data-find-sync");
  if (findSyncIndex !== null) {
    handleFindSync(Number(findSyncIndex));
    return;
  }
  const removeIndex = event.target.getAttribute("data-remove-assignment");
  if (removeIndex === null) {
    return;
  }
  syncAssignmentStateFromDom();
  state.assignments.splice(Number(removeIndex), 1);
  renderAssignments();
}

function initEvents() {
  $("inspectButton").addEventListener("click", inspectEpub);
  $("runButton").addEventListener("click", runBuild);
  $("previewButton").addEventListener("click", loadPreview);
  $("chooseOutputButton").addEventListener("click", chooseOutputFolder);
  $("addAssignmentButton").addEventListener("click", addAssignment);
  $("testMfaButton").addEventListener("click", testMfaSettings);
  $("closeSegmentPickerButton").addEventListener("click", closeSegmentPicker);
  $("segmentPickerModal").addEventListener("click", (event) => {
    if (event.target === $("segmentPickerModal")) {
      closeSegmentPicker();
    }
  });
  $("errorPopupClose").addEventListener("click", closeErrorPopup);
  $("assignmentBody").addEventListener("click", handleTableClicks);

  window.desktopBridge.onMenuEpubSelected((filePath) => {
    state.epub = filePath;
    refreshProjectSummary();
  });
  window.desktopBridge.onMenuAudioSelected((filePaths) => {
    state.audioFiles = Array.from(new Set(state.audioFiles.concat(filePaths)));
    refreshProjectSummary();
    renderAssignments();
  });
  window.desktopBridge.onMenuOutputSelected((folderPath) => {
    state.out = folderPath;
    refreshProjectSummary();
  });
  window.desktopBridge.onMenuInspect(() => {
    inspectEpub();
  });
  window.desktopBridge.onMenuRun(() => {
    runBuild();
  });
  window.desktopBridge.onBuildLog((message) => {
    appendLog(message);
  });
  window.desktopBridge.onBuildError((message) => {
    appendLog(message);
    setStatus("Build Failed", "error");
  });
}

function boot() {
  refreshProjectSummary();
  renderPlanner();
  setStatus("Idle", "idle");
  initEvents();
  if (window.desktopBridge.getMfaExecutablePath) {
    window.desktopBridge.getMfaExecutablePath().then((p) => {
      if (p) { $("mfaExecutablePathInput").value = p; }
    });
  }
}

boot();
