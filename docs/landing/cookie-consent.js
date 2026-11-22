/**
 * Согласие на cookie.
 *
 * Пока согласия нет — баннер виден, а Яндекс.Метрика НЕ загружается.
 * «Только необходимые» — аналитика не подключается вообще.
 * «Принять все» — загружает счётчик и запоминает выбор.
 *
 * Ключ хранения: fa-cookie-consent = 'accepted' | 'necessary'.
 */

(function () {
  var KEY = 'fa-cookie-consent';
  var banner = document.getElementById('fa-cookie-banner');

  function read() {
    try {
      return localStorage.getItem(KEY);
    } catch (error) {
      return null;
    }
  }

  function write(value) {
    try {
      localStorage.setItem(KEY, value);
    } catch (error) {
      /* приватный режим — выбор не сохранится, баннер покажется снова */
    }
  }

  function apply(value) {
    if (value === 'accepted') {
      if (typeof window.__faLoadMetrica === 'function') window.__faLoadMetrica();
      if (typeof window.__faLoadUmami === 'function') window.__faLoadUmami();
    }
  }

  function show() {
    if (banner) banner.hidden = false;
  }

  function hide() {
    if (banner) banner.hidden = true;
  }

  var stored = read();
  if (stored) {
    apply(stored);
  } else {
    show();
  }

  if (banner) {
    banner.addEventListener('click', function (event) {
      var button = event.target.closest('[data-consent]');
      if (!button) return;
      var value = button.getAttribute('data-consent');
      write(value);
      apply(value);
      hide();
    });
  }

  // «Настройки cookie» в подвале: сбрасываем выбор и перезагружаем страницу,
  // чтобы уже загруженный счётчик действительно перестал работать.
  document.addEventListener('click', function (event) {
    var link = event.target.closest('[data-cookie-settings]');
    if (!link) return;
    event.preventDefault();
    try {
      localStorage.removeItem(KEY);
    } catch (error) {
      /* ничего */
    }
    window.location.reload();
  });
})();
