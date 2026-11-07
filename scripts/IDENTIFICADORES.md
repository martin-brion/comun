# Compuerta de identificadores

`verificar_identificadores.py` compara el texto y los nombres de archivo contra `identificadores.hashes.json`: hashes
SHA-256, nunca los valores. La lista en claro y su conversor viven fuera de este repo. El mismo esquema usa el motor.

- **Corre** en `scripts/test.sh`, en `scripts/etiquetar.sh` y en la entrega de un componente.
- **Mira** todo el árbol, salvo `.git`. Frena con un binario, un archivo que no sea UTF-8, uno de más de 8 MiB o un enlace.
- **Dice** archivo y línea, nunca el texto hallado. Código 0: limpio. 1: hallazgos. 2: no se pudo verificar.
- **Este repo no admite excepciones:** `exceptions` queda vacío. Ninguna regla se salta por archivo.

Las reglas son de dos clases: `substring` (dentro de cualquier palabra) y `word` (palabra entera), más las plantillas de
códigos y numeraciones que trae el motor. Texto y valores se normalizan con Unicode NFKC y `casefold`.

Si aparece un nombre nuevo, quien lleva la lista regenera el manifiesto. Este repo no lo escribe a mano.

Límites: solo cubre lo que está en la lista. No descubre un nombre nuevo, una traducción, un texto partido entre líneas ni
los escapes. Un hash de un nombre corto se puede adivinar probando un diccionario: es un freno, no un cifrado.
