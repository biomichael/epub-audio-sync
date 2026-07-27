#!/usr/bin/env python
# coding=utf-8

from __future__ import annotations

import threading
import uuid

from .pipeline import EPUBAudioSyncBuilder


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>EPUB Audio Sync Builder</title>
  <style>
    :root {
      --bg: #f4f0e7;
      --panel: #fffaf1;
      --ink: #1f1f1f;
      --accent: #7a2e1f;
      --line: #d8c7ad;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Georgia, "Times New Roman", serif; color: var(--ink); background: linear-gradient(180deg, #efe4d2, var(--bg)); }
    header { padding: 24px 28px 8px; }
    h1 { margin: 0 0 6px; font-size: 32px; }
    main { display: grid; grid-template-columns: 420px 1fr; gap: 18px; padding: 0 28px 28px; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 16px; box-shadow: 0 12px 32px rgba(77, 52, 24, 0.08); }
    label { display: block; font-size: 13px; margin-bottom: 10px; }
    input[type=text], input[type=number], select { width: 100%; padding: 10px 12px; border-radius: 10px; border: 1px solid #c9b99f; background: #fff; }
    textarea { width: 100%; min-height: 180px; padding: 10px 12px; border-radius: 10px; border: 1px solid #c9b99f; background: #fffdf8; font-family: Consolas, monospace; }
    button { border: 0; border-radius: 999px; padding: 10px 16px; background: var(--accent); color: white; cursor: pointer; }
    button.secondary { background: #725f49; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { text-align: left; border-bottom: 1px solid #e7d9c2; padding: 8px 6px; vertical-align: top; }
    .stack { display: grid; gap: 12px; }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; }
    .muted { color: #6a6256; font-size: 13px; }
    .preview-shell { min-height: 420px; border: 1px solid #dbc8ae; border-radius: 12px; background: white; padding: 18px; overflow: auto; line-height: 1.65; }
    .audio-active { background-color: rgba(255, 230, 120, 0.65); border-radius: 0.15em; }
    ul.paths { margin: 0; padding-left: 18px; }
  </style>
</head>
<body>
  <header>
    <h1>EPUB Audio Sync Builder</h1>
    <div class="muted">Local inspection, alignment, preview, and EPUB 3 media overlay export.</div>
  </header>
  <main>
    <div class="stack">
      <section>
        <h2>Project Setup</h2>
        <label>EPUB path<input id="epub" type="text" placeholder="C:\\books\\book.epub"></label>
        <label>Audio file paths, one per line<textarea id="audioFiles" placeholder="C:\\audio\\chapter01.mp3&#10;C:\\audio\\chapter02.mp3"></textarea></label>
        <label>aeneas repo path<input id="repo" type="text" placeholder="C:\\Projects\\aeneas"></label>
        <label>Output folder<input id="out" type="text" placeholder="C:\\output\\book-sync"></label>
        <label>Language code<input id="language" type="text" value="eng"></label>
        <label>Audio mode
          <select id="audioMode">
            <option value="external">external audio</option>
            <option value="embedded">embedded audio</option>
          </select>
        </label>
        <label>Alignment engine
          <select id="alignmentEngine">
            <option value="aeneas">aeneas</option>
            <option value="mfa">MFA</option>
          </select>
        </label>
        <h2>MFA Settings</h2>
        <div class="muted">Stored now for future MFA support. The current build still runs aeneas.</div>
        <label>MFA executable path<input id="mfaExecutablePath" type="text" placeholder="C:\\Program Files\\MFA\\montreal-forced-aligner.exe"></label>
        <label>Acoustic model<input id="mfaAcousticModel" type="text" placeholder="english_us_arpa"></label>
        <label>Dictionary or model<input id="mfaDictionary" type="text" placeholder="C:\\models\\dict.txt or english_us_arpa"></label>
        <label>MFA output folder<input id="mfaOutputFolder" type="text" placeholder="Uses the build output folder"></label>
        <label>Temporary corpus folder<input id="mfaCorpusFolder" type="text" placeholder="Uses output\\mfa-corpus"></label>
        <label>Audio conversion
          <select id="mfaAudioConversion">
            <option value="none">no conversion</option>
            <option value="wav">convert to WAV</option>
          </select>
        </label>
        <label><input id="mfaProofMode" type="checkbox"> Validate MFA only</label>
        <div class="toolbar">
          <button id="inspectBtn">Inspect EPUB</button>
          <button id="runBtn" class="secondary">Run Build</button>
          <button id="proofBtn" class="secondary">Test MFA Settings</button>
        </div>
        <div class="muted" id="warningBox"></div>
      </section>
      <section>
        <h2>Chapter Mapping</h2>
        <div id="mappingWrap" class="muted">Inspect an EPUB to load spine XHTML files.</div>
      </section>
      <section>
        <h2>Alignment</h2>
        <textarea id="logBox" readonly></textarea>
      </section>
      <section>
        <h2>Export</h2>
        <div id="exportBox" class="muted">No build has completed yet.</div>
      </section>
    </div>
    <div class="stack">
      <section>
        <h2>Preview</h2>
        <div class="toolbar">
          <select id="previewSelect"></select>
          <button id="loadPreviewBtn">Load Preview</button>
        </div>
        <audio id="audioPlayer" controls style="width: 100%; margin: 12px 0;"></audio>
        <div id="preview" class="preview-shell"></div>
      </section>
    </div>
  </main>
  <script>
    const state = { inspection: null, jobId: null, job: null, preview: null };

    function getAudioFiles() {
      return document.getElementById("audioFiles").value.split(/\\r?\\n/).map(v => v.trim()).filter(Boolean);
    }

    function getConfig() {
      return {
        epub: document.getElementById("epub").value.trim(),
        audio_files: getAudioFiles(),
        aeneas_repo_path: document.getElementById("repo").value.trim(),
        out: document.getElementById("out").value.trim(),
        language: document.getElementById("language").value.trim() || "eng",
        external_audio: document.getElementById("audioMode").value === "external",
        alignment_engine: document.getElementById("alignmentEngine").value,
        mfa: {
          executable_path: document.getElementById("mfaExecutablePath").value.trim(),
          acoustic_model: document.getElementById("mfaAcousticModel").value.trim(),
          dictionary: document.getElementById("mfaDictionary").value.trim(),
          output_folder: document.getElementById("mfaOutputFolder").value.trim(),
          corpus_dir: document.getElementById("mfaCorpusFolder").value.trim(),
          audio_conversion: document.getElementById("mfaAudioConversion").value,
          proof_mode: document.getElementById("mfaProofMode").checked
        }
      };
    }

    function renderMappings(items, audioFiles) {
      if (!items.length) {
        document.getElementById("mappingWrap").innerHTML = "<div class='muted'>No XHTML spine files were found.</div>";
        return;
      }
      let html = "<table><thead><tr><th>Chapter</th><th>Audio</th><th>Segment</th></tr></thead><tbody>";
      for (const item of items) {
        html += `<tr>
          <td>${item.root_href}</td>
          <td><select data-kind="audio" data-chapter="${item.root_href}"><option value="">Skip</option>${audioFiles.map(a => `<option value="${a}">${a}</option>`).join("")}</select></td>
          <td><select data-kind="segment" data-chapter="${item.root_href}"><option value="sentence">sentence</option><option value="paragraph">paragraph</option></select></td>
        </tr>`;
      }
      html += "</tbody></table>";
      document.getElementById("mappingWrap").innerHTML = html;
    }

    function collectMappings() {
      if (!state.inspection) return [];
      return state.inspection.spine_items.map(item => {
        const audio = document.querySelector(`select[data-kind="audio"][data-chapter="${CSS.escape(item.root_href)}"]`)?.value || "";
        const segment = document.querySelector(`select[data-kind="segment"][data-chapter="${CSS.escape(item.root_href)}"]`)?.value || "sentence";
        return { chapter: item.root_href, audio, segment };
      });
    }

    async function inspect() {
      const response = await fetch("/api/inspect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(getConfig())
      });
      const data = await response.json();
      state.inspection = data;
      renderMappings(data.spine_items || [], getAudioFiles());
      document.getElementById("warningBox").textContent = data.has_encryption_xml ? "Warning: encryption.xml detected. Use a decrypted editable EPUB only." : "";
      document.getElementById("logBox").value = JSON.stringify(data, null, 2);
    }

    function renderExport(result) {
      document.getElementById("exportBox").innerHTML = `
        <ul class="paths">
          <li>Patched EPUB: ${result.patched_epub}</li>
          <li>JSON folder: ${result.json_dir}</li>
          <li>SMIL folder: ${result.smil_dir}</li>
          <li>Debug log: ${result.debug_log}</li>
        </ul>`;
      const select = document.getElementById("previewSelect");
      select.innerHTML = "";
      result.preview.forEach((item, index) => {
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = item.chapter_href;
        select.appendChild(option);
      });
    }

    async function runBuild() {
      const payload = Object.assign(getConfig(), {
        mappings: collectMappings()
      });
      const response = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      state.jobId = data.job_id;
      pollJob();
    }

    async function proofMfa() {
      const payload = getConfig();
      payload.mfa.proof_mode = true;
      const response = await fetch("/api/proof-mfa", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      document.getElementById("logBox").value = JSON.stringify(data, null, 2);
      document.getElementById("exportBox").textContent = data.mfa_proof && data.mfa_proof.success ? "MFA proof succeeded" : (data.mfa_proof ? data.mfa_proof.stderr || data.mfa_proof.stdout || "MFA proof failed" : data.error || "MFA proof failed");
    }

    async function pollJob() {
      if (!state.jobId) return;
      const response = await fetch(`/api/jobs/${state.jobId}`);
      const data = await response.json();
      state.job = data;
      document.getElementById("logBox").value = (data.logs || []).join("\\n");
      if (data.status === "complete") {
        renderExport(data.result);
      } else if (data.status === "failed") {
        document.getElementById("exportBox").textContent = data.error;
      } else {
        setTimeout(pollJob, 1000);
      }
    }

    async function loadPreview() {
      if (!state.jobId) return;
      const index = document.getElementById("previewSelect").value || "0";
      const response = await fetch(`/api/jobs/${state.jobId}/preview/${index}`);
      const data = await response.json();
      state.preview = data;
      document.getElementById("audioPlayer").src = data.audioUrl;
      document.getElementById("preview").innerHTML = data.xhtml;
      hookPreview(data);
    }

    function hookPreview(data) {
      const audio = document.getElementById("audioPlayer");
      const byId = new Map(data.segments.map(s => [s.textId, s]));
      document.querySelectorAll("#preview [id]").forEach(node => {
        const segment = byId.get(node.id);
        if (!segment) return;
        node.style.cursor = "pointer";
        node.addEventListener("click", () => {
          audio.currentTime = segment.begin;
          audio.play();
        });
      });
      audio.ontimeupdate = () => {
        let activeId = null;
        for (const segment of data.segments) {
          if (audio.currentTime >= segment.begin && audio.currentTime <= segment.end) {
            activeId = segment.textId;
            break;
          }
        }
        document.querySelectorAll("#preview .audio-active").forEach(node => node.classList.remove("audio-active"));
        if (activeId) {
          const target = document.getElementById(activeId);
          if (target) target.classList.add("audio-active");
        }
      };
    }

    document.getElementById("inspectBtn").addEventListener("click", inspect);
    document.getElementById("runBtn").addEventListener("click", runBuild);
    document.getElementById("proofBtn").addEventListener("click", proofMfa);
    document.getElementById("loadPreviewBtn").addEventListener("click", loadPreview);
  </script>
</body>
</html>
"""


class JobStore(object):
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def create(self, payload):
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {"status": "queued", "logs": [], "payload": payload, "result": None, "error": None}
        return job_id

    def update(self, job_id, **kwargs):
        with self._lock:
            self._jobs[job_id].update(kwargs)

    def append_log(self, job_id, message):
        with self._lock:
            self._jobs[job_id]["logs"].append(message)

    def get(self, job_id):
        with self._lock:
            return dict(self._jobs[job_id])


def create_app(aeneas_repo_path=None):
    try:
        from fastapi import FastAPI
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    except ImportError as exc:
        raise RuntimeError("FastAPI and Uvicorn are required for the web UI") from exc

    app = FastAPI(title="EPUB Audio Sync Builder")
    jobs = JobStore()

    @app.get("/", response_class=HTMLResponse)
    def home():
        return HTML

    @app.post("/api/inspect")
    def inspect(payload: dict):
        builder = EPUBAudioSyncBuilder(aeneas_repo_path=payload.get("aeneas_repo_path"))
        inspection = builder.inspect_epub(payload["epub"], payload["out"])
        return JSONResponse(inspection)

    @app.post("/api/run")
    def run(payload: dict):
        job_id = jobs.create(payload)

        def worker():
            builder = EPUBAudioSyncBuilder(aeneas_repo_path=payload.get("aeneas_repo_path"), logger=lambda msg: jobs.append_log(job_id, msg))
            jobs.update(job_id, status="running")
            try:
                result = builder.run(payload)
                jobs.update(job_id, status="complete", result=result)
            except Exception as exc:
                jobs.append_log(job_id, str(exc))
                jobs.update(job_id, status="failed", error=str(exc))

        threading.Thread(target=worker, daemon=True).start()
        return {"job_id": job_id}

    @app.post("/api/proof-mfa")
    def proof_mfa(payload: dict):
        builder = EPUBAudioSyncBuilder(aeneas_repo_path=payload.get("aeneas_repo_path"))
        try:
            result = builder.run(payload)
            return JSONResponse(result)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return jobs.get(job_id)

    @app.get("/api/jobs/{job_id}/preview/{index}")
    def preview(job_id: str, index: int):
        job = jobs.get(job_id)
        if job.get("result") is None:
            return JSONResponse({"error": "Job has not completed"}, status_code=409)
        preview_items = job["result"]["preview"]
        if index < 0 or index >= len(preview_items):
            return JSONResponse({"error": "Preview chapter index out of range"}, status_code=404)
        chapter = preview_items[index]
        builder = EPUBAudioSyncBuilder(aeneas_repo_path=aeneas_repo_path)
        payload = builder.preview_payload(job["result"], chapter["chapter_root_href"])
        payload["audioUrl"] = "/api/jobs/%s/audio/%d" % (job_id, index)
        return payload

    @app.get("/api/jobs/{job_id}/audio/{index}")
    def audio(job_id: str, index: int):
        job = jobs.get(job_id)
        preview_items = job.get("result", {}).get("preview", [])
        if index < 0 or index >= len(preview_items):
            return JSONResponse({"error": "Audio index out of range"}, status_code=404)
        return FileResponse(preview_items[index]["audio_path"])

    return app


def run_server(host="127.0.0.1", port=8765, aeneas_repo_path=None):
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn is required to serve the web UI") from exc
    app = create_app(aeneas_repo_path=aeneas_repo_path)
    uvicorn.run(app, host=host, port=port)
