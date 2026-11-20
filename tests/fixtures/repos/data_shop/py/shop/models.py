from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"


class Customer(Base):
    __tablename__ = "customers"


class Note:
    """Not a model: no table."""
