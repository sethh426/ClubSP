"use strict";
let selectedContact = null;
let selectedLesson = "discovery";

function communicationDetails(title, className, open = false) {
  const details = workspaceDetails(title, open);
  const form = node("form", undefined, className + " workspace-form");
  details.append(form); return {details, form};
}
function communicationText(form, name, label, value = "", required = false) {
  const input = workspaceField(form, name, label, "textarea", value, required);
  input.maxLength = 8000; input.parentElement.classList.add("full-width"); return input;
}
function communicationCheck(form, name, label, required = false) {
  const wrap = node("label", label, "check-label");
  const input = node("input"); input.type = "checkbox"; input.name = name; input.required = required;
  wrap.prepend(input); form.append(wrap); return input;
}
function renderSellerInsights() {
  const box = $("pain-point-insights"); box.replaceChildren();
  const c = state.communications, i = c.insights;
  box.append(node("p", i.confirmed_profiles + " confirmed profiles · " + i.hypotheses + " hypotheses · " + i.unknown_profiles + " not recorded", "muted small"));
  const table = node("table", undefined, "insight-table");
  const head = node("tr"); head.append(node("th", "Stated concern"), node("th", "Contacts"));
  const header = node("thead"); header.append(head); const body = node("tbody");
  Object.entries(c.pain_point_labels).forEach(([key, label]) => { const row = node("tr"); row.append(node("td", label), node("td", String(i.confirmed_pain_points[key]))); body.append(row); });
  table.append(header, body); box.append(table, node("p", i.method + ". Multiple concerns can belong to one person. These counts do not predict an individual seller's circumstances.", "muted small"));
}
function renderContacts() {
  const box = $("seller-workspace"); box.replaceChildren();
  box.append(node("p", "OPERATIONS 07–09 / SELLER CONVERSATIONS", "eyebrow"), node("h2", "Seller conversations & reply drafts"), node("p", "Save the actual conversation and confirmed priorities. Drafts stay here for your review; email delivery is not connected.", "muted small"));
  const contacts = state.communications.contacts.filter(c => c.property_id === selected);
  const add = communicationDetails("Add a seller or decision participant", "contact-form", !contacts.length);
  workspaceField(add.form, "name", "Contact name");
  workspaceField(add.form, "email", "Email (optional)", "email", "", false);
  workspaceSelect(add.form, "role", "Recorded role", [["unverified", "Unverified"], ["owner", "Owner (requires evidence)"], ["representative", "Representative (requires evidence)"], ["other", "Other participant"]]);
  workspaceField(add.form, "role_reference", "Role evidence reference", "text", "", false);
  workspaceSubmit(add.form, "Save contact");
  add.form.addEventListener("submit", e => {e.preventDefault(); const data = values(add.form); data.property_id = selected; runForm(add.form, async()=>{ selectedContact = (await api("/api/contacts", data)).id; }, "Contact saved.");});
  box.append(add.details);
  if (!contacts.length) { box.append(node("p", "No conversation records yet.", "muted small")); return; }
  if (!contacts.some(c => c.id === selectedContact)) selectedContact = contacts[0].id;
  const tabs = node("div", undefined, "contact-tabs");
  contacts.forEach(c => { const button = node("button", c.name, "button"); button.type = "button"; button.setAttribute("aria-pressed", String(c.id === selectedContact)); button.addEventListener("click", ()=>{selectedContact=c.id;renderContacts();}); tabs.append(button); });
  box.append(tabs);
  const contact = contacts.find(c => c.id === selectedContact);
  const head = node("div", undefined, "row-head"); head.append(node("h3", contact.name), node("span", "Permission: " + contact.permission_status, "pill open")); box.append(head);
  box.append(node("p", (contact.email || "No email recorded") + " · Role: " + readable(contact.role), "muted small"));
  const permission = communicationDetails("Contact permission & suppression", "permission-form");
  workspaceSelect(permission.form, "status", "Permission record", [["unknown", "Unknown / pause"], ["permitted", "Owner recorded permitted"], ["suppressed", "Stop / suppressed"]], contact.permission_status);
  communicationText(permission.form, "note", "Review note", "", true);
  workspaceField(permission.form, "evidence_reference", "Permission / request evidence reference", "text", "", false);
  workspaceSubmit(permission.form, "Save permission record");
  permission.form.addEventListener("submit", e=>{e.preventDefault();runForm(permission.form,()=>api("/api/contacts/"+contact.id+"/permission",values(permission.form)),"Permission record saved.");});
  permission.details.append(node("p", "A recorded stop request cancels pending drafts and suppresses matching email addresses across properties. This release cannot clear a suppression. Permission evidence alone is not campaign clearance.", "muted small")); box.append(permission.details);
  renderSellerProfile(contact, box);
  const messageForm = communicationDetails("Record a conversation", "conversation-form", !contact.messages.length);
  workspaceSelect(messageForm.form, "direction", "Direction", [["incoming", "Received from contact"], ["outgoing", "Already sent / said manually"], ["note", "Private note"]]);
  workspaceSelect(messageForm.form, "channel", "Channel", [["email", "Email"], ["phone", "Phone"], ["in_person", "In person"], ["note", "Private note"]]);
  workspaceSelect(messageForm.form, "category", "Concern / category", state.communications.message_categories.map(k=>[k,readable(k)]), "general");
  workspaceField(messageForm.form, "occurred_on", "Conversation date", "date", state.today);
  communicationText(messageForm.form, "body", "Actual message or conversation notes", "", true);
  workspaceField(messageForm.form, "evidence_reference", "Conversation evidence reference");
  workspaceSubmit(messageForm.form, "Save conversation");
  const messageKey = crypto.randomUUID();
  messageForm.form.addEventListener("submit", e=>{e.preventDefault();const data=values(messageForm.form);data.message_key=messageKey;runForm(messageForm.form,()=>api("/api/contacts/"+contact.id+"/messages",data),"Conversation saved.");});
  box.append(messageForm.details);
  const history = workspaceDetails("Conversation history · " + contact.messages.length);
  [...contact.messages].reverse().forEach(m=>{const card=node("article",undefined,"communication-card");card.append(node("strong",readable(m.direction)+" · "+readable(m.category)),node("p",m.body,"conversation-body"),node("p",m.occurred_on+" · "+m.channel+" · Evidence: "+m.evidence_reference,"muted small"));history.append(card);}); box.append(history);
  const reply = node("button", "Suggest a reply from saved conversation", "button"); reply.type="button"; reply.disabled=contact.permission_status==="suppressed"||!contact.messages.some(m=>m.direction==="incoming");
  reply.addEventListener("click",()=>runForm(box,()=>api("/api/contacts/"+contact.id+"/reply",{}),"Reply draft saved for review."));box.append(reply);
  if(contact.permission_status==="suppressed")box.append(node("p","Contact is suppressed. Sales reply generation is stopped.","note"));
  renderReplyDrafts(contact,box);
}
function renderSellerProfile(contact, box) {
  const current = contact.profiles[0];
  const profile = communicationDetails("Seller priorities & pain points", "seller-profile-form", !current);
  workspaceSelect(profile.form,"status","Record basis",[["confirmed","Confirmed from contact evidence"],["hypothesis","Owner hypothesis — unconfirmed"]],current?.status||"hypothesis");
  [["goal","Desired outcome"],["timing","Timing / flexibility"],["condition_notes","Stated condition concerns"],["authority_notes","Decision participants / authority"],["alternatives","Other options mentioned"],["priority","Most important priority"]].forEach(([key,label])=>communicationText(profile.form,key,label,current?.[key]||"",key==="goal"));
  const points=node("fieldset",undefined,"full-width");points.append(node("legend","Stated pain points"));const checks=node("div",undefined,"pain-point-list");
  Object.entries(state.communications.pain_point_labels).forEach(([key,label])=>{const wrap=node("label",label);const input=node("input");input.type="checkbox";input.name="pain_points";input.value=key;input.checked=current?.pain_points.includes(key)||false;wrap.prepend(input);checks.append(wrap);});points.append(checks);profile.form.append(points);
  workspaceField(profile.form,"evidence_reference","Evidence reference (required for confirmed statements)","text",current?.evidence_reference||"",false);
  workspaceSubmit(profile.form,"Save seller priorities");
  profile.form.addEventListener("submit",e=>{e.preventDefault();const data=values(profile.form);data.pain_points=new FormData(profile.form).getAll("pain_points");runForm(profile.form,()=>api("/api/contacts/"+contact.id+"/profile",data),"Seller priorities saved.");});
  if(current)profile.details.append(node("p","Current profile: "+current.status+" · "+date(current.created_at)+" · "+contact.profiles.length+" saved versions","muted small"));box.append(profile.details);
}
function renderReplyDrafts(contact, box) {
  const drafts=workspaceDetails("Reply drafts · "+contact.drafts.length,contact.drafts.some(d=>d.current));
  contact.drafts.forEach(d=>{
    const card=node("article",undefined,"communication-card");card.append(node("strong",d.subject),node("p",d.status+" · "+(d.current?"current context":"changed context"),"muted small"));
    card.append(node("p","Based on the saved "+readable(d.category)+" conversation. Review wording and facts before use. Email delivery is not connected.","muted small"));
    if(d.review_blockers.length)card.append(node("p",d.review_blockers.join(". "),"note"));
    if(d.reviewed_at){card.append(node("p",d.final_body,"reply-body"),node("p","Owner review: "+d.review_note,"muted small"));const original=workspaceDetails("Original suggestion");original.append(node("p",d.body,"reply-body"));card.append(original);}
    else {card.append(node("p",d.body,"reply-body"));if(d.current){const form=node("form",undefined,"reply-review-form workspace-form");communicationText(form,"final_body","Your edited reply",d.body,true);communicationText(form,"note","Grounding / role / next-step review note","",true);communicationCheck(form,"owner_reviewed","I reviewed the facts, actual role, commitments and contact permission.",true);const submit=workspaceSubmit(form,"Save reviewed draft");submit.disabled=Boolean(d.review_blockers.length);form.addEventListener("submit",e=>{e.preventDefault();const data=values(form);data.owner_reviewed=form.elements.owner_reviewed.checked;runForm(form,()=>api("/api/replies/"+d.id+"/review",data),"Reviewed draft saved. No email was sent.");});card.append(form);}}
    drafts.append(card);
  });box.append(drafts);
}
function renderTraining() {
  const box=$("training-workspace");box.replaceChildren();const training=state.training;
  box.append(node("p","Synthetic scenarios · Internal draft curriculum · "+training.version,"muted small"));
  const selector=node("form");const select=workspaceSelect(selector,"scenario_id","Practice an objection",training.lessons.map(l=>[l.id,l.title]),selectedLesson);select.addEventListener("change",()=>{selectedLesson=select.value;renderTraining();});box.append(selector);
  const lesson=training.lessons.find(l=>l.id===selectedLesson);const article=node("article",undefined,"training-lesson");article.append(node("h3",lesson.title),node("p",lesson.prompt,"reply-body"),node("p",lesson.guidance),node("p","Clarify: "+lesson.clarifying_question),node("p","Avoid: "+lesson.avoid,"note"),node("p","Next step: "+lesson.next_step),node("p",lesson.status+" · Source: "+lesson.source_reference,"muted small"));box.append(article);
  const form=node("form",undefined,"practice-form workspace-form");communicationText(form,"response","Your practice reply","",true);
  const ratings=node("div",undefined,"assessment-grid full-width");
  Object.entries(training.rubric).forEach(([key,label])=>{workspaceSelect(ratings,key,readable(key)+": "+label,[["","Choose a score"],["0","0 — Missing / unsuitable"],["1","1 — Partial"],["2","2 — Supported and clear"]]);ratings.querySelector("select[name="+key+"]").required=true;});form.append(ratings);
  const issues=node("fieldset",undefined,"full-width");issues.append(node("legend","Hard failures observed"));const checks=node("div",undefined,"pain-point-list");Object.entries(training.hard_failures).forEach(([key,label])=>{const wrap=node("label",label);const input=node("input");input.type="checkbox";input.name="hard_failures";input.value=key;wrap.prepend(input);checks.append(wrap);});issues.append(checks);form.append(issues);
  communicationText(form,"review_note","Review evidence: what worked and what needs to improve","",true);workspaceSubmit(form,"Save practice & self-assessment");const key=crypto.randomUUID();
  form.addEventListener("submit",e=>{e.preventDefault();const data=values(form);data.attempt_key=key;data.scenario_id=lesson.id;data.ratings=Object.fromEntries(Object.keys(training.rubric).map(k=>[k,Number(form.elements[k].value)]));data.hard_failures=new FormData(form).getAll("hard_failures");runForm(form,()=>api("/api/training/practice",data),"Practice and owner self-assessment saved.");});
  box.append(node("p",training.assessment_method+". The practice threshold is 10/12 with no hard failure; it does not approve a live template.","muted small"),form);
  const history=workspaceDetails("Practice history · "+training.attempts.length);
  training.attempts.forEach(a=>{const item=node("article",undefined,"training-attempt");item.append(node("strong",a.scenario_id+" · "+a.assessment.total+"/12 · "+(a.assessment.meets_practice_threshold?"Practice threshold met":"Needs further practice")),node("p",a.response),node("p",a.review_note,"muted small"));if(a.assessment.hard_failures.length)item.append(node("p","Hard failures: "+a.assessment.hard_failures.join(", "),"note"));history.append(item);});box.append(history);
}
