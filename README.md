# Price Anon

Buscador de precios de una sucursal de supermercado, con datos abiertos de
[Precios Claros / SEPA](https://datos.produccion.gob.ar/dataset/sepa-precios).

- Buscá por nombre o marca: la lista se filtra mientras escribís.
- Tocá un producto para ver precio, promos, código de barras e historial.
- Tipeá un código EAN para buscarlo directo; si no está, igual te dibuja el código.
- Enlace directo a un producto: `.../#7790000000000`

## Configuración

La cadena y la sucursal no están en el código: se configuran con dos secrets en
**Settings → Secrets and variables → Actions**:

| Secret      | Qué poner |
|-------------|-----------|
| `CADENA`    | Palabras que aparecen en la razón social o bandera, separadas por coma |
| `LOCALIDAD` | Texto que aparece en la localidad o el nombre de la sucursal |

El script no escribe nombres de cadena, sucursal ni dirección en los datos ni en el log.

## Cómo se actualiza

`.github/workflows/actualizar.yml` corre todos los días a las 19:47 y 22:47 (hora
Argentina) y a mano desde **Actions → Actualizar precios → Run workflow**. Ejecuta
`scripts/actualizar.py`, que:

1. Intenta descargar el paquete SEPA del portal oficial. Si no responde, usa el
   [espejo público](https://github.com/catdevnull/sepa-precios-metadata).
2. Busca la cadena y la sucursal configuradas.
3. Guarda sus productos con EAN válido en `docs/data/productos.json` y los cambios
   de precio en `docs/data/historial.json`.

El resultado de la última corrida queda en `docs/data/ultimo-log.txt`.

Prueba local con un paquete ya descargado (zip o carpeta):

```
CADENA="..." LOCALIDAD="..." SEPA_ZIP=ruta/al/paquete python scripts/actualizar.py
```

## Imágenes

SEPA no trae fotos. La página intenta, por EAN, el servidor de imágenes de Precios
Claros y después Open Food Facts; si no hay ninguna, muestra un ícono.

## Aviso

Los precios son los que la cadena informa a SEPA, no una lectura de góndola.
