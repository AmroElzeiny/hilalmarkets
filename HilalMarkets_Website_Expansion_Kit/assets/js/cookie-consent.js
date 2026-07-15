(function(){
  const KEY='hm-cookie-consent-v1';
  const DEFAULT={essential:true,analytics:false,functional:false,marketing:false};
  window.dataLayer=window.dataLayer||[];
  window.gtag=window.gtag||function(){dataLayer.push(arguments);};

  // Google Consent Mode v2 defaults must execute before GTM or gtag configuration.
  gtag('consent','default',{
    ad_storage:'denied',
    analytics_storage:'denied',
    ad_user_data:'denied',
    ad_personalization:'denied',
    functionality_storage:'denied',
    personalization_storage:'denied',
    security_storage:'granted',
    wait_for_update:500
  });

  function read(){
    try{return JSON.parse(localStorage.getItem(KEY));}catch(e){return null;}
  }
  function apply(c){
    const value=Object.assign({},DEFAULT,c||{});
    gtag('consent','update',{
      ad_storage:value.marketing?'granted':'denied',
      analytics_storage:value.analytics?'granted':'denied',
      ad_user_data:value.marketing?'granted':'denied',
      ad_personalization:value.marketing?'granted':'denied',
      functionality_storage:value.functional?'granted':'denied',
      personalization_storage:value.functional?'granted':'denied',
      security_storage:'granted'
    });
    document.documentElement.dataset.consentAnalytics=value.analytics?'granted':'denied';
    document.documentElement.dataset.consentFunctional=value.functional?'granted':'denied';
    document.documentElement.dataset.consentMarketing=value.marketing?'granted':'denied';
    window.dispatchEvent(new CustomEvent('hm:consent-updated',{detail:value}));
  }
  function save(c){localStorage.setItem(KEY,JSON.stringify(Object.assign({version:1,updatedAt:new Date().toISOString()},c)));apply(c);}
  function els(){return {
    banner:document.querySelector('[data-cookie-banner]'),
    modal:document.querySelector('[data-cookie-modal]'),
    analytics:document.querySelector('[data-consent-analytics]'),
    functional:document.querySelector('[data-consent-functional]'),
    marketing:document.querySelector('[data-consent-marketing]')
  };}
  function showBanner(){els().banner?.classList.add('is-visible');}
  function hideBanner(){els().banner?.classList.remove('is-visible');}
  function showModal(){
    const e=els(),c=read()||DEFAULT;
    if(e.analytics)e.analytics.checked=!!c.analytics;
    if(e.functional)e.functional.checked=!!c.functional;
    if(e.marketing)e.marketing.checked=!!c.marketing;
    e.modal?.classList.add('is-visible');
    e.modal?.querySelector('button')?.focus();
  }
  function hideModal(){els().modal?.classList.remove('is-visible');}
  document.addEventListener('DOMContentLoaded',()=>{
    const current=read();
    if(current)apply(current);else showBanner();
    document.querySelectorAll('[data-cookie-accept-all]').forEach(b=>b.addEventListener('click',()=>{save({essential:true,analytics:true,functional:true,marketing:false});hideBanner();hideModal();}));
    document.querySelectorAll('[data-cookie-essential]').forEach(b=>b.addEventListener('click',()=>{save(DEFAULT);hideBanner();hideModal();}));
    document.querySelectorAll('[data-cookie-customize],[data-cookie-settings]').forEach(b=>b.addEventListener('click',e=>{e.preventDefault();showModal();}));
    document.querySelectorAll('[data-cookie-close]').forEach(b=>b.addEventListener('click',hideModal));
    document.querySelectorAll('[data-cookie-save]').forEach(b=>b.addEventListener('click',()=>{
      const e=els();save({essential:true,analytics:!!e.analytics?.checked,functional:!!e.functional?.checked,marketing:!!e.marketing?.checked});hideBanner();hideModal();
    }));
    els().modal?.addEventListener('click',e=>{if(e.target===els().modal)hideModal();});
  });
})();