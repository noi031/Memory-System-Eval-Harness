export function createSystemActions({ api, state }) {
  async function stopAllTasks() {
    await api("/api/tasks/stop-all", {method: "POST", body: JSON.stringify({scope: "all"})});
    return {refresh: true};
  }

  async function openPath(path) {
    if (!path) throw new Error("没有可打开的路径");
    await api("/api/open-path", {method: "POST", body: JSON.stringify({path})});
  }

  async function saveLocomoQaConfig(config) {
    const account = state?.selectedAccount || "default";
    const payload = { account, config: {...(config || {})} };
    await api("/api/account-config", {method: "POST", body: JSON.stringify(payload)});
    return {refresh: true};
  }

  return {
    openPath,
    saveLocomoQaConfig,
    stopAllTasks,
  };
}
