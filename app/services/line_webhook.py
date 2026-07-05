from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings
from app.services.chat_connectors import (
    build_chat_metadata_json,
    build_line_source_path,
    extract_case_code,
    ingest_chat_payload,
)
from app.services.ingestion import IngestionService


def build_line_event_summary(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "unknown")
    source = event.get("source") or {}
    source_type = str(source.get("type") or "")
    source_id = source.get("groupId") or source.get("roomId") or source.get("userId") or ""
    summary = f"LINE {event_type} event"
    if source_type or source_id:
        summary += f" from {source_type or 'unknown'}"
        if source_id:
            summary += f" {source_id}"
    if event_type == "unsend":
        unsend = event.get("unsend") or {}
        unsend_message_id = unsend.get("messageId") or unsend.get("message_id")
        if unsend_message_id:
            summary += f" for message {unsend_message_id}"
    elif event_type == "postback":
        postback = event.get("postback") or {}
        data = str(postback.get("data") or "").strip()
        if data:
            summary += f" data {data}"
        params = postback.get("params") or {}
        if isinstance(params, dict) and params:
            details = ", ".join(f"{key}={value}" for key, value in sorted(params.items()))
            summary += f" params ({details})"
    elif event_type == "beacon":
        beacon = event.get("beacon") or {}
        hwid = str(beacon.get("hwid") or "").strip()
        dm = str(beacon.get("dm") or "").strip()
        if hwid:
            summary += f" hwid {hwid}"
        if dm:
            summary += f" dm {dm}"
    elif event_type == "accountLink":
        link = event.get("link") or {}
        result = str(link.get("result") or "").strip()
        nonce = str(link.get("nonce") or "").strip()
        if result:
            summary += f" result {result}"
        if nonce:
            summary += f" nonce {nonce}"
    elif event_type == "videoPlayComplete":
        video_play_complete = event.get("videoPlayComplete") or {}
        tracking_id = str(video_play_complete.get("trackingId") or "").strip()
        if tracking_id:
            summary += f" trackingId {tracking_id}"
    elif event_type == "message":
        message = event.get("message") or {}
        message_type = str(message.get("type") or "").strip()
        if message_type:
            summary += f" {message_type}"
        quoted_message_id = str(message.get("quotedMessageId") or "").strip()
        if quoted_message_id:
            summary += f" quotedMessageId {quoted_message_id}"
    return summary


