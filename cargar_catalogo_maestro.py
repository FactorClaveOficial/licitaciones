#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cargar_catalogo_maestro.py
==========================
Factor Clave Analytics / El Buen Sazon

Puebla el sistema de catalogo maestro (3 capas) en MySQL a partir de los
CSV generados por el pipeline:

    insumos_con_id.csv   (normalizador) -> bd_ctrl_sat
    precios_actuales.csv (scraper)      -> lista_precios (tipo MERCADO)
    comparacion_precios.csv (opcional)  -> lista_precios (tipo VENTA)

Ademas aplica un CLASIFICADOR FISCAL AUTOMATICO que asigna, por reglas:
    - code_prod_serv (clave SAT de 8 digitos por familia)
    - unit_code (KGM / LTR / H87) deducido de la presentacion
    - objeto_imp (02 por defecto en alimentos)
    - ieps_pct (8% para chocolates/confiteria/botanas)
    - ieps_cuota_litro (bebidas saborizadas)
    - tasa_iva (0% para canasta basica: leche, huevo, fruta, verdura)

Lo que el clasificador no pueda resolver queda en estatus PENDIENTE para
revision humana (la columna Errores de la vista lo marcara).

Requisitos:  pip install mysql-connector-python

Uso:
    python cargar_catalogo_maestro.py \
        --host TU_VPS --usuario buen_sazon --password *** --bd buen_sazon \
        --insumos insumos_con_id.csv \
        --precios precios_actuales.csv \
        --solo-clasificados        # (opcional) carga solo verdes/amarillos

Prueba en seco (no toca la BD, solo muestra que haria):
    python cargar_catalogo_maestro.py --dry-run --insumos insumos_con_id.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass


# =====================================================================
#  CLASIFICADOR FISCAL  (reglas del manual CFDI 4.0 / LIEPS)
# =====================================================================

# Reglas por palabra clave -> (clave SAT, descripcion). Orden importa:
# la primera que coincida gana. Las mas especificas van primero.
REGLAS_CLAVE_SAT = [
    # --- BEBIDAS (prioridad alta: un 'jugo de manzana' NO es fruta fresca) ---
    (r"\b(REFRESCO|COCA|GASEOSA)\b",            "50202200", "Refrescos"),
    (r"\b(JUGO|NECTAR|BEBIDA SABORIZADA)\b",    "50202300", "Jugos y nectares"),
    (r"\b(AGUA EMBOTELLADA|AGUA NATURAL|AGUA MINERAL)\b", "50202201", "Agua"),
    # --- lacteos y huevo ---
    (r"\bQUESO\b",                              "50131800", "Quesos"),
    (r"\b(LECHE EN POLVO|NUTRI RINDES)\b",      "50131704", "Leche en polvo"),
    (r"\b(LECHE).*(UHT|EVAPORADA|CONDENSADA|DESLACTOSADA|ENTERA|LIGHT)\b", "50131702", "Leche de estante"),
    (r"\b(CREMA|YOGURT|YOGURTH|REQUESON|QUESILLO)\b", "50131701", "Lacteos frescos"),
    (r"\b(MANTEQUILLA|MANTECA|MARGARINA)\b",    "50131700", "Productos de leche y mantequilla"),
    (r"\bLECHE\b",                              "50131700", "Leche generica"),
    (r"\bHUEVO\b",                              "50131600", "Huevo"),
    # --- panaderia / repesteria ---
    (r"\bLEVADURA\b",                           "50181700", "Levadura"),
    (r"\b(HARINA|HOT ?CAKE|TORTILLA|MASECA)\b", "50221300", "Harinas y preparados"),
    (r"\b(CHOCOLATE|COCOA|CHISPAS DE CHOCOLATE)\b", "50161813", "Chocolates reposteria"),
    # --- carnicos ---
    (r"\b(RES|BISTEC|ARRACHERA|MOLIDA|SIRLOIN|RIB EYE|CHAMORRO|TUETANO|RETAZO|MACIZA)\b", "50111500", "Carne de res"),
    (r"\b(CERDO|PUERCO|CHORIZO|TOCINO|COSTILLA|PIERNA)\b", "50111600", "Carne de cerdo"),
    (r"\b(POLLO|PECHUGA|MUSLO|PAVO|GALLINA)\b", "50112000", "Aves"),
    (r"\b(PESCADO|CAMARON|MARISCO|ATUN|SALMON|TILAPIA|TRUCHA|PULPO|MOJARRA|HUACHINANGO)\b", "50121500", "Pescados y mariscos"),
    # --- frutas y verduras ---
    (r"\b(JITOMATE|TOMATE|CEBOLLA|CHILE|PAPA|ZANAHORIA|LECHUGA|PEPINO|CALABAZA|BROCOLI|ESPINACA|ACELGA|NOPAL|EJOTE|CHAYOTE|PIMIENTO|AJO|APIO)\b", "50100000", "Verduras frescas"),
    (r"\b(MANZANA|PLATANO|NARANJA|LIMON|PINA|MELON|SANDIA|FRESA|UVA|PAPAYA|MANGO|GUAYABA|PERA|DURAZNO|KIWI|MANDARINA|TORONJA|ARANDANO|FRAMBUESA|ZARZAMORA)\b", "50130000", "Frutas frescas"),
    # --- abarrotes basicos ---
    (r"\b(ARROZ|FRIJOL|LENTEJA|GARBANZO|ALUBIA|MAIZ|AVENA|AMARANTO)\b", "50221200", "Granos y cereales"),
    (r"\bPASTA\b",                              "50221100", "Pastas"),
    (r"\b(ACEITE|OLIVA)\b",                     "50151500", "Aceites comestibles"),
    (r"\b(AZUCAR|PILONCILLO)\b",                "50161500", "Azucar"),
    (r"\b(SAL|PIMIENTA|COMINO|OREGANO|CANELA|CLAVO|LAUREL|TOMILLO|PAPRIKA|CURRY|ESPECIA|SAZONADOR)\b", "50171500", "Especias y condimentos"),
    (r"\b(CAFE)\b",                             "50201700", "Cafe"),
    (r"\b(TE )\b",                              "50201800", "Te"),
]

