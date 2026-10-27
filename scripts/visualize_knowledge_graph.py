# -*- coding: utf-8 -*-
"""Generate a standalone offline Canvas graph explorer, with no CDN or server."""
import argparse
import json
from pathlib import Path


def render(folder, output):
    nodes = [json.loads(s) for s in (folder/'nodes.jsonl').read_text(encoding='utf-8').splitlines()]
    edges = [json.loads(s) for s in (folder/'edges.jsonl').read_text(encoding='utf-8').splitlines()]
    lookup = {n['id']:n for n in nodes}
    links = {}
    for edge in edges:
        links.setdefault(edge['source'],{}).setdefault(edge['type'],[]).append(edge['target'])
    mentions = [dict(id=n['id'],label=n['annotation']['text'],type=n['annotation']['type'],layer=n['layer']) for n in nodes if n['kind']=='entity_mention']
    statements = []
    for n in nodes:
        if n['kind'] not in {'relations','qualified_statements'}:
            continue
        roles=links.get(n['id'],{})
        if not roles.get('HEAD') or not roles.get('TAIL'):
            continue
        scope=lookup[n['scope']]
        statements.append(dict(id=n['id'],source=roles['HEAD'][0],target=roles['TAIL'][0],
            predicate=n['predicate'],layer=n['layer'],kind=n['kind'],fact=n['default_fact_eligible'],
            annotation=n['annotation'],document=scope['document_id'],passage=scope['passage']))
    payload=json.dumps(dict(nodes=mentions,edges=statements),ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    html=HTML.replace('__GRAPH_DATA__',payload)
    output.write_text(html,encoding='utf-8')
    return dict(nodes=len(mentions),relations=len(statements),output=str(output))


HTML=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>煤地质 · 知识图谱</title>
<style>*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#e2e8f0;font:14px system-ui,"Microsoft YaHei",sans-serif}header{padding:22px 28px;border-bottom:1px solid #26334a;display:flex;align-items:center;gap:25px}h1{font-size:23px;margin:0}small,.muted{color:#91a4bf}main{display:grid;grid-template-columns:260px 1fr 340px;height:calc(100vh - 81px)}aside{padding:22px;overflow:auto;background:#101a2b}label{display:block;color:#9bb0cc;margin:18px 0 8px}input,select,button{width:100%;padding:10px;background:#18263b;border:1px solid #34445e;border-radius:7px;color:#e2e8f0}button{cursor:pointer;margin-top:12px}button:hover{background:#243b58}canvas{width:100%;height:100%;touch-action:none}.stage{position:relative;overflow:hidden}#count{position:absolute;top:16px;left:18px;color:#a9bfd9;pointer-events:none}#hint{position:absolute;bottom:20px;left:20px;color:#8ca2bf;pointer-events:none}h2{font-size:17px}#details{white-space:pre-wrap;word-break:break-word;line-height:1.75} .badge{display:inline-block;margin:5px 4px 0 0;padding:4px 8px;border-radius:20px;background:#243650}.legend{line-height:2.1;margin-top:22px}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:8px}#relations button{text-align:left;font-size:12px} @media(max-width:1000px){main{grid-template-columns:210px 1fr}#right{position:absolute;right:0;bottom:0;width:280px;max-height:40vh;border:1px solid #34445e}header small{display:none}}</style>
<header><h1>煤地质 <span style="color:#46d5ba">知识图谱</span></h1><small>基于标注证据的关系视图 · 本地离线 · 银标数据，未经人工金标验收</small></header>
<main><aside><h2>探索图谱</h2><label>实体搜索</label><input id="search" placeholder="例如 coal、Formation、煤"><label>审核层</label><select id="layer"><option value="facts">默认银标事实</option><option value="all">全部层</option><option value="binary">普通样本（含转述）</option><option value="extended">扩展 / 限定</option><option value="pending">待核查</option></select><label>实体类型</label><select id="type"><option value="all">全部类型</option></select><label>关系类型</label><select id="predicate"><option value="all">全部关系</option></select><button id="reset">重置视野</button><button id="clear">清除选择</button><div class="legend"><span class="dot" style="background:#46d5ba"></span>普通样本<br><span class="dot" style="background:#f5b85a"></span>扩展 / 限定<br><span class="dot" style="background:#ec7994"></span>待核查</div><p class="muted">每个节点是原文中的实体提及。同名节点不会自动合并。观测值与统计记录保留在 SQLite 中，本图显示有明确首尾实体的普通及限定关系。</p></aside>
<div class="stage"><div id="count"></div><canvas id="canvas"></canvas><div id="hint">滚轮缩放 · 拖动背景平移 · 拖动节点 · 点击实体或连线查看证据</div></div>
<aside id="right"><h2 id="detailTitle">选择一个节点</h2><div id="details">点击图中的实体查看相邻关系，点击连线查看原文、语气和限定条件。</div><div id="relations"></div></aside></main>
<script id="graph-data" type="application/json">__GRAPH_DATA__</script><script>
const data=JSON.parse(document.getElementById('graph-data').textContent),byId=new Map(data.nodes.map(n=>[n.id,n])),$=id=>document.getElementById(id),canvas=$('canvas'),ctx=canvas.getContext('2d');
let visible=[],links=[],selected=null,zoom=1,pan={x:0,y:0},W=0,H=0,drag=null,moved=false,frame=0;
const colors={binary:'#46d5ba',extended:'#f5b85a',pending:'#ec7994'};
for(const [id,values] of [['type',data.nodes.map(n=>n.type)],['predicate',data.edges.map(n=>n.predicate)]])for(const value of [...new Set(values)].sort()){const o=document.createElement('option');o.value=value;o.textContent=value;$(id).appendChild(o)}
function detail(title,text){$('detailTitle').textContent=title;$('details').textContent=text;$('relations').replaceChildren()}
function showEdge(e){selected=e.id;detail(e.predicate,`${byId.get(e.source).label} → ${byId.get(e.target).label}\n\n层：${e.layer} / ${e.kind}\n默认事实：${e.fact?'是（银标）':'否'}\n文档：${e.document}\n\n完整标注（含语气与限定）：\n${JSON.stringify(e.annotation,null,2)}\n\n原文范围：\n${e.passage}`);draw()}
function showNode(n){selected=n.id;detail(n.label,`实体类型：${n.type}\n审核层：${n.layer}\n\n当前视图关联关系：`);for(const e of links.filter(e=>e.source===n.id||e.target===n.id)){const b=document.createElement('button');b.textContent=`${byId.get(e.source).label} → ${e.predicate} → ${byId.get(e.target).label}`;b.onclick=()=>showEdge(e);$('relations').appendChild(b)}draw()}
function filter(){const q=$('search').value.trim().toLowerCase(),layer=$('layer').value,type=$('type').value,predicate=$('predicate').value;links=data.edges.filter(e=>(layer==='all'||(layer==='facts'?e.fact:e.layer===layer))&&(predicate==='all'||e.predicate===predicate)&&(type==='all'||byId.get(e.source).type===type||byId.get(e.target).type===type)&&(!q||byId.get(e.source).label.toLowerCase().includes(q)||byId.get(e.target).label.toLowerCase().includes(q)));const ids=new Set(links.flatMap(e=>[e.source,e.target]));visible=data.nodes.filter(n=>ids.has(n.id));const groups=new Map();for(const n of visible){const k=n.id; if(!groups.has(k))groups.set(k,[])}const cols=Math.max(1,Math.ceil(Math.sqrt(visible.length*W/Math.max(H,1))));visible.forEach((n,i)=>{n.x=(i%cols-cols/2)*100;n.y=(Math.floor(i/cols)-Math.ceil(visible.length/cols)/2)*85;n.vx=0;n.vy=0});$('count').textContent=`${visible.length} 个实体提及 · ${links.length} 条关系`;frame=0;fit();}
function fit(){const xs=visible.map(n=>n.x),ys=visible.map(n=>n.y);zoom=Math.min(1,W/(Math.max(200,...xs)-Math.min(-200,...xs)+150),H/(Math.max(180,...ys)-Math.min(-180,...ys)+150));pan={x:0,y:0};draw()}
function draw(){ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);ctx.clearRect(0,0,W,H);ctx.translate(W/2+pan.x,H/2+pan.y);ctx.scale(zoom,zoom);for(const e of links){const a=byId.get(e.source),b=byId.get(e.target);ctx.strokeStyle=selected===e.id?'#ffffff':'#3b526e';ctx.lineWidth=(selected===e.id?2.5:1)/zoom;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();const angle=Math.atan2(b.y-a.y,b.x-a.x),x=b.x-Math.cos(angle)*12,y=b.y-Math.sin(angle)*12;ctx.fillStyle=colors[e.layer];ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x-9*Math.cos(angle-.4),y-9*Math.sin(angle-.4));ctx.lineTo(x-9*Math.cos(angle+.4),y-9*Math.sin(angle+.4));ctx.fill();if(zoom>.8||selected===e.id){ctx.fillStyle='#8197b3';ctx.font='10px system-ui';ctx.fillText(e.predicate,(a.x+b.x)/2,(a.y+b.y)/2-5)}}for(const n of visible){ctx.beginPath();ctx.arc(n.x,n.y,selected===n.id?10:6,0,Math.PI*2);ctx.fillStyle=colors[n.layer];ctx.fill();if(selected===n.id){ctx.strokeStyle='#fff';ctx.lineWidth=2/zoom;ctx.stroke()}if(zoom>.4||selected===n.id){ctx.fillStyle='#d9e5f4';ctx.font=`${Math.min(18,12/Math.max(zoom,.5))}px system-ui`;ctx.fillText(n.label.length>32?n.label.slice(0,30)+'…':n.label,n.x+10,n.y+4)}}}
function tick(){if(frame++<260&&visible.length){const k=visible.length;for(let i=0;i<k;i++)for(let j=i+1;j<k;j++){let a=visible[i],b=visible[j],dx=a.x-b.x,dy=a.y-b.y,d=Math.max(40,dx*dx+dy*dy);if(d<140000){a.vx+=dx*180/d;b.vx-=dx*180/d;a.vy+=dy*180/d;b.vy-=dy*180/d}}for(const e of links){let a=byId.get(e.source),b=byId.get(e.target),dx=b.x-a.x,dy=b.y-a.y,d=Math.max(1,Math.hypot(dx,dy)),f=(d-135)*.008;a.vx+=dx/d*f;b.vx-=dx/d*f;a.vy+=dy/d*f;b.vy-=dy/d*f}for(const n of visible){if(drag?.node===n)continue;n.vx=(n.vx-n.x*.0004)*.7;n.vy=(n.vy-n.y*.0004)*.7;n.x+=n.vx;n.y+=n.vy}draw()}requestAnimationFrame(tick)}
function point(e){const r=canvas.getBoundingClientRect();return{x:(e.clientX-r.left-W/2-pan.x)/zoom,y:(e.clientY-r.top-H/2-pan.y)/zoom}}
canvas.onpointerdown=e=>{const p=point(e),node=visible.find(n=>Math.hypot(n.x-p.x,n.y-p.y)<12/zoom);drag={node,x:e.clientX,y:e.clientY};moved=false;canvas.setPointerCapture(e.pointerId)};
canvas.onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.hypot(dx,dy)>2)moved=true;if(drag.node){const p=point(e);drag.node.x=p.x;drag.node.y=p.y}else{pan.x+=dx;pan.y+=dy}drag.x=e.clientX;drag.y=e.clientY;draw()};
canvas.onpointerup=e=>{if(!moved){if(drag?.node)showNode(drag.node);else{const p=point(e);let hit=null,best=10/zoom;for(const l of links){const a=byId.get(l.source),b=byId.get(l.target),dx=b.x-a.x,dy=b.y-a.y,t=Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy||1))),d=Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);if(d<best){best=d;hit=l}}if(hit)showEdge(hit)}}drag=null};
canvas.addEventListener('wheel',e=>{e.preventDefault();const p=point(e),z=Math.max(.06,Math.min(5,zoom*Math.exp(-e.deltaY*.001)));pan.x+=(zoom-z)*p.x;pan.y+=(zoom-z)*p.y;zoom=z;draw()},{passive:false});
function resize(){const r=canvas.getBoundingClientRect();W=r.width;H=r.height;canvas.width=W*devicePixelRatio;canvas.height=H*devicePixelRatio;draw()}window.onresize=resize;
for(const id of ['search','layer','type','predicate'])$(id).addEventListener(id==='search'?'input':'change',filter);$('reset').onclick=fit;$('clear').onclick=()=>{selected=null;detail('选择一个节点','点击节点或连线查看证据。');draw()};resize();filter();tick();
</script></html>'''


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--graph',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(render(a.graph,a.output)))