def build_line_event_extra_metadata(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type") or "unknown")
    extra_metadata: dict[str, Any] = {}
    reply_token = str(event.get("replyToken") or "").strip()
    if reply_token:
        extra_metadata["reply_token"] = reply_token
    delivery_context = event.get("deliveryContext") or {}
    if isinstance(delivery_context, dict) and "isRedelivery" in delivery_context:
        extra_metadata["is_redelivery"] = bool(delivery_context.get("isRedelivery"))
    if event_type == "unsend":
        unsend = event.get("unsend") or {}
        unsend_message_id = unsend.get("messageId") or unsend.get("message_id")
        if unsend_message_id:
            extra_metadata["unsend_message_id"] = unsend_message_id
    elif event_type == "postback":
        postback = event.get("postback") or {}
        data = str(postback.get("data") or "").strip()
        if data:
            extra_metadata["postback_data"] = data
        params = postback.get("params") or {}
        if isinstance(params, dict) and params:
            extra_metadata["postback_params"] = {
                str(key): value for key, value in sorted(params.items())
            }
    elif event_type == "beacon":
        beacon = event.get("beacon") or {}
        hwid = str(beacon.get("hwid") or "").strip()
        dm = str(beacon.get("dm") or "").strip()
        if hwid:
            extra_metadata["beacon_hwid"] = hwid
        if dm:
            extra_metadata["beacon_dm"] = dm
    elif event_type == "accountLink":
        link = event.get("link") or {}
        result = str(link.get("result") or "").strip()
        nonce = str(link.get("nonce") or "").strip()
        if result:
            extra_metadata["account_link_result"] = result
        if nonce:
            extra_metadata["account_link_nonce"] = nonce
    elif event_type == "videoPlayComplete":
        video_play_complete = event.get("videoPlayComplete") or {}
        tracking_id = str(video_play_complete.get("trackingId") or "").strip()
        if tracking_id:
            extra_metadata["video_play_complete_tracking_id"] = tracking_id
    elif event_type == "message":
        message = event.get("message") or {}
        quoted_message_id = str(message.get("quotedMessageId") or "").strip()
        if quoted_message_id:
            extra_metadata["quoted_message_id"] = quoted_message_id
    return extra_metadata


def build_line_message_extra_metadata(event: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    extra_metadata = build_line_event_extra_metadata(event)
    quote_token = str(message.get("quoteToken") or event.get("quoteToken") or "").strip()
    if quote_token:
        extra_metadata["quote_token"] = quote_token
    quoted_message_id = str(message.get("quotedMessageId") or event.get("quotedMessageId") or "").strip()
    if quoted_message_id:
        extra_metadata["quoted_message_id"] = quoted_message_id
    return extra_metadata


@dataclass(frozen=True)
class LineWebhookItemResult:
    event_type: str
    status: str
    case_code: str | None = None
    case_id: int | None = None
    document_id: int | None = None
    reason: str | None = None
    event_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class LineWebhookResult:
    processed_count: int
    ingested_count: int
    pending_count: int
    skipped_count: int
    items: list[LineWebhookItemResult] = field(default_factory=list)


class LineWebhookClient:
    def __init__(
        self,
        settings: Settings,
        ingestion_service: IngestionService,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.ingestion_service = ingestion_service
        self.transport = transport

    def verify_signature(self, raw_body: bytes, signature: str | None) -> bool:
        secret = (self.settings.line_channel_secret or "").strip()
        if not secret:
            return True
        if not signature:
            return False
        digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected, signature)

    async def process_webhook(self, raw_body: bytes, signature: str | None) -> LineWebhookResult:
        if not self.verify_signature(raw_body, signature):
            raise ValueError("Invalid LINE webhook signature.")

        payload = json.loads(raw_body.decode("utf-8"))
        events = payload.get("events") or []
        items: list[LineWebhookItemResult] = []
        ingested_count = 0
        pending_count = 0
        for event in events:
            item = await self.process_event(event)
            items.append(item)
            if item.status == "ingested":
                ingested_count += 1
            elif item.status == "pending":
                pending_count += 1
        return LineWebhookResult(
            processed_count=len(events),
            ingested_count=ingested_count,
            pending_count=pending_count,
            skipped_count=len(events) - ingested_count - pending_count,
            items=items,
        )

    async def process_event(self, event: dict[str, Any]) -> LineWebhookItemResult:
        event_type = str(event.get("type") or "unknown")
        if event_type != "message":
            case_code = (self.settings.line_inbox_case_code or "").strip() or "LINE-INBOX"
            source = event.get("source") or {}
            summary = build_line_event_summary(event)
            content = self._build_event_snapshot(event)
            filename = f"line-{event_type}.json"
            source_path = f"line/event/{event_type}/{event.get('webhookEventId') or event_type}"
            title = f"LINE {event_type} event"
            extra_metadata = {
                "webhook_event_id": event.get("webhookEventId"),
                "event_type": event_type,
                "event_summary": summary,
                "source_type": source.get("type"),
                "event_json": event,
            }
            extra_metadata.update(build_line_event_extra_metadata(event))
            structured_json = build_chat_metadata_json(
                platform="line",
                source_path=source_path,
                message_id=str(event.get("webhookEventId") or ""),
                channel_id=str(source.get("groupId") or source.get("roomId") or ""),
                author_name=str(source.get("userId") or ""),
                message_text=None,
                extra=extra_metadata,
            )
            result = ingest_chat_payload(
                self.ingestion_service,
                platform="line",
                case_code=case_code,
                title=title,
                filename=filename,
                content=content,
                mime_type="application/json",
                source_path=source_path,
                client_name=None,
                due_date=None,
                invoice_status="unbilled",
                output_status="pending",
                extracted_text=summary + "\n\n" + json.dumps(event, ensure_ascii=False, indent=2),
                structured_json=structured_json,
                rag_chunks=[],
                output_html=None,
            )
            return LineWebhookItemResult(
                event_type=event_type,
                status="ingested",
                case_code=case_code,
                case_id=getattr(result, "case_id", None),
                document_id=getattr(result, "document_id", None),
                event_json=event,
            )

        message = event.get("message") or {}
        message_type = str(message.get("type") or "unknown")
        message_text = message.get("text")
        message_file_name = str(message.get("fileName") or "")
        case_code = extract_case_code(message_text or "") or extract_case_code(message_file_name)
        if not case_code and message_type in {"image", "video", "audio", "file", "sticker", "location"}:
            case_code = (self.settings.line_inbox_case_code or "").strip() or "LINE-INBOX"
        if not case_code:
            return LineWebhookItemResult(
                event_type=event_type,
                status="skipped",
                reason="case_code_not_found",
                event_json=event,
            )

        source = event.get("source") or {}
        content_provider = event.get("contentProvider") or {}
        source_path = build_line_source_path(
            room_id=source.get("roomId"),
            group_id=source.get("groupId"),
            user_id=source.get("userId"),
            message_id=str(message.get("id") or event.get("webhookEventId") or ""),
        )
        content, filename, mime_type, status, reason = await self._resolve_content(message, message_type, content_provider)
        if status == "pending":
            return LineWebhookItemResult(
                event_type=event_type,
                status="pending",
                case_code=case_code,
                reason=reason,
                event_json=event,
            )
        if content is None:
            return LineWebhookItemResult(
                event_type=event_type,
                status="skipped",
                case_code=case_code,
                reason=reason or "content_unavailable",
                event_json=event,
            )

        title = message_text or message.get("fileName") or f"LINE {message_type} message"
        structured_json = build_chat_metadata_json(
            platform="line",
            source_path=source_path,
            message_id=str(message.get("id") or event.get("webhookEventId") or ""),
            channel_id=str(source.get("groupId") or source.get("roomId") or ""),
            author_name=str(source.get("userId") or ""),
            message_text=message_text,
            extra={
                "webhook_event_id": event.get("webhookEventId"),
                "message_type": message_type,
                "file_name": message_file_name or None,
                "source_type": source.get("type"),
                **build_line_message_extra_metadata(event, message),
            },
        )
        result = ingest_chat_payload(
            self.ingestion_service,
            platform="line",
            case_code=case_code,
            title=title,
            filename=filename,
            content=content,
            mime_type=mime_type,
            source_path=source_path,
            client_name=None,
            due_date=None,
            invoice_status="unbilled",
            output_status="pending",
            extracted_text=message_text if message_type == "text" else None,
            structured_json=structured_json,
            rag_chunks=[],
            output_html=None,
        )
        return LineWebhookItemResult(
            event_type=event_type,
            status="ingested",
            case_code=case_code,
            case_id=getattr(result, "case_id", None),
            document_id=getattr(result, "document_id", None),
            event_json=event,
        )

    async def _resolve_content(
        self,
        message: dict[str, Any],
        message_type: str,
        content_provider: dict[str, Any],
    ) -> tuple[bytes | None, str, str | None, str, str | None]:
        if message_type == "text":
            text = str(message.get("text") or "")
            return text.encode("utf-8"), "message.txt", "text/plain", "ready", None
        if message_type == "sticker":
            sticker_lines = [
                "LINE sticker message",
                f"stickerId: {message.get('stickerId') or ''}",
                f"packageId: {message.get('packageId') or ''}",
                f"stickerResourceType: {message.get('stickerResourceType') or ''}",
            ]
            keywords = message.get("keywords") or []
            if isinstance(keywords, list) and keywords:
                sticker_lines.append(f"keywords: {', '.join(str(keyword) for keyword in keywords)}")
            text = message.get("text")
            if text:
                sticker_lines.append(f"text: {text}")
            quoted_message_id = message.get("quotedMessageId")
            if quoted_message_id:
                sticker_lines.append(f"quotedMessageId: {quoted_message_id}")
            return "\n".join(sticker_lines).encode("utf-8"), self._build_filename(message, message_type), "text/plain", "ready", None
        if message_type == "location":
            location_lines = [
                "LINE location message",
                f"title: {message.get('title') or ''}",
                f"address: {message.get('address') or ''}",
                f"latitude: {message.get('latitude') or ''}",
                f"longitude: {message.get('longitude') or ''}",
            ]
            return "\n".join(location_lines).encode("utf-8"), self._build_filename(message, message_type), "text/plain", "ready", None

        access_token = (self.settings.notification_line_channel_access_token or "").strip()
        message_id = str(message.get("id") or "").strip()
        if message_type in {"video", "audio"} and content_provider.get("type") == "line":
            if not access_token or not message_id:
                return None, self._build_filename(message, message_type), None, "skipped", "content_unavailable"
            transcoding_status = await self._get_transcoding_status(message_id, access_token)
            if transcoding_status == "processing":
                return None, self._build_filename(message, message_type), None, "pending", "content_processing"
            if transcoding_status == "failed":
                return None, self._build_filename(message, message_type), None, "skipped", "transcoding_failed"
            if transcoding_status is None:
                return None, self._build_filename(message, message_type), None, "skipped", "content_unavailable"

        if not access_token or not message_id:
            return None, self._build_filename(message, message_type), None, "skipped", "content_unavailable"

        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            response = await client.get(
                f"{self.settings.notification_line_data_api_base_url}/v2/bot/message/{message_id}/content",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            content_type = response.headers.get("Content-Type")
            return response.content, self._build_filename(message, message_type, content_type), content_type, "ready", None

    async def _get_transcoding_status(self, message_id: str, access_token: str) -> str | None:
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            response = await client.get(
                f"{self.settings.notification_line_data_api_base_url}/v2/bot/message/{message_id}/content/transcoding",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code == 404 or response.status_code == 410:
                return None
            response.raise_for_status()
            payload = response.json()
            return str(payload.get("status") or "").strip() or None

    def _build_filename(self, message: dict[str, Any], message_type: str, content_type: str | None = None) -> str:
        file_name = str(message.get("fileName") or "").strip()
        if file_name:
            return file_name
        suffix = {
            "image": ".png",
            "video": ".mp4",
            "audio": ".m4a",
            "file": ".bin",
            "sticker": ".txt",
            "location": ".txt",
        }.get(message_type, ".bin")
        if content_type == "image/jpeg":
            suffix = ".jpg"
        elif content_type == "image/png":
            suffix = ".png"
        return f"line-{message_type}{suffix}"

    def _build_event_snapshot(self, event: dict[str, Any]) -> bytes:
        return json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
