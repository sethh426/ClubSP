"use strict";

const relNode = (tag, text, cls) => {const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node;};
function relKey() {
  const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const h = [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}
async function relApi(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)});
  const body = await response.json(); if (!response.ok) throw new Error(body.error || "Request failed"); return body;
}
function relMessage(text, error = false) {const n = document.getElementById("relationship-message"); n.textContent = text; n.className = "message" + (error ? " error" : ""); n.hidden = false;}
function relField(form, name, label, value = "", type = "text", required = false, limit = 500) {
  const wrap = relNode("label", label), input = relNode(type === "textarea" ? "textarea" : "input");
  if (type !== "textarea") input.type = type; input.name = name; input.value = value; input.required = required; input.maxLength = limit;
  wrap.append(input); form.append(wrap); return input;
}
function relSelect(form, name, label, choices, value) {
  const wrap = relNode("label", label), select = relNode("select"); select.name = name;
  choices.forEach(([key, text]) => {const option = relNode("option", text); option.value = key; select.append(option);}); select.value = value; wrap.append(select); form.append(wrap);
}
function relSubmit(form, button, path, build) {
  let key = relKey(), signature = null;
  form.addEventListener("submit", async event => {
    event.preventDefault(); const data = build(Object.fromEntries(new FormData(form)));
    const next = JSON.stringify(data); if (signature !== null && signature !== next) key = relKey(); signature = next; data.request_key = key;
    button.disabled = true; let saved = false;
    try {await relApi(path, data); saved = true; relMessage("Record saved. No message was sent."); await relLoad();}
    catch (error) {relMessage(saved ? "Saved, but the view could not refresh. Reload before adding another record." : error.message, true);}
    finally {if (!saved) button.disabled = false;}
  });
}
function relProfileForm(record, state) {
  const p = record?.profile || {}, form = relNode("form", undefined, "relationship-form");
  relField(form,"name","Name",p.name || "","text",true,120);
  relField(form,"company","Company",p.company || "","text",false,160);
  relField(form,"email","Email",p.email || "","email",false,254);
  relSelect(form,"kind","Relationship type",[["investor","Investor"],["agent","Agent"],["closing_partner","Closing partner"],["other","Other"]],p.kind || "investor");
  relSelect(form,"status","Relationship status",[["prospect","Prospect"],["engaged","Engaged"],["active","Active relationship"],["paused","Paused"],["closed","Closed"]],p.status || "prospect");
  relSelect(form,"buyer_id","Linked buyer",[["","No buyer linked"],...state.buyers.map(b=>[b.id,`${b.name} (${b.status})`])],p.buyer_id || "");
  relField(form,"markets","Areas of interest (one per line)",(p.markets || []).join("\n"),"textarea",false,2000);
  relField(form,"needs","What they need / conversation notes",p.needs || "","textarea",false,2000);
  relField(form,"source_reference","Relationship source / evidence reference",p.source_reference || "","text",true);
  relSelect(form,"permission","Contact permission review",[["unknown","Not reviewed"],["owner_reviewed","Owner reviewed evidence"],["blocked","Do not contact"]],p.permission || "unknown");
  relField(form,"permission_reference","Permission review reference",p.permission_reference || "");
  relField(form,"owner","Follow-up owner",p.owner || "Owner","text",true,120);
  relField(form,"follow_up_on","Next follow-up date",record?.follow_up_on || "","date");
  relField(form,"next_action","Next action",record?.next_action || "");
  const button = relNode("button",record ? "Save profile version" : "Save relationship","button primary"); button.type = "submit"; form.append(button);
  relSubmit(form,button,"/api/relationships",data=>({...data, markets:data.markets.split("\n").map(v=>v.trim()).filter(Boolean),relationship_id:record?.id || "",profile_id:p.id || "",event_id:record?.event_id || ""}));
  return form;
}
function relInteractionForm(record, state) {
  const form = relNode("form",undefined,"relationship-form");
  relSelect(form,"direction","Interaction direction",[["incoming","Incoming conversation"],["outgoing","Manually sent conversation"],["note","Internal note"]],"note");
  relSelect(form,"outcome","Recorded outcome",[["general","General"],["interested","Interested"],["not_interested","Not interested"],["stop","Stop request"],["wrong_person","Wrong person"]],"general");
  relField(form,"occurred_on","Interaction date",state.today,"date",true);
  relField(form,"evidence_reference","Conversation evidence reference","","text",true);
  relField(form,"note","What happened / actual conversation","","textarea",true,4000);
  relField(form,"follow_up_on","Follow-up after this conversation","","date");
  relField(form,"next_action","Action after this conversation","");
  form.append(relNode("p","This records a conversation or note; it does not send one. Blank follow-up removes the prior schedule. Stop and wrong-person records block contact and preserve history.","muted small wide"));
  const button=relNode("button","Record interaction","button primary");button.type="submit";form.append(button);
  relSubmit(form,button,`/api/relationships/${record.id}/interactions`,data=>({...data,profile_id:record.profile.id,event_id:record.event_id || ""})); return form;
}


