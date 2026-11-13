(function(){
  var $=function(s,r){return (r||document).querySelector(s)};
  var h=function(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML};
  document.querySelectorAll('[data-theme-toggle]').forEach(function(b){b.onclick=function(){var t=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=t;try{localStorage.setItem('theme',t)}catch(e){}}});
  var cl=function(){document.body.classList.remove('drawer-open')};
  $('#menuBtn').onclick=function(){document.body.classList.add('drawer-open')};$('#drawerOverlay').onclick=cl;
  document.querySelectorAll('#drawer a').forEach(function(a){a.addEventListener('click',cl)});
  document.addEventListener('keydown',function(e){if(e.key==='Escape')cl()});
  var sb=$('#searchbar');$('#searchBtn').onclick=function(){sb.classList.toggle('open');if(sb.classList.contains('open'))$('#q').focus()};
  document.querySelectorAll('.dd>a').forEach(function(a){a.addEventListener('click',function(e){e.preventDefault();a.parentNode.classList.toggle('open')})});
  // AJAX suggestions
  var q=$('#q'),box=$('#suggest'),tm;
  q.addEventListener('input',function(){clearTimeout(tm);var v=q.value.trim();if(v.length<2){box.innerHTML='';return}
    tm=setTimeout(function(){fetch(PN.base+'/api/suggest?q='+encodeURIComponent(v)).then(function(r){return r.json()}).then(function(l){
      box.innerHTML=l.map(function(i){return '<a href="'+h(i.url)+'"><img src="'+h(i.icon)+'" alt=""><span><b>'+h(i.name)+'</b><br><small>'+h(i.dev)+'</small></span></a>'}).join('')}).catch(function(){})},200)});
  // Load more
  var lm=$('#loadMore');
  if(lm)lm.onclick=function(){lm.disabled=true;var p=lm.dataset.page;
    fetch(PN.base+'/api/more?type='+lm.dataset.type+'&cat='+lm.dataset.cat+'&sort='+lm.dataset.sort+'&page='+p).then(function(r){return r.json()}).then(function(d){
      var g=$('#list .grid');if(g)g.insertAdjacentHTML('beforeend',d.html);lm.dataset.page=(+p)+1;lm.disabled=false;if(!d.more)lm.remove()}).catch(function(){lm.disabled=false})};
  // Reviews
  var rf=$('#reviewForm');
  if(rf)rf.onsubmit=function(e){e.preventDefault();var m=$('#reviewMsg');
    fetch(PN.base+'/api/review',{method:'POST',body:new FormData(rf)}).then(function(r){return r.json()}).then(function(d){m.textContent=d.msg;if(d.ok)rf.reset()}).catch(function(){m.textContent='Error. Try again.'})};
  // Recently viewed + update badges (stored in this browser only)
  var KEY='pn_recent',list=[];try{list=JSON.parse(localStorage.getItem(KEY)||'[]')}catch(e){}
  var d=$('.detail[data-slug]');
  if(d){var it={slug:d.dataset.slug,name:d.dataset.name,icon:d.dataset.icon,url:d.dataset.url,version:d.dataset.version};
    list=list.filter(function(x){return x.slug!==it.slug});list.unshift(it);list=list.slice(0,6);try{localStorage.setItem(KEY,JSON.stringify(list))}catch(e){}}
  var rw=$('#recent-wrap');
  if(rw&&list.length){rw.hidden=false;var g=$('#recent');g.innerHTML=list.map(function(i){return '<a class="card app-card" href="'+h(i.url)+'"><img src="'+h(i.icon)+'" width="64" height="64" alt=""><div class="meta"><h3>'+h(i.name)+'</h3><div class="dev">'+(i.version?'Viewed v'+h(i.version)+' ':'Viewed ')+'<span class="badge upd" data-s="'+h(i.slug)+'" hidden>Update available</span></div></div></a>'}).join('');
    fetch(PN.base+'/api/versions?s='+list.map(function(i){return i.slug}).join(',')).then(function(r){return r.json()}).then(function(v){
      list.forEach(function(i){if(i.version&&v[i.slug]&&v[i.slug]!==i.version){var b=$('.upd[data-s="'+i.slug+'"]');if(b)b.hidden=false}})}).catch(function(){})}
})();
