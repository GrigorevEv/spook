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

  document.addEventListener("DOMContentLoaded", tick);
  document.addEventListener("htmx:sseMessage", tick);
  setInterval(tick, 1000);

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

  var STICK_PX = 48;
  var stickToEnd = true;

  function live() {
    return document.getElementById("live");
  }

  function rememberUserScroll(event) {
    var column = live();
    if (event.target === column) {
      stickToEnd = column.scrollHeight - column.scrollTop - column.clientHeight <= STICK_PX;
    }
  }

  document.addEventListener("scroll", rememberUserScroll, true);

  document.addEventListener("htmx:sseMessage", function () {
    var column = live();
    if (column && stickToEnd) {
      column.scrollTop = column.scrollHeight;
    }
  });
})();
