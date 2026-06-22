# Prueba de humo del scraper — instrucciones para Claude Code

Objetivo: validar que el scraper de precios funciona en esta PC ANTES de
lanzar los 916 productos. Google cambia su HTML seguido, así que primero
probamos con 5 productos genéricos.

## Contexto importante para Claude Code
- El scraper usa Playwright con **navegador VISIBLE** (`headless=False`).
- Se abrirá una ventana de Chrome. Si Google muestra un CAPTCHA, el USUARIO
  (Lalo) lo resuelve a mano en esa ventana y presiona ENTER en la terminal.
- Claude Code NO puede resolver el CAPTCHA. Es trabajo del humano.
- NO correr en headless. NO correr en la VPS. Esto es local en Windows.

## Archivos necesarios en la carpeta
- `scraper_precios.py`
- `prueba_5_productos.csv`  (5 productos genéricos)

## Pasos

### 1. Instalar Playwright (una vez)
```
pip install playwright
playwright install chromium
```

### 2. Prueba de UN solo producto (lo más rápido para ver si sirve)
```
python scraper_precios.py --query "aceite de oliva 1 litro"
```
Esto abre Chrome, busca en Google Shopping y debe imprimir algo como:
```
  $   189.00  Walmart              https://...
  $   210.50  Soriana              https://...
  min $189.00  prom $...  max $...  (N ofertas)
```

### 3. Interpretar el resultado
- **Si imprime precios y tiendas** → los selectores funcionan. Avísale a Lalo
  que ya se puede lanzar el lote completo.
- **Si dice "0 ofertas" o lista vacía** (pero SIN error de conexión) → los
  selectores de Google están desactualizados. NO es un bug de lógica. Hay que
  ajustar los selectores marcados con `***` en la función `_extraer_ofertas`
  y `_adivinar_tienda` del scraper. Para diagnosticar, abrir las DevTools en la
  ventana de Chrome que se abrió, inspeccionar una tarjeta de producto y ver
  qué clase CSS usa ahora el contenedor, el precio y el nombre de tienda.
  Reportarle a Lalo qué selectores nuevos encontraste.
- **Si da CAPTCHA** → es normal. Lalo lo resuelve en la ventana y presiona
  ENTER. Si sale CAPTCHA en CADA búsqueda, Google está bloqueando fuerte;
  considerar bajar el volumen o esperar un rato.

### 4. Si el paso 2 funcionó, prueba el lote de 5
```
python scraper_precios.py --entrada prueba_5_productos.csv --col nombre_normalizado --salida prueba_resultado.csv --max 5
```
Revisar `prueba_resultado.csv`: debe tener 5 filas con precio_min, precio_prom,
tienda_min y link_min llenos (al menos en la mayoría).

## Qué reportarle a Lalo
1. ¿Los selectores funcionan? (sí/no)
2. ¿Cuántos CAPTCHAs aparecieron en las 5 búsquedas?
3. Si falló: ¿qué selectores nuevos viste en las DevTools?

Con eso decidimos si lanzamos los 916 o ajustamos primero.
