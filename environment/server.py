import os
from fastapi import FastAPI
from .api import router
__version__ = os.environ.get("VERSION", "0.0.0")
app = FastAPI(title=os.getenv("ENV_APP_NAME", "AI Society"), version=__version__)
app.include_router(router)





