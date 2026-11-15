# informe

Arma un sitio navegable a partir de un archivo de datos. Quien investiga escribe los datos; la plantilla, los estilos y el
visor de diagramas los pone el componente. Sirve para presentar un análisis sin una respuesta larga de texto: se lee de
arriba hacia abajo y cada punto se puede profundizar hasta el documento fuente.

- Contrato de los datos y garantías: `CONTRATO.md`.
- Reglas para tocar el código: `AGENTS.md`.

## Probarlo con el ejemplo

Desde esta carpeta:

```
python -m informe armar ejemplo/datos.json --raiz casa=ejemplo/fuentes --salida <carpeta-nueva>
```

Abrir `<carpeta-nueva>/index.html`. El ejemplo es inventado y usa todos los bloques.

## Cómo se escribe un informe

El orden es el mismo siempre. Lo que cambia es el contenido.

1. **Veredicto.** Qué está bien y dónde está el problema, en dos columnas. Después, tres o cuatro cifras.
2. **Mapa.** Uno o dos diagramas que muestren el mecanismo, no el inventario.
3. **Regla contra realidad.** Una matriz: filas son piezas, columnas son los casos que se comparan.
4. **Hallazgos.** Cada uno con severidad, tema, una explicación corta y su evidencia.
5. **Propuesta.** Pocas tarjetas. Si algo cambia de lugar, un «antes y después».
6. **Plan y decisiones.** Fases, y una tabla con lo que tiene que decidir una persona y la recomendación.

Criterios al escribir los datos:

- **La evidencia, en tres niveles.** La paráfrasis dice qué significa; la cita es textual, copiada del archivo; el enlace
  lleva al documento con el pasaje señalado. Citá solo el pasaje que prueba el punto.
- **Lo que no sale de un documento** (una medición, una consulta) va con `fuente`, que dice de dónde sale.
- **Diagramas chicos.** Hasta unas veinte cajas se leen sin ampliar. Uno más grande va dentro de un `desplegable`, o se
  parte por niveles.
- **Textos de caja cortos.** Unos 26 caracteres por renglón; si hace falta más, se corta a mano con `<br/>`.
- **Las cajas, en el orden del recorrido**, no alfabético: así las flechas no se cruzan.
- **Flechas con etiqueta** cuando la relación no es obvia.

## Pruebas

```
bash scripts/test.sh
```

No usan red. Cubren el conversor de markdown, la búsqueda de citas, la validación de los datos, el armado y el comando.

## Cómo se comprueba el visor

El dibujo pasa en el navegador y no tiene prueba automática. Con el ejemplo armado y abierto:

1. El diagrama chico entra entero, con la barra arriba a la derecha.
2. El «antes y después» muestra en color lo que cambia.
3. Al abrir el desplegable aparece el diagrama grande, y se recorre arrastrando o en pantalla completa.
4. En la consola del navegador, `window.__visor_diagramas` dice cuántos diagramas vio y dibujó, y el último error.

## Terceros

Mermaid (licencia MIT), que carga el navegador de quien lee. La lupa es código propio.
