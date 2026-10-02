"use strict";
document.addEventListener("DOMContentLoaded", async () => {
  const status = document.getElementById("gmail-status");
  const connect = document.getElementById("gmail-connect");
  const disconnect = document.getElementById("gmail-disconnect");
  const refreshAccess = document.getElementById("gmail-refresh");
  const callback = document.getElementById("gmail-callback");
  const syncForm = document.getElementById("gmail-sync-form");
  const next = document.getElementById("gmail-next");
  const inbox = document.getElementById("gmail-inbox");
  let nextToken = "", batchQuery = "", syncing = false;
  const query = new URLSearchParams(location.search).get("gmail");
  if (query) {
    document.getElementById("gmail-result").textContent = query === "connected"
      ? "Google authorization completed. You can now manually sync message previews. Sending remains disabled."
      : "Connection was not saved. Check the callback URL, Gmail API, test-user access, and selected mailbox, then try again.";
    history.replaceState(null, "", "/");
  }
  async function refresh() {
    const response = await fetch("/api/gmail/status");
    if (!response.ok) throw new Error("Connection status unavailable");
    const data = await response.json();
    callback.textContent = data.redirect_uri;
    status.textContent = data.connected
      ? "Authorized mailbox: " + data.email + (data.access_token_expired ? " · Access token expired; refresh access or reconnect." : " · Read-only access.")
      : data.configured ? "Ready for Google authorization: " + data.expected_email : "Google credentials or expected mailbox are missing from private configuration.";
    connect.disabled = !data.configured;
    disconnect.hidden = !data.connected;
    refreshAccess.hidden = !data.connected;
    syncForm.hidden = !data.connected;
  }
  async function post(path, data = {}) {
    const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Connection action failed");
    return result;
  }
  connect.addEventListener("click", async () => {
    connect.disabled = true;
    try {
      const result = await post("/api/gmail/connect");
      const url = new URL(result.authorization_url);
      if (url.origin !== "https://accounts.google.com") throw new Error("Unexpected authorization destination");
      location.assign(url.href);
    } catch (error) { status.textContent = error.message; connect.disabled = false; }
  });
  disconnect.addEventListener("click", async () => {
    try { await post("/api/gmail/disconnect"); await refresh(); }
    catch (error) { status.textContent = error.message; }
  });
  refreshAccess.addEventListener("click", async () => {
    refreshAccess.disabled = true;
    try {
      await post("/api/gmail/refresh");
      await refresh();
      document.getElementById("gmail-result").textContent = "Gmail access refreshed. Sending remains disabled.";
    } catch (error) {
      status.textContent = error.message + " If the grant expired or was revoked, reconnect Gmail.";
    } finally { refreshAccess.disabled = false; }
  });
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  async function renderInbox() {
    const responses = await Promise.all([fetch("/api/gmail/inbox"), fetch("/api/state")]);
    if (responses.some(response => !response.ok)) throw new Error("Saved previews unavailable");
    const [data, state] = await Promise.all(responses.map(response => response.json()));
    document.getElementById("gmail-inbox-count").textContent = "Showing " + data.messages.length + " of " + data.total + " saved previews.";
    inbox.replaceChildren();
    for (const message of data.messages) {
      const card = element("article", "", "gmail-preview panel");
      card.append(element("h3", message.subject || "(No subject)"),
        element("p", "From: " + message.sender + " · " + message.received_at, "small"),
        element("p", message.snippet || "No preview text provided."),
        element("p", "Short preview only; not a full conversation.", "muted small"));
      const link = element("a", "Open original in Gmail");
      link.href = "https://mail.google.com/mail/u/" + encodeURIComponent(message.mailbox) + "/#all/" + encodeURIComponent(message.gmail_id);
      link.target = "_blank"; link.rel = "noopener noreferrer";
      card.append(link);
      if (message.contact_id) {
        card.append(element("p", "Linked to " + message.contact_name + " · " + message.property_address));
        const unlink = element("button", "Unlink", "button"); unlink.type = "button";
        unlink.addEventListener("click", () => review(message.id, {action: "unlink"}, unlink));
        card.append(unlink);
      } else {
        const choices = (state.communications?.contacts || []).filter(contact => contact.email && contact.email.toLowerCase() === message.sender_email);
        const label = element("label", "Link to matching contact");
        const select = element("select");
        const empty = element("option", "Choose a contact"); empty.value = ""; select.append(empty);
        for (const contact of choices) {
          const property = state.properties.find(property => property.id === contact.property_id);
          const option = element("option", contact.name + " · " + (property?.address || "Saved property"));
          option.value = contact.id; select.append(option);
        }
        label.append(select); card.append(label);
        const save = element("button", "Link preview", "button"); save.type = "button"; save.disabled = true;
        select.addEventListener("change", () => { save.disabled = !select.value; });
        save.addEventListener("click", () => review(message.id, {action: "link", contact_id: select.value}, save));
        card.append(save);
        if (!choices.length) card.append(element("p", "Add a contact under the correct property with this sender's email, then reload saved previews.", "muted small"));
      }
      const remove = element("button", "Remove preview", "button"); remove.type = "button";
      remove.addEventListener("click", () => review(message.id, {action: "remove"}, remove));
      card.append(remove); inbox.append(card);
    }
  }
  async function review(id, data, button) {
    button.disabled = true;
    try { await post("/api/gmail/previews/" + encodeURIComponent(id) + "/review", data); await renderInbox(); }
    catch (error) { document.getElementById("gmail-result").textContent = error.message; button.disabled = false; }
  }
  async function sync(pageToken = "") {
    if (syncing) return;
    syncing = true;
    syncForm.querySelectorAll("button").forEach(button => { button.disabled = true; });
    const query = syncForm.elements.query.value;
    document.getElementById("gmail-result").textContent = "Fetching a limited batch of message previews…";
    try {
      const result = await post("/api/gmail/sync", {query, limit: Number(syncForm.elements.limit.value), page_token: pageToken});
      nextToken = result.next_page_token; batchQuery = query;
      next.hidden = !nextToken || batchQuery !== syncForm.elements.query.value;
      document.getElementById("gmail-result").textContent = result.inserted + " new previews saved; " + result.duplicates + " duplicates skipped. Sending remains disabled.";
      await renderInbox(); await refresh();
    } catch (error) { document.getElementById("gmail-result").textContent = error.message; }
    finally { syncing = false; syncForm.querySelectorAll("button").forEach(button => { button.disabled = false; }); }
  }
  syncForm.addEventListener("submit", event => { event.preventDefault(); nextToken = ""; next.hidden = true; sync(); });
  syncForm.elements.query.addEventListener("input", () => { nextToken = ""; next.hidden = true; });
  next.addEventListener("click", () => { if (nextToken && batchQuery === syncForm.elements.query.value) sync(nextToken); });
  document.getElementById("gmail-inbox-reload").addEventListener("click", async () => {
    try { await renderInbox(); } catch (error) { document.getElementById("gmail-result").textContent = error.message; }
  });
  try { await refresh(); } catch (error) { status.textContent = error.message; }
  try { await renderInbox(); } catch (error) { document.getElementById("gmail-inbox-count").textContent = error.message; }
});
