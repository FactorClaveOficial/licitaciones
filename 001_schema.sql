-- =====================================================================
--  SISTEMA DE CATALOGO MAESTRO  ·  El Buen Sazon / Factor Clave Analytics
--  Modelo de 3 capas (Gobernanza de Datos + Sincronizacion Fiscal CFDI 4.0)
-- ---------------------------------------------------------------------
--  Capa 1  bd_ctrl_sat        Maestro de datos. ADN fiscal y tecnico.
--                             Casi estatico (alta de SKU / reforma fiscal).
--  Capa 2  lista_precios      Precios. Volatil (diario). Historial.
--  Capa 3  vw_catalogo_productos  VISTA que une 1+2 en vivo (= los
--                             VLOOKUP/XLOOKUP del manual, sin redundancia).
--
--  Charset utf8mb4 para acentos/enies. InnoDB para llaves foraneas.
--  Ejecutar:  mysql -u USUARIO -p catalogo_maestro < 001_schema.sql
-- =====================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- =====================================================================
--  USUARIOS DEL MINI-PORTAL (independiente del portal El Buen Sazon)
--  Roles: ADMIN (todo), FISCAL (clasifica/edita), CONSULTA (solo ve)
-- =====================================================================
CREATE TABLE IF NOT EXISTS cm_usuarios (
    id        INT          NOT NULL AUTO_INCREMENT,
    usuario   VARCHAR(60)  NOT NULL,
    nombre    VARCHAR(120) NOT NULL,
    pass_hash VARCHAR(255) NOT NULL,
    rol       ENUM('ADMIN','FISCAL','CONSULTA') NOT NULL DEFAULT 'CONSULTA',
    activo    TINYINT(1)   NOT NULL DEFAULT 1,
    creado_en TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_usuario (usuario)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;



-- =====================================================================
--  CATALOGOS DE REFERENCIA SAT  (valores oficiales, para integridad)
-- =====================================================================

-- c_ObjetoImp del CFDI 4.0
CREATE TABLE IF NOT EXISTS cat_objeto_imp (
    clave       CHAR(2)      NOT NULL,
    descripcion VARCHAR(160) NOT NULL,
    PRIMARY KEY (clave)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO cat_objeto_imp (clave, descripcion) VALUES
    ('01', 'No objeto de impuesto'),
    ('02', 'Si objeto de impuesto'),
    ('03', 'Si objeto del impuesto y no obligado al desglose'),
    ('04', 'Si objeto de impuesto y no causa impuesto')
ON DUPLICATE KEY UPDATE descripcion = VALUES(descripcion);

-- c_ClaveUnidad (subconjunto usado en alimentos)
CREATE TABLE IF NOT EXISTS cat_unidad (
    clave        VARCHAR(3)   NOT NULL,
    nombre       VARCHAR(80)  NOT NULL,
    uso_tipico   VARCHAR(160) NULL,
    PRIMARY KEY (clave)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO cat_unidad (clave, nombre, uso_tipico) VALUES
    ('KGM', 'Kilogramo',          'Carnes, quesos a granel, frutas, verduras, azucar'),
    ('LTR', 'Litro',              'Leche, aceites comestibles'),
    ('H87', 'Pieza',              'Pan empaquetado, latas, sobres'),
    ('E48', 'Unidad de servicio', 'Fletes, reparto, comisiones'),
    ('EA',  'Elemento',           'Unidades de venta individuales'),
    ('XUN', 'Unidad',             'Generico - evitar si aplica H87')
ON DUPLICATE KEY UPDATE nombre = VALUES(nombre);

-- =====================================================================
--  CAPA 1 — MAESTRO DE DATOS (bd_ctrl_sat)
--  El nombre normalizado y su ID estable vienen del normalizador.py
-- =====================================================================
CREATE TABLE IF NOT EXISTS bd_ctrl_sat (
    -- --- identidad (clave maestra) ---
    id_producto       VARCHAR(20)  NOT NULL COMMENT 'ID estable INS-XXXXXXXXXX del normalizador (SHA-256)',
    nombre_estandar   VARCHAR(255) NOT NULL COMMENT 'Nombre normalizado, version oficial unica',
    descripcion       TEXT         NULL     COMMENT 'Descripcion tecnica completa (orden de compra)',

    -- --- equivalencias (capa de traduccion del MDM existente) ---
    nombre_central    VARCHAR(255) NULL     COMMENT 'Como lo nombra la Central de Abasto',
    nombre_facturacion VARCHAR(255) NULL    COMMENT 'Como debe aparecer en CFDI',
    marca             VARCHAR(120) NULL,

    -- --- clasificacion logistica/comercial ---
    macro_categoria   VARCHAR(80)  NULL     COMMENT 'Perecederos / Abarrotes / etc.',
    sub_categoria     VARCHAR(80)  NULL     COMMENT 'Lacteos / Proteina Animal / etc.',
    categoria         VARCHAR(80)  NULL,
    requisito_logistico VARCHAR(120) NULL   COMMENT 'Cadena de frio / Seco / FEFO',

    -- --- presentacion ---
    presentacion      VARCHAR(120) NULL     COMMENT 'Ej: 1 kg, 250 ml, caja 12 pzas',
    presentacion_litros DECIMAL(8,3) NOT NULL DEFAULT 0
                         COMMENT 'Litros por presentacion, para IEPS cuota fija (0 si no aplica)',
    tolerancia        VARCHAR(20)  NULL     COMMENT 'Ej: +/-10%',

    -- --- ADN FISCAL (CFDI 4.0) ---
    code_prod_serv    CHAR(8)      NULL     COMMENT 'c_ClaveProdServ (8 digitos)',
    unit_code         VARCHAR(3)   NULL     COMMENT 'c_ClaveUnidad (KGM/LTR/H87...)',
    objeto_imp        CHAR(2)      NULL     DEFAULT '02' COMMENT 'c_ObjetoImp',
    tasa_iva          DECIMAL(5,4) NULL     DEFAULT 0.1600 COMMENT '0.16 / 0.00',
    iva_aplica        TINYINT(1)   NOT NULL DEFAULT 1,
    ieps_pct          DECIMAL(5,4) NULL     DEFAULT 0.0000 COMMENT '0.08 si densidad >=275 kcal/100g',
    ieps_cuota_litro  DECIMAL(8,4) NULL     DEFAULT 0.0000 COMMENT 'Cuota fija $/L (bebidas saborizadas)',
    densidad_calorica SMALLINT     NULL     COMMENT 'kcal/100g (para regla IEPS 8%)',

    -- --- gobernanza / auditoria ---
    estatus_clasificacion VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE'
                         COMMENT 'PENDIENTE / CLASIFICADO / REVISAR',
    fuente_origen     VARCHAR(80)  NULL     COMMENT 'De donde se dio de alta (licitacion, MDM, etc.)',
    creado_en         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id_producto),
    KEY idx_code_prod_serv (code_prod_serv),
    KEY idx_macro (macro_categoria),
    KEY idx_estatus (estatus_clasificacion),
    KEY idx_nombre (nombre_estandar),
    CONSTRAINT fk_objeto_imp FOREIGN KEY (objeto_imp)
        REFERENCES cat_objeto_imp (clave) ON UPDATE CASCADE,
    CONSTRAINT fk_unit_code FOREIGN KEY (unit_code)
        REFERENCES cat_unidad (clave) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Capa 1: Maestro de datos. ADN fiscal y tecnico de cada SKU.';

-- =====================================================================
--  CAPA 2 — LISTA DE PRECIOS (volatil + historial)
--  Cada cambio de precio = una fila nueva. El precio vigente es el
--  ultimo por fecha. Asi alimentamos historial para el semaforo XELHUA.
-- =====================================================================
CREATE TABLE IF NOT EXISTS lista_precios (
    id_precio    BIGINT       NOT NULL AUTO_INCREMENT,
    id_producto  VARCHAR(20)  NOT NULL,
    precio       DECIMAL(12,4) NOT NULL COMMENT 'Precio base sin impuestos',
    tipo_precio  VARCHAR(12)  NOT NULL DEFAULT 'VENTA'
                 COMMENT 'COMPRA / VENTA / MERCADO',
    proveedor    VARCHAR(120) NULL     COMMENT 'XELHUA, WALMART, Central, etc.',
    fuente       VARCHAR(120) NULL     COMMENT 'scraper / factura / manual',
    moneda       CHAR(3)      NOT NULL DEFAULT 'MXN',
    vigente_desde DATE        NOT NULL DEFAULT (CURRENT_DATE),
    creado_en    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id_precio),
    KEY idx_prod_fecha (id_producto, vigente_desde),
    KEY idx_tipo (tipo_precio),
    CONSTRAINT fk_precio_producto FOREIGN KEY (id_producto)
        REFERENCES bd_ctrl_sat (id_producto) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Capa 2: Precios con historial. Una fila por cambio.';

-- =====================================================================
--  VISTA AUXILIAR — ultimo precio VENTA vigente por producto
-- =====================================================================
CREATE OR REPLACE VIEW vw_precio_vigente AS
SELECT lp.id_producto,
       lp.precio,
       lp.tipo_precio,
       lp.proveedor,
       lp.vigente_desde
FROM lista_precios lp
INNER JOIN (
    SELECT id_producto, tipo_precio, MAX(vigente_desde) AS max_fecha
    FROM lista_precios
    GROUP BY id_producto, tipo_precio
) ult
  ON lp.id_producto = ult.id_producto
 AND lp.tipo_precio = ult.tipo_precio
 AND lp.vigente_desde = ult.max_fecha
WHERE lp.tipo_precio = 'VENTA';

-- =====================================================================
--  CAPA 3 — CATALOGO_PRODUCTOS  (VISTA = JOIN en vivo, sin redundancia)
--  Reproduce las 11 columnas del nodo Conceptos del CFDI 4.0 y agrega
--  la columna "Errores" auto-validada (logica del manual).
-- =====================================================================
CREATE OR REPLACE VIEW vw_catalogo_productos AS
SELECT
    m.id_producto                                   AS Codigo,
    m.nombre_estandar                               AS Nombre,
    m.descripcion                                   AS Descripcion,
    m.presentacion                                  AS Unidad,
    cu.nombre                                        AS NombreCodeProdServ,
    p.precio                                         AS Precio,
    m.unit_code                                      AS UnitCode,
    m.code_prod_serv                                 AS CodeProdServ,
    m.objeto_imp                                     AS ObjetoImp,
    -- precio neto con impuestos escalonados (IEPS cuota + IEPS % + IVA)
    ROUND(
        (p.precio
         + (m.presentacion_litros * m.ieps_cuota_litro)        -- IEPS cuota fija $/L
        ) * (1 + COALESCE(m.ieps_pct, 0))                       -- IEPS porcentual
          * (1 + CASE WHEN m.iva_aplica = 1 THEN COALESCE(m.tasa_iva,0) ELSE 0 END)
    , 2)                                             AS PrecioNeto,
    m.actualizado_en                                 AS Agregado,
    -- columna ERRORES (auto-validacion, regla del manual)
    CASE
        WHEN m.code_prod_serv IS NULL OR m.unit_code IS NULL
             THEN 'Faltan Claves SAT'
        WHEN p.precio IS NULL
             THEN 'Falta Precio en Lista'
        WHEN m.code_prod_serv = '01010101'
             THEN 'Revisar Clave 01010101 (generica, riesgo auditoria)'
        WHEN m.estatus_clasificacion <> 'CLASIFICADO'
             THEN 'Producto sin clasificar fiscalmente'
        ELSE 'OK'
    END                                              AS Errores
FROM bd_ctrl_sat m
LEFT JOIN vw_precio_vigente p ON p.id_producto = m.id_producto
LEFT JOIN cat_unidad cu       ON cu.clave = m.unit_code;

SET FOREIGN_KEY_CHECKS = 1;
