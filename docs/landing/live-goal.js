/**
 * Гол в реальном матче — на футбольном поле в шапке.
 *
 * Раз в 30 секунд читает /live.json (его собирает sync_live.py) и сравнивает
 * счёт с предыдущим чтением. Если счёт изменился у идущего матча — один раз
 * проигрывает анимацию: мяч летит в ворота, появляется «ГОООЛ!» со счётом.
 *
 * Первое чтение только запоминает счёт: иначе при загрузке страницы
 * анимация сработала бы на каждый уже забитый гол.
 */
(function () {
  var pitch = document.querySelector('.hero-visual');
  var flash = document.querySelector('.goal-flash');
  if (!pitch || !flash) return;

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var scores = {};
  var started = false;
  var timer = null;

  function showGoal(text) {
    flash.textContent = text;
    pitch.classList.add('live-goal');
    clearTimeout(timer);
    timer = setTimeout(function () {
      pitch.classList.remove('live-goal');
      flash.textContent = 'ГОООЛ!';
    }, 4600);
    if (reduce) {
      // Без анимации — просто короткая надпись.
      pitch.classList.remove('live-goal');
      flash.textContent = text;
      flash.style.opacity = '1';
      setTimeout(function () { flash.style.opacity = ''; }, 4000);
    }
  }

  function read(data) {
    var goals = [];
    (data.matches || []).forEach(function (match) {
      var key = match.id;
      var current = (match.home_score || '0') + ':' + (match.away_score || '0');
      if (started && match.state === 'in' && scores[key] && scores[key] !== current) {
        goals.push(
          match.home + ' ' + match.home_score + ':' + match.away_score + ' ' +
          match.away + ' · ' + match.label
        );
      }
      scores[key] = current;
    });
    if (goals.length) showGoal('ГОООЛ! ' + goals.join(' · '));
    started = true;
  }

  function load() {
    fetch('/live.json?' + Date.now())
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) { if (data) read(data); })
      .catch(function () { /* тихо: шапка не должна ломаться из-за сети */ });
  }

  load();
  setInterval(load, 30000);
})();
