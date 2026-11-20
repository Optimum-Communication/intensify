from django.db import models


class Invoice(models.Model):
    number = models.CharField(max_length=20)


class Refund(models.Model):
    class Meta:
        db_table = "refunds"
