/* ===== 共享工具(四页复用,单一权威) ===== */

// 统一调色板
const PALETTE = ['#c8102e','#2b6cb0','#e07b39','#1a9e5f','#8a56c2','#d4a017',
  '#3aa0a0','#c2506e','#5b8c2a','#b5651d','#4a6fa5','#9c3848','#2f8f6b','#a06cd5',
  '#0e7490','#be123c','#7c3aed','#0891b2','#6b7280','#c99a2e'];

// 行业稳定取色:按 meta.industry_order 的固定序号,保证同一行业跨页同色
let _ORDER = [];
function setIndustryOrder(a){ _ORDER = a || []; }
function hashIdx(s){ let h=0; for(let i=0;i<s.length;i++) h=(h*31+s.charCodeAt(i))>>>0; return h; }
function colorOf(name){ const i=_ORDER.indexOf(name); return PALETTE[(i>=0?i:hashIdx(name))%PALETTE.length]; }
function colorAt(i){ return PALETTE[((i%PALETTE.length)+PALETTE.length)%PALETTE.length]; }

// 数值格式化
function yi(v){ if(v==null)return'—'; const a=Math.abs(v);
  if(a>=1e12)return(v/1e12).toFixed(2)+'万亿'; if(a>=1e8)return(v/1e8).toFixed(1)+'亿';
  if(a>=1e4)return(v/1e4).toFixed(1)+'万'; return(+v).toFixed(0); }
function pct(v){ return v==null?'—':(+v).toFixed(2)+'%'; }

// 用户偏好持久化(日/周/月 + 拖动时间段,跨页共享)
const LS = {
  get period(){ return localStorage.getItem('etf.period')||'D'; },
  set period(v){ try{ localStorage.setItem('etf.period',v); }catch(e){} },
  get zoom(){ try{ return JSON.parse(localStorage.getItem('etf.zoom'))||null; }catch(e){ return null; } },
  set zoom(v){ try{ localStorage.setItem('etf.zoom',JSON.stringify(v)); }catch(e){} },
};

// 日/周/月 分桶键(按日期本地零点解析,与时区无关地反映该日历日的 ISO 周)
function bucketKey(d,p){
  if(p==='M') return d.slice(0,7);
  if(p==='W'){ const t=new Date(d+'T00:00:00'); t.setDate(t.getDate()+3-((t.getDay()+6)%7));
    const w1=new Date(t.getFullYear(),0,4);
    const wn=1+Math.round(((t-w1)/864e5-3+((w1.getDay()+6)%7))/7);
    return t.getFullYear()+'-W'+String(wn).padStart(2,'0'); }
  return d;
}
