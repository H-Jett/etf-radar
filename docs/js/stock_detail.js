/* ===== 个股详情：行业内成员股 持股市值(报告期)多线 + 个股持有机构明细 + 多行业标签 ===== */
const qs=new URLSearchParams(location.search);
let CHART, PERIODS=[], STOCKMAP={}, BYIND={}, CUR=null, PREFER=null, LEGEND=null;
const SERIESCACHE={};

async function boot(){
  CHART=echarts.init(document.getElementById('chart'));
  window.addEventListener('resize',()=>CHART.resize());
  document.getElementById('btn-all').onclick=()=>toggleAll(true);
  document.getElementById('btn-none').onclick=()=>toggleAll(false);
  try{
    const [meta,stocks,periods]=await Promise.all([
      fetch('data/stock/meta.json').then(r=>r.json()),
      fetch('data/stock/stocks.json').then(r=>r.json()),
      fetch('data/stock/holders/periods.json').then(r=>r.json()),
    ]);
    setIndustryOrder(meta.industry_order||[]);
    PERIODS=periods.periods||[];
    stocks.forEach(s=>{STOCKMAP[s.code]=s; (BYIND[s.industry]=BYIND[s.industry]||[]).push(s);});
    buildIndSelect();
    const wantCode=qs.get('code');
    const want=qs.get('ind')||(wantCode&&STOCKMAP[wantCode]?STOCKMAP[wantCode].industry:null);
    const inds=Object.keys(BYIND).sort((a,b)=>sumMv(BYIND[b])-sumMv(BYIND[a]));
    const hit=inds.includes(want)?want:inds[0];
    document.getElementById('ind-select').value=hit;
    PREFER=(wantCode&&STOCKMAP[wantCode])?wantCode:null;
    await loadIndustry(hit);
  }catch(e){ document.getElementById('chart').innerHTML='<div class="loading">数据尚未生成：'+e+'</div>'; }
}
function sumMv(arr){return arr.reduce((s,x)=>s+(x.mv||0),0);}
function buildIndSelect(){
  const sel=document.getElementById('ind-select');
  const inds=Object.keys(BYIND).sort((a,b)=>sumMv(BYIND[b])-sumMv(BYIND[a]));
  sel.innerHTML=inds.map(i=>`<option value="${i}">${i}（${BYIND[i].length} 只）</option>`).join('');
  sel.onchange=()=>{history.replaceState(null,'','stock_detail.html?ind='+encodeURIComponent(sel.value));loadIndustry(sel.value);};
}
async function loadIndustry(ind){
  LEGEND=null;
  const members=[...BYIND[ind]].sort((a,b)=>b.mv-a.mv);
  // 逐成员载入分期持仓(缓存)
  const series={};
  await Promise.all(members.map(async s=>{
    if(!SERIESCACHE[s.code]){
      SERIESCACHE[s.code]=await fetch(`data/stock/holders/stock/${s.code}.json`).then(r=>r.json()).catch(()=>({periods:{}}));
    }
    const per=SERIESCACHE[s.code].periods||{};
    series[s.code]=PERIODS.map(d=>per[d]?per[d].mv:null);
  }));
  CUR={ind, members, series};
  renderHead(); renderChart(); buildHolderSelect();
}
function renderHead(){ document.getElementById('ind-head').innerHTML=`<b style="font-size:19px">${CUR.ind}</b>`; }
function renderChart(){
  const members=CUR.members;
  const total=PERIODS.map((_,i)=>{let t=0,has=false;members.forEach(s=>{const v=CUR.series[s.code][i];if(v!=null){t+=v;has=true;}});return has?t:null;});
  const names=['总量',...members.map(s=>`${s.name}(${s.code})`)];
  if(!LEGEND){LEGEND={}; names.forEach((n,i)=>LEGEND[n]=(i<=8));}
  const series=[
    {name:'总量',type:'line',smooth:false,showSymbol:true,symbolSize:5,z:10,connectNulls:true,
     lineStyle:{width:3,color:'#c8102e'},itemStyle:{color:'#c8102e'},areaStyle:{color:'rgba(200,16,46,.05)'},data:total},
    ...members.map((s,i)=>({name:`${s.name}(${s.code})`,type:'line',smooth:false,showSymbol:true,symbolSize:4,connectNulls:true,
     lineStyle:{width:1.5,color:colorAt(i)},itemStyle:{color:colorAt(i)},data:CUR.series[s.code]})),
  ];
  CHART.setOption({animationDuration:400,grid:{left:58,right:20,top:40,bottom:36},
    legend:{type:'scroll',top:4,data:names,selected:LEGEND,textStyle:{fontSize:11}},
    tooltip:{trigger:'axis',formatter:ps=>{let s=ps[0].axisValue+'<br/>';
      ps.filter(p=>p.value!=null).sort((a,b)=>b.value-a.value).slice(0,15).forEach(p=>{s+=`${p.marker}${p.seriesName}：<b>${yi(p.value)}元</b><br/>`;});return s;}},
    xAxis:{type:'category',data:PERIODS,boundaryGap:false,axisLabel:{fontSize:11}},
    yAxis:{type:'value',name:'持股市值',scale:false,axisLabel:{fontSize:11,formatter:v=>yi(v)},splitLine:{lineStyle:{color:'#eef1f6'}}},
    series},true);
  CHART.off('legendselectchanged'); CHART.on('legendselectchanged',p=>{LEGEND=p.selected;});
}
function toggleAll(on){ if(!LEGEND)return; Object.keys(LEGEND).forEach(k=>LEGEND[k]=on||k==='总量'); CHART.setOption({legend:{selected:LEGEND}}); }
function buildHolderSelect(){
  const sel=document.getElementById('holder-select');
  const opts=[...CUR.members].sort((a,b)=>b.mv-a.mv);
  sel.innerHTML=opts.map(s=>`<option value="${s.code}">${s.name}（${s.code}）</option>`).join('');
  sel.onchange=()=>renderHolders(sel.value);
  const def=(PREFER&&opts.some(s=>s.code===PREFER))?PREFER:(opts[0]&&opts[0].code); PREFER=null;
  if(def){sel.value=def; renderHolders(def);}
}
function renderHolders(code){
  const s=STOCKMAP[code]; if(!s)return;
  document.getElementById('rpt').textContent=s.report_date||'—';
  document.getElementById('stk-tags').innerHTML='行业：'+(s.industries||[s.industry]).map(t=>
    t===s.industry?`<b>${t}</b>`:t).join(' · ');
  document.querySelector('#nt-holders tbody').innerHTML=(s.holders||[]).map(h=>
    `<tr class="nt-row"><td class="ta-l nt">${h.holder}</td><td class="ta-l">${h.group||'—'}</td>`+
    `<td>${yi(h.num)}</td><td>${pct(h.ratio)}</td><td>${yi(h.mv)}</td><td>${h.change||'—'}</td></tr>`
  ).join('')||'<tr><td colspan="6" class="muted">无</td></tr>';
}
boot();
