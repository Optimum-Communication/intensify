from sqlalchemy import delete, select, text

from shop.models import Customer, Order

RECENT = """
    SELECT o.id, c.name
    FROM orders o JOIN customers c ON c.id = o.customer_id
    WHERE o.created > :since
"""


def recent(session, since):
    return session.execute(text(RECENT), {"since": since}).all()


def open_orders(session):
    return session.scalars(select(Order).where(Order.status == "open")).all()


def place(session, customer_id):
    session.add(Order(customer_id=customer_id))


def purge(session):
    session.execute(delete(Order).where(Order.status == "void"))


def rename(conn, customer_id, name):
    conn.execute(
        "UPDATE customers SET name = %s " "WHERE id = %s",
        (name, customer_id),
    )


def message():
    return "Select a file from the list"


def count(session):
    return session.get(Customer, 1)
