from shop.orders.models import Invoice, Refund


def invoices():
    return list(Invoice.objects.filter(number__startswith="A"))


def void(number):
    Invoice.objects.filter(number=number).delete()


def refund_total(db):
    return db.fetchone("select sum(amount) from refunds")