function relPermissionReviewForm(record) {
  const p=record.profile;
  if (p.permission !== "unknown" || record.blocked) return null;
  const form=relNode("form",undefined,"relationship-form permission-review-form");
  form.append(relNode("p","Review the public business-contact evidence before deciding whether outreach is appropriate. A published email is evidence to review, not automatic consent.","muted small wide"));
  const evidence = (record.interactions || []).find(item => (item.note || "").startsWith("Public contact evidence packet:"));
  if (evidence) {
    const packet=relNode("section",undefined,"history-entry wide");
    packet.append(
      relNode("strong","Recorded public-contact evidence"),
      relNode("p",evidence.note.replace(/^Public contact evidence packet:\s*/,""),"small"),
      relNode("p","Evidence reference: "+evidence.evidence_reference,"muted small")
    );
    form.append(packet);
  } else {
    form.append(relNode("p","No recorded evidence packet yet; use the source link below and document what you reviewed.","relationship-blocked small wide"));
  }
  const source=relNode("a","Open recorded public source");source.href=p.source_reference;source.target="_blank";source.rel="noopener noreferrer";form.append(source);
  relSelect(form,"decision","Permission decision",[["allow_outreach","Allow reviewed business outreach"],["block_outreach","Do not contact"]],"allow_outreach");
  relField(form,"evidence_reference","Permission evidence reference",p.source_reference || "","text",true,500);
  relField(form,"review_note","What you reviewed and why this decision is appropriate","","textarea",true,2000);
  const confirmLabel=relNode("label",undefined,"wide"),confirm=relNode("input");confirm.type="checkbox";confirm.required=true;
  confirmLabel.append(confirm,document.createTextNode(" I reviewed the contact source, recipient identity, and this permission decision."));form.append(confirmLabel);
  const button=relNode("button","Record permission review","button primary");button.type="submit";form.append(button);
  relSubmit(form,button,`/api/relationships/${record.id}/permission-review`,data=>({...data,
    profile_id:p.id,event_id:record.event_id || "",owner_confirmed_review:confirm.checked
  }));
  return form;
}

