import pytest

from ofertas.formatter import contagem_br
from ofertas.utils import parse_contagem, parse_nota


@pytest.mark.parametrize("texto, esperado", [
    ("+10mil", 10_000),
    ("+1000", 1_000),
    ("+100mil vendidos", 100_000),
    ("+250mil", 250_000),
    ("+1M", 1_000_000),
    ("2\xa0mil", 2_000),            # a Amazon usa espaço não separável
    ("(12,4\xa0mil)", 12_400),
    ("(1,9 mil)", 1_900),
    ("(505)", 505),
    ("Mais de 600", 600),
    ("1.234", 1_234),
    ("sem número", None),
    ("", None),
    (None, None),
])
def test_parse_contagem(texto, esperado):
    assert parse_contagem(texto) == esperado


@pytest.mark.parametrize("texto, esperado", [
    ("4,8 de 5 estrelas", 4.8),
    ("4.9", 4.9),
    ("5,0 de 5 estrelas", 5.0),
    ("10", None),          # fora da escala 0–5
    ("", None),
    (None, None),
])
def test_parse_nota(texto, esperado):
    assert parse_nota(texto) == esperado


@pytest.mark.parametrize("n, esperado", [
    (600, "600"), (1000, "1 mil"), (12_345, "12,3 mil"), (10_000, "10 mil"), (1_500_000, "1,5 mi"),
])
def test_contagem_br(n, esperado):
    assert contagem_br(n) == esperado
