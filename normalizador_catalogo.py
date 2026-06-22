#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
normalizador_catalogo.py
========================
Factor Clave Analytics / El Buen Sazon

Modulo 1 de 2 del pipeline de precios.

Que hace:
  1. Toma un nombre crudo de producto (de una licitacion, de una tienda, etc.)
  2. Lo normaliza (sin acentos, sin ruido de empaque, en minusculas/limpio)
  3. Le asigna un ID ESTABLE Y DETERMINISTA (mismo nombre -> mismo ID siempre)
  4. Extrae presentacion (kg/gr/ml/lt/pieza) y tolerancia (+/- %)
  5. Lo cruza contra tu catalogo maestro y devuelve el mejor match + score

Diseno:
  - Sin dependencias pesadas. Solo rapidfuzz (pip install rapidfuzz).
  - El ID usa SHA-256 truncado sobre el nombre normalizado -> reproducible,
    sirve como clave en MySQL/BigQuery y para deduplicar entre corridas.

Uso rapido (CLI):
    python normalizador_catalogo.py --catalogo catalogo_ebs.csv \
        --insumos anexo2.csv --salida insumos_con_id.csv

Uso como libreria:
    from normalizador_catalogo import Normalizador
    nz = Normalizador("catalogo_ebs.csv")
    r = nz.procesar("ACEITE DE OLIVA EXTRA VIRGEN, BOTELLA CON 1 LITRO")
    print(r.id, r.match_catalogo, r.score)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import unicodedata
from dataclasses import dataclass, asdict, field
from typing import Optional

try:
    from rapidfuzz import fuzz, process
except ImportError:
    sys.exit("Falta rapidfuzz. Instala con:  pip install rapidfuzz")


# ---------------------------------------------------------------------------
# Configuracion de limpieza
# ---------------------------------------------------------------------------

# Palabras de empaque / ruido que NO identifican al producto.
# OJO: aqui NO van nombres de producto (aceite, pollo, etc.), solo relleno.
STOPWORDS = set("""
con tolerancia de del la el los las presentacion botella bote frasco lata
galon porron caja bolsa pieza piezas pza pzas kg gr grs g ml mililitros litro
litros lt gramaje primera calidad debera cumplir norma para en sin temperatura
menor mayor aspecto color olor tacto libre marca cubeta granel su masa drenada
tipo por que una un dos tres producto caracteristico textura superficie lisa
polvo tierra humedad picadura insectos materias indeseables bien presentado
fresco crujiente variedad hoja delgada hojas brillante tallo blanco atractivo
magulladuras danos fisiologicos registro certificado rastro tif inspeccion
federal ssa secretaria salud salubridad asistencia onzas tetrapack
""".split())

# Unidades que reconocemos para extraer la presentacion.
_UNIDAD_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(kg|kilo|kilos|kilogramos?|g|gr|grs|gramos?|mg|"
    r"l|lt|lts|litros?|ml|mililitros?|"
    r"pza|pzas|piezas?|pieza|und|unidades?)\b",
    re.IGNORECASE,
)

_TOLERANCIA_RE = re.compile(r"[+]?/?[-]?\s*(\d+(?:[.,]\d+)?)\s*%")


def quitar_acentos(texto: str) -> str:
    return (
        unicodedata.normalize("NFKD", texto)
        .encode("ascii", "ignore")
        .decode("ascii")
    )


# Palabras donde TIPICAMENTE arranca la cláusula de especificación tecnica.
# El nombre del producto va ANTES de cualquiera de estas. Cortamos ahi para
# no arrastrar toda la descripcion al nombre (ni al ID).
_CORTE_ESPEC = re.compile(
    r"\b(TEMPERATURA|GRAMAJE|PRESENTACION|CONSISTENCIA|DESCRIPCION|MEDIDAS|"
    r"FORMA|COLORACION|TEXTURA|ASPECTO|CARACTERISTIC|DEBERA|CUMPLIR|"
    r"LIBRE DE|SIN INDICIOS|NO MAYOR|NO MENOR|CADA )\b"
)

