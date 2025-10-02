(function () {
  const existing = window.__CBGQuickRegen;
  if (existing && existing.initialized) {
    if (typeof existing.updateData === "function") {
      existing.updateData(window.__CBGQuickRegenData || null);
    }
    return;
  }

  const BUTTON_CLASS = "heading-regenerate";
  const BLOCK_CLASS = "cbg-quick-regen-block";
  const ANCHOR_SELECTOR = ".heading-input-anchor";
  const TOAST_CLASS = "quick-regen-toast";
  const config = { webhook: "", body: null, envelope: null };
  const anchorRegistry = new Map();
  const inflight = new Map();

  function deepClone(value) {
    if (value == null) {
      return value;
    }
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (err) {
      return value;
    }
  }

  function updateConfig(data) {
    if (!data || typeof data !== "object") {
      return;
    }
    if (typeof data.webhook === "string") {
      config.webhook = data.webhook;
    }
    if (data.body) {
      config.body = deepClone(data.body);
    }
    if (data.envelope) {
      config.envelope = deepClone(data.envelope);
    }
  }

  function cleanupAnchors() {
    anchorRegistry.forEach((anchor, key) => {
      if (!anchor || !anchor.isConnected) {
        anchorRegistry.delete(key);
      }
    });
  }

  function findMarkerBlock(anchor) {
    if (!anchor) return null;
    const markdownBlock = anchor.closest('[data-testid="stMarkdown"]');
    return markdownBlock || anchor.parentElement;
  }

  function findInputBlock(anchor) {
    const markerBlock = findMarkerBlock(anchor);
    let node = markerBlock;
    while (node) {
      let sibling = node.nextElementSibling;
      while (sibling) {
        if (sibling.matches && sibling.matches('[data-testid="stTextInput"]')) {
          return sibling;
        }
        if (sibling.querySelector && sibling.querySelector('input')) {
          return sibling;
        }
        sibling = sibling.nextElementSibling;
      }
      node = node.parentElement;
    }
    return null;
  }

  function getInputElement(block) {
    if (!block) return null;
    return block.querySelector('input');
  }

  function setLoading(button, loading) {
    if (!button) return;
    if (loading) {
      button.classList.add('is-loading');
      button.disabled = true;
    } else {
      button.classList.remove('is-loading');
      button.disabled = false;
    }
  }

  function showToast(message) {
    if (!message) return;
    const toast = document.createElement('div');
    toast.className = TOAST_CLASS;
    toast.textContent = message;
    document.body.appendChild(toast);
    requestAnimationFrame(() => {
      toast.classList.add('is-visible');
    });
    setTimeout(() => {
      toast.classList.remove('is-visible');
      setTimeout(() => {
        if (toast.parentNode) {
          toast.parentNode.removeChild(toast);
        }
      }, 260);
    }, 4000);
  }

  function generateRequestId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      return window.crypto.randomUUID();
    }
    return 'req-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  }

  function buildEnvelope(body) {
    if (!body) return null;
    const env = config.envelope ? deepClone(config.envelope) : {};
    const envelope = {
      headers: env && env.headers ? env.headers : {},
      params: env && env.params ? env.params : {},
      query: env && env.query ? env.query : {},
      body: body,
    };
    if (env && Object.prototype.hasOwnProperty.call(env, 'webhookUrl')) {
      envelope.webhookUrl = env.webhookUrl;
    }
    if (env && Object.prototype.hasOwnProperty.call(env, 'executionMode')) {
      envelope.executionMode = env.executionMode;
    }
    return [envelope];
  }

  function syncAnchor(anchor) {
    if (!anchor || !(anchor instanceof HTMLElement)) {
      return;
    }
    const anchorKey = anchor.dataset.anchorKey || anchor.dataset.headingId || '';
    if (anchorKey) {
      anchorRegistry.set(anchorKey, anchor);
    }
    const block = findInputBlock(anchor);
    if (!block) {
      return;
    }
    const locked = anchor.dataset.locked === 'true';
    const existingButton = block.querySelector('.' + BUTTON_CLASS);
    if (locked) {
      block.classList.remove(BLOCK_CLASS);
      if (existingButton) {
        existingButton.remove();
      }
      return;
    }
    block.classList.add(BLOCK_CLASS);
    let button = existingButton;
    if (!button) {
      button = document.createElement('button');
      button.type = 'button';
      button.className = BUTTON_CLASS;
      button.setAttribute('aria-label', 'Regenerate heading');
      button.title = 'Regenerate heading';
      button.textContent = '🔁';
      block.appendChild(button);
    }
    button.dataset.anchorKey = anchorKey;
    button.dataset.headingId = anchor.dataset.headingId || '';
    button.dataset.sectionPath = anchor.dataset.sectionPath || '';
    button.dataset.headingLevel = anchor.dataset.headingLevel || '';
  }

  function refreshAnchors() {
    cleanupAnchors();
    document.querySelectorAll(ANCHOR_SELECTOR).forEach(syncAnchor);
  }

  function handleClick(event) {
    const button = event.target.closest('.' + BUTTON_CLASS);
    if (!button) return;
    const anchorKey = button.dataset.anchorKey || button.dataset.headingId || '';
    const anchor = anchorKey ? anchorRegistry.get(anchorKey) : null;
    if (!anchor || !anchor.isConnected) {
      refreshAnchors();
      return;
    }
    if (anchor.dataset.locked === 'true') {
      return;
    }
    const block = findInputBlock(anchor);
    const input = getInputElement(block);
    if (!input) {
      showToast('Unable to locate heading input.');
      return;
    }
    if (!config.webhook) {
      showToast('Quick regenerate webhook is not configured.');
      return;
    }
    const baseBody = deepClone(config.body);
    if (!baseBody) {
      showToast('Unable to build request payload.');
      return;
    }
    const requestId = generateRequestId();
    const headingId = anchor.dataset.headingId || '';
    const sectionPath = anchor.dataset.sectionPath || '';
    const headingLevel = (anchor.dataset.headingLevel || '').toUpperCase();
    baseBody['Heading To Regenerate'] = input.value || '';
    if (headingId) {
      baseBody.heading_id = headingId;
    }
    if (sectionPath) {
      baseBody.section_path = sectionPath;
    }
    if (headingLevel) {
      baseBody.heading_level = headingLevel;
    }
    baseBody.request_id = requestId;
    baseBody.timestamp = Date.now();

    const payload = buildEnvelope(baseBody);
    if (!payload) {
      showToast('Unable to assemble request.');
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    setLoading(button, true);
    const inflightKey = headingId || sectionPath || requestId;
    inflight.set(inflightKey, requestId);

    fetch(config.webhook, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(async (response) => {
      const finish = () => {
        if (inflight.get(inflightKey) === requestId) {
          inflight.delete(inflightKey);
        }
        setLoading(button, false);
      };
      if (!response.ok) {
        let message = 'Failed to regenerate heading.';
        try {
          const errorData = await response.json();
          if (errorData && typeof errorData.error === 'string') {
            message = errorData.error;
          } else if (response.status) {
            message = `Failed to regenerate heading (HTTP ${response.status}).`;
          }
        } catch (err) {
          if (response.status) {
            message = `Failed to regenerate heading (HTTP ${response.status}).`;
          }
        }
        finish();
        showToast(message);
        return;
      }
      let data;
      try {
        data = await response.json();
      } catch (err) {
        finish();
        showToast('Received invalid response from automation.');
        return;
      }
      if (inflight.get(inflightKey) !== requestId) {
        finish();
        return;
      }
      if (data && data.request_id && data.request_id !== requestId) {
        finish();
        return;
      }
      if (data && data.heading_id && headingId && data.heading_id !== headingId) {
        finish();
        return;
      }
      if (data && data.section_path && sectionPath && data.section_path !== sectionPath) {
        finish();
        return;
      }
      if (!data || typeof data.new_heading !== 'string') {
        finish();
        showToast('Automation did not return a heading.');
        return;
      }
      const newHeading = data.new_heading;
      if (input.value !== newHeading) {
        input.value = newHeading;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
      finish();
    }).catch((error) => {
      if (inflight.get(inflightKey) === requestId) {
        inflight.delete(inflightKey);
      }
      setLoading(button, false);
      showToast('Unable to reach quick regenerate service.');
      console.error('Quick regenerate error:', error);
    });
  }

  function handleUpdate() {
    if (window.__CBGQuickRegenData) {
      updateConfig(window.__CBGQuickRegenData);
      refreshAnchors();
    }
  }

  document.addEventListener('click', handleClick, true);

  const observer = new MutationObserver(() => {
    refreshAnchors();
  });
  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (window.__CBGQuickRegenData) {
    updateConfig(window.__CBGQuickRegenData);
  }
  requestAnimationFrame(() => {
    refreshAnchors();
  });

  window.addEventListener('cbg:update-quick-regen', handleUpdate);

  window.__CBGQuickRegen = {
    initialized: true,
    refresh: refreshAnchors,
    updateData: (data) => {
      if (data) {
        updateConfig(data);
      } else if (window.__CBGQuickRegenData) {
        updateConfig(window.__CBGQuickRegenData);
      }
      refreshAnchors();
    },
    showToast: showToast,
  };
})();
