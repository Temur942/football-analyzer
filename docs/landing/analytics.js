/**
 * Umami для статичного лендинга.
 *
 * Правило то же, что у Метрики: пока нет согласия на аналитические cookie,
 * счётчик не загружается. На страницах без баннера согласия (например,
 * статьи блога) выбор предложить негде — там счётчик грузится сразу,
 * потому что Umami не ставит cookie и не собирает персональные данные.
 */
(function () {
  var SRC = 'https://promptom.ru/stats/script.js';
  var HOST = 'https://promptom.ru/stats';
  var ID = '28c70cfb-a163-465a-a296-3bc31670ea84';

  function load() {
    if (document.querySelector('script[data-website-id]')) return;
    var s = document.createElement('script');
    s.async = true;
    s.defer = true;
    s.src = SRC;
    s.setAttribute('data-website-id', ID);
    s.setAttribute('data-host-url', HOST);
    document.head.appendChild(s);
  }

  window.__faLoadUmami = load;

  var banner = document.getElementById('fa-cookie-banner');
  if (!banner) {
    load();
    return;
  }

  var value = null;
  try {
    value = localStorage.getItem('fa-cookie-consent');
  } catch (error) {
    value = null;
  }
  if (value === 'accepted') load();
})();
