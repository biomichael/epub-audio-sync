const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopBridge", {
  getMfaExecutablePath: () => ipcRenderer.invoke("env:mfa-executable-path"),
  inspect: (payload) => ipcRenderer.invoke("backend:inspect", payload),
  findSync: (payload) => ipcRenderer.invoke("backend:find-sync", payload),
  inspectPreview: (payload) => ipcRenderer.invoke("backend:inspect-preview", payload),
  runBuild: (payload) => ipcRenderer.invoke("backend:run", payload),
  proofMfa: (payload) => ipcRenderer.invoke("backend:proof-mfa", payload),
  loadPreview: (payload) => ipcRenderer.invoke("backend:preview", payload),
  chooseOutputFolder: () => ipcRenderer.invoke("dialog:select-output"),
  readTextFile: (filePath) => ipcRenderer.invoke("file:read-text", filePath),
  onMenuEpubSelected: (handler) => ipcRenderer.on("menu:epub-selected", (_event, path) => handler(path)),
  onMenuAudioSelected: (handler) => ipcRenderer.on("menu:audio-selected", (_event, paths) => handler(paths)),
  onMenuOutputSelected: (handler) => ipcRenderer.on("menu:output-selected", (_event, path) => handler(path)),
  onMenuInspect: (handler) => ipcRenderer.on("menu:inspect", handler),
  onMenuRun: (handler) => ipcRenderer.on("menu:run", handler),
  onBuildLog: (handler) => ipcRenderer.on("build:log", (_event, message) => handler(message)),
  onBuildError: (handler) => ipcRenderer.on("build:error", (_event, message) => handler(message)),
});
