from __future__ import annotations

import logging
import ssl
from email.message import EmailMessage
from typing import List
from urllib.parse import urlencode

import aiosmtplib
import certifi

from .config import Anliegen, Config

log = logging.getLogger(__name__)


def _build_message(cfg: Config, to_email: str, subject: str, body_text: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = cfg.smtp.from_address
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)
    return msg


async def _send(cfg: Config, msg: EmailMessage) -> None:
    await aiosmtplib.send(
        msg,
        hostname=cfg.smtp.host,
        port=cfg.smtp.port,
        start_tls=True,
        username=cfg.smtp.username,
        password=cfg.smtp.password,
        tls_context=ssl.create_default_context(cafile=certifi.where()),
    )


async def send_verification(cfg: Config, email: str, verify_token: str) -> None:
    link = f"{cfg.app.base_url.rstrip('/')}/verify?{urlencode({'token': verify_token})}"
    body = (
        "您好 / Hello,\n\n"
        "感谢订阅亚琛外管局 Termin 提醒。\n"
        "Thank you for subscribing to Aachen Ausländerbehörde Termin alerts.\n\n"
        "请点击以下链接确认订阅(24 小时内有效):\n"
        "Please confirm your subscription by clicking the link below "
        "(valid for 24 hours):\n\n"
        f"{link}\n\n"
        "如果这不是您本人操作,请忽略此邮件。\n"
        "If you did not request this, please ignore this email.\n"
    )
    msg = _build_message(
        cfg,
        to_email=email,
        subject="确认订阅 / Confirm your Aachen Termin subscription",
        body_text=body,
    )
    await _send(cfg, msg)
    log.info("sent verification email to %s", email)


async def send_heartbeat(cfg: Config, to_email: str, summary: str) -> None:
    """Daily 'I'm alive' email so the operator knows the scraper is still running
    even during long dry spells with no slots."""
    body = (
        "Aachen Termin Tracker — 每日状态 / Daily status\n\n"
        f"{summary}\n\n"
        "这是一封自动状态邮件,说明追踪器在正常运行。如果你连续几天收不到这封信,"
        "说明爬虫可能挂了,需要去服务器上看一眼。\n"
        "This is an automated heartbeat confirming the tracker is running. "
        "If these stop arriving for several days, the scraper is probably down.\n"
    )
    msg = _build_message(
        cfg,
        to_email=to_email,
        subject="✅ Aachen Termin Tracker 运行正常 / daily status",
        body_text=body,
    )
    await _send(cfg, msg)
    log.info("sent heartbeat to %s", to_email)


async def send_termin_alert(
    cfg: Config,
    email: str,
    unsubscribe_token: str,
    anliegen: Anliegen,
    new_slot_dates: List[str],
) -> None:
    booking_url = cfg.scraper.target_url
    unsubscribe_url = (
        f"{cfg.app.base_url.rstrip('/')}/unsubscribe?{urlencode({'token': unsubscribe_token})}"
    )
    dates_block = "\n".join(f"  - {d}" for d in new_slot_dates)
    body = (
        f"亚琛外管局有新的 Termin 名额放出了!\n"
        f"New Aachen Ausländerbehörde Termin slots are available!\n\n"
        f"类别 / Category: {anliegen.label_zh} ({anliegen.label_de})\n\n"
        f"可预约日期 / Available dates:\n{dates_block}\n\n"
        f"立刻去预约 / Book now:\n{booking_url}\n\n"
        f"——\n"
        f"不想再收到通知?点这里退订 / Unsubscribe: {unsubscribe_url}\n"
    )
    msg = _build_message(
        cfg,
        to_email=email,
        subject=f"⏰ 亚琛 Termin 有空位!{anliegen.label_zh} / Aachen slots open",
        body_text=body,
    )
    await _send(cfg, msg)
    log.info("sent termin alert to %s for %s (%d slots)", email, anliegen.id, len(new_slot_dates))
