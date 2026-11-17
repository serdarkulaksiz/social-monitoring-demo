// Dashboard actions: acknowledge, resolve, notes and labels. No login exists yet, so the
// person's name is typed once and kept in this browser; the server records it with
// every action. Every request carries the X-Dashboard-Action header, which other web
// sites cannot send (CSRF protection together with the server's Origin check).
(function () {
  "use strict";

  var ACTOR_KEY = "social-monitor-actor";
  var API_KEY_KEY = "social-monitor-api-key";
  var mode = document.body.getAttribute("data-actions");

  function storageGet(store, key) {
    try { return store.getItem(key) || ""; } catch (error) { return ""; }
  }
  function storageSet(store, key, value) {
    try { store.setItem(key, value); } catch (error) { /* storage can be unavailable; the field still works */ }
  }

  var actorInput = document.getElementById("actor-name");
  if (actorInput) {
    actorInput.value = storageGet(window.localStorage, ACTOR_KEY);
    actorInput.addEventListener("change", function () { storageSet(window.localStorage, ACTOR_KEY, actorInput.value.trim()); });
  }
  var keyInput = document.getElementById("api-key");
  if (keyInput) {
    keyInput.value = storageGet(window.sessionStorage, API_KEY_KEY);
    keyInput.addEventListener("change", function () { storageSet(window.sessionStorage, API_KEY_KEY, keyInput.value); });
  }

  var flash = document.getElementById("flash");
  function show(message, isError) {
    if (!flash) { return; }
    flash.textContent = message;
    flash.className = isError ? "flash error" : "flash";
    flash.hidden = false;
  }

  function post(url, body) {
    var headers = { "Content-Type": "application/json", "X-Dashboard-Action": "1" };
    if (mode === "api_key" && keyInput) { headers["X-API-Key"] = keyInput.value; }
    return fetch(url, { method: "POST", headers: headers, body: JSON.stringify(body), credentials: "same-origin" })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (!response.ok) {
            var message = data && data.error && data.error.message ? data.error.message : "HTTP " + response.status;
            throw new Error(message);
          }
          return data;
        });
      });
  }

  function actor() {
    var name = actorInput ? actorInput.value.trim() : "";
    if (!name) {
      show("Önce sağ üstteki 'İşlemi yapan' alanına adınızı yazın.", true);
      if (actorInput) { actorInput.focus(); }
      return null;
    }
    storageSet(window.localStorage, ACTOR_KEY, name);
    return name;
  }

  function incidentRequest(button, name) {
    var box = button.closest("[data-incident]");
    var id = encodeURIComponent(box.getAttribute("data-incident"));
    var noteField = document.getElementById("incident-note");
    var note = noteField ? noteField.value.trim() : "";
    var action = button.getAttribute("data-action");
    if (action === "note") {
      if (!note) { show("Eklemek için not alanına bir şey yazın.", true); return null; }
      return { url: "/dashboard/actions/incidents/" + id + "/notes", body: { actor: name, text: note }, done: "Not eklendi." };
    }
    var body = { actor: name };
    if (note) { body.note = note; }
    var label = action === "acknowledge" ? "Olay onaylandı." : "Olay çözüldü olarak kapatıldı.";
    return { url: "/dashboard/actions/incidents/" + id + "/" + action, body: body, done: label };
  }

  function feedbackRequest(button, name) {
    var box = button.closest("[data-post]");
    var id = encodeURIComponent(box.getAttribute("data-post"));
    var body = { verdict: button.getAttribute("data-verdict"), actor: name, sample: box.getAttribute("data-sample") || "other" };
    if (button.getAttribute("data-clear-risk") === "1") { body.risk_signals = []; }
    if (body.verdict === "complaint") {
      body.service_category = box.querySelector("select.service").value;
      body.issue_category = box.querySelector("select.issue").value;
    }
    return { url: "/dashboard/actions/posts/" + id + "/feedback", body: body, done: "Etiket kaydedildi." };
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("button[data-action]");
    if (!button) { return; }
    var name = actor();
    if (!name) { return; }
    var request = button.getAttribute("data-action") === "feedback" ? feedbackRequest(button, name) : incidentRequest(button, name);
    if (!request) { return; }
    button.disabled = true;
    post(request.url, request.body)
      .then(function () {
        show(request.done + " Sayfa yenileniyor…", false);
        window.setTimeout(function () { window.location.reload(); }, 700);
      })
      .catch(function (error) {
        show("İşlem yapılamadı: " + error.message, true);
        button.disabled = false;
      });
  });

  // Pages with data-refresh reload themselves, but never while someone is typing or choosing.
  var refresh = parseInt(document.body.getAttribute("data-refresh") || "0", 10);
  if (refresh > 0) {
    window.setInterval(function () {
      var active = document.activeElement;
      var busy = active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.tagName === "SELECT");
      var open = document.querySelector("details[open]");
      if (!busy && !open) { window.location.reload(); }
    }, refresh * 1000);
  }
})();