# Familias con IEPS 8% (densidad calorica >=275 kcal/100g)
IEPS_8_KW = [
    r"\bCHOCOLATE\b", r"\bCOCOA\b", r"\bCHISPAS DE CHOCOLATE\b",
    r"\b(DULCE|CONFITERIA|MAZAPAN|BOMBITO|CARAMELO)\b",
    r"\b(PAPA.*FRITA|BOTANA|TOTOPO|FRITURA|CACAHUATE ENCHILADO)\b",
    r"\b(CEREAL).*(ZUCARITA|CHOCO|AZUCARAD)\b",
]

# Bebidas saborizadas con cuota fija $/L
IEPS_CUOTA_KW = [r"\b(REFRESCO|COCA|JUGO|NECTAR|BEBIDA SABORIZADA|CONCENTRADO DE)\b"]
IEPS_CUOTA_VALOR = 1.6451  # $/L vigente aprox; actualizar por inflacion

# Canasta basica -> IVA tasa 0% (sigue siendo objeto de impuesto, clave 02)
IVA_CERO_KW = [
    r"\bLECHE\b", r"\bHUEVO\b", r"\bQUESO\b", r"\bTORTILLA\b", r"\bPAN\b",
    r"\b(FRUTA|VERDURA|JITOMATE|CEBOLLA|PAPA|MANZANA|PLATANO|NARANJA|LIMON)\b",
    r"\b(CARNE|RES|CERDO|POLLO|PESCADO|HUEVO)\b",
    r"\b(ARROZ|FRIJOL|AZUCAR|SAL|ACEITE|HARINA|MAIZ)\b",
]

# Unidad por presentacion
def deducir_unit_code(presentacion: str, nombre: str) -> str:
    p = (presentacion or "").lower()
    n = (nombre or "").upper()
    if re.search(r"\b(ml|lt|litro|l)\b", p) or re.search(r"\b(ACEITE|LECHE|JUGO|REFRESCO|CONCENTRADO|VINAGRE|JARABE)\b", n):
        # liquidos por litro... salvo presentacion en pieza/botella pequena
        if re.search(r"\b(kg|gr|g)\b", p):
            return "KGM"
        return "LTR"
    if re.search(r"\b(kg|kilo|gr|g|gramos?)\b", p):
        return "KGM"
    if re.search(r"\b(pza|pieza|caja|bolsa|lata|frasco|sobre|paquete|bote)\b", p):
        return "H87"
    return ""  # sin determinar -> queda PENDIENTE


def litros_de_presentacion(presentacion: str) -> float:
    """Extrae litros de '1 lt', '250 ml', '1.5 l'. 0 si no aplica."""
    p = (presentacion or "").lower()
    m = re.search(r"([\d.]+)\s*(ml|lt|l|litro)", p)
    if not m:
        return 0.0
    val = float(m.group(1))
    unidad = m.group(2)
    return val / 1000.0 if unidad == "ml" else val


