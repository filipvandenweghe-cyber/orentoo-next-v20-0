from datetime import datetime, timedelta
from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import UserError


# Payment post-processing creates account.payment records, which need the
# company's outstanding/transfer account. That accounting wiring is only
# guaranteed once all modules and demo data have loaded, so run these
# post_install rather than at_install (at at_install the ambient company
# could still be missing its chart-of-accounts setup, raising the
# "No outstanding account could be found to make the payment" error). [PY03]
@tagged('post_install', '-at_install')
class TestPaymentOrchestration(TransactionCase):
    """Tests for dossier-level payment orchestration.  [PY01–PY10]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        cls.partner = cls.env['res.partner'].create({
            'name': 'MCRF Payment Test Customer',
            'email': 'paytest@mcrf.example.com',
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'MCRF Payment Pricelist',
            'company_id': cls.company.id,
        })

        # Storable rental product with stock
        cls.product_rental = cls.env['product.product'].create({
            'name': 'MCRF Pay Test Kayak',
            'type': 'consu',
            'is_storable': True,
            'list_price': 50.0,
            'rent_periodicity': 'days',
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'rental',
        })
        cls.env['stock.quant']._update_available_quantity(
            cls.product_rental,
            cls.warehouse.lot_stock_id,
            10.0,
        )

        cls.product_service = cls.env['product.product'].create({
            'name': 'MCRF Pay Test Guide',
            'type': 'service',
            'list_price': 40.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'service',
        })

        # Demo provider — ships disabled by default, enable it for tests.
        # Odoo 20 replaced payment.provider.state by 'active' (archived ==
        # disabled) + 'is_live' (live vs. test mode).
        cls.demo_provider = cls.env['payment.provider'].with_context(
            active_test=False,
        ).search([('code', '=', 'demo')], limit=1)
        if cls.demo_provider and not cls.demo_provider.active:
            cls.demo_provider.write({'active': True, 'is_live': False})
        cls.demo_method = cls.env['payment.method'].search([], limit=1)

        # Profile with demo payment
        cls.profile = cls.env['multi.channel.rental.profile'].create({
            'name': 'MCRF Pay Test Profile',
            'profile_type': 'kiosk',
            'warehouse_id': cls.warehouse.id,
            'pricelist_id': cls.pricelist.id,
            'allow_demo_payment': True,
            'payment_pending_timeout_minutes': 10,
        })

        cls.tomorrow = (datetime.now() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )

    def _create_dossier(self, **kwargs):
        vals = {
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'backend',
        }
        vals.update(kwargs)
        return self.env['rental.dossier'].create(vals)

    def _create_ready_dossier(self):
        """Create a dossier with a slot + item, prepared for payment."""
        dossier = self._create_dossier()
        slot = self.env['rental.dossier.slot'].create({
            'dossier_id': dossier.id,
            'start_datetime': self.tomorrow.replace(hour=10),
            'end_datetime': self.tomorrow.replace(hour=12),
            'warehouse_id': self.warehouse.id,
        })
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'slot_id': slot.id,
            'product_id': self.product_rental.id,
            'item_role': 'rental',
            'quantity': 1,
            'price_unit': 50.0,
        })
        # Prepare for payment (generate + confirm orders)
        dossier.action_prepare_for_payment()
        return dossier

    # ------------------------------------------------------------------
    # Transaction creation  [PY01, PY02]
    # ------------------------------------------------------------------

    def test_01_start_payment_creates_transaction(self):
        """action_start_payment creates a transaction linked to dossier."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx = dossier.action_start_payment()

        self.assertTrue(tx)
        self.assertEqual(tx.mcrf_dossier_id, dossier)
        self.assertEqual(tx.partner_id, self.partner)
        self.assertGreater(tx.amount, 0)
        self.assertEqual(tx.currency_id, dossier.currency_id)

    def test_02_transaction_linked_to_sale_orders(self):
        """Transaction is linked to all generated sale orders."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx = dossier.action_start_payment()

        generated = self.env['sale.order'].search([
            ('mcrf_dossier_id', '=', dossier.id),
        ])
        for order in generated:
            self.assertIn(order, tx.sale_order_ids)

    def test_03_payment_pending_until_set(self):
        """Payment deadline is set from profile timeout."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        dossier.action_start_payment()

        self.assertTrue(dossier.payment_pending_until)
        self.assertTrue(dossier.payment_reference)

    def test_04_payment_reference_set(self):
        """Payment reference is stored on dossier."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx = dossier.action_start_payment()

        self.assertEqual(dossier.payment_reference, tx.reference)

    # ------------------------------------------------------------------
    # Idempotency  [PY10]
    # ------------------------------------------------------------------

    def test_10_start_payment_idempotent(self):
        """Calling start_payment twice returns the same transaction."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx1 = dossier.action_start_payment()
        tx2 = dossier.action_start_payment()

        self.assertEqual(tx1, tx2)
        self.assertEqual(dossier.transaction_count, 1)

    # ------------------------------------------------------------------
    # Payment success  [PY05]
    # ------------------------------------------------------------------

    def test_20_payment_success(self):
        """Successful payment marks dossier as paid."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx = dossier.action_start_payment()

        dossier.action_payment_success(transaction=tx)

        self.assertEqual(dossier.state, 'paid')
        self.assertTrue(all(
            i.state == 'confirmed' for i in dossier.item_ids
        ))

    def test_21_payment_success_idempotent(self):
        """Calling payment_success twice is safe."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx = dossier.action_start_payment()

        dossier.action_payment_success(transaction=tx)
        dossier.action_payment_success(transaction=tx)

        self.assertEqual(dossier.state, 'paid')

    def test_22_website_payment_success_sends_email(self):
        """Website dossier payment success triggers email hook."""
        if not self.demo_provider:
            return
        website = self.env['website'].search([], limit=1)
        profile_web = self.env['multi.channel.rental.profile'].create({
            'name': 'Web Payment Profile',
            'profile_type': 'website',
            'warehouse_id': self.warehouse.id,
            'website_id': website.id,
            'allow_demo_payment': True,
        })
        dossier = self._create_dossier(
            source='website',
            profile_id=profile_web.id,
            partner_email='webtest@mcrf.example.com',
        )
        slot = self.env['rental.dossier.slot'].create({
            'dossier_id': dossier.id,
            'start_datetime': self.tomorrow.replace(hour=10),
            'end_datetime': self.tomorrow.replace(hour=12),
            'warehouse_id': self.warehouse.id,
        })
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'slot_id': slot.id,
            'product_id': self.product_rental.id,
            'item_role': 'rental',
            'quantity': 1,
            'price_unit': 50.0,
        })
        dossier.action_prepare_for_payment()
        tx = dossier.action_start_payment()

        # Payment success on website dossier → should post message
        dossier.action_payment_success(transaction=tx)
        self.assertEqual(dossier.state, 'paid')
        # Check chatter has confirmation message
        messages = dossier.message_ids.mapped('body')
        self.assertTrue(any('paid' in (m or '').lower() for m in messages))

    # ------------------------------------------------------------------
    # Payment failure  [PY06]
    # ------------------------------------------------------------------

    def test_30_payment_failed(self):
        """Failed payment cancels generated orders."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx = dossier.action_start_payment()

        dossier.action_payment_failed(transaction=tx)

        self.assertEqual(dossier.state, 'payment_failed')
        generated = self.env['sale.order'].search([
            ('mcrf_dossier_id', '=', dossier.id),
        ])
        self.assertTrue(all(o.state == 'cancel' for o in generated))

    def test_31_payment_failed_idempotent(self):
        """Calling payment_failed twice is safe."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx = dossier.action_start_payment()

        dossier.action_payment_failed(transaction=tx)
        dossier.action_payment_failed(transaction=tx)

        self.assertEqual(dossier.state, 'payment_failed')

    # ------------------------------------------------------------------
    # Payment expiry  [PY07, PY08]
    # ------------------------------------------------------------------

    def test_40_payment_expired(self):
        """Expired payment cancels generated orders."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        dossier.action_start_payment()

        dossier.action_payment_expired()

        self.assertEqual(dossier.state, 'payment_expired')

    def test_41_cron_expires_pending(self):
        """Cron expires dossiers past their deadline."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        dossier.action_start_payment()

        # Set deadline to the past
        dossier.payment_pending_until = datetime.now() - timedelta(hours=1)

        self.env['rental.dossier']._cron_expire_pending_dossiers()

        self.assertEqual(dossier.state, 'payment_expired')

    def test_42_cron_ignores_future_deadline(self):
        """Cron does not expire dossiers with future deadline."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        dossier.action_start_payment()
        # Deadline is in the future (set by action_start_payment)

        self.env['rental.dossier']._cron_expire_pending_dossiers()

        self.assertEqual(dossier.state, 'payment_pending')

    # ------------------------------------------------------------------
    # Post-process override  [PY09]
    # ------------------------------------------------------------------

    def test_50_post_process_done_marks_paid(self):
        """Transaction _post_process with state=done marks dossier paid."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx = dossier.action_start_payment()

        # Simulate provider callback: set tx to done
        tx.with_context(payment_safe_write=True)._set_done()
        tx._post_process_with_lock()

        self.assertEqual(dossier.state, 'paid')

    def test_51_post_process_cancel_marks_failed(self):
        """Transaction _post_process with state=cancel marks dossier failed."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx = dossier.action_start_payment()

        # Simulate cancellation
        tx.with_context(payment_safe_write=True)._set_canceled()
        tx._post_process_with_lock()

        self.assertEqual(dossier.state, 'payment_failed')

    # ------------------------------------------------------------------
    # Service product only (no stock needed)
    # ------------------------------------------------------------------

    def test_60_service_only_payment(self):
        """Dossier with only service items can start payment."""
        if not self.demo_provider:
            return
        dossier = self._create_dossier()
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.product_service.id,
            'item_role': 'service',
            'quantity': 1,
            'price_unit': 40.0,
        })
        dossier.action_prepare_for_payment()
        tx = dossier.action_start_payment()

        self.assertTrue(tx)
        self.assertEqual(dossier.state, 'payment_pending')

    # ------------------------------------------------------------------
    # State guards
    # ------------------------------------------------------------------

    def test_70_cannot_start_payment_on_paid(self):
        """Cannot start payment on an already paid dossier."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx = dossier.action_start_payment()
        dossier.action_payment_success(transaction=tx)

        with self.assertRaises(UserError):
            dossier.action_start_payment()

    def test_71_last_transaction_computed(self):
        """last_transaction_id returns the most recent transaction."""
        if not self.demo_provider:
            return
        dossier = self._create_ready_dossier()
        tx = dossier.action_start_payment()

        self.assertEqual(dossier.last_transaction_id, tx)
