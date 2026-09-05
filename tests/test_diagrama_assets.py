"""Contratos del diagrama que se rompieron una vez y no deben repetirse.

Son comprobaciones sobre el CSS/JS servidos, no sobre Python, porque los dos
fallos que cubren vivían ahí: el header quedaba bajo la Dynamic Island del
iPhone y sus botones no se podían pulsar, y los cables se dibujaban con un
requestAnimationFrame que no corre con la pestaña oculta.
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def test_el_diagrama_reserva_el_area_segura_superior():
    """Sin esto el header cae bajo la barra de estado y no es pulsable."""
    css = read("static/css/diagrama.css")
    assert "env(safe-area-inset-top)" in css
    assert "padding-top:max(" in css.replace(" ", "")


def test_la_barra_inferior_reserva_el_area_segura():
    css = read("static/css/navbar.css")
    assert "env(safe-area-inset-bottom)" in css


def test_las_tarjetas_nacen_plegadas():
    css = read("static/css/diagrama.css")
    compacto = css.replace(" ", "").replace("\n", "")
    assert ".node-body{display:none;}" in compacto
    assert ".node.open.node-body{" in compacto


def test_los_cables_se_redibujan_sin_esperar_a_un_frame():
    """requestAnimationFrame no corre con la pestaña oculta y dejaba las
    flechas apuntando a donde ya no estaban las cajas."""
    js = read("static/js/diagrama.js")
    codigo = "\n".join(l for l in js.split("\n") if not l.strip().startswith("//"))
    assert "requestAnimationFrame" not in codigo
    assert codigo.count("drawWires();") >= 3


def test_el_estado_de_plegado_se_persiste():
    js = read("static/js/diagrama.js")
    assert "OPEN_KEY" in js and "localStorage" in js


def test_las_tres_paginas_llevan_la_barra_de_navegacion():
    for tpl in ("index.html", "diagrama.html", "historial.html"):
        html = read("templates/" + tpl)
        assert 'class="tabbar"' in html, tpl
        assert "navbar.css" in html, tpl
        assert html.count('class="active"') == 1, f"{tpl}: una sola pestaña activa"
