from pydantic import BaseModel, field_validator

class CallCreate(BaseModel):
    name: str
    phone: str
    
    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        # Remove common formatting characters
        cleaned = ''.join(c for c in v if c.isdigit() or c in '+ ()-')
        # Must have at least 10 digits
        digits_only = ''.join(c for c in cleaned if c.isdigit())
        if len(digits_only) < 10:
            raise ValueError('Phone number must contain at least 10 digits')
        return cleaned