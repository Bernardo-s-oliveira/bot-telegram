import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut

from .formatter import montar_caption
from .models import Oferta

log = logging.getLogger("ofertas.poster")

# O Telegram baixa a imagem do lado dele; com o limite padrão (5 s) a resposta costuma estourar mesmo com o post feito.
_TIMEOUT_ENVIO = 30


def teclado_oferta(o: Oferta) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Pegar oferta", url=o.url_afiliado)]])


async def postar_oferta(bot: Bot, oferta: Oferta, chat_id: str | int,
                        teclado: InlineKeyboardMarkup | None = None) -> Message | None:
    """Publica a oferta (foto + legenda; texto se o Telegram recusar a imagem). Retorna None se o Telegram não
    respondeu a tempo: a mensagem PODE ter sido publicada, então não reenvia (duplicaria o post no canal)."""
    caption = montar_caption(oferta)
    markup = teclado or teclado_oferta(oferta)
    if oferta.imagem:
        try:
            return await bot.send_photo(chat_id, oferta.imagem, caption=caption, parse_mode=ParseMode.HTML,
                                        reply_markup=markup, read_timeout=_TIMEOUT_ENVIO, write_timeout=_TIMEOUT_ENVIO)
        except TimedOut:
            log.warning("Telegram não respondeu ao envio da foto de '%s'; ela pode ter sido publicada, "
                        "não reenvio para não duplicar", oferta.titulo[:50])
            return None
        except BadRequest as e:  # imagem recusada pelo Telegram (nada foi publicado) -> cai para texto
            log.warning("send_photo recusado (%s), enviando como texto", e)
    try:
        return await bot.send_message(chat_id, caption, parse_mode=ParseMode.HTML, reply_markup=markup,
                                      disable_web_page_preview=True, read_timeout=_TIMEOUT_ENVIO,
                                      write_timeout=_TIMEOUT_ENVIO)
    except TimedOut:
        log.warning("Telegram não respondeu ao envio de '%s'; pode ter sido publicado, não reenvio", oferta.titulo[:50])
        return None
