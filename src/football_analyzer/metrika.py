"""Яндекс.Метрика: счётчик и разметка ссылок на бота."""

from __future__ import annotations

METRIKA_ID = "114174484"

# Ссылка на бота с меткой источника: бот запишет source=site.
BOT_LINK = "https://t.me/footballizator_bot?start=ch_site"

SNIPPET = """<!-- Yandex.Metrika counter -->
<script type="text/javascript">
    (function(m,e,t,r,i,k,a){
        m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
        m[i].l=1*new Date();
        for (var j = 0; j < document.scripts.length; j++) {if (document.scripts[j].src === r) { return; }}
        k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)
    })(window, document,'script','https://mc.yandex.ru/metrika/tag.js?id=%(id)s', 'ym');

    ym(%(id)s, 'init', {ssr:true, webvisor:true, clickmap:true, ecommerce:"dataLayer", referrer: document.referrer, url: location.href, accurateTrackBounce:true, trackLinks:true});
</script>
<noscript><div><img src="https://mc.yandex.ru/watch/%(id)s" style="position:absolute; left:-9999px;" alt="" /></div></noscript>
<!-- /Yandex.Metrika counter -->
<script>
document.addEventListener('click', function (event) {
  var link = event.target.closest && event.target.closest('a[href*="t.me/footballizator_bot"]');
  if (link && window.ym) {
    window.ym(%(id)s, 'reachGoal', 'to_bot');
  }
});
</script>
""" % {"id": METRIKA_ID}
