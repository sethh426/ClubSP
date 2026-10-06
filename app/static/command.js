"use strict";

function commandNode(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function commandAmount(value) {
  return new Intl.NumberFormat("en-US", {style:"currency", currency:"USD", maximumFractionDigits:0}).format(value);
}

function readable(value) { return String(value || "").replaceAll("_", " "); }

function renderSummary(summary) {
  const box = document.getElementById("command-summary"); box.replaceChildren();
  [
    ["Owner-review deals", summary.owner_review_deals],
    ["Deal actions", summary.deal_actions],
    ["Due relationships", summary.due_relationships],
    ["Buyer criteria to confirm", summary.buyer_criteria_confirmations || 0],
    ["Buyer criteria to reconfirm", summary.buyer_criteria_reconfirmations || 0],
    ["Outreach setup blockers", summary.outreach_setup_blockers || 0],
    ["Buyer-matched candidates", summary.buyer_matched_candidates],
  ].forEach(([label,value]) => {
    const card=commandNode("article"); card.append(commandNode("span",label),commandNode("strong",String(value))); box.append(card);
  });
}

function renderItem(item) {
  const card=commandNode("article",undefined,"panel command-item");
  card.dataset.kind=item.kind;
  const head=commandNode("div",undefined,"panel-heading");
  const title=commandNode("div"); title.append(commandNode("p",readable(item.kind).toUpperCase(),"eyebrow"),commandNode("h3",item.title));
  head.append(title,commandNode("span",readable(item.status),"pill"));
  card.append(head,commandNode("p",item.next_action,"command-action"),commandNode("p","Source: "+item.source,"muted small"));
  const evidence=commandNode("div",undefined,"command-evidence");
  if(item.opportunity_score!==undefined && item.opportunity_score!==null) evidence.append(commandNode("span","Opportunity readiness "+item.opportunity_score+"/100","pill"));
  if(item.deal_readiness_score!==undefined && item.deal_readiness_score!==null) evidence.append(commandNode("span","Close-path evidence "+item.deal_readiness_score+"/100","pill"));
  if(item.criteria_fit_buyers!==undefined && item.criteria_fit_buyers!==null) evidence.append(commandNode("span",item.criteria_fit_buyers+" current criteria-fit buyer(s)","pill"));
  if(item.downside_net!==undefined && item.downside_net!==null) evidence.append(commandNode("span","Recorded downside "+commandAmount(item.downside_net),"pill"));
  if(item.follow_up_on) evidence.append(commandNode("span","Follow-up "+item.follow_up_on,"pill"));
  if(item.commitment_match_count) evidence.append(commandNode("span",item.commitment_match_count+" standing mandate match(es)","pill"));
  card.append(evidence);
  if(item.blockers?.length){
    const details=commandNode("details");details.append(commandNode("summary","Current blockers / reasons"));
    const list=commandNode("ul");item.blockers.forEach(blocker=>list.append(commandNode("li",blocker)));details.append(list);card.append(details);
  }
  const link=commandNode("a","Open source workspace","button");link.href=item.href;card.append(link);
  return card;
}

async function refreshCommand() {
  const result=document.getElementById("command-message");
  try {
    const [response,gmailResponse]=await Promise.all([
      fetch("/api/command-center"),
      fetch("/api/gmail/status"),
    ]);
    const data=await response.json();
    if(!response.ok) throw new Error(data.error || "Command center unavailable");
    const gmail=gmailResponse.ok ? await gmailResponse.json() : null;
    if(gmail?.configured && !gmail.connected){
      data.items.unshift({
        id:"setup:gmail",kind:"outreach_setup",source:"Gmail connection",
        title:"Reconnect Gmail · "+(gmail.expected_email || "configured mailbox"),
        status:"blocked",priority_band:0,source_rank:-1,
        next_action:"Connect Gmail read-only so ClubSP can review replies. Approved sending remains a separate permission and explicit action.",
        href:"/#gmail-workspace",blockers:["Gmail is configured but not connected."],
      });
      data.summary.outreach_setup_blockers=(data.summary.outreach_setup_blockers||0)+1;
      data.summary.focus_items=(data.summary.focus_items||0)+1;
    }
    document.getElementById("command-scope").textContent=data.scope;
    renderSummary(data.summary);
    const list=document.getElementById("command-list"); list.replaceChildren();
    if(!data.items.length) list.append(commandNode("p","No current revenue-focus items. Review sourcing, buyer mandates, and relationship schedules.","muted"));
    data.items.forEach(item=>list.append(renderItem(item)));
    result.hidden=true;
  } catch(error) {
    result.textContent=error.message;result.className="message error";result.hidden=false;
  }
}

document.getElementById("command-refresh").addEventListener("click",refreshCommand);
refreshCommand();
