from sqlalchemy import Column, Integer, String, Text
from app.database import Base

class CallRecord(Base):
    __tablename__ = "call_records"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    status = Column(String, nullable=False)
    duration = Column(String, nullable=True)
    external_id = Column(String, nullable=True)
    questions = Column(Text, nullable=True)
    responses = Column(Text, nullable=True)
    transcript = Column(Text, nullable=True)
    created_at = Column(String, nullable=True)