from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Union, Literal
from .pipeline import DIRECT_PIPELINE, INTERACTIVE_PIPELINE
from packages.core.session import session_manager
from packages.core.models import (
    ConversationState,
    MessageRole,
    UserPreferences,
    Sprint,
    Task,
)
from packages.core.agents import (
    breakdown_task_llm,
    integrate_clarifications_llm,
    plan_llm,
    prioritize_llm,
    estimate_llm,
    package_llm,
)


class Goal(BaseModel):
    message: str


class ConversationStart(BaseModel):
    message: str
    preferences: Optional[Dict] = None  # allow updating preferences


class ConversationContinue(BaseModel):
    session_id: str
    answers: Union[str, Dict[str, str]]  # single answer / question->answer mapping


class TaskBreakdownRequest(BaseModel):
    task_id: Optional[str] = None
    task: Optional[Task] = None  # full task object if not fetching by ID
    breakdown_type: Literal["subtasks", "pomodoros"]  # "subtasks" or "pomodoros"


class ConversationResponse(BaseModel):
    session_id: str
    type: str  # "questions" or "plan"
    questions: Optional[List[str]] = None
    plan: Optional[List[Sprint]] = None
    message: Optional[str] = None


class PreferencesUpdate(BaseModel):
    sprint_length_weeks: Optional[int] = None
    priority_system: Optional[str] = None
    tone: Optional[str] = None
    work_hours_per_week: Optional[int] = None
    preferred_task_size: Optional[str] = None
    include_breaks: Optional[bool] = None
    timezone: Optional[str] = None


app = FastAPI(
    title="Proximal API",
    description="Agentic ecosystem helping to scaffold executing functioning",
    version="0.2.0",
)


@app.post("/plan", response_model=List[Sprint])
async def plan(goal: Goal):
    """One-shot planning endpoint (backward compatible)"""
    initial_state = {"goal": goal.message}
    result = await DIRECT_PIPELINE.ainvoke(initial_state)
    return result["sprints"]


@app.post("/conversation/start", response_model=ConversationResponse)
async def start_conversation(request: ConversationStart):
    """Start an interactive planning conversation or update preferences only."""
    # if only updating preferences, update and return
    if request.preferences:
        current_prefs = session_manager.get_user_preferences()
        for key, value in request.preferences.items():
            if hasattr(current_prefs, key):
                setattr(current_prefs, key, value)
        session_manager.save_user_preferences(current_prefs)
        return ConversationResponse(session_id="", type="")

    # else fall through to launching the pipeline
    session = session_manager.create_session(request.message)
    session.add_message(MessageRole.user, request.message)
    initial_state = {"goal": request.message, "session_id": session.session_id}
    result = await INTERACTIVE_PIPELINE.ainvoke(initial_state)

    # if clarifications needed, return questions
    if result.get("needs_clarification", False):
        questions = result.get("clarification_questions", [])
        if questions:
            text = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
            session.add_message(MessageRole.assistant, text)
        return ConversationResponse(
            session_id=session.session_id,
            type="questions",
            questions=questions,
        )
    # else return the plan directly
    return ConversationResponse(
        session_id=session.session_id,
        type="plan",
        plan=result.get("sprints", []),
    )


@app.post("/conversation/continue", response_model=ConversationResponse)
async def continue_conversation(request: ConversationContinue):
    """Continue an existing conversation"""
    session = session_manager.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    # record user's answers
    if isinstance(request.answers, str):
        answers_text = request.answers
    else:
        answers_text = "\n".join(f"{q}: {a}" for q, a in request.answers.items())
    session.add_message(MessageRole.user, answers_text)

    # proceed directly to planning (integration + pipeline)
    enriched = await integrate_clarifications_llm(
        {
            "goal": session.goal,
            "session_id": session.session_id,
        }
    )
    enriched["session_id"] = session.session_id

    state = await plan_llm(enriched)
    state = await prioritize_llm(state)
    state = await estimate_llm(state)
    state = await package_llm(state)

    return ConversationResponse(
        session_id=session.session_id,
        type="plan",
        plan=state.get("sprints", []),
    )


@app.get("/conversation/{session_id}")
async def get_conversation(session_id: str):
    """Get current conversation state"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return {
        "session_id": session.session_id,
        "status": session.status,
        "messages": [m.model_dump() for m in session.messages],
        "clarification_count": session.clarification_count,
    }


@app.post("/task/breakdown")
async def breakdown_task(request: TaskBreakdownRequest):
    """Break down a task into subtasks or pomodoros"""
    task = request.task
    if not task:
        raise HTTPException(status_code=400, detail="Task required")
    breakdown = await breakdown_task_llm(task, request.breakdown_type)
    return {
        "task_id": task.id,
        "task_title": task.title,
        "breakdown_type": request.breakdown_type,
        "breakdown": breakdown,
    }


@app.get("/preferences")
async def get_preferences():
    """Get current user preferences"""
    prefs = session_manager.get_user_preferences()
    return prefs.model_dump()


@app.put("/preferences")
async def update_preferences(update: PreferencesUpdate):
    """Update user preferences"""
    current = session_manager.get_user_preferences()
    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(current, key, value)
    session_manager.save_user_preferences(current)
    return current.model_dump()


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "version": "0.2.0"}


def start():
    import uvicorn

    uvicorn.run(
        "apps.server.main:app",
        host="0.0.0.0",
        port=7315,
        reload=True,
    )
