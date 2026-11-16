/* Visor de diagramas: dibuja cada <pre class="mermaid"> y le pone la lupa (zoom, arrastre, pantalla completa).
   Deja un rastro en window.__visor_diagramas: una línea de texto para saber si corrió y qué encontró. */
(function () {
  'use strict';
  var PASO = 1.25, MINIMO = 0.4, MAXIMO = 8;
  var D = { version: '0.2.0', corrio: false, busquedas: 0, vistos: 0, dibujados: 0, error: '' };
  var serie = 0, visores = [];

  function rastro() {
    window.__visor_diagramas = 'visor ' + D.version + ' corrio=' + D.corrio + ' busquedas=' + D.busquedas +
      ' vistos=' + D.vistos + ' dibujados=' + D.dibujados + ' error=' + (D.error || '-');
  }
  function token(nombre) { return getComputedStyle(document.documentElement).getPropertyValue(nombre).trim(); }

  function configurar() {
    var letra = token('--f-diagrama') || 'sans-serif';
    window.mermaid.initialize({
      startOnLoad: false, securityLevel: 'strict', theme: 'base', fontFamily: letra,
      flowchart: { useMaxWidth: false, htmlLabels: true },
      themeVariables: {
        fontFamily: letra, fontSize: '14px', background: token('--superficie'),
        primaryColor: token('--superficie'), primaryTextColor: token('--tinta'), primaryBorderColor: token('--linea-fuerte'),
        secondaryColor: token('--hundido'), tertiaryColor: token('--hundido'), lineColor: token('--tinta-suave'),
        textColor: token('--tinta'), titleColor: token('--tinta'), clusterBkg: token('--hundido'),
        clusterBorder: token('--linea'), edgeLabelBackground: token('--superficie'), nodeTextColor: token('--tinta')
      }
    });
  }

  /* Clases de uso común en un diagrama de flujo: «cambia», «nuevo» y «apagado». Se agregan si el texto las usa. */
  function conClases(texto) {
    var primera = texto.replace(/%%\{[\s\S]*?\}%%/g, '').trim().split(/\s+/)[0] || '';
    if (!/^(flowchart|graph)/.test(primera)) return texto;
    var extra = '';
    if (/\bcambia\b/.test(texto)) extra += '\nclassDef cambia fill:' + token('--aviso-suave') + ',stroke:' + token('--aviso') + ',stroke-width:2px;';
    if (/\bnuevo\b/.test(texto)) extra += '\nclassDef nuevo fill:' + token('--acento-suave') + ',stroke:' + token('--acento') + ',stroke-width:2px;';
    if (/\bapagado\b/.test(texto)) extra += '\nclassDef apagado fill:' + token('--hundido') + ',stroke:' + token('--linea-fuerte') + ',stroke-dasharray:4 3,color:' + token('--tinta-suave') + ';';
    return texto + extra;
  }

  function visible(nodo) { return !!(nodo.offsetWidth || nodo.offsetHeight || nodo.getClientRects().length); }

  function Visor(marco, lienzo, pct) {
    var yo = this;
    this.marco = marco; this.lienzo = lienzo; this.pct = pct;
    this.k = 1; this.x = 0; this.y = 0; this.k0 = 1; this.x0 = 0; this.punteros = {}; this.llena = false;
    this.bk = 1; this.bx = 0; this.by = 0;      /* el encuadre de partida: contra él se sabe si alguien lo movió */

    /* Tamaño inicial: entero si entra; si no, achicado hasta 55 % y el resto se recorre arrastrando. */
    this.medir = function () {
      var ancho = marco.clientWidth, w = lienzo.offsetWidth, h = lienzo.offsetHeight;
      if (!ancho || !w) { yo.medido = false; return; }
      yo.medido = true;
      yo.k0 = Math.min(1, Math.max(0.55, ancho / w));
      yo.x0 = Math.max(0, (ancho - w * yo.k0) / 2);
      marco.style.height = Math.round(Math.min(h * yo.k0, window.innerHeight * 0.7)) + 'px';
    };

    this.aplicar = function () {
      lienzo.style.transform = 'translate(' + yo.x + 'px,' + yo.y + 'px) scale(' + yo.k + ')';
      pct.textContent = Math.round(yo.k * 100) + ' %';
      var movido = Math.abs(yo.k - yo.bk) > 0.001 || Math.abs(yo.x - yo.bx) > 1 || Math.abs(yo.y - yo.by) > 1;
      marco.classList.toggle('lupa-movida', movido);
    };
    this.zoom = function (factor, px, py) {
      var caja = marco.getBoundingClientRect();
      if (px === undefined) { px = caja.width / 2; py = caja.height / 2; }
      var k2 = Math.min(MAXIMO, Math.max(MINIMO, yo.k * factor));
      yo.x = px - (px - yo.x) * (k2 / yo.k);
      yo.y = py - (py - yo.y) * (k2 / yo.k);
      yo.k = k2; yo.aplicar();
    };
    this.fijar = function () { yo.bk = yo.k; yo.bx = yo.x; yo.by = yo.y; yo.aplicar(); };
    this.inicio = function () { yo.k = yo.k0; yo.x = yo.x0; yo.y = 0; yo.fijar(); };
    /* Vuelve al encuadre de partida: el tamaño inicial o, en pantalla completa, el ajuste a la pantalla. */
    this.volver = function () { if (yo.llena) yo.ajustar(); else yo.inicio(); };
    this.ajustar = function () {
      var ancho = marco.clientWidth, alto = marco.clientHeight, w = lienzo.offsetWidth, h = lienzo.offsetHeight;
      if (!w || !h) return;
      var k = Math.min(MAXIMO, Math.max(MINIMO, Math.min(ancho / w, alto / h) * 0.95));
      yo.k = k; yo.x = (ancho - w * k) / 2; yo.y = (alto - h * k) / 2; yo.fijar();
    };
    this.entrar = function () {
      function listo() { yo.llena = true; marco.style.height = ''; marco.classList.add('lupa-llena'); setTimeout(yo.ajustar, 80); }
      if (marco.requestFullscreen) {
        marco.requestFullscreen().then(listo).catch(function () { marco.classList.add('lupa-fija'); listo(); });
      } else { marco.classList.add('lupa-fija'); listo(); }
    };
    this.salir = function () {
      if (document.fullscreenElement === marco && document.exitFullscreen) document.exitFullscreen();
      yo.llena = false; marco.classList.remove('lupa-llena', 'lupa-fija'); yo.medir(); yo.inicio();
    };
    this.alternar = function () { if (yo.llena) yo.salir(); else yo.entrar(); };

    marco.addEventListener('wheel', function (e) {
      if (!(e.ctrlKey || e.metaKey || yo.llena)) return;
      e.preventDefault();
      var caja = marco.getBoundingClientRect();
      yo.zoom(e.deltaY < 0 ? PASO : 1 / PASO, e.clientX - caja.left, e.clientY - caja.top);
    }, { passive: false });

    marco.addEventListener('pointerdown', function (e) {
      if (e.target.closest('.lupa-barra')) return;
      marco.focus({ preventScroll: true });
      /* La captura espera a que el puntero se mueva: así un clic sobre una caja con vínculo sigue siendo un clic. */
      yo.punteros[e.pointerId] = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, capturado: false };
      yo.arrastro = false;
      marco.classList.add('arrastrando');
    });
    marco.addEventListener('pointermove', function (e) {
      var p = yo.punteros[e.pointerId];
      if (!p) return;
      if (!p.capturado && Math.hypot(e.clientX - p.x0, e.clientY - p.y0) > 4) {
        p.capturado = true; yo.arrastro = true;
        try { marco.setPointerCapture(e.pointerId); } catch (error) { /* sin captura, sigue igual */ }
      }
      var ids = Object.keys(yo.punteros);
      if (ids.length === 1) {
        yo.x += e.clientX - p.x; yo.y += e.clientY - p.y; yo.aplicar();
      } else if (ids.length === 2) {
        var otro = yo.punteros[ids[0] === String(e.pointerId) ? ids[1] : ids[0]];
        var antes = Math.hypot(p.x - otro.x, p.y - otro.y), ahora = Math.hypot(e.clientX - otro.x, e.clientY - otro.y);
        var caja = marco.getBoundingClientRect();
        if (antes > 0) yo.zoom(ahora / antes, (e.clientX + otro.x) / 2 - caja.left, (e.clientY + otro.y) / 2 - caja.top);
      }
      p.x = e.clientX; p.y = e.clientY;
    });
    function soltar(e) {
      delete yo.punteros[e.pointerId];
      if (!Object.keys(yo.punteros).length) marco.classList.remove('arrastrando');
    }
    marco.addEventListener('pointerup', soltar);
    marco.addEventListener('pointercancel', soltar);
    marco.addEventListener('dragstart', function (e) { e.preventDefault(); });
    /* Después de arrastrar, soltar sobre una caja con vínculo no navega. */
    marco.addEventListener('click', function (e) {
      if (yo.arrastro && e.target.closest('a')) e.preventDefault();
      yo.arrastro = false;
    }, true);
    marco.addEventListener('dblclick', function (e) { if (!e.target.closest('.lupa-barra')) yo.alternar(); });
    marco.addEventListener('keydown', function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      var k = e.key;
      if (k === '+' || k === '=') yo.zoom(PASO);
      else if (k === '-' || k === '_') yo.zoom(1 / PASO);
      else if (k === '0') yo.volver();
      else if (k === 'f' || k === 'F') yo.alternar();
      else if (k === 'Escape' && yo.llena) yo.salir();
      else return;
      e.preventDefault();
    });
    function alCambiarPantalla() {
      if (yo.llena && !document.fullscreenElement && !marco.classList.contains('lupa-fija')) {
        yo.llena = false; marco.classList.remove('lupa-llena'); yo.medir(); yo.inicio();
      }
    }
    document.addEventListener('fullscreenchange', alCambiarPantalla);
    this.descartar = function () { document.removeEventListener('fullscreenchange', alCambiarPantalla); };
  }

  function boton(barra, simbolo, titulo, accion) {
    var b = document.createElement('button');
    b.type = 'button'; b.textContent = simbolo; b.title = titulo; b.setAttribute('aria-label', titulo);
    b.addEventListener('click', accion);
    barra.appendChild(b);
    return b;
  }

  /* El botón de volver: discreto mientras nadie movió el dibujo, con su texto y a la vista cuando alguien lo movió. */
  function botonVolver(barra, v) {
    var b = boton(barra, '', 'Volver al encuadre', function () { v.volver(); });
    b.className = 'lupa-volver';
    var signo = document.createElement('span'); signo.textContent = '⟲';
    var texto = document.createElement('span'); texto.className = 'lupa-volver-texto'; texto.textContent = ' Volver al encuadre';
    b.appendChild(signo); b.appendChild(texto);
    return b;
  }

  function montar(pre) {
    var fuente = pre.textContent;
    var marco = document.createElement('div');
    marco.className = 'lupa'; marco.tabIndex = 0;
    marco.setAttribute('role', 'group');
    marco.setAttribute('aria-label', 'Diagrama. Ctrl y rueda para ampliar, arrastrar para mover, doble clic para pantalla completa.');
    var barra = document.createElement('div'); barra.className = 'lupa-barra';
    var lienzo = document.createElement('div'); lienzo.className = 'lupa-lienzo';
    var pct = document.createElement('span'); pct.className = 'lupa-pct';
    var v = new Visor(marco, lienzo, pct);
    boton(barra, '−', 'Alejar', function () { v.zoom(1 / PASO); });
    barra.appendChild(pct);
    boton(barra, '+', 'Acercar', function () { v.zoom(PASO); });
    boton(barra, '⤢', 'Pantalla completa', function () { v.alternar(); });
    botonVolver(barra, v);
    marco.appendChild(barra); marco.appendChild(lienzo);
    pre.insertAdjacentElement('beforebegin', marco);
    pre.hidden = true;
    v.dibujar = function () {
      serie += 1;
      return window.mermaid.render('diagrama-' + serie, conClases(fuente)).then(function (r) {
        lienzo.innerHTML = r.svg;
        if (v.llena) v.ajustar(); else { v.medir(); v.inicio(); }
      });
    };
    return v.dibujar().then(function () {
      var destino = pre.id;
      if (destino) { pre.removeAttribute('id'); marco.id = destino; }
      if (pre.classList.contains('resaltado')) marco.classList.add('resaltado');
      pre.remove(); visores.push(v); D.dibujados += 1;
      if (destino && location.hash === '#' + destino) marco.scrollIntoView({ block: 'center' });
    }).catch(function (error) {
      v.descartar(); marco.remove(); pre.hidden = false;
      D.error = String(error && error.message || error).slice(0, 160);
      var aviso = document.createElement('p');
      aviso.className = 'lupa-error'; aviso.textContent = 'Este diagrama no se pudo dibujar; queda su texto.';
      pre.dataset.visor = 'fallo';
      pre.insertAdjacentElement('afterend', aviso);
      var resto = document.getElementById('ddiagrama-' + serie);
      if (resto) resto.remove();
    });
  }

  /* Un dibujo que ya viene hecho (un mapa): no hay nada que dibujar, solo se le pone la lupa. */
  function montarHecho(caja) {
    var svg = caja.querySelector('svg');
    if (!svg) return;
    var marco = document.createElement('div');
    marco.className = 'lupa'; marco.tabIndex = 0;
    marco.setAttribute('role', 'group');
    marco.setAttribute('aria-label', 'Mapa. Ctrl y rueda para ampliar, arrastrar para mover, doble clic para pantalla completa.');
    var barra = document.createElement('div'); barra.className = 'lupa-barra';
    var lienzo = document.createElement('div'); lienzo.className = 'lupa-lienzo';
    var pct = document.createElement('span'); pct.className = 'lupa-pct';
    var v = new Visor(marco, lienzo, pct);
    boton(barra, '−', 'Alejar', function () { v.zoom(1 / PASO); });
    barra.appendChild(pct);
    boton(barra, '+', 'Acercar', function () { v.zoom(PASO); });
    boton(barra, '⤢', 'Pantalla completa', function () { v.alternar(); });
    botonVolver(barra, v);
    lienzo.appendChild(svg);
    marco.appendChild(barra); marco.appendChild(lienzo);
    caja.appendChild(marco);
    v.dibujar = function () { return Promise.resolve(); };
    v.medir(); v.inicio();
    visores.push(v); D.dibujados += 1;
  }

  function buscarHechos() {
    var pendientes = document.querySelectorAll('.mapa-dibujo:not([data-visor])');
    for (var i = 0; i < pendientes.length; i++) {
      if (!visible(pendientes[i])) continue;
      var hecho = pendientes[i].querySelector('svg');
      var natural = hecho ? parseFloat(hecho.getAttribute('width')) : 0;
      if (natural && natural <= pendientes[i].clientWidth + 1) continue;   /* entra entero: no necesita lupa */
      pendientes[i].dataset.visor = 'listo'; D.vistos += 1;
      try { montarHecho(pendientes[i]); } catch (error) { D.error = String(error).slice(0, 160); }
    }
  }

  var cola = Promise.resolve();
  function buscar() {
    D.busquedas += 1;
    buscarHechos();
    if (!window.mermaid) { rastro(); return; }
    var pendientes = document.querySelectorAll('pre.mermaid:not([data-visor])');
    for (var i = 0; i < pendientes.length; i++) {
      (function (pre) {
        if (!visible(pre)) return;            /* dentro de un desplegable cerrado: se dibuja al abrirlo */
        pre.dataset.visor = 'en-curso'; D.vistos += 1;
        cola = cola.then(function () { return montar(pre); }).then(rastro);
      })(pendientes[i]);
    }
    rastro();
  }

  function arrancar() {
    D.corrio = true;
    if (window.mermaid) {
      try { configurar(); } catch (error) { D.error = String(error).slice(0, 160); window.mermaid = null; }
    } else if (document.querySelector('pre.mermaid')) {
      D.error = 'sin la libreria de diagramas';
    }
    buscar();
    document.addEventListener('toggle', function (e) {
      if (!e.target.open) return;
      buscar();
      visores.forEach(function (v) { if (!v.llena && !v.medido) { v.medir(); v.inicio(); } });
    }, true);
    window.addEventListener('hashchange', function () { setTimeout(buscar, 0); });
    var espera = null;
    window.addEventListener('resize', function () {
      clearTimeout(espera);
      espera = setTimeout(function () {
        visores.forEach(function (v) { if (!v.llena) { v.medir(); v.inicio(); } });
        buscarHechos(); rastro();           /* un mapa que dejó de entrar recibe su lupa */
      }, 150);
    });
    if (window.matchMedia) {
      var oscuro = window.matchMedia('(prefers-color-scheme: dark)');
      var alCambiar = function () {
        if (!window.mermaid) return;
        configurar();
        visores.forEach(function (v) { cola = cola.then(v.dibujar).catch(function () {}); });
      };
      if (oscuro.addEventListener) oscuro.addEventListener('change', alCambiar);
    }
  }

  rastro();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arrancar);
  else arrancar();
})();
