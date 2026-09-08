from pydantic import BaseModel


class ProfileResponse(BaseModel):
    user_id: str
    message: str
