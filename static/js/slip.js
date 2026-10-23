(function () {
  var b = document.getElementById('printBtn');
  if (b) b.addEventListener('click', function () { window.print(); });
  if (location.search.indexOf('print=1') > -1) setTimeout(function () { window.print(); }, 300);
})();