def _coincide(nombre: str, patrones) -> bool:
    n = nombre.upper()
    return any(re.search(pat, n) for pat in patrones)


@dataclass
class Clasificacion:
    code_prod_serv: str | None
    unit_code: str | None
    objeto_imp: str
    tasa_iva: float
    iva_aplica: int
    ieps_pct: float
    ieps_cuota_litro: float
    presentacion_litros: float
    estatus: str


def clasificar(nombre: str, presentacion: str) -> Clasificacion:
    n = nombre.upper()

    # clave SAT
    code = None
    for pat, clave, _desc in REGLAS_CLAVE_SAT:
        if re.search(pat, n):
            code = clave
            break

    unit = deducir_unit_code(presentacion, nombre)

    # IVA tasa 0% (canasta basica). Se decide por la CLAVE SAT asignada
    # (mas confiable que keywords sueltos) o por keyword de respaldo.
    # Claves de alimentos basicos a tasa 0%: frutas, verduras, carnes,
    # lacteos, huevo, granos, azucar, aceite, harina/tortilla.
    CLAVES_IVA_CERO = {
        "50100000", "50130000",                          # verduras, frutas
        "50111500", "50111600", "50112000", "50121500",  # carnes y pescado
        "50131600", "50131700", "50131701", "50131702",  # huevo y lacteos
        "50131704", "50131800",                          # leche polvo, queso
        "50221200", "50221300", "50221100",              # granos, harinas, pasta
        "50161500", "50151500",                          # azucar, aceite
    }
    if (code in CLAVES_IVA_CERO) or _coincide(n, IVA_CERO_KW):
        tasa_iva, iva_aplica = 0.0, 1   # objeto de impuesto, tasa 0
    else:
        tasa_iva, iva_aplica = 0.16, 1

    # Excepcion: las bebidas saborizadas (clave 5020xx) gravan 16% aunque
    # contengan una fruta en el nombre ('jugo de manzana' != 'manzana').
    if code and code.startswith("5020"):
        tasa_iva, iva_aplica = 0.16, 1

    # IEPS porcentual
    ieps_pct = 0.08 if _coincide(n, IEPS_8_KW) else 0.0

    # IEPS cuota fija por litro (bebidas)
    litros = litros_de_presentacion(presentacion)
    ieps_cuota = IEPS_CUOTA_VALOR if (_coincide(n, IEPS_CUOTA_KW) and litros > 0) else 0.0

    # estatus: clasificado solo si tenemos clave SAT y unidad
    estatus = "CLASIFICADO" if (code and unit) else "PENDIENTE"

    return Clasificacion(
        code_prod_serv=code,
        unit_code=unit or None,
        objeto_imp="02",
        tasa_iva=tasa_iva,
        iva_aplica=iva_aplica,
        ieps_pct=ieps_pct,
        ieps_cuota_litro=ieps_cuota,
        presentacion_litros=litros,
        estatus=estatus,
    )


# =====================================================================
#  CARGA A MYSQL
# =====================================================================

