#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_precios.py
==================
Factor Clave Analytics / El Buen Sazon

Modulo 2 de 2 del pipeline de precios. EL IMPORTANTE.

Que hace:
  Busca en Google Shopping el precio ACTUAL de cada producto y devuelve,
  por cada uno:
      - lista de ofertas: precio + tienda + link
      - precio minimo, maximo y promedio entre tiendas

Estrategia (decidida con Lalo):
  - Fuente: Google Shopping (busqueda general).
  - Ejecucion: navegador VISIBLE con Playwright (headless=False).
    Razon: Google bloquea agresivamente headless y CAPTCHEA seguido.
    Visible permite resolver el CAPTCHA a mano mientras se refina.
    Cuando este estable -> migrar a VPS headless + proxy residencial.
  - Anti-bloqueo basico: pausas aleatorias, user-agent real, perfil
    persistente para conservar cookies entre corridas.

Requisitos:
    pip install playwright
    playwright install chromium

Uso (CLI):
    # buscar precios de un CSV con columna 'nombre_busqueda' (o 'DESCRIPCION')
    python scraper_precios.py --entrada insumos_con_id.csv \
        --salida precios_actuales.csv --col nombre_normalizado --max 50

    # o un solo producto de prueba
    python scraper_precios.py --query "aceite de oliva extra virgen 1 litro"

Notas legales/eticas:
  Respeta robots y terminos. Usa volumen bajo y pausas. Esto es para
  inteligencia de precios propia, no para revender datos de terceros.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit(
        "Falta Playwright. Instala con:\n"
        "    pip install playwright\n"
        "    playwright install chromium"
    )


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

# Perfil persistente: conserva cookies/consentimiento entre corridas.
# Esto reduce CAPTCHAs porque Google "reconoce" la sesion.
PERFIL_DIR = str(Path.home() / ".fca_scraper_perfil")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Pausas aleatorias (segundos) entre busquedas para no parecer bot.
PAUSA_MIN, PAUSA_MAX = 4.0, 9.0

# Limita ofertas guardadas por producto.
MAX_OFERTAS = 8

# Regex para extraer un precio en pesos: $1,234.56 / 1234 / 1.234,56
_PRECIO_RE = re.compile(r"\$?\s*([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?|\d+(?:[.,]\d{1,2})?)")


# ---------------------------------------------------------------------------
# Estructuras
# ---------------------------------------------------------------------------

@dataclass
class Oferta:
    precio: float
    tienda: str
    link: str


@dataclass
class ResultadoPrecio:
    query: str
    ofertas: list[Oferta] = field(default_factory=list)
    precio_min: Optional[float] = None
    precio_max: Optional[float] = None
    precio_prom: Optional[float] = None
    n_ofertas: int = 0
    error: str = ""

    def resumir(self) -> None:
        precios = [o.precio for o in self.ofertas if o.precio > 0]
        self.n_ofertas = len(precios)
        if precios:
            self.precio_min = round(min(precios), 2)
            self.precio_max = round(max(precios), 2)
            self.precio_prom = round(statistics.mean(precios), 2)


# ---------------------------------------------------------------------------
# Parsing de precio
# ---------------------------------------------------------------------------

