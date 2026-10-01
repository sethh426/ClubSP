"use strict";
document.addEventListener("DOMContentLoaded", async () => {
  const status = document.getElementById("gmail-status");
  const connect = document.getElementById("gmail-connect");
  const disconnect = document.getElementById("gmail-disconnect");
  const refreshAccess = document.getElementById("gmail-refresh");
  const callback = document.getElementById("gmail-callback");
  const query = new URLSearchParams(location.search).get("gmail");
  if (query) {
    document.getElementById("gmail-result").textContent = query === "connected"
      ? "Google authorization completed. Sending and conversation import remain disabled."
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
  }
  async function post(path) {
    const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
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
      document.getElementById("gmail-result").textContent = "Gmail access refreshed. Sending and inbox sync remain disabled.";
    } catch (error) {
      status.textContent = error.message + " If the grant expired or was revoked, reconnect Gmail.";
    } finally { refreshAccess.disabled = false; }
  });
  try { await refresh(); } catch (error) { status.textContent = error.message; }
});
