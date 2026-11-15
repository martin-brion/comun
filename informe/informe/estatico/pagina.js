/* Página del informe: filtro de hallazgos por tema y apertura del desplegable al llegar por un enlace. */
(function () {
  'use strict';
  document.querySelectorAll('.grupo-hallazgos').forEach(function (grupo) {
    var botones = grupo.querySelectorAll('.filtros button');
    botones.forEach(function (b) {
      b.addEventListener('click', function () {
        var tema = b.getAttribute('data-tema');
        botones.forEach(function (o) { o.setAttribute('aria-pressed', o === b ? 'true' : 'false'); });
        grupo.querySelectorAll('.hallazgo').forEach(function (h) {
          h.hidden = !!tema && h.getAttribute('data-tema') !== tema;
        });
      });
    });
  });
  function abrirDestino() {
    var destino = location.hash.length > 1 ? document.getElementById(location.hash.slice(1)) : null;
    for (var n = destino; n; n = n.parentElement) { if (n.tagName === 'DETAILS') n.open = true; }
    if (destino) destino.scrollIntoView({ block: 'center' });
  }
  window.addEventListener('hashchange', abrirDestino);
  abrirDestino();
})();
