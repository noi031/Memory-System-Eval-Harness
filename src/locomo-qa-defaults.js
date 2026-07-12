function normalizeLocomoAccountConfig(config = {}) {
  return { ...(config || {}) };
}

function normalizeLocomoQaForm(form = {}) {
  return { ...(form || {}) };
}

export { normalizeLocomoAccountConfig, normalizeLocomoQaForm };
