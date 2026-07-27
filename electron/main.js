const { app, BrowserWindow, Menu, dialog, ipcMain } = require("electron");
const path = require("path");
const { spawn, execSync } = require("child_process");
const fs = require("fs");

const projectRoot = path.resolve(__dirname, "..");
const rendererPath = path.join(__dirname, "renderer", "index.html");
const appIconCandidates = [
  path.join(projectRoot, "audiobook.ico"),
  path.join(projectRoot, "audiobook.png"),
];

function resolveAppIcon() {
  return appIconCandidates.find((candidate) => fs.existsSync(candidate)) || appIconCandidates[appIconCandidates.length - 1];
}

function findBundledMfaArchive() {
  const candidates = [
    path.join(__dirname, "..", "mfa", "mfa_env.tar.gz"),
    path.join(process.resourcesPath, "app", "mfa", "mfa_env.tar.gz"),
  ];
  return candidates.find((c) => fs.existsSync(c)) || null;
}

function extractMfaArchive(archivePath) {
  const mfaDir = path.join(app.getPath("temp"), "epub-audio-sync", "mfa_env");
  if (fs.existsSync(mfaDir)) {
    return mfaDir;
  }
  fs.mkdirSync(mfaDir, { recursive: true });
  execSync(`tar -xzf "${archivePath}" -C "${mfaDir}"`, {
    stdio: "ignore",
    timeout: 120000,
  });
  const condaUnpack = path.join(mfaDir, "conda-unpack.exe");
  if (fs.existsSync(condaUnpack)) {
    execSync(`"${condaUnpack}"`, { cwd: mfaDir, stdio: "ignore", timeout: 30000 });
  }
  const mfaExe = path.join(mfaDir, "Scripts", "mfa.exe");
  if (!fs.existsSync(mfaExe)) {
    console.error("MFA executable not found after extraction:", mfaExe);
    fs.rmSync(mfaDir, { recursive: true, force: true });
    return null;
  }
  return mfaDir;
}

function resolveBackendCommand() {
  if (process.env.AENEAS_BACKEND_EXE) {
    return {
      executable: process.env.AENEAS_BACKEND_EXE,
      args: [],
    };
  }
  const packagedBackend = path.join(projectRoot, "backend", "desktop_api.exe");
  if (require("fs").existsSync(packagedBackend)) {
    return {
      executable: packagedBackend,
      args: [],
    };
  }
  return {
    executable: process.env.PYTHON || "python",
    args: ["-m", "aeneas.epubsync.desktop_api"],
  };
}

let mainWindow = null;

function sendToRenderer(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1180,
    minHeight: 780,
    backgroundColor: "#eadfce",
    icon: resolveAppIcon(),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile(rendererPath);
}

async function selectSingleFile(filters) {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openFile"],
    filters,
  });
  if (!result.canceled && result.filePaths.length > 0) {
    return result.filePaths[0];
  }
  return null;
}

async function selectMultipleAudioFiles() {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openFile", "multiSelections"],
    filters: [
      { name: "Audio", extensions: ["mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "mp4"] },
      { name: "All Files", extensions: ["*"] },
    ],
  });
  return result.canceled ? [] : result.filePaths;
}

async function selectDirectory() {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openDirectory", "createDirectory"],
  });
  if (!result.canceled && result.filePaths.length > 0) {
    return result.filePaths[0];
  }
  return null;
}

