// Единственный файл скриптов страницы: таймер Сессии, индикаторы уровня Источников и прокрутка Живого текста.
// Состояние приходит с сервера потоком событий.
(function () {
  "use strict";

  function pad(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function format(totalSeconds) {
    var hours = Math.floor(totalSeconds / 3600);
    var minutes = Math.floor((totalSeconds % 3600) / 60);
    var seconds = totalSeconds % 60;
    return hours + ":" + pad(minutes) + ":" + pad(seconds);
  }

  // Сервер присылает прошедшее время в data-elapsed; дальше таймер идёт сам, пока Сессия «идёт».
  function tick() {
    var timers = document.querySelectorAll("time.timer[data-running='true']");
    for (var i = 0; i < timers.length; i++) {
      var timer = timers[i];
      if (!timer.dataset.since) {
        timer.dataset.since = String(Date.now() - Number(timer.dataset.elapsed) * 1000);
      }
      timer.textContent = format(Math.floor((Date.now() - Number(timer.dataset.since)) / 1000));
    }
  }

  // Точка отсчёта фиксируется сразу, как только состояние попало на страницу, а не на первом тике.
  document.addEventListener("DOMContentLoaded", tick);
  document.addEventListener("htmx:sseMessage", tick);
  setInterval(tick, 1000);

  // Уровень сигнала: событие `levels` несёт JSON {"system": {"rms": 0.35, "peak": 0.5}, …};
  // RMS переводится в децибелы полной шкалы, шкала индикатора — от −60 дБ (тишина) до 0.
  var QUIETEST_DB = -60;

  function percent(rms) {
    if (!(rms > 0)) {
      return 0;
    }
    var db = 20 * Math.log10(rms);
    return Math.round(Math.max(0, Math.min(100, (1 - db / QUIETEST_DB) * 100)));
  }

  function showLevels(event) {
    var levels;
    try {
      levels = JSON.parse(event.detail.data);
    } catch (error) {
      return;
    }
    for (var source in levels) {
      var meter = document.querySelector('.source[data-source="' + source + '"] .level');
      if (!meter) {
        continue;
      }
      var value = percent(levels[source].rms);
      meter.setAttribute("aria-valuenow", String(value));
      var fill = meter.querySelector(".level-fill");
      if (fill) {
        fill.style.width = value + "%";
      }
    }
  }

  document.addEventListener("sse:levels", showLevels);

  // Прокрутка Живого текста держится у конца, пока пользователь сам не ушёл вверх.
  var STICK_PX = 48;
  var stick = true;

  function live() {
    return document.getElementById("live");
  }

  // Прокрутку делает только пользователь (или эта же функция); рост текста её не вызывает.
  document.addEventListener(
    "scroll",
    function (event) {
      var column = live();
      if (event.target === column) {
        stick = column.scrollHeight - column.scrollTop - column.clientHeight <= STICK_PX;
      }
    },
    true
  );

  document.addEventListener("htmx:sseMessage", function () {
    var column = live();
    if (column && stick) {
      column.scrollTop = column.scrollHeight;
    }
  });
})();
