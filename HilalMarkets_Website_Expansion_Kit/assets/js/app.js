document.addEventListener('DOMContentLoaded', () => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  document.querySelectorAll('[data-preview-href]').forEach(link => {
    link.addEventListener('click', e => {
      const href = link.dataset.previewHref;
      if (!href) return;
      e.preventDefault();
      location.href = href;
    });
  });

  const menu = document.querySelector('[data-sidebar]');
  const backdrop = document.querySelector('[data-sidebar-backdrop]');
  const openMenu = () => { menu?.classList.add('is-open'); backdrop?.classList.add('is-open'); document.body.classList.add('no-scroll'); };
  const closeMenu = () => { menu?.classList.remove('is-open'); backdrop?.classList.remove('is-open'); document.body.classList.remove('no-scroll'); };
  document.querySelectorAll('[data-open-sidebar]').forEach(x=>x.addEventListener('click',openMenu));
  document.querySelectorAll('[data-close-sidebar]').forEach(x=>x.addEventListener('click',closeMenu));
  backdrop?.addEventListener('click',closeMenu);

  document.querySelectorAll('[data-tabs]').forEach(group => {
    const buttons = group.querySelectorAll('[data-tab]');
    const scope = group.closest('[data-tab-scope]') || document;
    buttons.forEach(btn => btn.addEventListener('click', () => {
      buttons.forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      scope.querySelectorAll('[data-panel]').forEach(p => p.classList.toggle('is-active', p.dataset.panel === btn.dataset.tab));
    }));
  });

  document.querySelectorAll('[data-accordion-button]').forEach(btn => {
    btn.addEventListener('click', () => {
      const item = btn.closest('.accordion-item');
      const open = item.classList.toggle('is-open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  });

  document.querySelectorAll('[data-toast-trigger]').forEach(btn => {
    btn.addEventListener('click', () => showToast(btn.dataset.toast || 'Prototype action — connect this control in Codex.'));
  });

  document.querySelectorAll('[data-copy]').forEach(btn=>{
    btn.addEventListener('click', async ()=>{
      const text = btn.dataset.copy || location.href;
      try { await navigator.clipboard.writeText(text); showToast('Copied to clipboard.'); }
      catch { showToast('Copy placeholder ready for Codex.'); }
    });
  });

  function showToast(message){
    let toast = document.querySelector('.toast');
    if(!toast){ toast=document.createElement('div'); toast.className='toast'; document.body.appendChild(toast); }
    toast.textContent=message; toast.classList.add('is-visible');
    clearTimeout(window.__toastTimer); window.__toastTimer=setTimeout(()=>toast.classList.remove('is-visible'),2800);
  }
  window.showToast = showToast;

  const observer = new IntersectionObserver(entries=>{
    entries.forEach(entry=>{
      if(entry.isIntersecting){ entry.target.classList.add('is-visible'); observer.unobserve(entry.target); }
    });
  },{threshold:.12});
  document.querySelectorAll('.reveal').forEach(el=>observer.observe(el));

  if(!reduced){
    document.querySelectorAll('[data-count]').forEach(el=>{
      const target = Number(el.dataset.count || 0), suffix=el.dataset.suffix||'', duration=900;
      let started=false;
      const ob = new IntersectionObserver(entries=>{
        if(entries[0].isIntersecting && !started){
          started=true; const start=performance.now();
          const tick=now=>{
            const p=Math.min(1,(now-start)/duration), eased=1-Math.pow(1-p,3);
            el.textContent=Math.round(target*eased)+suffix;
            if(p<1) requestAnimationFrame(tick);
          }; requestAnimationFrame(tick); ob.disconnect();
        }
      }); ob.observe(el);
    });
  }

  document.querySelectorAll('[data-progress]').forEach(el=>{
    requestAnimationFrame(()=>el.style.width=(el.dataset.progress||'0')+'%');
  });

  document.querySelectorAll('[data-filter-chip]').forEach(chip=>{
    chip.addEventListener('click',()=>{
      const parent=chip.parentElement;
      parent.querySelectorAll('[data-filter-chip]').forEach(x=>x.classList.remove('is-active'));
      chip.classList.add('is-active');
    });
  });

  document.querySelectorAll('[data-toggle]').forEach(t=>{
    t.addEventListener('click',()=>{
      const on=t.classList.toggle('is-on'); t.setAttribute('aria-pressed',on?'true':'false');
      const label=t.closest('.setting-row')?.querySelector('[data-toggle-label]');
      if(label) label.textContent=on?'On':'Off';
    });
  });

  const scanButton=document.querySelector('[data-run-scan]');
  if(scanButton){
    scanButton.addEventListener('click',()=>{
      scanButton.disabled=true; scanButton.innerHTML=window.icon('scan')+' Checking screened market…';
      const bar=document.querySelector('[data-scan-progress]');
      const status=document.querySelector('[data-scan-status]');
      let v=0; const timer=setInterval(()=>{
        v=Math.min(100,v+Math.ceil(Math.random()*13)); if(bar) bar.style.width=v+'%';
        if(status) status.textContent=`${Math.min(54,Math.round(v*.54))} of 54 eligible assets checked`;
        if(v>=100){ clearInterval(timer); scanButton.disabled=false; scanButton.innerHTML=window.icon('check')+' Market check complete'; showToast('Prototype scan completed. Results are sample data.'); document.querySelector('[data-scan-results]')?.classList.add('is-visible'); }
      },180);
    });
  }

  const builderInput=document.querySelector('[data-builder-input]');
  const builderSend=document.querySelector('[data-builder-send]');
  if(builderInput && builderSend){
    builderSend.addEventListener('click',()=>{
      if(!builderInput.value.trim()){ showToast('Describe what you would like HilalMarkets to watch.'); return; }
      const thread=document.querySelector('[data-builder-thread]');
      thread.insertAdjacentHTML('beforeend',`<div class="chat-row is-user"><div class="chat-bubble">${escapeHtml(builderInput.value)}</div></div>`);
      builderInput.value='';
      setTimeout(()=>thread.insertAdjacentHTML('beforeend',`<div class="chat-row"><div class="chat-avatar">${window.icon('moon')}</div><div class="chat-bubble"><strong>I understood the market idea.</strong><br>I added a recent-high condition and stronger-activity confirmation. Choose how early you want to be notified.</div></div>`),350);
    });
  }

  function escapeHtml(s){ return s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c])); }
});