def cargar(args):
    # leer insumos
    registros = []
    with open(args.insumos, encoding="utf-8-sig", newline="") as f:
        for fila in csv.DictReader(f):
            ident = (fila.get("id") or "").strip()
            nombre = (fila.get("nombre_normalizado") or fila.get("match_catalogo") or "").strip()
            if not ident or not nombre:
                continue
            if args.solo_clasificados and fila.get("estatus") == "FALTA_EN_CATALOGO":
                continue
            presentacion = (fila.get("presentacion") or "").strip()
            cls = clasificar(nombre, presentacion)
            registros.append({
                "id": ident,
                "nombre": nombre,
                "descripcion": (fila.get("nombre_original") or "").strip(),
                "marca": (fila.get("marca_catalogo") or "").strip(),
                "categoria": (fila.get("categoria_catalogo") or "").strip(),
                "presentacion": presentacion,
                "tolerancia": (fila.get("tolerancia") or "").strip(),
                "precio_ref": fila.get("precio_ref_catalogo") or None,
                "cls": cls,
            })

    # deduplicar por id (el mismo id estable puede repetirse)
    unicos = {}
    for r in registros:
        unicos[r["id"]] = r
    registros = list(unicos.values())

    clasif = sum(1 for r in registros if r["cls"].estatus == "CLASIFICADO")
    print(f"[carga] {len(registros)} productos unicos")
    print(f"  clasificados fiscalmente: {clasif}")
    print(f"  pendientes de revision:   {len(registros) - clasif}")

    if args.dry_run:
        print("\n[dry-run] muestra de clasificacion (primeros 12):")
        for r in registros[:12]:
            c = r["cls"]
            print(f"  {r['id']} | {r['nombre'][:30]:30} | SAT={c.code_prod_serv} "
                  f"un={c.unit_code} iva={c.tasa_iva} ieps={c.ieps_pct} "
                  f"cuota={c.ieps_cuota_litro} -> {c.estatus}")
        print("\n[dry-run] no se escribio nada en la BD.")
        return

    # conexion
    try:
        import mysql.connector
    except ImportError:
        sys.exit("Falta el conector. Instala:  pip install mysql-connector-python")

    cnx = mysql.connector.connect(
        host=args.host, user=args.usuario, password=args.password,
        database=args.bd, charset="utf8mb4",
    )
    cur = cnx.cursor()

    sql_maestro = """
        INSERT INTO bd_ctrl_sat
            (id_producto, nombre_estandar, descripcion, marca, categoria,
             presentacion, presentacion_litros, tolerancia,
             code_prod_serv, unit_code, objeto_imp, tasa_iva, iva_aplica,
             ieps_pct, ieps_cuota_litro, estatus_clasificacion, fuente_origen)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            nombre_estandar=VALUES(nombre_estandar),
            code_prod_serv=VALUES(code_prod_serv),
            unit_code=VALUES(unit_code),
            tasa_iva=VALUES(tasa_iva),
            ieps_pct=VALUES(ieps_pct),
            ieps_cuota_litro=VALUES(ieps_cuota_litro),
            estatus_clasificacion=VALUES(estatus_clasificacion),
            actualizado_en=CURRENT_TIMESTAMP
    """
    datos = []
    for r in registros:
        c = r["cls"]
        datos.append((
            r["id"], r["nombre"], r["descripcion"], r["marca"], r["categoria"],
            r["presentacion"], c.presentacion_litros, r["tolerancia"],
            c.code_prod_serv, c.unit_code, c.objeto_imp, c.tasa_iva, c.iva_aplica,
            c.ieps_pct, c.ieps_cuota_litro, c.estatus, args.fuente,
        ))
    cur.executemany(sql_maestro, datos)
    print(f"[bd_ctrl_sat] {cur.rowcount} filas afectadas")

    # precios de referencia del catalogo como VENTA inicial
    sql_precio = """
        INSERT INTO lista_precios (id_producto, precio, tipo_precio, proveedor, fuente, vigente_desde)
        VALUES (%s,%s,'VENTA',%s,'carga_inicial',CURRENT_DATE)
    """
    precios = [(r["id"], float(r["precio_ref"]), r["marca"] or "catalogo")
               for r in registros if r["precio_ref"]]
    if precios:
        cur.executemany(sql_precio, precios)
        print(f"[lista_precios] {cur.rowcount} precios de referencia cargados")

    # precios de mercado del scraper (opcional)
    if args.precios:
        sql_mkt = """
            INSERT INTO lista_precios (id_producto, precio, tipo_precio, proveedor, fuente, vigente_desde)
            VALUES (%s,%s,'MERCADO',%s,'scraper',CURRENT_DATE)
        """
        mkt = []
        with open(args.precios, encoding="utf-8-sig", newline="") as f:
            for fila in csv.DictReader(f):
                ident = (fila.get("id") or "").strip()
                pmin = fila.get("precio_min")
                if ident and pmin:
                    try:
                        mkt.append((ident, float(pmin), fila.get("tienda_min", "")))
                    except ValueError:
                        pass
        if mkt:
            cur.executemany(sql_mkt, mkt)
            print(f"[lista_precios] {cur.rowcount} precios de mercado cargados")

    cnx.commit()
    cur.close()
    cnx.close()
    print("[ok] carga completa.")


def main():
    ap = argparse.ArgumentParser(description="Carga el catalogo maestro (3 capas) en MySQL.")
    ap.add_argument("--insumos", required=True, help="insumos_con_id.csv")
    ap.add_argument("--precios", help="precios_actuales.csv (opcional)")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--usuario", default="root")
    ap.add_argument("--password", default="")
    ap.add_argument("--bd", default="buen_sazon")
    ap.add_argument("--fuente", default="licitacion_2026")
    ap.add_argument("--solo-clasificados", action="store_true",
                    help="Omite los insumos marcados FALTA_EN_CATALOGO")
    ap.add_argument("--dry-run", action="store_true",
                    help="No escribe en la BD; muestra que haria")
    args = ap.parse_args()
    cargar(args)


if __name__ == "__main__":
    main()