function relBuyerQualificationForm(record) {
  const latest = record.interactions[0], p = record.profile;
  if (record.buyer || record.blocked || record.paused || p.kind !== "investor" || !latest
      || latest.direction !== "incoming" || !["general","interested"].includes(latest.outcome)) return null;
  const form = relNode("form",undefined,"relationship-form buyer-qualification-form");
  form.append(relNode("p","Use only criteria confirmed in the latest incoming conversation. This creates a buyer and a time-bounded mandate; it does not verify funding unless you record reviewed evidence.","muted small wide"));
  relField(form,"markets","Confirmed markets (one per line)",(p.markets || []).join("\n"),"textarea",true,2000);
  const strategies=relNode("fieldset",undefined,"wide"),legend=relNode("legend","Confirmed strategies");
  strategies.append(legend);
  for(const value of ["assignment","resale"]){
    const label=relNode("label"),box=relNode("input");box.type="checkbox";box.name="buyer_strategy";box.value=value;
    if(value==="assignment") box.checked=true;
    label.append(box,document.createTextNode(" "+value));strategies.append(label);
  }
  form.append(strategies);
  relField(form,"property_types","Confirmed property types (one per line)","","textarea",false,2000);
  const price=relField(form,"max_total_price","Maximum total acquisition price","","number",true);price.min="0";price.step="0.01";
  const repairs=relField(form,"max_repairs","Maximum repairs","","number",true);repairs.min="0";repairs.step="0.01";
  relSelect(form,"funding_status","Funding evidence status",[["unverified","Unverified"],["owner_reviewed","Owner reviewed evidence"],["verified","Verified evidence on record"]],"unverified");
  relField(form,"funding_reference","Funding evidence reference");
  const refresh=relField(form,"refresh_days","Reconfirm criteria after days","30","number",true);refresh.min="1";refresh.max="90";refresh.step="1";
  const priority=relField(form,"priority","Mandate priority (0–100)","50","number",true);priority.min="0";priority.max="100";priority.step="1";
  const active=relField(form,"max_active_reservations","Max active reservations","1","number",true);active.min="1";active.max="100";active.step="1";
  const units=relField(form,"target_units_per_month","Target units per month","1","number",true);units.min="1";units.max="1000";units.step="1";
  relField(form,"mandate_name","Mandate name (optional)");
  relField(form,"review_note","What the investor actually confirmed","","textarea",true,2000);
  const confirmLabel=relNode("label",undefined,"wide"),confirm=relNode("input");confirm.type="checkbox";confirm.required=true;
  confirmLabel.append(confirm,document.createTextNode(" I reviewed the latest incoming conversation and these fields reflect its confirmed buyer criteria."));form.append(confirmLabel);
  const button=relNode("button","Promote confirmed reply to buyer demand","button primary");button.type="submit";form.append(button);
  relSubmit(form,button,`/api/relationships/${record.id}/buyer-qualification`,data=>{
    const strategies=[...form.querySelectorAll('input[name="buyer_strategy"]:checked')].map(x=>x.value);
    delete data.buyer_strategy;
    return {...data,
      profile_id:p.id,event_id:record.event_id || "",
      markets:data.markets.split("\n").map(v=>v.trim()).filter(Boolean),
      strategies,
      property_types:data.property_types.split("\n").map(v=>v.trim()).filter(Boolean),
      max_total_price:Number(data.max_total_price),max_repairs:Number(data.max_repairs),
      refresh_days:Number(data.refresh_days),priority:Number(data.priority),
      max_active_reservations:Number(data.max_active_reservations),
      target_units_per_month:Number(data.target_units_per_month),
      owner_confirmed_criteria:confirm.checked
    };
  });
  return form;
}

