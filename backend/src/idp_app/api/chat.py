"""Document chat API: questions to the chat Supervisor and the caller's own history."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from idp_app.api.dependencies import get_authenticated_user, get_document_chat_service
from idp_app.services.document_chat import DocumentChatService

chat_router = APIRouter(prefix="/chat", tags=["chat"])

User = Annotated[str, Depends(get_authenticated_user)]
Service = Annotated[DocumentChatService, Depends(get_document_chat_service)]


class Question(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=36)
    text: str = Field(min_length=1, max_length=4000)


@chat_router.get("/conversations")
async def conversations(user: User, service: Service) -> dict[str, Any]:
    return {"conversations": await run_in_threadpool(service.conversations, user)}


@chat_router.get("/conversations/{conversation_id}")
async def conversation(conversation_id: str, user: User, service: Service) -> dict[str, Any]:
    messages = await run_in_threadpool(service.conversation, user, conversation_id)
    return {"conversation_id": conversation_id, "messages": messages}


@chat_router.post("/messages")
async def ask(question: Question, user: User, service: Service) -> dict[str, Any]:
    return await run_in_threadpool(service.ask, user, question.conversation_id, question.text)