# Tope duro de palabras para un nombre de producto (evita nombres-parrafo).
_MAX_PALABRAS_NOMBRE = 9

def normalizar(nombre: str) -> str:
    """Devuelve la forma canonica usada para comparar y para el ID."""
    s = quitar_acentos(nombre).upper()
    s = re.sub(r"\([^)]*\)", " ", s)          # quita parentesis y su contenido
    s = re.sub(r"[+]/?-?\s*\d+(?:[.,]\d+)?\s*%", " ", s)  # quita tolerancias

    # cortar en la primera palabra de especificacion tecnica (sobre texto
    # con acentos quitados pero antes de limpiar puntuacion, para detectar
    # frases como "NO MAYOR")
    m = _CORTE_ESPEC.search(s)
    if m:
        s = s[:m.start()]

    s = re.sub(r"[^A-Z ]", " ", s)            # solo letras y espacios
    toks = [t for t in s.split() if t.lower() not in STOPWORDS and len(t) > 1]

    # tope duro: un nombre de producto no necesita mas de N palabras
    toks = toks[:_MAX_PALABRAS_NOMBRE]
    return " ".join(toks).strip()


def generar_id(nombre_normalizado: str, prefijo: str = "INS") -> str:
    """ID estable y determinista. Mismo nombre normalizado -> mismo ID."""
    if not nombre_normalizado:
        nombre_normalizado = "__VACIO__"
    h = hashlib.sha256(nombre_normalizado.encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefijo}-{h}"


def extraer_presentacion(nombre: str) -> str:
    m = _UNIDAD_RE.search(quitar_acentos(nombre))
    if not m:
        return ""
    cantidad = m.group(1).replace(",", ".")
    unidad = m.group(2).lower()
    return f"{cantidad} {unidad}"


def extraer_tolerancia(nombre: str) -> str:
    m = _TOLERANCIA_RE.search(nombre)
    return f"+/-{m.group(1)}%" if m else ""


# ---------------------------------------------------------------------------
# Estructura de resultado
# ---------------------------------------------------------------------------

@dataclass
class ResultadoNormalizacion:
    nombre_original: str
    nombre_normalizado: str
    id: str
    presentacion: str
    tolerancia: str
    match_catalogo: str = ""
    marca_catalogo: str = ""
    categoria_catalogo: str = ""
    precio_ref_catalogo: Optional[float] = None
    score: int = 0
    estatus: str = "SIN_CATALOGO"

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Normalizador con catalogo
# ---------------------------------------------------------------------------

