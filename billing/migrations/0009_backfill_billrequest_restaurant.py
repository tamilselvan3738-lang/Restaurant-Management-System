from django.db import migrations


def backfill_bill_request_restaurant(apps, schema_editor):
    BillRequest = apps.get_model('billing', 'BillRequest')
    Restaurant = apps.get_model('accounts', 'Restaurant')

    default_restaurant = (
        Restaurant.objects.filter(status='ACTIVE').first()
        or Restaurant.objects.first()
    )

    for br in BillRequest.objects.filter(restaurant__isnull=True):
        rest = None
        if br.table and getattr(br.table, 'restaurant', None):
            rest = br.table.restaurant
        elif br.session and getattr(br.session, 'restaurant', None):
            rest = br.session.restaurant
        elif default_restaurant:
            rest = default_restaurant

        if rest:
            br.restaurant = rest
            br.save(update_fields=['restaurant'])


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0008_invoice_restaurant_payment_restaurant'),
    ]

    operations = [
        migrations.RunPython(backfill_bill_request_restaurant, migrations.RunPython.noop),
    ]
