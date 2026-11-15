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
  /* Vistas de un mapa: «Todas» muestra todo; otra vista atenúa u oculta lo que no le pertenece. */
  document.querySelectorAll('figure.mapa').forEach(function (mapa) {
    var botones = mapa.querySelectorAll('.vistas button');
    botones.forEach(function (b) {
      b.addEventListener('click', function () {
        var vista = b.getAttribute('data-vista');
        botones.forEach(function (o) { o.setAttribute('aria-pressed', o === b ? 'true' : 'false'); });
        mapa.querySelectorAll('[data-v]').forEach(function (nodo) {
          var fuera = !!vista && (' ' + nodo.getAttribute('data-v') + ' ').indexOf(' ' + vista + ' ') < 0;
          nodo.classList.toggle('fuera', fuera);
        });
      });
    });
  });
  function abrirDestino() {
    var destino = location.hash.length > 1 ? document.getElementById(location.hash.slice(1)) : null;
    for (var n = destino; n; n = n.parentElement) { if (n.tagName === 'DETAILS') n.open = true; }
    document.querySelectorAll('.destino').forEach(function (v) { v.classList.remove('destino'); });
    if (!destino) return;
    /* Un ancla vacía marca una segunda cita del mismo bloque: lo que se señala es el bloque. */
    var bloque = destino.classList.contains('ancla') ? (destino.closest('.resaltado') || destino.nextElementSibling || destino) : destino;
    bloque.classList.add('destino');
    bloque.scrollIntoView({ block: 'center' });
  }
  window.addEventListener('hashchange', abrirDestino);
  abrirDestino();
})();
