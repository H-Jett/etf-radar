/* ===== 个股行业走势：持股市值 / 占比 报告期(季度)序列 ===== */
let CHART, METRIC='mv', PERIODS=null, LEGEND_SEL=null;

async function boot(){
  CHART=echarts.init(document.getElementById('chart'));
  window.addEventListener('resize',()=>CHART.resize());
  document.querySelectorAll('#metric-seg button').forEach(b=>b.onclick=()=>{
    document.querySelectorAll('#metric-seg button').forEach(x=>x.classList.remove('active'));
    b.classList.add('active'); METRIC=b.dataset.m; render();
  });
  try{
    const meta=await fetch('data/stock/meta.json').then(r=>r.json());
    setIndustryOrder(meta.industry_order||[]);
    PERIODS=await fetch('data/stock/holders/periods.json').then(r=>r.json());
    render();
  }catch(e){ document.getElementById('chart').innerHTML='<div class="loading">数据尚未生成：'+e+'</div>'; }
}
function latestVal(a){for(let i=a.length-1;i>=0;i--)if(a[i]!=null)return a[i];return -Infinity;}
function render(){
  const key=METRIC; // 'mv' | 'ratio'
  const inds={}; for(const[ind,s]of Object.entries(PERIODS.industries)) inds[ind]=s[key];
  const order=Object.keys(inds).sort((a,b)=>latestVal(inds[b])-latestVal(inds[a]));
  const colorFor={}; order.forEach(ind=>colorFor[ind]=colorOf(ind));
  if(!LEGEND_SEL){LEGEND_SEL={}; order.forEach((ind,i)=>LEGEND_SEL[ind]=i<8);}
  order.forEach((ind,i)=>{ if(!(ind in LEGEND_SEL)) LEGEND_SEL[ind]=i<8; });
  const isRatio=METRIC==='ratio';
  const series=order.map(ind=>({name:ind,type:'line',smooth:false,showSymbol:true,symbolSize:5,
    connectNulls:true,lineStyle:{width:2,color:colorFor[ind]},itemStyle:{color:colorFor[ind]},data:inds[ind]}));
  CHART.setOption({animationDuration:400,grid:{left:60,right:24,top:8,bottom:40},
    legend:{type:'scroll',top:0,data:order,selected:LEGEND_SEL,textStyle:{fontSize:11}},
    tooltip:{trigger:'axis',formatter:ps=>{let s=ps[0].axisValue+'<br/>';
      ps.filter(p=>p.value!=null).sort((a,b)=>b.value-a.value).slice(0,12).forEach(p=>{
        s+=`${p.marker}${p.seriesName}：<b>${isRatio?pct(p.value):yi(p.value)+'元'}</b><br/>`;});return s;}},
    xAxis:{type:'category',data:PERIODS.periods,boundaryGap:false,axisLabel:{fontSize:11}},
    yAxis:{type:'value',scale:isRatio,axisLabel:{fontSize:11,formatter:v=>isRatio?v+'%':yi(v)},splitLine:{lineStyle:{color:'#eef1f6'}}},
    series},true);
  CHART.off('legendselectchanged'); CHART.on('legendselectchanged',p=>{LEGEND_SEL=p.selected;});
  renderTable(inds,order,colorFor);
}
function renderTable(inds,order,colorFor){
  const P=PERIODS.periods, n=P.length, take=Math.min(10,n);
  const cols=[]; for(let i=n-take;i<n;i++)cols.push(i);
  document.querySelector('#trend-table thead').innerHTML='<tr><th class="ta-l">行业</th>'+
    cols.map(i=>`<th>${P[i]}</th>`).join('')+'<th>区间变化</th></tr>';
  const tb=document.querySelector('#trend-table tbody'); tb.innerHTML='';
  order.forEach(ind=>{
    const v=inds[ind], first=v[cols[0]], last=v[cols[cols.length-1]];
    let chg='—';
    if(first!=null&&last!=null){
      if(METRIC==='ratio'){const d=last-first;chg=`<span class="${d>0?'pos':(d<0?'neg':'')}">${d>0?'+':''}${d.toFixed(2)}pt</span>`;}
      else{const d=first?(last-first)/first*100:null;chg=d==null?'—':`<span class="${d>0?'pos':(d<0?'neg':'')}">${d>0?'+':''}${d.toFixed(1)}%</span>`;}
    }
    tb.innerHTML+=`<tr><td class="ta-l"><span class="ind-name"><span class="dot" style="background:${colorFor[ind]}"></span>${ind}</span></td>`+
      cols.map(i=>`<td>${v[i]==null?'—':(METRIC==='ratio'?pct(v[i]):yi(v[i]))}</td>`).join('')+`<td>${chg}</td></tr>`;
  });
  document.getElementById('table-hint').textContent=(METRIC==='ratio'?'平均持股占比':'国家队持股市值')+' · 最近 '+take+' 个报告期';
}
boot();