function relFilter() {
  const filter=document.getElementById("relationship-filter").value, query=document.getElementById("relationship-search").value.trim().toLowerCase(); let count=0;
  document.querySelectorAll(".relationship-card").forEach(card=>{card.hidden= !((filter==="all" || filter===card.dataset.status) && card.dataset.search.includes(query)); if(!card.hidden)count++;});
  document.getElementById("relationship-empty").hidden=count>0;
}
function relDraftForm(record) {
  const latest=record.saved_drafts[0], form=relNode("form",undefined,"relationship-form");
  form.append(relNode("p",`Draft recipient: ${record.profile.email}. Saving a version requires a fresh owner review.`,"muted small wide"));
  relField(form,"subject","Message subject",latest?.subject || record.draft.subject,"text",true,200);
  relField(form,"body","Editable message",latest?.body || record.draft.body,"textarea",true,8000);
  const button=relNode("button","Save message draft","button primary");button.type="submit";form.append(button);
  relSubmit(form,button,`/api/relationships/${record.id}/drafts`,data=>({...data,profile_id:record.profile.id,event_id:record.event_id || "",draft_id:latest?.id || ""}));
  return form;
}
function relSendForm(record, draft) {
  const form=relNode("form",undefined,"relationship-form");
  form.append(relNode("p","This sends the exact approved subject/body to the reviewed recipient through the connected Gmail account. A send attempt cannot be silently retried.","relationship-blocked small wide"));
  relField(form,"follow_up_on","Follow-up date after sending","","date");
  relField(form,"next_action","Next action after sending","");
  const confirm=relNode("label",undefined,"wide"), box=relNode("input");box.type="checkbox";box.name="owner_confirmed_send";box.required=true;
  confirm.append(box,document.createTextNode(" I reviewed the recipient and exact message and authorize this send now."));form.append(confirm);
  const button=relNode("button","Send approved email","button primary");button.type="submit";form.append(button);
  const key=relKey();
  form.addEventListener("submit",async event=>{
    event.preventDefault();button.disabled=true;
    try{
      const data=Object.fromEntries(new FormData(form));
      data.request_key=key;data.draft_id=draft.id;data.review_id=draft.reviews[0].id;data.owner_confirmed_send=box.checked;
      const result=await relApi(`/api/relationships/${record.id}/send`,data);
      relMessage(result.status==="sent"?"Email sent. Gmail receipt recorded.":"Send attempt recorded: "+result.status+".");
      await relLoad();
    }catch(error){relMessage(error.message,true);button.disabled=false;}
  });
  return form;
}
function relDraftHistory(record, parent) {
  record.saved_drafts.forEach(draft=>{
    const item=relNode("section",undefined,"history-entry");
    item.append(relNode("strong",`Draft: ${draft.review_status} · ${draft.created_at}`),relNode("p",`Recipient at save: ${draft.recipient}`),relNode("p",draft.subject),relNode("p",draft.body));
    draft.review_blockers.forEach(text=>item.append(relNode("p",text,"relationship-blocked small")));
    const form=relNode("form",undefined,"relationship-form");
    relSelect(form,"decision","Review decision",draft.review_blockers.length?[["rejected","Reject message text"]]:[["approved","Approve message text"],["rejected","Reject message text"]],draft.review_blockers.length?"rejected":"approved");
    relField(form,"reviewer","Reviewer","Owner","text",true,120);
    relField(form,"note","Review notes / evidence","","textarea",true,2000);
    const button=relNode("button","Record draft review","button");button.type="submit";form.append(button);
    relSubmit(form,button,`/api/relationships/${record.id}/draft-reviews`,data=>({...data,draft_id:draft.id,review_id:draft.reviews[0]?.id || ""}));
    item.append(form);
    draft.reviews.forEach(review=>item.append(relNode("p",`${review.decision} · ${review.reviewer} · ${review.created_at}\n${review.note}`,"small")));
    if(draft.send){
      const event=draft.send.latest_event;
      item.append(relNode("p",`Send status: ${draft.send.status} · ${event?.created_at || draft.send.created_at}`,"small"));
      if(event?.provider_message_id)item.append(relNode("p",`Gmail message: ${event.provider_message_id} · thread: ${event.provider_thread_id}`,"muted small"));
      if(event?.note)item.append(relNode("p",event.note,draft.send.status==="unknown"?"relationship-blocked small":"muted small"));
    } else if(draft.sending_enabled) {
      item.append(relSendForm(record,draft));
    }
    parent.append(item);
  });
}
function relDetails(title, parent) {const details=relNode("details",undefined,"workspace-details");details.append(relNode("summary",title));parent.append(details);return details;}
async function relLoad() {
  const state=await relApi("/api/relationships");
  const metrics=document.getElementById("relationship-summary");metrics.replaceChildren();
  for(const [key,label] of [["total","Relationships"],["due","Follow-ups due"],["qualified_buyers","Qualified buyers"],["overdue","Overdue"],["blocked","Do not contact"]]){const n=relNode("article");n.append(relNode("span",label),relNode("strong",String(state.summary[key])));metrics.append(n);}
  document.getElementById("relationship-create-form").replaceChildren(relProfileForm(null,state));
  const focus=document.getElementById("relationship-focus");focus.replaceChildren();
  state.daily_focus.forEach(id=>{const r=state.relationships.find(x=>x.id===id),li=relNode("li"),link=relNode("a",r.profile.name);link.href=`#relationship-${id}`;link.addEventListener("click",()=>{document.getElementById("relationship-filter").value="all";document.getElementById("relationship-search").value="";relFilter();});li.append(link,relNode("p",`${r.next_action} · ${r.follow_up_on} · ${r.profile.owner}`,"small"));focus.append(li);});
  if(!state.daily_focus.length)focus.append(relNode("li","No scheduled follow-ups are due. Add a next action and date to a relationship."));
  const list=document.getElementById("relationship-list");list.replaceChildren();
  state.relationships.forEach(r=>{
    const p=r.profile,card=relNode("article",undefined,"panel relationship-card");card.id=`relationship-${r.id}`;card.dataset.relationshipId=r.id;card.dataset.status=r.queue_status;card.dataset.search=[p.name,p.company,p.email,...p.markets].join(" ").toLowerCase();
    card.append(relNode("h2",p.name),relNode("p",`${p.company || "No company recorded"} · ${p.kind.replaceAll("_"," ")} · ${p.status}`,"muted small"));
    card.append(relNode("p",r.blocked ? "Do not contact — recorded suppression or owner block" : r.paused ? "Paused / closed — excluded from follow-ups" : r.due ? `Follow-up ${r.overdue ? "overdue" : "due today"}` : r.queue_status==="upcoming" ? "Upcoming follow-up" : "Next step not scheduled","relationship-status"+(r.blocked?" relationship-blocked":"")));
    card.append(relNode("p",`${r.next_action || "Record a next action"} · ${r.follow_up_on || "No date"} · Owner: ${p.owner}`),relNode("p",p.needs || "Buying needs have not been recorded.","small"),relNode("p",`Contact: ${p.email || "No email recorded"} · Areas: ${p.markets.join(", ") || "Unknown"}`,"small"));
    card.append(relNode("p",r.buyer?`Linked buyer: ${r.buyer.name} (${r.buyer.status}). Review current criteria in the buyer registry.`:"No buyer linked. A relationship does not establish buyer qualification.","muted small"));
    const permissionForm=relPermissionReviewForm(r);
    if(permissionForm){const permission=relDetails("Review contact permission",card);permission.append(permissionForm);}
    const interaction=relDetails("Record conversation / next step",card);interaction.append(relInteractionForm(r,state));
    const qualificationForm=relBuyerQualificationForm(r);
    if(qualificationForm){
      const qualification=relDetails("Promote confirmed reply to buyer demand",card);qualification.append(qualificationForm);
    }
    if(r.qualification?.mandate){
      const q=r.qualification, m=q.mandate;
      card.append(relNode("p",`Qualified buyer demand · mandate active through ${m.expires_at || "no expiry"} · max price ${Number(m.max_total_price).toLocaleString("en-US")} · max repairs ${Number(m.max_repairs).toLocaleString("en-US")}`,"small"));
    }
    const edit=relDetails("Edit relationship / schedule",card);edit.append(relProfileForm(r,state));
    if(r.draft){const d=relDetails("Message text for owner review",card);d.classList.add("relationship-draft");d.append(relDraftForm(r));d.append(relNode("p","Review identity, context, recipient and permission. Saved drafts and reviews remain in history. Approval records review of this exact text; no email is sent.","muted small"));}
    else card.append(relNode("p","Message text unavailable: record an email and current permission review; paused and blocked records are excluded.","muted small"));
    if(r.saved_drafts.length){const d=relDetails(`Saved message drafts · ${r.saved_drafts.length}`,card);d.classList.add("relationship-saved-drafts");relDraftHistory(r,d);}
    const history=relDetails(`History · ${r.interactions.length} interactions · ${r.profile_history.length} profile versions`,card);
    r.interactions.forEach(e=>{const item=relNode("section",undefined,"history-entry");item.append(relNode("strong",`${e.occurred_on} · ${e.direction} · ${e.outcome}`),relNode("p",e.note),relNode("p",`Evidence: ${e.evidence_reference}`,"small"));history.append(item);});
    r.profile_history.forEach(v=>{const item=relNode("section",undefined,"history-entry");item.append(relNode("p",`${v.name} · ${v.status} · ${v.created_at}`,"small"),relNode("p",`Source: ${v.source_reference} · Permission: ${v.permission} · Reference: ${v.permission_reference || "Unknown"}`,"small"));history.append(item);});
    list.append(card);
  });relFilter();
}
document.addEventListener("DOMContentLoaded",()=>{document.getElementById("relationship-filter").addEventListener("change",relFilter);document.getElementById("relationship-search").addEventListener("input",relFilter);relLoad().catch(error=>relMessage(error.message,true));});
