"""Envio ao Telegram: nunca publicar o mesmo produto duas vezes (foto + texto)."""
import asyncio

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut

from ofertas import telegram_poster
from ofertas.models import Oferta


class BotFalso:
    """Registra os envios; `foto` e `texto` são o que cada método faz (retorna a mensagem ou levanta)."""
    def __init__(self, foto=None, texto=None):
        self.enviados, self._foto, self._texto = [], foto, texto

    async def send_photo(self, chat_id, imagem, **kw):
        self.enviados.append(("foto", kw))
        if isinstance(self._foto, Exception):
            raise self._foto
        return "mensagem-foto"

    async def send_message(self, chat_id, texto, **kw):
        self.enviados.append(("texto", kw))
        if isinstance(self._texto, Exception):
            raise self._texto
        return "mensagem-texto"


def oferta(imagem="https://img/x.jpg"):
    return Oferta("mercadolivre", "MLB1", "Secador De Cabelo 2000 Watts", "https://afiliado/x", preco=194.75, imagem=imagem)


def postar(bot, o=None):
    return asyncio.run(telegram_poster.postar_oferta(bot, o or oferta(), -100))


def test_com_imagem_manda_so_a_foto():
    bot = BotFalso()
    assert postar(bot) == "mensagem-foto"
    assert [t for t, _ in bot.enviados] == ["foto"]


def test_sem_imagem_manda_so_o_texto():
    bot = BotFalso()
    assert postar(bot, oferta(imagem=None)) == "mensagem-texto"
    assert [t for t, _ in bot.enviados] == ["texto"]


def test_timeout_na_foto_nao_reenvia_como_texto_pois_a_foto_pode_ter_saido():
    """O bug do post duplicado: a foto era publicada, a resposta estourava o tempo e o texto saía em seguida."""
    bot = BotFalso(foto=TimedOut("Timed out"))
    assert postar(bot) is None
    assert [t for t, _ in bot.enviados] == ["foto"]


def test_timeout_no_texto_tambem_nao_e_reenviado():
    bot = BotFalso(texto=TimedOut("Timed out"))
    assert postar(bot, oferta(imagem=None)) is None
    assert [t for t, _ in bot.enviados] == ["texto"]


def test_imagem_recusada_pelo_telegram_cai_para_texto():
    bot = BotFalso(foto=BadRequest("Wrong type of the web page content"))
    assert postar(bot) == "mensagem-texto"
    assert [t for t, _ in bot.enviados] == ["foto", "texto"]


@pytest.mark.parametrize("erro", [Forbidden("bot foi removido do canal"), NetworkError("sem rede")])
def test_outros_erros_nao_viram_um_segundo_envio(erro):
    bot = BotFalso(foto=erro)
    with pytest.raises(type(erro)):
        postar(bot)
    assert [t for t, _ in bot.enviados] == ["foto"]


def test_da_tempo_de_sobra_para_o_telegram_baixar_a_imagem():
    bot = BotFalso()
    postar(bot)
    assert bot.enviados[0][1]["read_timeout"] >= 20
