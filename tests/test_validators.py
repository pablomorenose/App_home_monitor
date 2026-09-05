"""Tests de la validación de monitores."""

from validators import validate_monitor


def ok(**kwargs):
    base = {"id": "web1", "name": "Web", "type": "http", "url": "https://example.com"}
    base.update(kwargs)
    return base


def errors_for(**kwargs):
    return " ".join(validate_monitor(ok(**kwargs)))


def test_monitor_valido_no_da_errores():
    assert validate_monitor(ok()) == []


def test_id_rechaza_caracteres_raros():
    assert "id:" in errors_for(id="web 1")
    assert "id:" in errors_for(id="web/../etc")
    assert "id:" in errors_for(id="")
    assert "id:" in errors_for(id="x" * 51)


def test_tipo_desconocido():
    assert "type:" in errors_for(type="carrier_pigeon")


def test_depends_on_no_puede_apuntar_a_si_mismo():
    """Un ciclo de longitud 1 dejaría al monitor colgando de sí mismo."""
    assert "depends_on:" in errors_for(depends_on="web1")   # el id es web1


def test_depends_on_acepta_otro_monitor_y_vacio():
    assert errors_for(depends_on="raspberry_pi") == ""
    assert errors_for(depends_on="") == ""


def test_depends_on_rechaza_un_id_invalido():
    assert "depends_on:" in errors_for(depends_on="../etc/passwd")


def test_remote_system_es_un_tipo_valido():
    assert validate_monitor({"id": "pc", "name": "PC", "type": "remote_system"}) == []


def test_http_exige_esquema_valido():
    assert "url:" in errors_for(url="ftp://example.com")
    assert "url:" in errors_for(url="example.com")
    assert "url:" in errors_for(url="")


def test_host_rechaza_metacaracteres_de_shell():
    """El host acaba en la línea de comandos de ping."""
    e = validate_monitor({"id": "p", "name": "P", "type": "ping",
                          "host": "8.8.8.8; rm -rf /"})
    assert any("host:" in x for x in e)


def test_puerto_fuera_de_rango():
    def port_errors(port):
        return validate_monitor({"id": "p", "name": "P", "type": "port",
                                 "host": "nas.local", "port": port})
    assert port_errors(0)
    assert port_errors(65536)
    assert port_errors("http")
    assert port_errors(443) == []


def test_rangos_numericos():
    assert "timeout:" in errors_for(timeout=61)
    assert "timeout:" in errors_for(timeout=0)
    assert "check_interval:" in errors_for(check_interval=4)
    assert "check_interval:" in errors_for(check_interval=3601)
    assert "max_retries:" in errors_for(max_retries=11)


def test_expected_status_codes():
    assert errors_for(expected_status_codes="200-399") == ""
    assert errors_for(expected_status_codes="200,201,301") == ""
    assert "expected_status_codes:" in errors_for(expected_status_codes="doscientos")
    assert "expected_status_codes:" in errors_for(expected_status_codes="399-200")
