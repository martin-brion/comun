# vigia · vigía de parches

Mira qué corre en cada servidor, lo compara con lo publicado y deja registro. **Esta versión observa: no aplica nada.**
Nace de la especificación del vigía que ya corría de un lado; se escribe de cero para que los dos lados usen la misma pieza.

Solo biblioteca estándar de Python (3.9 en adelante). No es un plugin del bot: es un servicio del servidor.

## Qué hace hoy

1. **Foto** de cada servidor (`vigia/foto.py`): distribución, kernel, reinicio pedido, paquetes pendientes y su origen,
   contenedores en marcha y sus imágenes. Un solo archivo que corre sin instalar nada: `python3 - < foto.py`.
2. **Fuentes**: el índice público de Docker Hub, cualquier registro OCI aprobado en la configuración y la lista de
   vulnerabilidades explotadas (KEV). Solo https, sin credenciales. Una imagen propia nunca se nombra hacia afuera.
3. **Análisis**: para cada imagen, la versión que corre (aunque esté detrás de una etiqueta flotante), el paso más chico
   disponible, su tamaño (parche, menor, mayor) y si pasaría la compuerta de versión. Para una imagen propia con base
   declarada, si la base se renovó después de armarla.
4. **Estado**: cada candidata guarda cuándo se detectó; el plazo se cuenta desde ahí y sobrevive a los reinicios.
5. **Registro**: una ficha Markdown por corrida, con índice, y un resumen corto para una persona.

Lo que no se pudo saber queda «desconocido». Nunca «al día», nunca «cero vulnerabilidades».

## Lo que todavía no hace

- Aplicar. El ejecutor, su diario de intenciones y la lista cerrada de acciones llegan en una versión siguiente.
- Ensayar en la copia de pruebas de Odoo y correr su batería.
- Mirar los addons de Odoo montados desde un repositorio, las dependencias de las aplicaciones propias y los avisos
  de seguridad propios de cada producto.
- Avisar. El resumen queda en `<estado_dir>/resumen.txt`; cómo llega a una persona lo decide la instancia.
- Pedir un dictamen al modelo.

## Uso

```
python -m vigia correr --config vigia.json     # una corrida; imprime el resumen
python -m vigia correr --config vigia.json --sin-fuentes
python -m vigia resumen --config vigia.json    # el de la última corrida
python -m vigia foto                           # la foto de este servidor, en JSON
```

La primera corrida no pide instalar nada en el servidor: desde otra máquina, con un lector por ssh (ver el ejemplo).
Para dejarlo fijo: un temporizador con `OnCalendar` y `Persistent=true`, nunca uno relativo al último arranque.

## Configuración

`ejemplo/vigia.json` tiene todas las claves. Lo que nombra a la instancia vive ahí y fuera de este repo.

| Clave | Qué es |
|---|---|
| `servidores` | Lista de `{nombre, lector}`. `lector` es `"local"` o un comando (lista) que recibe `foto.py` por la entrada. |
| `propias` | Prefijos de imágenes propias. No se consultan afuera. |
| `componentes` | Por pieza (`registro/repositorio`): `nivel` (`nunca`, `patch`, `minor`), `expuesto`, `linea`, `base`, `nota`. |
| `por_defecto` | Lo que vale para una pieza sin declarar. De fábrica: `nivel: nunca`. |
| `maduracion_dias` | Días desde la publicación antes de que una versión pase la compuerta. De fábrica, 7. |
| `plazos_h` | Horas de plazo por clase: `kev_expuesto`, `alta_expuesta`, `alta_interna`, `ordinario`. |
| `fuentes` | `docker_hub`, `kev` y la lista de `registros` OCI aprobados. Un registro fuera de la lista no se consulta. |
| `escaner` | `comando` (lista; se le agrega la imagen) y `tope_s` de toda la pasada. Sin comando: «desconocido». |
| `estado_dir`, `registro_dir` | Dónde guarda el estado y dónde publica las fichas. |
| `foto_vigencia_s` | Cuánto puede diferir la hora de una foto. De fábrica, 900. |

`linea` dice cuántos números de adelante forman la línea que un parche no cambia. Sirve para una versión por
calendario: con `linea: 1`, de `2026.10.0` a `2026.11.0` es un parche.

## Pruebas

`bash scripts/test.sh`. Datos inventados, sin red y sin tocar ningún servidor. Cubren: versiones y referencias, la foto
y su limpieza, las fuentes con una red de mentira, el análisis, el estado entre corridas, el escáner y la corrida entera.
