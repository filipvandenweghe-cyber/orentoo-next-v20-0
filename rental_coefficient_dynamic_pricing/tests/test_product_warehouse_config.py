from datetime import datetime

from odoo.tests import TransactionCase
from odoo.tools import mute_logger
from psycopg2 import IntegrityError


class TestProductWarehousePricingConfig(TransactionCase):
    """Tests for rental.product.warehouse.pricing.config  [RP01, RP02, RP03]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env['rental.product.warehouse.pricing.config']
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id
        cls.coeff_type = cls.env['rental.coefficient.type'].create({
            'name': 'Config Test Type',
        })
        cls.coeff_table = cls.env['rental.coefficient.table'].create({
            'name': 'Config Test Table',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
        })
        cls.dp_table = cls.env['rental.dynamic.pricing.table'].create({
            'name': 'Config Test DP',
            'company_id': cls.company.id,
            'line_ids': [
                (0, 0, {
                    'start_datetime': datetime(2026, 7, 1),
                    'end_datetime': datetime(2026, 7, 31),
                    'factor_percentage': 130.0,
                }),
            ],
        })
        cls.product = cls.env['product.template'].create({
            'name': 'Test Rental Product',
            'rent_periodicity': 'days',
            'type': 'consu',
        })

    # -- RP01: create config ---------------------------------------------------

    def test_rp01_create_config(self):
        """A pricing config links product + warehouse + tables."""
        config = self.Config.create({
            'product_tmpl_id': self.product.id,
            'warehouse_id': self.warehouse.id,
            'coefficient_table_ids': [(6, 0, [self.coeff_table.id])],
            'dynamic_pricing_table_id': self.dp_table.id,
        })
        self.assertEqual(config.company_id, self.company)
        self.assertIn(self.coeff_table, config.coefficient_table_ids)
        self.assertEqual(config.dynamic_pricing_table_id, self.dp_table)

    # -- RP02: unique product + warehouse --------------------------------------

    @mute_logger('odoo.sql_db')
    def test_rp02_duplicate_product_warehouse_blocked(self):
        """Duplicate product + warehouse config is blocked."""
        self.Config.create({
            'product_tmpl_id': self.product.id,
            'warehouse_id': self.warehouse.id,
        })
        with self.assertRaises(IntegrityError):
            self.Config.create({
                'product_tmpl_id': self.product.id,
                'warehouse_id': self.warehouse.id,
            })

    # -- RP03: company from warehouse ------------------------------------------

    def test_rp03_company_derived_from_warehouse(self):
        """Company is automatically set from the warehouse."""
        config = self.Config.create({
            'product_tmpl_id': self.product.id,
            'warehouse_id': self.warehouse.id,
        })
        self.assertEqual(config.company_id, self.warehouse.company_id)


class TestProductTemplateHelpers(TransactionCase):
    """Tests for product.template pricing helpers  [RP05, RP06, RP07]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id
        cls.coeff_type = cls.env['rental.coefficient.type'].create({
            'name': 'Helper Test Type',
        })
        cls.coeff_table = cls.env['rental.coefficient.table'].create({
            'name': 'Helper Test Table',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'line_ids': [
                (0, 0, {'as_from_duration': 1, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 7, 'coefficient': 5.0}),
            ],
        })
        cls.dp_table = cls.env['rental.dynamic.pricing.table'].create({
            'name': 'Helper Test DP',
            'company_id': cls.company.id,
        })
        cls.product = cls.env['product.template'].create({
            'name': 'Helper Test Product',
            'rent_periodicity': 'days',
            'type': 'consu',
            'rental_pricing_config_ids': [
                (0, 0, {
                    'warehouse_id': cls.warehouse.id,
                    'coefficient_table_ids': [(6, 0, [cls.coeff_table.id])],
                    'dynamic_pricing_table_id': cls.dp_table.id,
                }),
            ],
        })

    def test_rp05_get_applicable_pricing_config(self):
        """Returns the config for the given warehouse."""
        config = self.product._get_applicable_pricing_config(self.warehouse)
        self.assertTrue(config)
        self.assertEqual(config.warehouse_id, self.warehouse)

    def test_rp05_no_config_for_unknown_warehouse(self):
        """Returns empty recordset for unconfigured warehouse."""
        other_wh = self.env['stock.warehouse'].search(
            [('id', '!=', self.warehouse.id)], limit=1,
        )
        if not other_wh:
            self.skipTest("Need a second warehouse")
        config = self.product._get_applicable_pricing_config(other_wh)
        self.assertFalse(config)

    def test_rp05_no_config_for_empty_warehouse(self):
        """Returns empty recordset when no warehouse is given."""
        config = self.product._get_applicable_pricing_config(
            self.env['stock.warehouse'],
        )
        self.assertFalse(config)

    def test_rp06_get_applicable_dynamic_pricing_table(self):
        """Returns the dynamic pricing table from the config."""
        table = self.product._get_applicable_dynamic_pricing_table(self.warehouse)
        self.assertEqual(table, self.dp_table)

    def test_rp06_no_table_when_no_config(self):
        """Returns empty when no config for the warehouse."""
        table = self.product._get_applicable_dynamic_pricing_table(
            self.env['stock.warehouse'],
        )
        self.assertFalse(table)

    def test_rp07_get_applicable_coefficient_tables(self):
        """Returns the coefficient tables from the config."""
        tables = self.product._get_applicable_coefficient_tables(self.warehouse)
        self.assertIn(self.coeff_table, tables)

    def test_rp07_no_tables_when_no_config(self):
        """Returns empty when no config for the warehouse."""
        tables = self.product._get_applicable_coefficient_tables(
            self.env['stock.warehouse'],
        )
        self.assertFalse(tables)


class TestPartnerCoefficientTables(TransactionCase):
    """Tests for res.partner allowed coefficient tables  [RP08, RP09]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.coeff_type = cls.env['rental.coefficient.type'].create({
            'name': 'Partner Test Type',
        })
        cls.table1 = cls.env['rental.coefficient.table'].create({
            'name': 'Partner Table 1',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
        })
        cls.table2 = cls.env['rental.coefficient.table'].create({
            'name': 'Partner Table 2',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Customer',
            'allowed_coefficient_table_ids': [
                (6, 0, [cls.table1.id, cls.table2.id]),
            ],
        })

    def test_rp08_partner_has_allowed_tables(self):
        """Partner has the allowed coefficient tables set."""
        self.assertEqual(len(self.partner.allowed_coefficient_table_ids), 2)

    def test_rp09_get_customer_tables_all(self):
        """Returns all tables when no company filter."""
        tables = self.partner._get_customer_allowed_coefficient_tables()
        self.assertEqual(len(tables), 2)

    def test_rp09_get_customer_tables_filtered(self):
        """Returns only tables matching the company."""
        tables = self.partner._get_customer_allowed_coefficient_tables(
            company=self.company,
        )
        self.assertEqual(len(tables), 2)

    def test_rp09_empty_when_no_tables_set(self):
        """Returns empty for partner with no tables."""
        empty_partner = self.env['res.partner'].create({
            'name': 'No Tables Partner',
        })
        tables = empty_partner._get_customer_allowed_coefficient_tables()
        self.assertFalse(tables)