def parsear_precio(texto: str) -> Optional[float]:
    """Convierte '$1,234.56' / '1.234,56' a float. Devuelve None si no logra."""
    if not texto:
        return None
    m = _PRECIO_RE.search(texto)
    if not m:
        return None
    raw = m.group(1)
    # Heuristica de separadores: si hay coma Y punto, el ultimo es decimal.
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):       # 1.234,56 -> europeo
            raw = raw.replace(".", "").replace(",", ".")
        else:                                       # 1,234.56 -> us/mx
            raw = raw.replace(",", "")
    elif "," in raw:
        # solo coma: si 2 decimales tras coma -> decimal, si no -> miles
        if re.search(r",\d{2}$", raw):
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    try:
        val = float(raw)
        # filtro de cordura: precios de insumos entre 1 y 100,000 MXN
        return val if 1 <= val <= 100_000 else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class ScraperGoogleShopping:
    """
    Abre Chromium VISIBLE con perfil persistente y busca precios en
    Google Shopping. Pensado para correr local mientras se refina.
    """

    def __init__(self, visible: bool = True, perfil_dir: str = PERFIL_DIR,
                 region: str = "MX", idioma: str = "es"):
        self.visible = visible
        self.perfil_dir = perfil_dir
        self.region = region
        self.idioma = idioma
        self._pw = None
        self._ctx = None
        self._page = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        # launch_persistent_context conserva cookies -> menos CAPTCHAs
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=self.perfil_dir,
            headless=not self.visible,
            user_agent=USER_AGENT,
            locale=f"{self.idioma}-{self.region}",
            viewport={"width": 1366, "height": 850},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def __exit__(self, *exc):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    # -- manejo de bloqueos --------------------------------------------------

    def _hay_captcha(self) -> bool:
        url = (self._page.url or "").lower()
        if "sorry/index" in url or "/sorry/" in url:
            return True
        try:
            txt = self._page.content().lower()
        except Exception:
            return False
        marcas = ["unusual traffic", "trafico inusual", "no soy un robot",
                  "verify you're human", "recaptcha"]
        return any(m in txt for m in marcas)

    def _esperar_resolucion_manual(self):
        if not self.visible:
            raise RuntimeError(
                "CAPTCHA detectado en modo headless. Corre con visible=True "
                "o agrega proxy residencial."
            )
        print("\n  [!] CAPTCHA detectado. Resuelvelo en la ventana del "
              "navegador y presiona ENTER aqui para continuar...")
        try:
            input()
        except EOFError:
            time.sleep(20)

    def _aceptar_consentimiento(self):
        """Cierra el banner de cookies/consentimiento de Google si aparece."""
        for sel in [
            "button:has-text('Aceptar todo')",
            "button:has-text('Acepto')",
            "button:has-text('Accept all')",
            "#L2AGLb",  # id tipico del boton de consentimiento
        ]:
            try:
                btn = self._page.query_selector(sel)
                if btn:
                    btn.click(timeout=2000)
                    time.sleep(1)
                    return
            except Exception:
                continue

    # -- busqueda principal --------------------------------------------------

    def buscar(self, query: str) -> ResultadoPrecio:
        res = ResultadoPrecio(query=query)
        url = (
            "https://www.google.com/search?tbm=shop"
            f"&q={query.replace(' ', '+')}"
            f"&gl={self.region}&hl={self.idioma}"
        )
        try:
            self._page.goto(url, timeout=30000, wait_until="domcontentloaded")
            self._aceptar_consentimiento()

            if self._hay_captcha():
                self._esperar_resolucion_manual()
                self._page.goto(url, timeout=30000, wait_until="domcontentloaded")

            # Damos tiempo a que carguen las tarjetas de producto.
            self._page.wait_for_timeout(2500)
            res.ofertas = self._extraer_ofertas()
        except PWTimeout:
            res.error = "timeout"
        except Exception as e:  # noqa: BLE001
            res.error = f"{type(e).__name__}: {e}"

        res.resumir()
        return res

    def _extraer_ofertas(self) -> list[Oferta]:
        """
        Extrae tarjetas de Google Shopping.

        IMPORTANTE: Google rota sus clases CSS con frecuencia. Por eso esta
        funcion usa una estrategia tolerante: busca contenedores que tengan
        un texto con '$' y, dentro, un enlace y un nombre de tienda.
        Si Google cambia el layout, ajusta SOLO los selectores marcados (***).
        """
        ofertas: list[Oferta] = []

        # *** Selector de tarjetas. Probar en orden; el primero que de
        #     resultados gana. Ajustar aqui si Google cambia el DOM.
        selectores_tarjeta = [
            "div.sh-dgr__grid-result",     # grid clasico de shopping
            "div.sh-dlr__list-result",     # vista de lista
            "div[data-docid]",             # fallback generico
            "div.i0X6df",                  # variante reciente
        ]

        tarjetas = []
        for sel in selectores_tarjeta:
            tarjetas = self._page.query_selector_all(sel)
            if tarjetas:
                break

        for t in tarjetas[:MAX_OFERTAS * 2]:
            try:
                texto = t.inner_text()
            except Exception:
                continue
            if "$" not in texto:
                continue

            precio = parsear_precio(texto)
            if precio is None:
                continue

            # tienda: buscamos un texto corto que parezca nombre de comercio
            tienda = self._adivinar_tienda(t, texto)

            # link: primer href interno/externo de la tarjeta
            link = ""
            a = t.query_selector("a[href]")
            if a:
                href = a.get_attribute("href") or ""
                if href.startswith("/"):
                    href = "https://www.google.com" + href
                link = href

            ofertas.append(Oferta(precio=precio, tienda=tienda, link=link))
            if len(ofertas) >= MAX_OFERTAS:
                break

        return ofertas

    @staticmethod
    def _adivinar_tienda(tarjeta, texto: str) -> str:
        # *** intenta selectores tipicos del nombre de tienda
        for sel in ["div.aULzUe", "div.IuHnof", "span.E5ocAb", "div.mURBdb"]:
            el = tarjeta.query_selector(sel)
            if el:
                val = (el.inner_text() or "").strip()
                if val:
                    return val
        # fallback: linea del texto que no tenga $ ni sea el nombre largo
        for linea in texto.split("\n"):
            l = linea.strip()
            if l and "$" not in l and 2 < len(l) < 30 and not l[0].isdigit():
                return l
        return "desconocida"


