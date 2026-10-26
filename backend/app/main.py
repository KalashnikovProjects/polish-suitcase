from contextlib import asynccontextmanager
import asyncio
import json
import logging
from math import isfinite
import os
import secrets
import time
from typing import Any, Literal
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from pymongo import MongoClient

from . import auth
from .patches import PatchError, PatchPermissionError
from .publisher import Publisher
from .storage import ChatStore, Conflict, chat_view, event_view, public_message


LOG = logging.getLogger(__name__)
SSE_HEARTBEAT_SECONDS = float(os.getenv("SSE_HEARTBEAT_SECONDS", "15"))
SSE_POLL_SECONDS = float(os.getenv("SSE_POLL_SECONDS", "0.5"))
if SSE_HEARTBEAT_SECONDS <= 0 or SSE_POLL_SECONDS <= 0:
    raise ValueError("SSE intervals must be positive")


def _check_json(value: Any, depth: int = 0) -> None:
    if depth > 50:
        raise ValueError("JSON nesting is too deep")
    if type(value) is int and not -(2**63) <= value < 2**63:
        raise ValueError("integer is outside MongoDB's supported range")
    if type(value) is float and not isfinite(value):
        raise ValueError("non-finite numbers are not allowed")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or "\x00" in key:
                raise ValueError("invalid JSON object key")
            _check_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_json(item, depth + 1)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("email must be a valid address")
        return normalized


