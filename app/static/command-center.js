"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const box=document.getElementById("daily-money-actions");
  const status=document.getElementById("daily-money-status");
  const refresh=document.getElementById("daily-money-refresh");
  if(!box || !status || !refresh) return;

  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
  const readable=value=>String(value||"").replaceAll("_"," ");

  function render(data){
    box.replaceChildren();
    const top=data.top_action;
    status.textContent=data.total_actions
      ? data.total_actions+" current action(s). Top priority: "+top.title+"."
      : "No current money-path actions are recorded.";
    if(!data.actions.length){
      box.append(el("p","Nothing is currently due. Record buyer demand, sync replies, review candidates, or schedule relationship follow-ups.","muted small"));
      return;
    }
    data.actions.forEach((action,index)=>{
      const card=el("article",undefined,"evidence-row daily-money-action");
      const head=el("div",undefined,"row-head");
      head.append(el("strong",(index+1)+". "+action.title),el("span",readable(action.kind),"pill"));
      card.append(head,el("p",action.why,"small"),el("p","Next: "+action.next_action,"muted small"));
      if(action.external_action) card.append(el("p","External action requires explicit authorization at the destination workspace.","muted small"));
      const link=el("a","Open action","button secondary");link.href=action.href;card.append(link);
      box.append(card);
    });
  }

  async function load(){
    refresh.disabled=true;status.textContent="Refreshing current priorities…";
    try{
      const response=await fetch("/api/command-center");
      const data=await response.json();
      if(!response.ok) throw new Error(data.error||"Command center unavailable");
      render(data);
    }catch(error){status.textContent=error.message;box.replaceChildren();}
    finally{refresh.disabled=false;}
  }
  refresh.addEventListener("click",load);
  load();
});
