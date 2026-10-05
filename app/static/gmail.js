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
      ? "Google authorization completed. You can now manually sync previews and send only current owner-approved Relationship Desk drafts."
      : "Connection was not saved. Check the callback URL, Gmail API, test-user access, and selected mailbox, then try again.";
    history.replaceState(null, "", "/");
  }
  async function refresh() {
    const response = await fetch("/api/gmail/status");
    if (!response.ok) throw new Error("Connection status unavailable");
    const data = await response.json();
    callback.textContent = data.redirect_uri;
    status.textContent = data.connected
      ? "Authorized mailbox: " + data.email + (data.access_token_expired ? " · Access token expired; refresh access or reconnect." : " · Read access plus approved-draft sending.")
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
      document.getElementById("gmail-result").textContent = "Gmail access refreshed. Approved-draft sending is enabled; automatic sending remains disabled.";
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
      const relBox = element("section", "", "gmail-relationship-review");
      if (message.relationship_id) {
        relBox.append(element("p", "Relationship Desk: " + (message.relationship_name || message.relationship_id), "small"));
        if (message.relationship_interaction_id) {
          relBox.append(element("p", "Imported into relationship history · " + message.relationship_imported_at, "muted small"));
        } else {
          const unlinkRel = element("button", "Unlink relationship", "button"); unlinkRel.type = "button";
          unlinkRel.addEventListener("click", () => relationshipReview(message.id, {action:"unlink"}, unlinkRel));
          relBox.append(unlinkRel);
          const form = element("form", "", "workspace-form gmail-reply-import");
          const outcomeLabel = element("label", "Reviewed reply outcome"), outcome = element("select");
          outcome.name = "outcome";
          [["general","General"],["interested","Interested"],["not_interested","Not interested"],["stop","Stop request"],["wrong_person","Wrong person"]]
            .forEach(([value,label])=>{const option=element("option",label);option.value=value;outcome.append(option);});
          outcomeLabel.append(outcome); form.append(outcomeLabel);
          const dateLabel=element("label","Interaction date"), occurred=element("input");occurred.type="date";occurred.name="occurred_on";occurred.required=true;occurred.value=(message.received_at||"").slice(0,10);dateLabel.append(occurred);form.append(dateLabel);
          const noteLabel=element("label","Owner review / what this reply means"), note=element("textarea");note.name="review_note";note.required=true;note.maxLength=2000;noteLabel.append(note);form.append(noteLabel);
          const followLabel=element("label","Next follow-up date"), follow=element("input");follow.type="date";follow.name="follow_up_on";followLabel.append(follow);form.append(followLabel);
          const nextLabel=element("label","Next action"), nextAction=element("input");nextAction.name="next_action";nextAction.maxLength=500;nextLabel.append(nextAction);form.append(nextLabel);
          form.append(element("p","The Gmail snippet is evidence context, not the full message. Choose the outcome after reviewing the original in Gmail. Stop/wrong-person immediately suppresses future outreach.","muted small full-width"));
          const importButton=element("button","Import reviewed reply","button primary");importButton.type="submit";form.append(importButton);
          const key=requestUUID();
          form.addEventListener("submit",async event=>{
            event.preventDefault();importButton.disabled=true;
            try{
              const data=Object.fromEntries(new FormData(form));
              data.action="import";data.relationship_id=message.relationship_id;data.request_key=key;
              await post("/api/gmail/previews/"+encodeURIComponent(message.id)+"/relationship",data);
              document.getElementById("gmail-result").textContent="Reviewed Gmail reply imported into Relationship Desk.";
              await renderInbox();
            }catch(error){document.getElementById("gmail-result").textContent=error.message;importButton.disabled=false;}
          });
          relBox.append(form);
        }
      } else {
        const candidates=message.relationship_candidates || [];
        if (candidates.length) {
          const label=element("label","Link to matching Relationship Desk record"), select=element("select");
          const empty=element("option","Choose a relationship");empty.value="";select.append(empty);
          candidates.forEach(candidate=>{const option=element("option",candidate.name+" · "+candidate.status);option.value=candidate.id;select.append(option);});
          label.append(select);relBox.append(label);
          const button=element("button","Link relationship","button");button.type="button";button.disabled=true;
          select.addEventListener("change",()=>{button.disabled=!select.value;});
          button.addEventListener("click",()=>relationshipReview(message.id,{action:"link",relationship_id:select.value},button));
          relBox.append(button);
        } else {
          relBox.append(element("p","No current Relationship Desk record has this sender email. Add or update the relationship before importing the reply.","muted small"));
        }
      }
      card.append(relBox);
      const remove = element("button", "Remove preview", "button"); remove.type = "button";
      remove.addEventListener("click", () => review(message.id, {action: "remove"}, remove));
      card.append(remove); inbox.append(card);
    }
  }
  async function relationshipReview(id, data, button) {
    button.disabled = true;
    try {
      await post("/api/gmail/previews/" + encodeURIComponent(id) + "/relationship", data);
      await renderInbox();
    } catch (error) {
      document.getElementById("gmail-result").textContent = error.message;
      button.disabled = false;
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
      document.getElementById("gmail-result").textContent = result.inserted + " new previews saved; " + result.duplicates + " duplicates skipped. Automatic sending remains disabled.";
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