class NewChat(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=10000)

    @field_validator("name", "message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()


class NewMessage(StrictModel):
    message: str | None = Field(default=None, max_length=10000)
    from_revision: int | None = Field(default=None, ge=1, strict=True)
    operations: list[dict[str, Any]] | None = Field(default=None, max_length=100)

    @field_validator("operations")
    @classmethod
    def valid_operations(cls, value: list[dict[str, Any]] | None):
        if value is not None:
            _check_json(value)
            if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 262144:
                raise ValueError("operations exceed 256 KiB")
        return value


class AgentStatus(StrictModel):
    status: Literal["working", "waiting_user", "completed", "error"]
    status_message: str | None = Field(default=None, alias="status-message", max_length=1000)
    last_processed_message_id: int | None = Field(default=None, ge=1, strict=True)


def _mongo_url() -> str:
    user = quote(os.getenv("MONGO_USER", "travel"), safe="")
    password = quote(os.getenv("MONGO_PASSWORD", "travel_dev_password"), safe="")
    host = os.getenv("MONGO_HOST", "mongodb")
    port = os.getenv("MONGO_PORT", "27017")
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("pika").setLevel(logging.WARNING)
    for name in ("JWT_SECRET", "AGENT_SERVICE_TOKEN"):
        if len(os.getenv(name, "")) < 32:
            raise RuntimeError(f"{name} must be configured with at least 32 characters")
    mongo = MongoClient(_mongo_url(), serverSelectionTimeoutMS=5000)
    mongo.admin.command("ping")
    store = ChatStore(mongo, os.getenv("MONGO_DATABASE", "travel_bot"))
    store.ensure_indexes()
    recovered = store.recover_unfinished_jobs()
    if recovered:
        LOG.warning("unfinished_tasks_replayed count=%s", recovered)
    publisher = Publisher(store)
    publisher.start()
    app.state.store = store
    app.state.publisher = publisher
    try:
        yield
    finally:
        publisher.stop()
        mongo.close()


app = FastAPI(title="Travel Bot Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in
                   os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


def error(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status)


@app.exception_handler(RequestValidationError)
async def validation_handler(_request: Request, exc: RequestValidationError):
    fields = [{"field": ".".join(map(str, issue["loc"])), "message": issue["msg"],
               "type": issue["type"]} for issue in exc.errors()]
    return error(422, "validation_error", "Invalid request", {"fields": fields})


@app.exception_handler(StarletteHTTPException)
async def http_handler(_request: Request, exc: StarletteHTTPException):
    return error(exc.status_code, "http_error", str(exc.detail))


@app.exception_handler(Exception)
async def internal_handler(_request: Request, _exc: Exception):
    LOG.exception("Unhandled backend error")
    return error(500, "internal_error", "Internal server error")


@app.exception_handler(Conflict)
async def conflict_handler(_request: Request, exc: Conflict):
    return error(409, exc.code, exc.message, exc.details)


@app.exception_handler(PatchPermissionError)
async def permission_handler(_request: Request, exc: PatchPermissionError):
    return error(403, "forbidden_path", str(exc))


@app.exception_handler(PatchError)
async def patch_handler(_request: Request, exc: PatchError):
    return error(400, "invalid_patch", str(exc))


@app.exception_handler(KeyError)
async def missing_handler(_request: Request, _exc: KeyError):
    return error(404, "not_found", "Chat not found")


def principal(request: Request, allow_cookie: bool = False) -> tuple[str, int | None]:
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    agent_token = os.environ["AGENT_SERVICE_TOKEN"]
    if token and secrets.compare_digest(token, agent_token):
        return "agent", None
    if not token and allow_cookie:
        token = request.cookies.get(auth.SSE_COOKIE_NAME, "")
    if not token:
        raise auth.AuthError("authentication required")
    user_id = auth.decode_access(token)
    if request.app.state.store.users.find_one({"_id": user_id}, {"_id": 1}) is None:
        raise auth.AuthError("user not found")
    return "user", user_id


@app.exception_handler(auth.AuthError)
async def auth_handler(_request: Request, exc: auth.AuthError):
    return error(401, "unauthorized", str(exc))


def user_only(request: Request) -> int:
    kind, user_id = principal(request)
    if kind != "user" or user_id is None:
        raise PatchPermissionError("user account required")
    return user_id


def owned_chat(request: Request, chat_id: int, user_id: int) -> dict:
    doc = request.app.state.store.get_chat(chat_id, user_id)
    if doc is None:
        raise KeyError(chat_id)
    return doc


def _set_auth_cookies(response: JSONResponse, access: str, refresh: str) -> None:
    secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    response.set_cookie(auth.COOKIE_NAME, refresh, httponly=True, secure=secure,
                        samesite="lax", max_age=auth.REFRESH_DAYS * 86400, path="/api/v1/auth")
    response.set_cookie(auth.SSE_COOKIE_NAME, access, httponly=True, secure=secure,
                        samesite="lax", max_age=auth.ACCESS_SECONDS, path="/api/v1/chats")


@app.get("/healthz")
def healthz(request: Request):
    request.app.state.store.db.command("ping")
    return {"ok": True}


@app.post("/api/v1/auth/register", status_code=201)
def register(request: Request, body: Credentials):
    try:
        user = auth.register(request.app.state.store, body.email, body.password)
    except auth.AuthError:
        return error(409, "email_exists", "Email already registered")
    return auth.user_view(user)


@app.post("/api/v1/auth/login")
def login(request: Request, body: Credentials):
    user = auth.verify_login(request.app.state.store, body.email, body.password)
    access = auth.create_access(user["_id"])
    refresh = auth.create_refresh(request.app.state.store, user["_id"])
    response = JSONResponse({"access_token": access, "token_type": "bearer",
                             "expires_in": auth.ACCESS_SECONDS, "user": auth.user_view(user)})
    _set_auth_cookies(response, access, refresh)
    return response


@app.post("/api/v1/auth/refresh")
def refresh(request: Request):
    token = request.cookies.get(auth.COOKIE_NAME)
    if not token:
        raise auth.AuthError("refresh token required")
    user_id, refresh_token = auth.rotate_refresh(request.app.state.store, token)
    access = auth.create_access(user_id)
    response = JSONResponse({"access_token": access, "token_type": "bearer",
                             "expires_in": auth.ACCESS_SECONDS})
    _set_auth_cookies(response, access, refresh_token)
    return response


@app.post("/api/v1/auth/logout", status_code=204)
def logout(request: Request):
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        auth.revoke_refresh(request.app.state.store, token)
    response = Response(status_code=204)
    response.delete_cookie(auth.COOKIE_NAME, path="/api/v1/auth")
    response.delete_cookie(auth.SSE_COOKIE_NAME, path="/api/v1/chats")
    return response


@app.get("/api/v1/users/me")
def me(request: Request):
    user_id = user_only(request)
    return auth.user_view(request.app.state.store.users.find_one({"_id": user_id}))


@app.get("/api/v1/chats")
def list_chats(request: Request, limit: int = 30):
    user_id = user_only(request)
    if limit < 1 or limit > 100:
        return error(400, "invalid_limit", "limit must be between 1 and 100")
    cursor = request.app.state.store.chats.find({"owner_id": user_id},
        {"_id": 1, "name": 1, "updated_at": 1}).sort("updated_at", -1).limit(limit)
    return [{"id": row["_id"], "name": row["name"], "updated_at": row["updated_at"]} for row in cursor]


@app.post("/api/v1/chats", status_code=201)
def create_chat(request: Request, body: NewChat):
    user_id = user_only(request)
    doc = request.app.state.store.create_chat(user_id, body.name, body.message)
    LOG.info("chat_created chat_id=%s revision=%s message_id=1 job_state=pending", doc["_id"], doc["revision"])
    request.app.state.publisher.wake()
    return {"id": doc["_id"], "revision": doc["revision"], "name": doc["name"]}


@app.get("/api/v1/chats/{chat_id}")
def get_chat(request: Request, chat_id: int):
    user_id = user_only(request)
    return chat_view(owned_chat(request, chat_id, user_id), agent=False)


@app.delete("/api/v1/chats/{chat_id}", status_code=204)
def delete_chat(request: Request, chat_id: int):
    user_id = user_only(request)
    deleted = request.app.state.store.chats.delete_one({"_id": chat_id, "owner_id": user_id})
    if not deleted.deleted_count:
        raise KeyError(chat_id)
    return Response(status_code=204)


@app.get("/api/v1/chats/{chat_id}/messages")
def get_messages(request: Request, chat_id: int):
    user_id = user_only(request)
    doc = owned_chat(request, chat_id, user_id)
    return [item for message in doc["history"] if (item := public_message(message, False)) is not None]


@app.post("/api/v1/chats/{chat_id}/messages", status_code=201)
def post_message(request: Request, chat_id: int, body: NewMessage):
    kind, user_id = principal(request)
    if body.message is not None and not body.message.strip():
        return error(400, "empty_message", "message must not be blank")
    if not body.message and not body.operations:
        return error(400, "empty_message", "message or operations required")
    if body.operations and body.from_revision is None:
        return error(400, "missing_revision", "from_revision is required with operations")
    if not body.operations and body.from_revision is not None:
        return error(400, "unexpected_revision", "from_revision is only used with operations")
    if body.operations == []:
        return error(400, "empty_operations", "operations must not be empty")
    if kind == "user" and body.message and body.operations:
        return error(400, "invalid_message", "user messages and actions are separate")
    actor = "agent" if kind == "agent" else "user-action" if body.operations else "user"
    doc, item = request.app.state.store.add_message(chat_id, actor, body.message,
        body.operations, body.from_revision, user_id)
    LOG.info("message_saved chat_id=%s message_id=%s actor=%s revision=%s operations=%s job_state=%s",
             chat_id, item["id"], actor, doc["revision"], len(body.operations or []), doc["job_state"])
    if doc["job_state"] == "pending":
        request.app.state.publisher.wake()
    result = public_message(item, kind == "agent")
    return {**result, "revision": doc["revision"]}


def _sse(kind: str, seq: int, data: dict[str, Any]) -> str:
    return f"event: {kind}\nid: {seq}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


@app.get("/api/v1/chats/{chat_id}/events")
async def events(request: Request, chat_id: int):
    kind, user_id = principal(request, allow_cookie=True)
    is_agent = kind == "agent"
    user_expiry = None
    if not is_agent:
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else request.cookies.get(auth.SSE_COOKIE_NAME, "")
        user_expiry = auth.access_expiry(token)
    store: ChatStore = request.app.state.store
    doc = await asyncio.to_thread(store.get_chat, chat_id, user_id)
    if doc is None:
        raise KeyError(chat_id)

    async def stream():
        seq = doc["event_seq"]
        if is_agent:
            LOG.info("agent_sse_opened chat_id=%s revision=%s event_seq=%s", chat_id, doc["revision"], seq)
        try:
            yield _sse("init_info", seq, chat_view(doc, is_agent))
            last_heartbeat = asyncio.get_running_loop().time()
            while not await request.is_disconnected():
                if user_expiry is not None and time.time() >= user_expiry:
                    return
                current = await asyncio.to_thread(store.get_chat, chat_id, user_id)
                if current is None:
                    return
                for entry in current["events"]:
                    if entry["seq"] > seq:
                        seq = entry["seq"]
                        visible = event_view(entry, is_agent)
                        if visible is not None:
                            if is_agent:
                                LOG.info("agent_sse_event chat_id=%s type=%s event_seq=%s",
                                         chat_id, visible["type"], seq)
                            yield _sse(visible["type"], seq, visible["data"])
                now = asyncio.get_running_loop().time()
                if now - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
                    yield ": ping\n\n"
                    last_heartbeat = now
                await asyncio.sleep(SSE_POLL_SECONDS)
        finally:
            if is_agent:
                LOG.info("agent_sse_closed chat_id=%s event_seq=%s", chat_id, seq)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@app.post("/internal/v1/chats/{chat_id}/agent")
def set_agent_status(request: Request, chat_id: int, body: AgentStatus):
    kind, _ = principal(request)
    if kind != "agent":
        raise PatchPermissionError("agent service token required")
    if body.status == "completed" and "last_processed_message_id" not in body.model_fields_set:
        return error(400, "missing_message_id", "last_processed_message_id is required")
    if body.status != "completed" and "last_processed_message_id" in body.model_fields_set:
        return error(400, "unexpected_message_id", "last_processed_message_id is only for completed")
    _doc, info = request.app.state.store.update_agent(chat_id, body.status,
        body.status_message, body.last_processed_message_id)
    LOG.info("agent_status_saved chat_id=%s status=%s last_processed_message_id=%s",
             chat_id, body.status, body.last_processed_message_id)
    if body.status == "completed":
        return {"status": "completed"}
    return info