# ---------------------------------------------------------------------------
# Orquestacion sobre un archivo
# ---------------------------------------------------------------------------

def correr_lote(entrada: str, salida: str, columna: str, limite: int,
                visible: bool) -> None:
    # leer queries
    queries: list[tuple[str, str]] = []  # (id_o_consec, texto)
    with open(entrada, encoding="utf-8-sig", newline="") as f:
        lector = csv.DictReader(f)
        cols = {c.lower(): c for c in (lector.fieldnames or [])}
        ccol = cols.get(columna.lower()) or cols.get("nombre_normalizado") \
            or cols.get("descripcion") or (lector.fieldnames or [None])[0]
        cid = cols.get("id") or cols.get("consec_lic") or cols.get("consecutivo")
        for i, fila in enumerate(lector):
            if limite and i >= limite:
                break
            ident = fila.get(cid, str(i + 1)) if cid else str(i + 1)
            texto = (fila.get(ccol, "") or "").strip()
            if texto:
                queries.append((ident, texto))

    print(f"[lote] {len(queries)} productos a buscar (visible={visible})")

    filas_salida = []
    with ScraperGoogleShopping(visible=visible) as scraper:
        for n, (ident, q) in enumerate(queries, 1):
            print(f"  [{n}/{len(queries)}] {q[:55]} ...", end=" ", flush=True)
            r = scraper.buscar(q)
            if r.error:
                print(f"ERROR ({r.error})")
            else:
                print(f"{r.n_ofertas} ofertas | min ${r.precio_min} "
                      f"prom ${r.precio_prom}")
            # una fila resumen + ofertas serializadas
            filas_salida.append({
                "id": ident,
                "query": q,
                "n_ofertas": r.n_ofertas,
                "precio_min": r.precio_min or "",
                "precio_prom": r.precio_prom or "",
                "precio_max": r.precio_max or "",
                "tienda_min": min(r.ofertas, key=lambda o: o.precio).tienda
                               if r.ofertas else "",
                "link_min": min(r.ofertas, key=lambda o: o.precio).link
                             if r.ofertas else "",
                "ofertas_detalle": " | ".join(
                    f"{o.tienda}:${o.precio}" for o in r.ofertas
                ),
                "error": r.error,
            })
            # pausa anti-bloqueo
            time.sleep(random.uniform(PAUSA_MIN, PAUSA_MAX))

    with open(salida, "w", encoding="utf-8-sig", newline="") as f:
        campos = ["id", "query", "n_ofertas", "precio_min", "precio_prom",
                  "precio_max", "tienda_min", "link_min", "ofertas_detalle",
                  "error"]
        esc = csv.DictWriter(f, fieldnames=campos)
        esc.writeheader()
        esc.writerows(filas_salida)
    print(f"[salida] {len(filas_salida)} filas -> {salida}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Busca precios actuales en Google Shopping "
                    "(navegador visible) y devuelve precio+tienda+link y "
                    "min/promedio por producto."
    )
    ap.add_argument("--query", help="Buscar un solo producto (prueba rapida)")
    ap.add_argument("--entrada", help="CSV con productos a buscar")
    ap.add_argument("--salida", default="precios_actuales.csv")
    ap.add_argument("--col", default="nombre_normalizado",
                    help="Columna con el texto de busqueda")
    ap.add_argument("--max", type=int, default=0,
                    help="Limite de productos (0 = todos)")
    ap.add_argument("--headless", action="store_true",
                    help="Correr sin ventana (NO recomendado: mas CAPTCHAs)")
    args = ap.parse_args()

    visible = not args.headless

    if args.query:
        with ScraperGoogleShopping(visible=visible) as s:
            r = s.buscar(args.query)
        print("\n=== RESULTADO ===")
        print(f"query: {r.query}")
        if r.error:
            print(f"error: {r.error}")
        for o in r.ofertas:
            print(f"  ${o.precio:>10.2f}  {o.tienda:<22} {o.link[:60]}")
        print(f"\n  min ${r.precio_min}  prom ${r.precio_prom}  "
              f"max ${r.precio_max}  ({r.n_ofertas} ofertas)")
        return

    if not args.entrada:
        ap.error("Da --query para una prueba o --entrada para procesar un CSV.")

    correr_lote(args.entrada, args.salida, args.col, args.max, visible)


if __name__ == "__main__":
    main()
