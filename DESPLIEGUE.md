# Catálogo Maestro — Despliegue en `srv.factorclaveanalytics.com`

Proyecto **independiente** del portal El Buen Sazón: base de datos propia
(`catalogo_maestro`), su propio login y roles, servido en el subdominio
`srv.factorclaveanalytics.com` (ya apunta a la VPS).

## Estructura
```
srv_catalogo/
├── config.example.php      → copiar a config.php (NO se sube a git)
├── login.php  logout.php  index.php
├── catalogo.php  editar.php  pendientes.php  precios.php
├── includes/   funciones.php  header.php  footer.php
├── assets/     estilo.css
├── migrations/ 001_schema.sql
├── scripts/    crear_usuario.php
└── .gitignore
```

## 1. Base de datos (MySQL en la VPS)
```bash
# crear BD y usuario dedicado (no usar root para la app)
sudo mysql <<'SQL'
CREATE DATABASE catalogo_maestro CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'cm_app'@'localhost' IDENTIFIED BY 'PON_UNA_CLAVE_FUERTE';
GRANT SELECT, INSERT, UPDATE, DELETE ON catalogo_maestro.* TO 'cm_app'@'localhost';
FLUSH PRIVILEGES;
SQL

# cargar el esquema (3 capas + usuarios + catálogos SAT)
mysql -u cm_app -p catalogo_maestro < migrations/001_schema.sql
```

## 2. Código
```bash
# subir el proyecto a, por ejemplo, /var/www/catalogo
sudo mkdir -p /var/www/catalogo
# (git clone o scp del contenido de srv_catalogo/ aquí)

cd /var/www/catalogo
cp config.example.php config.php
nano config.php          # poner usuario cm_app y la clave del paso 1

sudo chown -R www-data:www-data /var/www/catalogo
```

## 3. Primer usuario (admin)
```bash
php scripts/crear_usuario.php admin "Lalo (Admin)" TU_PASSWORD ADMIN
# y para tu papá, que valida lo fiscal:
php scripts/crear_usuario.php oscar "Oscar Jara" SU_PASSWORD FISCAL
```

## 4. Nginx — bloque del subdominio
```nginx
server {
    listen 80;
    server_name srv.factorclaveanalytics.com;
    root /var/www/catalogo;
    index index.php login.php;

    location / { try_files $uri $uri/ /index.php?$query_string; }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php8.3-fpm.sock;   # ajustar versión
    }

    # nunca servir config ni migraciones
    location ~* /(config\.php|migrations|scripts)/ { deny all; }
    location ~ /\. { deny all; }
}
```
```bash
sudo nginx -t && sudo systemctl reload nginx
# luego HTTPS:
sudo certbot --nginx -d srv.factorclaveanalytics.com
```

## 5. Cargar los datos del pipeline
Usa el cargador que ya tienes (`cargar_catalogo_maestro.py`) apuntando a esta BD:
```bash
python cargar_catalogo_maestro.py \
    --host localhost --usuario cm_app --password *** --bd catalogo_maestro \
    --insumos insumos_con_id.csv --precios precios_actuales.csv
```

## Roles
| Rol | Puede |
|-----|-------|
| ADMIN | Todo, incluido crear usuarios. |
| FISCAL | Ver y **editar** la clasificación fiscal (claves SAT, IVA, IEPS). |
| CONSULTA | Solo ver el catálogo y precios. |

## Páginas
- **Panel** (`index.php`) — semáforo de avance de clasificación.
- **Catálogo** (`catalogo.php`) — la vista en vivo, con búsqueda y la columna
  Errores que dice si cada producto está listo para facturar.
- **Pendientes** (`pendientes.php`) — los sin clasificar, agrupados por
  categoría, para que FISCAL los apruebe.
- **Precios** (`precios.php`) — historial por producto.

## Notas de seguridad (lo aprendido en el incidente anterior)
- `config.php` está en `.gitignore`. Nunca lo subas al repo.
- Usa `cm_app` con permisos mínimos, no root.
- Nginx bloquea el acceso web a `config.php`, `migrations/` y `scripts/`.