function buildMenu() {
  const template = [
    {
      label: "File",
      submenu: [
        {
          label: "Add EPUB",
          accelerator: "CmdOrCtrl+O",
          click: async () => {
            const filePath = await selectSingleFile([{ name: "EPUB", extensions: ["epub"] }]);
            if (filePath) {
              sendToRenderer("menu:epub-selected", filePath);
            }
          },
        },
        {
          label: "Add Audio Files",
          accelerator: "CmdOrCtrl+Shift+O",
          click: async () => {
            const filePaths = await selectMultipleAudioFiles();
            if (filePaths.length > 0) {
              sendToRenderer("menu:audio-selected", filePaths);
            }
          },
        },
        {
          label: "Choose Output Folder",
          accelerator: "CmdOrCtrl+Shift+S",
          click: async () => {
            const folderPath = await selectDirectory();
            if (folderPath) {
              sendToRenderer("menu:output-selected", folderPath);
            }
          },
        },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    {
      label: "Actions",
      submenu: [
        {
          label: "Inspect EPUB",
          accelerator: "F5",
          click: () => sendToRenderer("menu:inspect"),
        },
        {
          label: "Run Build",
          accelerator: "CmdOrCtrl+Enter",
          click: () => sendToRenderer("menu:run"),
        },
      ],
    },
    {
      label: "View",
      submenu: [{ role: "reload" }, { role: "toggledevtools" }, { role: "resetzoom" }, { role: "zoomin" }, { role: "zoomout" }],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function runPythonCommand(command, payload, { onLine } = {}) {
  return new Promise((resolve, reject) => {
    const backend = resolveBackendCommand();
    const child = spawn(
      backend.executable,
      backend.args.concat(command),
      {
        cwd: projectRoot,
        stdio: ["pipe", "pipe", "pipe"],
        env: Object.assign({}, process.env, {
          AENEAS_FFMPEG_DIR: path.join(projectRoot, "backend", "bin"),
          MFA_EXECUTABLE_PATH: global.mfaExePath || "",
        }),
      }
    );
    let stdoutBuffer = "";
    let stderr = "";
    let finalResult = null;
    child.stdout.on("data", (chunk) => {
      stdoutBuffer += chunk.toString();
      const lines = stdoutBuffer.split(/\r?\n/);
      stdoutBuffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) {
          continue;
        }
        let parsed = null;
        try {
          parsed = JSON.parse(line);
        } catch (error) {
          if (onLine) {
            onLine({ type: "log", message: line });
          }
          continue;
        }
        if (onLine) {
          onLine(parsed);
        }
        if (parsed.type === "result") {
          finalResult = parsed.data;
        }
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("close", (code) => {
      if (code !== 0 && finalResult === null) {
        reject(new Error(stderr.trim() || `Python command failed with exit code ${code}`));
        return;
      }
      resolve(finalResult);
    });
    child.on("error", reject);
    child.stdin.write(JSON.stringify(payload));
    child.stdin.end();
  });
}

ipcMain.handle("dialog:select-output", async () => selectDirectory());
ipcMain.handle("file:read-text", async (_event, filePath) => fs.promises.readFile(filePath, "utf8"));
ipcMain.handle("env:mfa-executable-path", () => global.mfaExePath || "");

ipcMain.handle("backend:inspect", async (_event, payload) => runPythonCommand("inspect", payload));
ipcMain.handle("backend:inspect-preview", async (_event, payload) => runPythonCommand("inspect_preview", payload));
ipcMain.handle("backend:proof-mfa", async (_event, payload) => runPythonCommand("proof_mfa", payload));
ipcMain.handle("backend:find-sync", async (_event, payload) => {
  const result = await runPythonCommand("find_sync", payload, {
    onLine: (event) => {
      if (event.type === "log") {
        sendToRenderer("build:log", event.message);
      } else if (event.type === "error") {
        sendToRenderer("build:error", event.message);
      }
    },
  });
  return result;
});

ipcMain.handle("backend:preview", async (_event, payload) => runPythonCommand("preview", payload));

ipcMain.handle("backend:run", async (_event, payload) => {
  const logs = [];
  const result = await runPythonCommand("run", payload, {
    onLine: (event) => {
      if (event.type === "log") {
        logs.push(event.message);
        sendToRenderer("build:log", event.message);
      } else if (event.type === "error") {
        sendToRenderer("build:error", event.message);
      }
    },
  });
  return { result, logs };
});

app.whenReady().then(() => {
  app.setName("EPUB Audio Sync Builder");
  app.setAppUserModelId("com.epubaudiosync.builder");
  const mfaArchive = findBundledMfaArchive();
  if (mfaArchive) {
    const mfaDir = extractMfaArchive(mfaArchive);
    if (mfaDir) {
      global.mfaExePath = path.join(mfaDir, "Scripts", "mfa.exe");
      console.log("Bundled MFA detected at:", global.mfaExePath);
    }
  }
  createWindow();
  buildMenu();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
