const $ = id => document.getElementById(id);
let docId=null, page=0, pages=0, filename="", blocks=[], pageSize=null, edits={}, selectedKey=null, draftChanged=false, undoStack=[], redoStack=[];
let previewTimer=null, previewAbort=null, previewUrl=null, previewSequence=0;
let availableFonts=[];
const keyFor=(p,k)=>`${p}:${k}`;
const wordSpacingValue=()=>{const value=$("word-spacing").value.trim();return value===""?null:Number(value)};
function toast(msg){$("toast").textContent=msg;$("toast").style.display="block";setTimeout(()=>$("toast").style.display="none",3200)}
async function api(url,opts){const r=await fetch(url,opts);if(!r.ok){let m="Request failed";try{const d=await r.json();m=d.detail||m}catch{}throw new Error(m)}return r}
function copyEdits(source){
 const copy={};
 Object.entries(source).forEach(([key,value])=>{copy[key]={...value,bbox:[...value.bbox],origin:[...value.origin],sourceIds:value.sourceIds?.map(id=>[...id])}});
 return copy;
}
function updateHistoryButtons(){$("undo").disabled=undoStack.length===0;$("redo").disabled=redoStack.length===0}
function renderFontOptions(){
 const select=$("font"),selected=select.value||"original";
 const query=$("font-search").value.trim().toLocaleLowerCase();
 const matches=availableFonts.filter(font=>`${font.family} ${font.style}`.toLocaleLowerCase().includes(query));
 select.replaceChildren(new Option("Original PDF font","original"));
 const selectedFont=availableFonts.find(font=>font.id===selected);
 if(selectedFont&&!matches.some(font=>font.id===selected)){
  select.add(new Option(`${selectedFont.family} — ${selectedFont.style} (selected)`,selectedFont.id));
 }
 matches.forEach(font=>select.add(new Option(`${font.family} — ${font.style}`,font.id)));
 select.value=selectedFont?selectedFont.id:"original";
}
async function loadFonts(){
 try{
  const response=await api("/api/fonts");availableFonts=await response.json();renderFontOptions();
 }catch(error){toast(`Installed fonts could not be loaded: ${error.message}`)}
}
function refreshPage(){
 renderBlocks();renderPageTargets(document.querySelector(".text-overlay"));renderPagePreviews();updateHistoryButtons();
}
function loadSelectedBlock(b){
 const staged=edits[keyFor(page,b.key)];
 draftChanged=false;
 $("replacement").value=staged?staged.text:b.text;
 $("size").value=staged?.size??b.size;
 $("color").value=staged?.color??b.color;
 $("font").value=staged?.font??"original";
 $("word-spacing").value=staged?.wordSpacing??"";
 $("align").value=staged?.align??"left";
 $("apply").disabled=false;
}
function changeHistory(from,to,message){
 if(draftChanged){
  const selected=blocks.find(b=>b.key===selectedKey);
  draftChanged=false;
  if(selected)loadSelectedBlock(selected);
  refreshPage();toast("Unapplied edit discarded.");return;
 }
 if(!from.length)return;
 to.push(copyEdits(edits));edits=from.pop();draftChanged=false;
 const selected=blocks.find(b=>b.key===selectedKey);
 if(selected)loadSelectedBlock(selected);
 refreshPage();toast(message);
}
$("file").addEventListener("change",async e=>{
 const f=e.target.files[0];if(!f)return;
 if(f.size>50*1024*1024){toast("For this prototype, choose a PDF smaller than 50 MB.");return}
 try{
  const fd=new FormData();fd.append("file",f);
  const r=await api("/api/upload",{method:"POST",body:fd});const d=await r.json();
  docId=d.id;pages=d.pages;page=0;filename=f.name;edits={};undoStack=[];redoStack=[];selectedKey=null;draftChanged=false;updateHistoryButtons();
  $("filename").textContent=filename;$("download").disabled=false;$("prev").disabled=false;$("next").disabled=pages<2;
  await loadPage();toast("PDF opened successfully.");
 }catch(err){toast(err.message)}
});
async function loadPage(){
 if(!docId)return;
 $("page-label").textContent=`Page ${page+1} / ${pages}`;
 const container=document.createElement("div");container.className="page-container";
 const img=document.createElement("img");img.className="page-image";img.alt=`Page ${page+1}`;
 const overlay=document.createElement("div");overlay.className="text-overlay";
 const previews=document.createElement("div");previews.className="edit-previews";
 img.addEventListener("load",()=>{renderPageTargets(overlay);renderPagePreviews()},{once:true});
 container.append(img,previews,overlay);$("canvas-wrap").replaceChildren(container);
 img.src=`/api/document/${docId}/page/${page}?t=${Date.now()}`;
 $("prev").disabled=page===0;$("next").disabled=page===pages-1;
 const r=await api(`/api/document/${docId}/blocks/${page}`);const data=await r.json();
 blocks=data.blocks;pageSize=data.page;renderBlocks();renderPageTargets(overlay);renderPagePreviews();
}
function renderBlocks(){
 const list=$("block-list");list.innerHTML="";
 if(!blocks.length){list.innerHTML='<div class="empty">No selectable text found on this page. It may be scanned.</div>';return}
 blocks.forEach(b=>{
  const el=document.createElement("div");el.className="block-item"+(selectedKey===b.key?" active":"");el.textContent=edits[keyFor(page,b.key)]?.text??b.text;el.title=b.text;
  el.onclick=()=>selectBlock(b);list.appendChild(el);
 });
}
function renderPageTargets(overlay){
 if(!overlay)return;
 overlay.replaceChildren();
 if(!pageSize)return;
 blocks.forEach(b=>{
  const [x0,y0,x1,y1]=b.bbox;
  const target=document.createElement("button");target.type="button";
  target.className="text-target"+(selectedKey===b.key?" active":"");
  target.style.left=`${x0/pageSize.width*100}%`;
  target.style.top=`${y0/pageSize.height*100}%`;
  target.style.width=`${(x1-x0)/pageSize.width*100}%`;
  target.style.height=`${(y1-y0)/pageSize.height*100}%`;
  target.title=b.text;target.setAttribute("aria-label",`Edit text: ${b.text}`);
  target.onclick=()=>selectBlock(b);overlay.appendChild(target);
 });
}
function renderPagePreviews(){
 const overlay=document.querySelector(".edit-previews");
 if(!overlay||!pageSize)return;
 clearTimeout(previewTimer);
 if(previewAbort)previewAbort.abort();
 const sequence=++previewSequence;
 const pageEdits=Object.entries(edits)
  .filter(([key,edit])=>edit.page===page&&!(draftChanged&&key===keyFor(page,selectedKey)))
  .map(([,edit])=>edit);
 if(draftChanged&&selectedKey){
  const block=blocks.find(item=>item.key===selectedKey);
  if(block){
   const edit={
    page,bbox:block.bbox,origin:block.origin,originalFont:block.font,originalText:block.text,sourceIds:block.sourceIds,
     originalSize:block.size,wordGap:block.wordGap,fontFlags:block.fontFlags,
    text:$("replacement").value,size:Number($("size").value)||block.size,
    color:$("color").value,font:$("font").value,wordSpacing:wordSpacingValue(),align:$("align").value
   };
   pageEdits.push(edit);
  }
 }
 if(!pageEdits.length){
  overlay.replaceChildren();
  if(previewUrl){URL.revokeObjectURL(previewUrl);previewUrl=null}
  return;
 }
 previewTimer=setTimeout(async()=>{
  const controller=new AbortController();previewAbort=controller;
  try{
   const response=await api(`/api/document/${docId}/preview/${page}`,{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({edits:pageEdits}),signal:controller.signal
   });
   const blob=await response.blob();
   if(sequence!==previewSequence)return;
   const url=URL.createObjectURL(blob);
   const image=document.createElement("img");image.className="preview-render";image.alt="Live PDF preview";image.src=url;
   overlay.replaceChildren(image);
   if(previewUrl)URL.revokeObjectURL(previewUrl);
   previewUrl=url;
  }catch(error){
   if(error.name!=="AbortError"&&sequence===previewSequence)toast(`Live preview unavailable: ${error.message}`);
  }
 },180);
}
function selectBlock(b){
 selectedKey=b.key;loadSelectedBlock(b);refreshPage();
}
$("replacement").addEventListener("input",()=>{draftChanged=true;renderPagePreviews()});
$("font-search").addEventListener("input",renderFontOptions);
["size","color","font","word-spacing","align"].forEach(id=>$(id).addEventListener("input",()=>{draftChanged=true;renderPagePreviews()}));
$("apply").onclick=()=>{
 if(!selectedKey)return;const b=blocks.find(x=>x.key===selectedKey);if(!b)return;
 undoStack.push(copyEdits(edits));redoStack=[];
 edits[keyFor(page,b.key)]={page,bbox:b.bbox,origin:b.origin,originalFont:b.font,originalText:b.text,sourceIds:b.sourceIds,originalSize:b.size,wordGap:b.wordGap,wordSpacing:wordSpacingValue(),fontFlags:b.fontFlags,text:$("replacement").value,size:Number($("size").value)||b.size,color:$("color").value,font:$("font").value,align:$("align").value};
 draftChanged=false;refreshPage();toast("Change staged. Download to create the edited PDF.");
};
$("undo").onclick=()=>changeHistory(undoStack,redoStack,"Last applied change undone.");
$("redo").onclick=()=>changeHistory(redoStack,undoStack,"Change restored.");
$("clear").onclick=()=>{selectedKey=null;draftChanged=false;$("replacement").value="";$("apply").disabled=true;refreshPage()};
$("prev").onclick=async()=>{if(page>0){page--;selectedKey=null;draftChanged=false;$("apply").disabled=true;await loadPage()}};
$("next").onclick=async()=>{if(page<pages-1){page++;selectedKey=null;draftChanged=false;$("apply").disabled=true;await loadPage()}};
$("download").onclick=async()=>{
 if(!docId)return;
 try{
  const editsArray=Object.values(edits);
  const r=await api(`/api/document/${docId}/export`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({edits:editsArray})});
  const blob=await r.blob();const url=URL.createObjectURL(blob);const a=document.createElement("a");
  a.href=url;a.download=filename.replace(/\.pdf$/i,"")+"_edited.pdf";document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
  toast("Edited PDF downloaded.");
 }catch(err){toast(err.message)}
};
updateHistoryButtons();
loadFonts();