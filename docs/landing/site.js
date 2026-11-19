/**
 * Появление блоков при прокрутке и плавные переходы.
 *
 * Работает без разметки: сам находит заголовки, карточки и таблицы.
 * Уважает настройку «уменьшить движение» — тогда ничего не анимируется.
 */
(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var selector = 'main h1, main h2, main .card, main .stat, main table, main .live-card, main .guide-row';
  var elements = document.querySelectorAll(selector);

  if (reduce || !('IntersectionObserver' in window)) {
    return;
  }

  elements.forEach(function (element) {
    element.classList.add('reveal');
  });

  var observer = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('reveal-in');
          observer.unobserve(entry.target);
        }
      });
    },
    { rootMargin: '0px 0px -8% 0px', threshold: 0.06 }
  );

  elements.forEach(function (element) {
    observer.observe(element);
  });
})();
