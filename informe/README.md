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
2. **Mapa.** Uno o dos dibujos que muestren el mecanismo, no el inventario. Con vistas, para elegir qué se mira.
3. **Regla contra realidad.** Una matriz: filas son piezas, columnas son los casos que se comparan.
4. **Hallazgos.** Cada uno con severidad, tema, una explicación corta y su evidencia.
5. **Propuesta.** Pocas tarjetas. Si algo cambia de lugar, un mapa con las vistas «hoy» y «propuesto».
6. **Plan y decisiones.** Fases, y una tabla con lo que tiene que decidir una persona y la recomendación.

Criterios al escribir los datos:

- **La evidencia, en tres niveles.** La paráfrasis dice qué significa; la cita es textual, copiada del archivo; el enlace
  lleva al documento con el pasaje señalado. Citá solo el pasaje que prueba el punto.
- **Lo que no sale de un documento** (una medición, una consulta) va con `fuente`, que dice de dónde sale.
- **El dibujo es un `mapa`.** Las columnas tienen que significar algo: quién guarda, quién atiende, quién usa. Lo que
  se corresponde va en la misma fila. Una flecha, solo donde hay una relación que el lector tiene que ver.
- **Vistas con criterio.** Para resaltar a quién le pertenece cada cosa, una vista por dueño y `atenuar`. Para mostrar
  un cambio, las vistas «hoy» y «propuesto» y `ocultar`. «Todas» está siempre.
- **Cajas con título corto y una aclaración.** El título dice qué es; el texto, lo que hay que saber.
- **El `diagrama` escrito como texto es el respaldo**, para lo que no entra en columnas. Chico: hasta unas veinte cajas
  se leen sin ampliar; uno más grande va dentro de un `desplegable`. Textos de unos 26 caracteres por renglón, cortados
  a mano con `<br/>`, y las cajas en el orden del recorrido, no alfabético.

## Pruebas

```
bash scripts/test.sh
```

No usan red. Cubren el conversor de markdown, la búsqueda de citas, la validación de los datos, el armado y el comando.

## Cómo se comprueba el visor

El dibujo pasa en el navegador y no tiene prueba automática. Con el ejemplo armado y abierto:

1. El mapa tiene la lupa, y al elegir una vista se atenúa o desaparece lo que no es de esa vista.
2. El diagrama chico entra entero, con la barra arriba a la derecha.
3. Al abrir el desplegable aparece el diagrama grande, y se recorre arrastrando o en pantalla completa.
4. En la consola del navegador, `window.__visor_diagramas` dice cuántos dibujos vio y montó, y el último error.

## Terceros

Mermaid (licencia MIT), solo para el diagrama escrito como texto: lo carga el navegador de quien lee, y solo en las
páginas que lo usan. El mapa y la lupa son código propio.
