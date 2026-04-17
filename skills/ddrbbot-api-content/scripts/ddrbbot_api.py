from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import os
from pathlib import Path
from typing import Any


def _parse_key_value(items: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid key=value argument: {item}")
        key, value = item.split("=", 1)
        values[key] = value
    return values


def _strip_env_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _load_dotenv_values(path: Path) -> dict[str, str]:
    if not path.exists() or not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _strip_env_value(value)
    return values


def _resolve_base_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value.rstrip("/")

    dotenv_values = _load_dotenv_values(Path.cwd() / ".env")
    for key in ("DDRBBOT_API", "DDRBBOT_API_BASE_URL", "DDRBBOT_API_URL"):
        value = dotenv_values.get(key) or os.getenv(key)
        if value:
            return value.rstrip("/")

    return "http://127.0.0.1:8000"


def _load_payload(args: argparse.Namespace, *, required: bool) -> Any:
    raw: str | None = None
    if getattr(args, "json", None) is not None:
        raw = args.json
    elif getattr(args, "file", None) is not None:
        raw = Path(args.file).read_text(encoding="utf-8")
    elif getattr(args, "stdin", False):
        raw = sys.stdin.read()

    if raw is None:
        if required:
            raise SystemExit("A JSON payload is required. Use --json, --file, or --stdin.")
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON payload: {exc}") from exc


def _build_url(base_url: str, endpoint: str, query: dict[str, str] | None = None) -> str:
    base = base_url.rstrip("/") + "/"
    url = urllib.parse.urljoin(base, endpoint.lstrip("/"))
    if query:
        return f"{url}?{urllib.parse.urlencode(query)}"
    return url


def _request_json(
    *,
    base_url: str,
    method: str,
    endpoint: str,
    payload: Any = None,
    query: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> int:
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        _build_url(base_url, endpoint, query=query),
        data=body,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read().decode("utf-8")
            _print_response(content)
            return 0
    except urllib.error.HTTPError as exc:
        content = exc.read().decode("utf-8", errors="replace")
        _print_response(content, stream=sys.stderr)
        return exc.code or 1
    except urllib.error.URLError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1


def _print_response(content: str, *, stream: Any = sys.stdout) -> None:
    stripped = content.strip()
    if not stripped:
        return
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        print(content, file=stream)
        return
    print(json.dumps(parsed, ensure_ascii=False, indent=2), file=stream)


def _add_payload_input(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--json", help="Inline JSON payload string.")
    group.add_argument("--file", help="Path to a UTF-8 JSON file.")
    group.add_argument("--stdin", action="store_true", help="Read the JSON payload from stdin.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call local DDRBBOT API endpoints.")
    parser.add_argument(
        "--base-url",
        default=None,
        help=(
            "DDRBBOT API base URL. Default order: --base-url > ./.env "
            "(DDRBBOT_API / DDRBBOT_API_BASE_URL / DDRBBOT_API_URL) > http://127.0.0.1:8000"
        ),
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="GET /api/health")

    review_queue = subparsers.add_parser("review-queue", help="GET /api/qq/delivery/review-queue")
    review_queue.add_argument("--limit", type=int, default=5)

    review_items = subparsers.add_parser("review-items", help="GET /api/review/items")
    review_items.add_argument("--status", default="open")
    review_items.add_argument("--limit", type=int, default=24)
    review_items.add_argument("--processed-event-id", default=None)

    review_item = subparsers.add_parser("review-item", help="GET /api/review/items/{processed_event_id}")
    review_item.add_argument("processed_event_id")

    render_preview = subparsers.add_parser("render-preview", help="POST /api/render/preview")
    _add_payload_input(render_preview)
    render_preview_image = subparsers.add_parser(
        "render-preview-image",
        help="POST /api/render/preview-image",
    )
    _add_payload_input(render_preview_image)

    webhook_discord = subparsers.add_parser("webhook-discord", help="POST /api/webhook/discord")
    _add_payload_input(webhook_discord)

    collect_rss = subparsers.add_parser("collect-rss", help="POST /api/collect/rss")
    _add_payload_input(collect_rss)

    collect_rsshub = subparsers.add_parser("collect-rsshub", help="POST /api/collect/rsshub")
    _add_payload_input(collect_rsshub)

    sources_list = subparsers.add_parser("sources-list", help="GET /api/sources")
    sources_list.add_argument("--limit", type=int, default=50)

    sources_upsert = subparsers.add_parser("sources-upsert", help="POST /api/sources")
    _add_payload_input(sources_upsert)
    subparsers.add_parser(
        "sources-bootstrap-rsshub2",
        help="POST /api/sources/bootstrap-rsshub2",
    )

    qq_send = subparsers.add_parser("qq-send", help="POST /api/qq/send-news-card")
    _add_payload_input(qq_send)

    adapter_status = subparsers.add_parser(
        "adapter-status",
        help="GET /api/qq/adapter/status",
    )
    adapter_status.add_argument("--include-groups", action="store_true")
    adapter_status.add_argument("--limit", type=int, default=20)

    retry_failed = subparsers.add_parser(
        "retry-failed",
        help="POST /api/qq/delivery/retry-failed",
    )
    _add_payload_input(retry_failed)

    dead_letter_list = subparsers.add_parser(
        "dead-letter-list",
        help="GET /api/delivery/dead-letter",
    )
    dead_letter_list.add_argument("--limit", type=int, default=20)

    dead_letter_retry = subparsers.add_parser(
        "dead-letter-retry",
        help="POST /api/delivery/dead-letter/{record_id}/retry",
    )
    dead_letter_retry.add_argument("record_id")

    ai_chat = subparsers.add_parser(
        "ai-chat",
        help="POST /api/ai/chat",
    )
    ai_chat.add_argument("--message", help="Quick message; overrides --file/--json/--stdin.")
    ai_chat.add_argument("--origin", default="api", choices=["api", "qq_chat", "scheduler"])
    _add_payload_input(ai_chat)

    review_rerender = subparsers.add_parser(
        "review-rerender",
        help="POST /api/review/{processed_event_id}/rerender",
    )
    review_rerender.add_argument("processed_event_id")
    _add_payload_input(review_rerender)

    review_approve = subparsers.add_parser(
        "review-approve-send",
        help="POST /api/review/{processed_event_id}/approve-send",
    )
    review_approve.add_argument("processed_event_id")
    _add_payload_input(review_approve)

    review_reject = subparsers.add_parser(
        "review-reject",
        help="POST /api/review/{processed_event_id}/reject",
    )
    review_reject.add_argument("processed_event_id")
    _add_payload_input(review_reject)

    review_resend = subparsers.add_parser(
        "review-resend",
        help="POST /api/review/{processed_event_id}/resend",
    )
    review_resend.add_argument("processed_event_id")
    _add_payload_input(review_resend)

    generic_get = subparsers.add_parser("get", help="Generic GET <endpoint>")
    generic_get.add_argument("endpoint")
    generic_get.add_argument(
        "--query",
        action="append",
        default=[],
        help="Query parameter in key=value form. Repeat as needed.",
    )

    generic_post = subparsers.add_parser("post", help="Generic POST <endpoint>")
    generic_post.add_argument("endpoint")
    _add_payload_input(generic_post)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    base_kwargs = {"base_url": _resolve_base_url(args.base_url), "timeout": args.timeout}

    if args.command == "health":
        return _request_json(method="GET", endpoint="/api/health", **base_kwargs)
    if args.command == "review-queue":
        return _request_json(
            method="GET",
            endpoint="/api/qq/delivery/review-queue",
            query={"limit": str(args.limit)},
            **base_kwargs,
        )
    if args.command == "review-items":
        query: dict[str, str] = {"status": args.status, "limit": str(args.limit)}
        if args.processed_event_id:
            query["processed_event_id"] = args.processed_event_id
        return _request_json(method="GET", endpoint="/api/review/items", query=query, **base_kwargs)
    if args.command == "review-item":
        return _request_json(
            method="GET",
            endpoint=f"/api/review/items/{args.processed_event_id}",
            **base_kwargs,
        )
    if args.command == "render-preview":
        return _request_json(
            method="POST",
            endpoint="/api/render/preview",
            payload=_load_payload(args, required=True),
            **base_kwargs,
        )
    if args.command == "render-preview-image":
        return _request_json(
            method="POST",
            endpoint="/api/render/preview-image",
            payload=_load_payload(args, required=True),
            **base_kwargs,
        )
    if args.command == "webhook-discord":
        return _request_json(
            method="POST",
            endpoint="/api/webhook/discord",
            payload=_load_payload(args, required=True),
            **base_kwargs,
        )
    if args.command == "collect-rss":
        return _request_json(
            method="POST",
            endpoint="/api/collect/rss",
            payload=_load_payload(args, required=True),
            **base_kwargs,
        )
    if args.command == "collect-rsshub":
        return _request_json(
            method="POST",
            endpoint="/api/collect/rsshub",
            payload=_load_payload(args, required=True),
            **base_kwargs,
        )
    if args.command == "sources-list":
        return _request_json(
            method="GET",
            endpoint="/api/sources",
            query={"limit": str(args.limit)},
            **base_kwargs,
        )
    if args.command == "sources-upsert":
        return _request_json(
            method="POST",
            endpoint="/api/sources",
            payload=_load_payload(args, required=True),
            **base_kwargs,
        )
    if args.command == "sources-bootstrap-rsshub2":
        return _request_json(
            method="POST",
            endpoint="/api/sources/bootstrap-rsshub2",
            payload={},
            **base_kwargs,
        )
    if args.command == "adapter-status":
        query: dict[str, str] = {"limit": str(args.limit)}
        if args.include_groups:
            query["include_groups"] = "true"
        return _request_json(
            method="GET",
            endpoint="/api/qq/adapter/status",
            query=query,
            **base_kwargs,
        )
    if args.command == "dead-letter-list":
        return _request_json(
            method="GET",
            endpoint="/api/delivery/dead-letter",
            query={"limit": str(args.limit)},
            **base_kwargs,
        )
    if args.command == "dead-letter-retry":
        return _request_json(
            method="POST",
            endpoint=f"/api/delivery/dead-letter/{args.record_id}/retry",
            payload={},
            **base_kwargs,
        )
    if args.command == "ai-chat":
        payload = _load_payload(args, required=False)
        if args.message:
            payload = {"message": args.message, "origin": args.origin}
        elif isinstance(payload, dict):
            payload.setdefault("origin", args.origin)
        else:
            raise SystemExit("ai-chat requires --message, --file, --json or --stdin.")
        return _request_json(
            method="POST",
            endpoint="/api/ai/chat",
            payload=payload,
            **base_kwargs,
        )
    if args.command == "qq-send":
        return _request_json(
            method="POST",
            endpoint="/api/qq/send-news-card",
            payload=_load_payload(args, required=True),
            **base_kwargs,
        )
    if args.command == "retry-failed":
        return _request_json(
            method="POST",
            endpoint="/api/qq/delivery/retry-failed",
            payload=_load_payload(args, required=True),
            **base_kwargs,
        )
    if args.command == "review-rerender":
        return _request_json(
            method="POST",
            endpoint=f"/api/review/{args.processed_event_id}/rerender",
            payload=_load_payload(args, required=True),
            **base_kwargs,
        )
    if args.command == "review-approve-send":
        return _request_json(
            method="POST",
            endpoint=f"/api/review/{args.processed_event_id}/approve-send",
            payload=_load_payload(args, required=True),
            **base_kwargs,
        )
    if args.command == "review-reject":
        return _request_json(
            method="POST",
            endpoint=f"/api/review/{args.processed_event_id}/reject",
            payload=_load_payload(args, required=False) or {},
            **base_kwargs,
        )
    if args.command == "review-resend":
        return _request_json(
            method="POST",
            endpoint=f"/api/review/{args.processed_event_id}/resend",
            payload=_load_payload(args, required=False) or {},
            **base_kwargs,
        )
    if args.command == "get":
        return _request_json(
            method="GET",
            endpoint=args.endpoint,
            query=_parse_key_value(args.query),
            **base_kwargs,
        )
    if args.command == "post":
        return _request_json(
            method="POST",
            endpoint=args.endpoint,
            payload=_load_payload(args, required=True),
            **base_kwargs,
        )

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
