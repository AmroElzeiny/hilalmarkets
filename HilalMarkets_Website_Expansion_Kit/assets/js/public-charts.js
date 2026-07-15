(function(){
  const NS='http://www.w3.org/2000/svg';
  function el(tag,attrs={}){const n=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));return n;}
  function lineChart(root,series){
    const w=720,h=290,p={l:44,r:18,t:22,b:34};
    const svg=el('svg',{viewBox:`0 0 ${w} ${h}`,role:'img','aria-label':root.dataset.chartLabel||'Illustrative product chart'});
    const all=series.flatMap(s=>s.values),max=Math.max(...all)*1.12,min=Math.min(0,...all);
    const x=i=>p.l+i*(w-p.l-p.r)/(series[0].values.length-1);
    const y=v=>h-p.b-(v-min)*(h-p.t-p.b)/(max-min||1);
    [0,.25,.5,.75,1].forEach(q=>{const yy=p.t+q*(h-p.t-p.b);svg.append(el('line',{x1:p.l,y1:yy,x2:w-p.r,y2:yy,stroke:'#e5ece9','stroke-width':1}));});
    const colors=['#1f8b74','#d2af63','#6f62a8'];
    series.forEach((s,si)=>{
      const pts=s.values.map((v,i)=>[x(i),y(v)]);
      const path=pts.map((pt,i)=>(i?'L':'M')+pt[0]+' '+pt[1]).join(' ');
      svg.append(el('path',{d:path,fill:'none',stroke:colors[si%colors.length],'stroke-width':3,'stroke-linecap':'round','stroke-linejoin':'round'}));
      pts.forEach(pt=>svg.append(el('circle',{cx:pt[0],cy:pt[1],r:4,fill:'#fff',stroke:colors[si%colors.length],'stroke-width':2})));
    });
    const labels=(root.dataset.labels||'Week 1,Week 2,Week 3,Week 4,Week 5,Week 6').split(',');
    labels.forEach((label,i)=>{const t=el('text',{x:x(i),y:h-10,'text-anchor':'middle',fill:'#71817b','font-size':10});t.textContent=label;svg.append(t);});
    root.replaceChildren(svg);
  }
  function bars(root,values){
    const w=720,h=290,p={l:50,r:20,t:20,b:42},max=Math.max(...values.map(x=>x.value))*1.15;
    const svg=el('svg',{viewBox:`0 0 ${w} ${h}`,role:'img','aria-label':root.dataset.chartLabel||'Illustrative bar chart'});
    const gap=20,bw=(w-p.l-p.r-gap*(values.length-1))/values.length;
    values.forEach((d,i)=>{
      const bh=(h-p.t-p.b)*d.value/max,x=p.l+i*(bw+gap),y=h-p.b-bh;
      svg.append(el('rect',{x,y,width:bw,height:bh,rx:9,fill:i===values.length-1?'#d2af63':'#1f8b74'}));
      const label=el('text',{x:x+bw/2,y:h-18,'text-anchor':'middle',fill:'#71817b','font-size':10});label.textContent=d.label;svg.append(label);
      const val=el('text',{x:x+bw/2,y:y-8,'text-anchor':'middle',fill:'#15231f','font-size':11,'font-weight':700});val.textContent=d.value;svg.append(val);
    });
    root.replaceChildren(svg);
  }
  document.addEventListener('DOMContentLoaded',()=>{
    document.querySelectorAll('[data-line-chart]').forEach(root=>lineChart(root,JSON.parse(root.dataset.lineChart)));
    document.querySelectorAll('[data-bar-chart]').forEach(root=>bars(root,JSON.parse(root.dataset.barChart)));
  });
})();