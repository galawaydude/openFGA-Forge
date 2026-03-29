from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from config import settings
from database import engine, Base
from routers import projects, compiler, templates, ws
from services.template_service import load_templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    load_templates()
    yield
    await engine.dispose()


app = FastAPI(title="FGA Forge", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(compiler.router)
app.include_router(templates.router)
app.include_router(ws.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.exception_handler(Exception)
async def global_exc(request, exc):
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
