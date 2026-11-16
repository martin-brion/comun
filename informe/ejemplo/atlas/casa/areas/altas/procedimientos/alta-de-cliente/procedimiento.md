# Alta de un cliente

Arma la ficha de un cliente nuevo y la deja lista para que otra persona la apruebe.

## Pasos

1. Pedir el nombre corto y comprobar que no exista.
2. Armar la ficha con la plantilla.
3. Pasarla a aprobar.

```mermaid
flowchart LR
  pedido[Pedido de alta] --> ficha[Ficha armada]
  ficha --> aprobada[Ficha aprobada]
```

## Lee

- La plantilla de ficha, en esta misma carpeta.
- El pedido de alta.

## Deja

- La ficha en `fichas/<nombre>.md`, sin aprobar.
