export function $(id) {
  return document.getElementById(id);
}

export function currentSearchParams() {
  return new URLSearchParams(window.location.search);
}

export function globalValue(key) {
  return window[key];
}

export function localStorageAdapter() {
  try {
    return window.localStorage || null;
  } catch (_) {
    return null;
  }
}

export function metaContent(name) {
  return document.querySelector(`meta[name="${name}"]`)?.getAttribute("content") || "";
}

export function queryAll(selector) {
  return document.querySelectorAll(selector);
}

export function onDocument(eventName, handler) {
  document.addEventListener(eventName, handler);
}

export function alertUser(message) {
  const toast = document.getElementById("wbGlobalAlert");
  if (!toast) {
    window.alert(message);
    return;
  }
  window.clearTimeout(toast._hideTimer);
  toast.textContent = String(message || "操作失败");
  toast.hidden = false;
  toast.classList.add("visible");
  toast._hideTimer = window.setTimeout(() => {
    toast.classList.remove("visible");
    toast.hidden = true;
  }, 9000);
}

export async function copyText(text) {
  const value = String(text || "");
  if (window.navigator?.clipboard?.writeText) {
    await window.navigator.clipboard.writeText(value);
    return true;
  }
  const area = document.createElement("textarea");
  area.value = value;
  area.setAttribute("readonly", "readonly");
  area.style.position = "absolute";
  area.style.left = "-9999px";
  document.body.appendChild(area);
  area.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(area);
  return ok;
}

export function openReferenceUrl(url) {
  window.open(url, "_blank", "noopener,noreferrer");
}

export function clearTimer(timerId) {
  window.clearTimeout(timerId);
}

export function delay(callback, timeoutMs) {
  return window.setTimeout(callback, timeoutMs);
}
