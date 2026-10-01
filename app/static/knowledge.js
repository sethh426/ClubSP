"use strict";
const observedKnowledgeRuns = new Set();

function watchKnowledgeRun(runId, attempts = 0) {
  if(attempts===0){if(observedKnowledgeRuns.has(runId))return;observedKnowledgeRuns.add(runId);}
  // These requests only observe the explicit run; they never start source checks.
  setTimeout(async()=>{
    try{
      // A queued observer must not rebuild review forms after the run has
      // finished or overwrite a newer owner-action refresh with an old response.
      const before=state.knowledge;
      const current=before.runs.find(r=>r.id===runId);
      if(!current||!["requested","running"].includes(current.status)){
        observedKnowledgeRuns.delete(runId);return;
      }
      const latest=await api("/api/state");
      if(state.knowledge===before){state.knowledge=latest.knowledge;renderKnowledge();}
      const run=state.knowledge.runs.find(r=>r.id===runId);
      if(run&&["requested","running"].includes(run.status)&&attempts<60)watchKnowledgeRun(runId,attempts+1);
      else observedKnowledgeRuns.delete(runId);
    }catch(error){observedKnowledgeRuns.delete(runId);message("Knowledge run status could not refresh. Use Refresh status or reload.",true);}
  },1200);
}
function renderKnowledge() {
  const box=$("knowledge-workspace");box.replaceChildren();const k=state.knowledge;
  const activeRun=k.runs.find(r=>["requested","running"].includes(r.status));
  box.append(node("p","Check selected sources when you press the button. Add your reviewed interpretation from the original source. Failed checks keep existing notes intact; research does not run on a schedule.","muted small"));
  box.append(node("p",k.source_requests_today+" / "+k.daily_source_limit+" source requests used today · "+k.cache_hours+"h cache · No paid-provider calls","small"));
  const form=node("form",undefined,"knowledge-update-form workspace-form");
  workspaceField(form,"jurisdiction","Intended market / jurisdiction for review","text","Allen County, Indiana; national context reviewed separately");
  workspaceField(form,"initiated_by","Requested by","text","Owner");
  const sources=node("fieldset",undefined,"full-width");sources.append(node("legend","Select sources to check"));const choices=node("div",undefined,"source-choices");
  k.sources.forEach((source,index)=>{const wrap=node("label",undefined,"source-choice");const input=node("input");input.type="checkbox";input.name="source_ids";input.value=source.id;input.checked=index<5;wrap.append(input,node("strong",source.name+" · "+source.domain),node("span",source.publisher));const coverage=k.coverage.find(c=>c.source_id===source.id);wrap.append(node("span","Latest check: "+readable(coverage.latest_status)+(coverage.last_successful_check?" · last successful "+date(coverage.last_successful_check):"")),node("span",source.limits));choices.append(wrap);});sources.append(choices);form.append(sources);
  const submit=workspaceSubmit(form,"Update Knowledge");submit.disabled=Boolean(activeRun);const key=crypto.randomUUID();
  form.addEventListener("submit",e=>{e.preventDefault();const data=values(form);data.source_ids=new FormData(form).getAll("source_ids");data.request_key=key;runForm(form,async()=>{const run=await api("/api/knowledge/update",data);watchKnowledgeRun(run.id);},"Knowledge check started. Results require review.");});box.append(form);
  if(activeRun){const actions=node("div",undefined,"knowledge-actions");const refreshButton=node("button","Refresh status","button");refreshButton.type="button";refreshButton.addEventListener("click",()=>runForm(box,async()=>{await api("/api/state");},"Run status refreshed."));const cancel=node("button",activeRun.cancel_requested?"Cancellation requested":"Cancel current update","button");cancel.type="button";cancel.disabled=activeRun.cancel_requested;cancel.addEventListener("click",()=>runForm(box,()=>api("/api/knowledge/runs/"+activeRun.id+"/cancel",{}),"Cancellation requested. An in-flight request can finish before stopping."));actions.append(refreshButton,cancel);box.append(actions);}
  if(activeRun)box.append(node("p","Source checking is running. Knowledge editing resumes when the run finishes.","note"));
  const gaps=workspaceDetails("Coverage still requiring setup or professional review");const list=node("ul",undefined,"knowledge-gaps");k.coverage_gaps.forEach(g=>list.append(node("li",g)));gaps.append(list);box.append(gaps);
  const items=workspaceDetails("Reviewed knowledge · "+k.items.filter(i=>i.status==="active").length+" active versions",k.items.some(i=>i.status==="active"));
  k.items.filter(i=>i.status==="active").forEach(item=>renderKnowledgeItem(item,items));box.append(items);
  const runs=workspaceDetails("Source check history · "+k.runs.length,Boolean(k.runs.length));
  k.runs.forEach((run,index)=>{
    const detail=workspaceDetails(date(run.created_at)+" · "+readable(run.status)+" · "+run.snapshots.filter(s=>s.status==="succeeded").length+" / "+run.snapshots.length+" sources checked",index===0);detail.classList.add("knowledge-run");detail.append(node("p",run.jurisdiction+" · Requested by "+run.initiated_by,"muted small"));run.snapshots.forEach(s=>renderKnowledgeSnapshot(s,detail));runs.append(detail);
  });box.append(runs);
  const history=workspaceDetails("Knowledge versions & rollback · "+k.items.length);
  k.items.filter(i=>i.status!=="active").forEach(item=>renderKnowledgeItem(item,history));
  k.events.forEach(event=>history.append(node("p",readable(event.action)+" · "+event.reviewer+" · "+date(event.created_at)+" · "+event.note,"muted small")));box.append(history);
  if(activeRun)box.querySelectorAll("form").forEach(item=>item.inert=true);
}
function renderKnowledgeSnapshot(snapshot, box) {
  const source=state.knowledge.sources.find(s=>s.id===snapshot.source_id);
  const card=node("article",undefined,"knowledge-source");card.dataset.snapshotId=snapshot.id;card.append(node("h4",source.name),node("span",readable(snapshot.status)+(snapshot.cached?" · cached":""),"pill open"));
  const link=node("a","Open original source");link.href=snapshot.source_url;link.target="_blank";link.rel="noopener noreferrer";card.append(node("p",source.publisher,"muted small"),link);
  if(snapshot.error)card.append(node("p",snapshot.error,"note"));
  if(snapshot.status==="succeeded"){
    card.append(node("p",snapshot.data.title),node("p",snapshot.data.excerpt,"reply-body"),node("p","Content result: "+readable(snapshot.diff.kind)+" · Retrieved "+date(snapshot.checked_at)+(snapshot.cached?"; reused cached check":""),"muted small"));
    card.append(node("p","Reported publication date: "+(snapshot.data.reported_published_on||"unknown")+". This is separate from an effective date or verification of applicability.","muted small"));
    if(snapshot.review_status!=="pending")card.append(node("p","Review: "+snapshot.review_status+" · "+snapshot.review_note,"muted small"));
    else{
      const details=workspaceDetails("Review source and write an internal knowledge note");const form=node("form",undefined,"knowledge-review-form workspace-form");
      workspaceField(form,"title","Your note title");workspaceSelect(form,"claim_type","Type of note",[["interpretation","Reviewed interpretation"],["operational_note","Internal process note"],["source_metadata","Source metadata note"]]);
      communicationText(form,"claim","Your paraphrased knowledge / observation","",true);communicationText(form,"applicability","Where it applies and what remains unverified","",true);
      workspaceField(form,"published_on","Confirmed source publication date (optional)","date",snapshot.data.reported_published_on||"",false);workspaceField(form,"effective_on","Confirmed effective date (optional)","date","",false);
      workspaceField(form,"reviewer","Reviewer","text","Owner");workspaceField(form,"review_reference","Review evidence reference");
      workspaceField(form,"professional_review_reference","Professional applicability review reference"+(snapshot.domain==="compliance"?" (required for compliance)":" (optional)"),"text","",snapshot.domain==="compliance");
      communicationText(form,"note","Review decision note","",true);communicationCheck(form,"owner_verified_source","I opened the original source and compared the note, dates and scope.",true);
      const buttons=node("div",undefined,"knowledge-actions full-width");const accept=node("button","Publish reviewed note","button primary");accept.type="submit";const reject=node("button","Reject source check","button");reject.type="button";
      reject.addEventListener("click",()=>runForm(form,()=>api("/api/knowledge/snapshots/"+snapshot.id+"/review",{decision:"reject",reviewer:form.elements.reviewer.value,note:form.elements.note.value}),"Source check rejected. Existing knowledge retained."));buttons.append(accept,reject);form.append(buttons);
      form.addEventListener("submit",e=>{e.preventDefault();const data=values(form);data.decision="accept";data.owner_verified_source=form.elements.owner_verified_source.checked;runForm(form,()=>api("/api/knowledge/snapshots/"+snapshot.id+"/review",data),"Reviewed internal knowledge published. Execution rules unchanged.");});details.append(node("p","A successful page check does not verify a legal rule, title, tax result, market price or financing. Publishing changes only your internal notes.","muted small"),form);card.append(details);
    }
  }box.append(card);
}
function renderKnowledgeItem(item, box) {
  const card=node("article",undefined,"knowledge-item");card.dataset.itemId=item.id;card.append(node("h3",item.title),node("p",item.claim),node("p",item.applicability,"muted small"));
  card.append(node("p",readable(item.domain)+" · "+item.status+" · "+item.jurisdiction+" · Reviewed by "+item.reviewer,"muted small"));
  const source=node("a","Reviewed source");source.href=item.source_url;source.target="_blank";source.rel="noopener noreferrer";card.append(source);
  if(item.source_changed_since_review||item.source_check_older_than_seven_days)card.append(node("p",item.source_changed_since_review?"The source changed after this version was reviewed. Review the new check before relying on the note.":"The last successful source check is older than seven days. Refresh before relying on the note.","note"));
  const details=workspaceDetails(item.status==="active"?"Withdraw this version":"Restore this historical version");const form=node("form",undefined,"knowledge-version-form workspace-form");
  workspaceField(form,"reviewer","Reviewer","text","Owner");communicationText(form,"note","Reason and applicability review","",true);workspaceField(form,"evidence_reference","Version review evidence reference");communicationCheck(form,"owner_reviewed","I reviewed this version and its source date. It changes internal notes only.",true);workspaceSubmit(form,item.status==="active"?"Withdraw version":"Restore version");
  form.addEventListener("submit",e=>{e.preventDefault();const data=values(form);data.owner_reviewed=form.elements.owner_reviewed.checked;data.action=item.status==="active"?"withdraw":"activate";runForm(form,()=>api("/api/knowledge/items/"+item.id+"/version",data),"Knowledge version updated with an audit record.");});details.append(form);card.append(details);box.append(card);
}
