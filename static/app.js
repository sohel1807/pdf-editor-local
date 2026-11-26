const $ = id => document.getElementById(id);
let docId=null, page=0, pages=0, filename="", blocks=[], pageSize=null, edits={}, selectedKey=null, selectedAdditionKey=null, draftChanged=false, undoStack=[], redoStack=[], pageImageUrl=null;
let addingText=false, additionLocation=null, drawingArea=null;
let previewTimer=null, previewAbort=null, previewUrl=null, previewSequence=0;
let availableFonts=[];
const keyFor=(p,k)=>`${p}:${k}`;
const wordSpacingValue=()=>{const value=$("word-spacing").value.trim();return value===""?null:Number(value)};
function toast(msg){$("toast").textContent=msg;$("toast").style.display="block";setTimeout(()=>$("toast").style.display="none",3200)}
async function api(url,opts={}){
 const response=await fetch(url,opts);
 if(!response.ok){
  let message="Request failed";
  try{const data=await response.json();message=data.detail||message}catch{}
  throw new Error(message);
 }
 return response;
}
function copyEdits(source){
 const copy={};
 Object.entries(source).forEach(([key,value])=>{copy[key]={...value,bbox:[...value.bbox],origin:value.origin?[...value.origin]:undefined,sourceIds:value.sourceIds?.map(id=>[...id])}});
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
 selectedAdditionKey=null;
 draftChanged=false;
 $("replacement-label").textContent="Replace with";
 $("replacement").placeholder="Select text on the page, then enter replacement text...";
 $("replacement").value=staged?staged.text:b.text;
 $("size").value=staged?.size??b.size;
 $("color").value=staged?.color??b.color;
 $("font").value=staged?.font??"original";
 $("word-spacing").value=staged?.wordSpacing??"";
 $("align").value=staged?.align??"left";
 $("apply").disabled=false;
}
function loadAddedText(key){
 const edit=edits[key];
 if(!edit)return;
 selectedAdditionKey=key;selectedKey=null;additionLocation=null;addingText=false;drawingArea=null;draftChanged=false;
 $("replacement-label").textContent="Edit added text";
 $("replacement").placeholder="Edit the text in this blue area...";
 $("replacement").value=edit.text;
 $("size").value=edit.size??11;
 $("color").value=edit.color??"#000000";
 $("font").value=edit.font??"original";
 $("word-spacing").value=edit.wordSpacing??"";
 $("align").value=edit.align??"left";
 $("apply").disabled=true;
 $("placement-help").textContent="This added area is saved. Edit its text, then click Apply changes.";
}
function changeHistory(from,to,message){
 if(draftChanged){
  const selected=blocks.find(b=>b.key===selectedKey);
  draftChanged=false;additionLocation=null;addingText=false;drawingArea=null;
  if(selectedAdditionKey&&edits[selectedAdditionKey])loadAddedText(selectedAdditionKey);
  else if(selected)loadSelectedBlock(selected);
  else{
   selectedAdditionKey=null;
   $("replacement-label").textContent="Replace with";
   $("replacement").value="";$("replacement").placeholder="Select text on the page, then enter replacement text...";
   $("apply").disabled=true;
  }
  if(!selectedAdditionKey)$("placement-help").textContent="Choose Fill blank area, then drag over an area on the page.";
  refreshPage();toast("Unapplied edit discarded.");return;
 }
 if(!from.length)return;
 to.push(copyEdits(edits));edits=from.pop();draftChanged=false;
 if(selectedAdditionKey&&!edits[selectedAdditionKey])selectedAdditionKey=null;
 if(selectedAdditionKey)loadAddedText(selectedAdditionKey);
 const selected=blocks.find(b=>b.key===selectedKey);
 if(!selectedAdditionKey&&selected)loadSelectedBlock(selected);
 refreshPage();toast(message);
}
$("file").addEventListener("change",async e=>{
 const f=e.target.files[0];if(!f)return;
 if(f.size>50*1024*1024){toast("For this prototype, choose a PDF smaller than 50 MB.");return}
 try{
  const fd=new FormData();fd.append("file",f);
  const r=await api("/api/upload",{method:"POST",body:fd});const d=await r.json();
  docId=d.id;pages=d.pages;page=0;filename=f.name;edits={};undoStack=[];redoStack=[];selectedKey=null;selectedAdditionKey=null;draftChanged=false;addingText=false;additionLocation=null;drawingArea=null;updateHistoryButtons();
  $("filename").textContent=filename;$("download").disabled=false;$("add-text").disabled=false;$("prev").disabled=false;$("next").disabled=pages<2;
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
 $("prev").disabled=page===0;$("next").disabled=page===pages-1;
 const imageResponse=await api(`/api/document/${docId}/page/${page}`);
 const imageUrl=URL.createObjectURL(await imageResponse.blob());
 if(pageImageUrl)URL.revokeObjectURL(pageImageUrl);
 pageImageUrl=imageUrl;img.src=imageUrl;
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
 overlay.classList.toggle("add-mode",addingText);
 $("canvas-wrap").classList.toggle("selection-mode",addingText);
 overlay.onpointerdown=event=>{
  if(!addingText||event.button!==0)return;
  const bounds=overlay.getBoundingClientRect();
  if(!bounds.width||!bounds.height)return;
  const x=(event.clientX-bounds.left)/bounds.width*pageSize.width;
  const y=(event.clientY-bounds.top)/bounds.height*pageSize.height;
  drawingArea={pointerId:event.pointerId,startX:x,startY:y,currentX:x,currentY:y};
  additionLocation=null;
  overlay.setPointerCapture(event.pointerId);
  event.preventDefault();
  updatePlacementMarker(overlay);
 };
 overlay.onpointermove=event=>{
  if(!drawingArea||event.pointerId!==drawingArea.pointerId)return;
  event.preventDefault();
  const bounds=overlay.getBoundingClientRect();
  drawingArea.currentX=Math.max(0,Math.min(pageSize.width,(event.clientX-bounds.left)/bounds.width*pageSize.width));
  drawingArea.currentY=Math.max(0,Math.min(pageSize.height,(event.clientY-bounds.top)/bounds.height*pageSize.height));
  updatePlacementMarker(overlay);
 };
 overlay.onpointerup=event=>{
  if(!drawingArea||event.pointerId!==drawingArea.pointerId)return;
  const bounds=overlay.getBoundingClientRect();
  drawingArea.currentX=Math.max(0,Math.min(pageSize.width,(event.clientX-bounds.left)/bounds.width*pageSize.width));
  drawingArea.currentY=Math.max(0,Math.min(pageSize.height,(event.clientY-bounds.top)/bounds.height*pageSize.height));
  const box=placementBounds(drawingArea);
  drawingArea=null;
  if(box[2]-box[0]<22||box[3]-box[1]<16){
   updatePlacementMarker(overlay);toast("Drag to select a larger writing area.");return;
  }
  additionLocation={page,bbox:box};
  addingText=false;selectedKey=null;draftChanged=false;
  $("replacement-label").textContent="Text to add";
  $("replacement").placeholder="Enter the text to place in the selected area...";
  $("apply").disabled=!$("replacement").value.trim();
  $("placement-help").textContent="Area selected. Enter text, then click Apply changes.";
  renderPageTargets(overlay);renderPagePreviews();$("replacement").focus({preventScroll:true});
 };
 overlay.onpointercancel=event=>{
  if(!drawingArea||event.pointerId!==drawingArea.pointerId)return;
  drawingArea=null;updatePlacementMarker(overlay);
 };
 updatePlacementMarker(overlay);
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
 Object.entries(edits).forEach(([key,edit])=>{
  if(edit.mode!=="add"||edit.page!==page)return;
  const [x0,y0,x1,y1]=edit.bbox;
  const target=document.createElement("button");target.type="button";
  target.className="added-text-target"+(selectedAdditionKey===key?" active":"");
  target.style.left=`${x0/pageSize.width*100}%`;
  target.style.top=`${y0/pageSize.height*100}%`;
  target.style.width=`${(x1-x0)/pageSize.width*100}%`;
  target.style.height=`${(y1-y0)/pageSize.height*100}%`;
  target.title="Click to edit added text";
  target.setAttribute("aria-label",`Edit added text: ${edit.text}`);
  target.onclick=()=>{loadAddedText(key);refreshPage()};
  overlay.appendChild(target);
 });
}
function placementBounds(area){
 return [
  Math.min(area.startX,area.currentX),Math.min(area.startY,area.currentY),
  Math.max(area.startX,area.currentX),Math.max(area.startY,area.currentY)
 ];
}
function updatePlacementMarker(overlay){
 let bbox=additionLocation&&additionLocation.page===page?additionLocation.bbox:null;
 if(drawingArea)bbox=placementBounds(drawingArea);
 let marker=overlay.querySelector(".add-placement-marker");
 if(!bbox){
  marker?.remove();return;
 }
 if(!marker){
  marker=document.createElement("div");marker.className="add-placement-marker";
  marker.onclick=event=>{event.stopPropagation();$("replacement").focus({preventScroll:true})};
  overlay.appendChild(marker);
 }
 const [x0,y0,x1,y1]=bbox;
 marker.style.left=`${x0/pageSize.width*100}%`;
 marker.style.top=`${y0/pageSize.height*100}%`;
 marker.style.width=`${(x1-x0)/pageSize.width*100}%`;
 marker.style.height=`${(y1-y0)/pageSize.height*100}%`;
}
function renderPagePreviews(){
 const overlay=document.querySelector(".edit-previews");
 if(!overlay||!pageSize)return;
 clearTimeout(previewTimer);
 if(previewAbort)previewAbort.abort();
 const sequence=++previewSequence;
 const pageEdits=Object.entries(edits)
  .filter(([key,edit])=>edit.page===page&&!(draftChanged&&(key===keyFor(page,selectedKey)||key===selectedAdditionKey)))
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
 if(additionLocation&&additionLocation.page===page&&$("replacement").value.trim()){
  pageEdits.push({
   mode:"add",page,bbox:additionLocation.bbox,
   text:$("replacement").value,size:Number($("size").value)||11,
   color:$("color").value,font:$("font").value,
   wordSpacing:wordSpacingValue(),align:$("align").value
  });
 }
 if(draftChanged&&selectedAdditionKey&&$("replacement").value.trim()){
  const original=edits[selectedAdditionKey];
  if(original){
   pageEdits.push({
    ...original,text:$("replacement").value,
    size:Number($("size").value)||original.size,
    color:$("color").value,font:$("font").value,
    wordSpacing:wordSpacingValue(),align:$("align").value
   });
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
 addingText=false;additionLocation=null;drawingArea=null;selectedAdditionKey=null;selectedKey=b.key;loadSelectedBlock(b);
 $("placement-help").textContent="Choose Fill blank area, then drag over an area on the page.";
 refreshPage();
}
$("replacement").addEventListener("input",()=>{
 draftChanged=true;
 $("apply").disabled=additionLocation||selectedAdditionKey?!$("replacement").value.trim():!selectedKey;
 renderPagePreviews();
});
$("font-search").addEventListener("input",renderFontOptions);
["size","color","font","word-spacing","align"].forEach(id=>$(id).addEventListener("input",()=>{
 draftChanged=true;
 $("apply").disabled=additionLocation||selectedAdditionKey?!$("replacement").value.trim():!selectedKey;
 renderPagePreviews();
}));
$("add-text").onclick=()=>{
 addingText=true;additionLocation=null;drawingArea=null;selectedKey=null;selectedAdditionKey=null;draftChanged=false;
 $("replacement-label").textContent="Text to add";
 $("replacement").value="";$("replacement").placeholder="Drag over a blank area on the page to select it...";
 $("apply").disabled=true;$("placement-help").textContent="Drag over the blank area where you want the new text.";
 renderPageTargets(document.querySelector(".text-overlay"));renderPagePreviews();
};
$("apply").onclick=()=>{
 if(additionLocation){
  if(!$("replacement").value.trim()){toast("Enter text before applying it.");return}
  undoStack.push(copyEdits(edits));redoStack=[];
  const key=`add:${Date.now()}:${Math.random().toString(36).slice(2)}`;
  edits[key]={
   mode:"add",page,bbox:additionLocation.bbox,
   text:$("replacement").value,size:Number($("size").value)||11,
   color:$("color").value,font:$("font").value,
   wordSpacing:wordSpacingValue(),align:$("align").value
  };
  selectedAdditionKey=null;additionLocation=null;addingText=false;drawingArea=null;draftChanged=false;
  $("replacement-label").textContent="Edit added text";
  $("replacement").placeholder="Edit the text in this blue area...";
  $("apply").disabled=true;$("placement-help").textContent="Added text is saved. Click it on the page to edit it again.";
  refreshPage();toast("Text added. Download to create the edited PDF.");return;
 }
 if(selectedAdditionKey){
  if(!$("replacement").value.trim()){toast("Enter text before applying it.");return}
  const existing=edits[selectedAdditionKey];if(!existing)return;
  undoStack.push(copyEdits(edits));redoStack=[];
  edits[selectedAdditionKey]={
   ...existing,text:$("replacement").value,
   size:Number($("size").value)||existing.size,
   color:$("color").value,font:$("font").value,
   wordSpacing:wordSpacingValue(),align:$("align").value
  };
  selectedAdditionKey=null;draftChanged=false;$("apply").disabled=true;
  $("placement-help").textContent="Added text is saved. Click it on the page to edit it again.";
  refreshPage();toast("Added text updated.");return;
 }
 if(!selectedKey)return;const b=blocks.find(x=>x.key===selectedKey);if(!b)return;
 undoStack.push(copyEdits(edits));redoStack=[];
 edits[keyFor(page,b.key)]={page,bbox:b.bbox,origin:b.origin,originalFont:b.font,originalText:b.text,sourceIds:b.sourceIds,originalSize:b.size,wordGap:b.wordGap,wordSpacing:wordSpacingValue(),fontFlags:b.fontFlags,text:$("replacement").value,size:Number($("size").value)||b.size,color:$("color").value,font:$("font").value,align:$("align").value};
 draftChanged=false;refreshPage();toast("Change staged. Download to create the edited PDF.");
};
$("undo").onclick=()=>changeHistory(undoStack,redoStack,"Last applied change undone.");
$("redo").onclick=()=>changeHistory(redoStack,undoStack,"Change restored.");
$("clear").onclick=()=>{selectedKey=null;selectedAdditionKey=null;draftChanged=false;addingText=false;additionLocation=null;drawingArea=null;$("replacement-label").textContent="Replace with";$("replacement").value="";$("replacement").placeholder="Select text on the page, then enter replacement text...";$("apply").disabled=true;$("placement-help").textContent="Choose Fill blank area, then drag over an area on the page.";refreshPage()};
$("prev").onclick=async()=>{if(page>0){page--;selectedKey=null;selectedAdditionKey=null;draftChanged=false;addingText=false;additionLocation=null;drawingArea=null;$("replacement-label").textContent="Replace with";$("apply").disabled=true;$("placement-help").textContent="Choose Fill blank area, then drag over an area on the page.";await loadPage()}};
$("next").onclick=async()=>{if(page<pages-1){page++;selectedKey=null;selectedAdditionKey=null;draftChanged=false;addingText=false;additionLocation=null;drawingArea=null;$("replacement-label").textContent="Replace with";$("apply").disabled=true;$("placement-help").textContent="Choose Fill blank area, then drag over an area on the page.";await loadPage()}};
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
