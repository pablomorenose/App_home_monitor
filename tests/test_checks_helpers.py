"""Tests de los helpers puros de checks.py."""

from checks import _parse_status_codes, _safe_host


def test_rango_de_codigos():
    codes = _parse_status_codes("200-204")
    assert codes == {200, 201, 202, 203, 204}


def test_lista_de_codigos():
    assert _parse_status_codes("200,301,404") == {200, 301, 404}


def test_mezcla_de_rangos_y_sueltos():
    codes = _parse_status_codes("200-201,404")
    assert codes == {200, 201, 404}


def test_vacio_usa_el_rango_por_defecto():
    for spec in ("", "   ", None):
        codes = _parse_status_codes(spec)
        assert 200 in codes and 399 in codes
        assert 400 not in codes


def test_safe_host_acepta_hosts_normales():
    assert _safe_host("8.8.8.8") == "8.8.8.8"
    assert _safe_host("nas.local") == "nas.local"
    assert _safe_host("my-host_1") == "my-host_1"
    assert _safe_host("fe80::1") == "fe80::1"


def test_safe_host_rechaza_inyeccion_de_shell():
    """El host se pasa a ping; nada que un shell pueda interpretar."""
    for host in ("8.8.8.8; rm -rf /", "$(whoami)", "a b", "host|cat", "`id`", ""):
        assert _safe_host(host) is None, host
