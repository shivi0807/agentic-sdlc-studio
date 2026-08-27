(function () {
  "use strict";

  const navToggle = document.querySelector(".nav-toggle");
  const mobileNav = document.querySelector("#mobile-nav");
  if (navToggle && mobileNav) {
    navToggle.addEventListener("click", function () {
      const open = navToggle.getAttribute("aria-expanded") === "true";
      navToggle.setAttribute("aria-expanded", String(!open));
      mobileNav.hidden = open;
    });
  }

  document.querySelectorAll("[data-password-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      const input = document.getElementById(button.dataset.passwordToggle);
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.textContent = show ? "Hide" : "Show";
      button.setAttribute("aria-label", (show ? "Hide" : "Show") + " password");
    });
  });

  const requirements = document.querySelector("textarea[name='requirements']");
  const counter = document.querySelector("[data-char-count]");
  if (requirements && counter) {
    const updateCount = function () {
      counter.textContent = requirements.value.length.toLocaleString() + " / 12,000";
    };
    requirements.addEventListener("input", updateCount);
    updateCount();
  }

  const projectSearch = document.querySelector("[data-project-search]");
  if (projectSearch) {
    const cards = Array.from(document.querySelectorAll("[data-project-card]"));
    const empty = document.querySelector("[data-empty-search]");
    projectSearch.addEventListener("input", function () {
      const query = projectSearch.value.trim().toLowerCase();
      let shown = 0;
      cards.forEach(function (card) {
        const match = (card.dataset.searchText || "").toLowerCase().includes(query);
        card.hidden = !match;
        if (match) shown += 1;
      });
      if (empty) empty.hidden = shown > 0;
    });
  }

  document.querySelectorAll("[data-dialog-open]").forEach(function (button) {
    button.addEventListener("click", function () {
      const dialog = document.getElementById(button.dataset.dialogOpen);
      if (dialog && typeof dialog.showModal === "function") dialog.showModal();
    });
  });
  document.querySelectorAll("[data-dialog-close]").forEach(function (button) {
    button.addEventListener("click", function () {
      const dialog = button.closest("dialog");
      if (dialog) dialog.close();
    });
  });
  document.querySelectorAll("dialog").forEach(function (dialog) {
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
  });

  const copyButton = document.querySelector("[data-copy-url]");
  if (copyButton) {
    copyButton.addEventListener("click", async function () {
      try {
        await navigator.clipboard.writeText(window.location.href);
        const original = copyButton.textContent;
        copyButton.textContent = "Link copied";
        window.setTimeout(function () { copyButton.textContent = original; }, 1600);
      } catch (_) {
        copyButton.textContent = "Copy unavailable";
      }
    });
  }

  const liveRun = document.querySelector("[data-live-run]");
  if (liveRun) {
    const setText = function (selector, value) {
      const element = document.querySelector(selector);
      if (element) element.textContent = String(value);
    };
    const refreshCoordination = async function () {
      try {
        const response = await fetch("/api/runs/" + liveRun.dataset.liveRun, {
          credentials: "same-origin"
        });
        if (!response.ok) return;
        const run = await response.json();
        const tasks = run.tasks || [];
        const next = tasks.find(function (task) { return task.status === "queued"; });
        const retryExhausted = run.status === "changes_requested" && !next;
        const current = retryExhausted ? "Changes requested" : (run.current_agent || "Human approval");
        const nextLabel = retryExhausted ? "Human decision" : (next ? next.agent_role : "Release decision");
        const relationship = retryExhausted
          ? "Validation findings → human decision"
          : current + " → " + (next ? next.agent_role : "release");
        setText('[data-coordination="current"]', current);
        setText('[data-coordination="next"]', nextLabel);
        setText('[data-coordination="relationship"]', relationship);
        ["working", "queued", "completed"].forEach(function (taskStatus) {
          const count = tasks.filter(function (task) { return task.status === taskStatus; }).length;
          setText('[data-coordination="' + taskStatus + '"]', count);
        });
        const usage = run.usage_summary || {};
        setText('[data-usage="prompt"]', usage.prompt_tokens || 0);
        setText('[data-usage="completion"]', usage.completion_tokens || 0);
        setText('[data-usage="cost"]', usage.estimated_cost_usd == null ? "Not priced" : "$" + Number(usage.estimated_cost_usd).toFixed(4));
        if (usage.models && usage.models.length) {
          const model = usage.models[usage.models.length - 1];
          setText('[data-usage="model"]', model.provider + " · " + model.model);
        }
      } catch (_) {
        // Keep the last safe server-rendered state when polling is unavailable.
      }
    };
    window.setInterval(refreshCoordination, 5000);
  }
})();