class Normalizador:
    """
    Carga un catalogo maestro y permite normalizar + cruzar productos.

    El CSV de catalogo debe tener al menos una columna con el nombre del
    producto. Por defecto busca 'producto' o 'DESCRIPCION'; configurable.
    """

    # Umbrales de confianza del match (ajustables)
    UMBRAL_VERDE = 80      # >= : producto ya en catalogo
    UMBRAL_AMARILLO = 62   # >= : posible coincidencia, revisar a mano

    # Bonus/castigo segun coincidencia del sustantivo principal (head noun).
    # El head noun es la palabra que IDENTIFICA al producto: en
    # "ACEITE DE OLIVA" el head es ACEITE. Comparar solo la frase completa
    # castiga injustamente "AVENA CRUDA" vs "AVENA" (mismo producto base) y
    # premia injustamente "ANCHOAS EN ACEITE" vs "ACEITE" (productos distintos).
    BONUS_HEAD = 14        # se suma si el head coincide
    CASTIGO_HEAD = 22      # se resta si el head NO coincide
    CASTIGO_VARIANTE = 16  # se resta si head coincide pero el calificador no
                           # (jugo uva vs jugo manzana, nuez moscada vs nuez)

    def __init__(
        self,
        ruta_catalogo: Optional[str] = None,
        col_producto: str = "producto",
        col_marca: str = "marca",
        col_categoria: str = "categoria",
        col_precio: str = "precio",
    ):
        self.col_producto = col_producto
        self.col_marca = col_marca
        self.col_categoria = col_categoria
        self.col_precio = col_precio
        self.catalogo: list[dict] = []
        self._normas: list[str] = []
        if ruta_catalogo:
            self.cargar_catalogo(ruta_catalogo)

    def cargar_catalogo(self, ruta: str) -> None:
        self.catalogo.clear()
        with open(ruta, encoding="utf-8-sig", newline="") as f:
            lector = csv.DictReader(f)
            cols = {c.lower(): c for c in (lector.fieldnames or [])}
            # resolucion flexible de nombres de columna
            cprod = cols.get(self.col_producto.lower()) or cols.get("descripcion") \
                or cols.get("nombre") or (lector.fieldnames or [None])[0]
            cmarca = cols.get(self.col_marca.lower()) or cols.get("marca/unidad")
            ccat = cols.get(self.col_categoria.lower())
            cprec = cols.get(self.col_precio.lower()) or cols.get("precio_ref_mxn")
            for fila in lector:
                prod = (fila.get(cprod, "") or "").strip()
                if not prod:
                    continue
                precio = None
                if cprec and fila.get(cprec):
                    try:
                        precio = float(str(fila[cprec]).replace(",", ""))
                    except ValueError:
                        precio = None
                self.catalogo.append({
                    "producto": prod,
                    "marca": (fila.get(cmarca, "") if cmarca else "").strip(),
                    "categoria": (fila.get(ccat, "") if ccat else "").strip(),
                    "precio": precio,
                    "norm": normalizar(prod),
                })
        self._normas = [c["norm"] for c in self.catalogo]
        print(f"[catalogo] cargados {len(self.catalogo)} productos desde {ruta}")

    @staticmethod
    def _head_noun(texto_norm: str) -> str:
        """Primera palabra significativa = sustantivo principal del producto."""
        toks = texto_norm.split()
        return toks[0] if toks else ""

    @staticmethod
    def _tokens_clave(texto_norm: str) -> set:
        """Tokens que distinguen variantes (sabor, tipo, corte): todo menos
        el head. Ej: 'JUGO SABOR UVA' -> {SABOR, UVA}. Sirve para detectar
        que 'JUGO UVA' y 'JUGO MANZANA' NO son el mismo producto."""
        toks = texto_norm.split()
        return set(toks[1:]) if len(toks) > 1 else set()

    def _mejor_match(self, query_norm: str):
        if not query_norm or not self._normas:
            return None, 0
        q_head = self._head_noun(query_norm)
        q_clave = self._tokens_clave(query_norm)

        candidatos = process.extract(
            query_norm, self._normas, scorer=fuzz.WRatio, limit=8
        )
        mejor_idx, mejor_score = None, -1
        for _, wratio, idx in candidatos:
            cat_norm = self._normas[idx]
            ts = fuzz.token_sort_ratio(query_norm, cat_norm)
            base = 0.5 * wratio + 0.5 * ts

            # --- ajuste por head noun (sustantivo principal) ---
            c_head = self._head_noun(cat_norm)
            head_sim = fuzz.ratio(q_head, c_head)
            head_ok = head_sim >= 85
            if head_ok:
                base += self.BONUS_HEAD
            elif head_sim < 60:
                base -= self.CASTIGO_HEAD

            # --- ajuste por token distintivo ---
            # Si AMBOS (query y candidato) tienen calificadores propios pero
            # ninguno coincide, son variantes distintas (jugo UVA vs jugo
            # MANZANA, nuez MOSCADA vs nuez MITADES) -> castigar.
            # Pero si el catalogo es generico (solo el head, ej. "AVENA"),
            # NO castigamos: "AVENA" abarca "AVENA CRUDA".
            if head_ok and q_clave:
                c_clave = self._tokens_clave(cat_norm)
                if c_clave:  # el catalogo tambien tiene calificador
                    comparte = any(
                        any(fuzz.ratio(qt, ct) >= 85 for ct in c_clave)
                        for qt in q_clave
                    )
                    if not comparte:
                        base -= self.CASTIGO_VARIANTE

            score = round(max(0, min(100, base)))
            if score > mejor_score:
                mejor_idx, mejor_score = idx, score

        if mejor_idx is None:
            return None, 0
        return self.catalogo[mejor_idx], mejor_score

    def procesar(self, nombre_crudo: str, prefijo_id: str = "INS") -> ResultadoNormalizacion:
        norm = normalizar(nombre_crudo)
        res = ResultadoNormalizacion(
            nombre_original=nombre_crudo,
            nombre_normalizado=norm,
            id=generar_id(norm, prefijo_id),
            presentacion=extraer_presentacion(nombre_crudo),
            tolerancia=extraer_tolerancia(nombre_crudo),
        )
        if self.catalogo:
            match, score = self._mejor_match(norm)
            res.score = score
            if match and score >= self.UMBRAL_AMARILLO:
                res.match_catalogo = match["producto"]
                res.marca_catalogo = match["marca"]
                res.categoria_catalogo = match["categoria"]
                res.precio_ref_catalogo = match["precio"]
            if score >= self.UMBRAL_VERDE:
                res.estatus = "YA_EN_CATALOGO"
            elif score >= self.UMBRAL_AMARILLO:
                res.estatus = "REVISAR"
            else:
                res.estatus = "FALTA_EN_CATALOGO"
        return res

    def procesar_archivo(
        self,
        ruta_insumos: str,
        ruta_salida: str,
        col_nombre: str = "DESCRIPCION",
        prefijo_id: str = "INS",
    ) -> list[ResultadoNormalizacion]:
        resultados: list[ResultadoNormalizacion] = []
        with open(ruta_insumos, encoding="utf-8-sig", newline="") as f:
            lector = csv.DictReader(f)
            cols = {c.lower(): c for c in (lector.fieldnames or [])}
            cnom = cols.get(col_nombre.lower()) or cols.get("descripcion") \
                or cols.get("nombre") or (lector.fieldnames or [None])[0]
            for fila in lector:
                nombre = (fila.get(cnom, "") or "").strip()
                if not nombre:
                    continue
                resultados.append(self.procesar(nombre, prefijo_id))

        with open(ruta_salida, "w", encoding="utf-8-sig", newline="") as f:
            campos = list(ResultadoNormalizacion.__annotations__.keys())
            esc = csv.DictWriter(f, fieldnames=campos)
            esc.writeheader()
            for r in resultados:
                esc.writerow(r.as_dict())

        verdes = sum(1 for r in resultados if r.estatus == "YA_EN_CATALOGO")
        amar = sum(1 for r in resultados if r.estatus == "REVISAR")
        rojos = sum(1 for r in resultados if r.estatus == "FALTA_EN_CATALOGO")
        print(f"[salida] {len(resultados)} insumos -> {ruta_salida}")
        print(f"  verde (ya en catalogo): {verdes}")
        print(f"  amarillo (revisar):     {amar}")
        print(f"  rojo (falta):           {rojos}")
        return resultados


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Normaliza nombres de productos, asigna ID estable y "
                    "cruza contra catalogo El Buen Sazon."
    )
    ap.add_argument("--insumos", required=True,
                    help="CSV con los nombres crudos (ej. anexo2.csv)")
    ap.add_argument("--salida", required=True, help="CSV de salida")
    ap.add_argument("--catalogo", default=None,
                    help="CSV del catalogo maestro (opcional)")
    ap.add_argument("--col-nombre", default="DESCRIPCION",
                    help="Columna con el nombre en --insumos")
    ap.add_argument("--prefijo", default="INS",
                    help="Prefijo del ID generado (default: INS)")
    args = ap.parse_args()

    nz = Normalizador(args.catalogo)
    nz.procesar_archivo(args.insumos, args.salida,
                        col_nombre=args.col_nombre, prefijo_id=args.prefijo)


if __name__ == "__main__":
    main()
